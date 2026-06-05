"""Router for POST /api/dagster/events — S050-F-050 / S052-F-052.

Receives run-status webhook events from the Dagster run_status_sensor and
updates the matching Run row in Postgres.  Extended in S052 to handle
ASSET_MATERIALIZATION events from @asset_sensor instances, which publish
AssetMaterializedEvent / ChunksAddedEvent to the NotificationBroker.

Auth: shared-secret header X-Dagster-Webhook-Secret (no Bearer JWT — the
Dagster daemon cannot obtain a JWT; shared-secret is the standard pattern
for service-to-service webhooks).

Security checks (order is load-bearing per agreed.md §5):
  1. Fail-closed guard: if DAGSTER_WEBHOOK_SECRET is empty → 500.
  2. secrets.compare_digest on the provided header → 401 on mismatch.
  3. Pydantic body parse → 422 on invalid payload.
  4. DB lookup by dagster_run_id → 200 processed=False if unknown.
  5. Dispatch by event_type:
     a. RUN_START / RUN_SUCCESS / RUN_FAILURE / RUN_CANCELED →
        State-transition: update status (+ started_at / ended_at).
        Commit. Fan-out run.status_changed to RunEventBroker.
     b. ASSET_MATERIALIZATION →
        Resolve triggered_by from Run row. Build notification event.
        Fan-out to NotificationBroker (NO DB mutation, NO commit).
  6. Return 200 processed=True.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.config import settings
from dataplat_api.db.models import Run
from dataplat_api.db.session import get_session
from dataplat_api.schemas.dagster_events import DagsterEventResponse, DagsterRunEventPayload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dagster", tags=["dagster"])


@router.post("/events", response_model=DagsterEventResponse)
async def post_dagster_event(
    body: DagsterRunEventPayload,
    request: Request,
    x_dagster_webhook_secret: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> DagsterEventResponse:
    """Receive a Dagster event and update state / publish notifications.

    Called by:
    - fastapi_run_status_sensor: run lifecycle events (RUN_START/SUCCESS/etc.)
    - extract_mineru_notification_sensor: asset.materialized for extract_mineru
    - chunks_notification_sensor: chunks.added with chunk_count from metadata

    HTTP status codes:
      200 — event processed (or silently ignored for unknown dagster_run_id)
      401 — missing or invalid X-Dagster-Webhook-Secret header
      422 — invalid payload (bad event_type, missing fields, etc.)
      500 — DAGSTER_WEBHOOK_SECRET not configured on this server
    """
    # ── Check 1: fail-closed guard (agreed.md §5 step 0) ─────────────────────
    # If the server secret is empty, secrets.compare_digest("", "") returns True
    # and any caller sending an empty header would pass auth — fail-open.
    # Raise 500 immediately so a misconfigured deployment is visible rather than
    # silently accepting unauthenticated callers.
    if not settings.DAGSTER_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Webhook secret not configured on this server",
        )

    # ── Check 2: timing-safe shared-secret comparison ─────────────────────────
    if x_dagster_webhook_secret is None or not secrets.compare_digest(
        x_dagster_webhook_secret, settings.DAGSTER_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    # ── Check 3: body already parsed + validated by Pydantic (FastAPI 422) ────

    # ── Check 4: look up the Run row ──────────────────────────────────────────
    result = await session.execute(
        select(Run).where(Run.dagster_run_id == body.dagster_run_id)
    )
    run = result.scalar_one_or_none()

    if run is None:
        # Unknown dagster_run_id — silently ignore.
        # Rationale: the sensor fires for ALL Dagster runs (incl. hello_world_job
        # and any non-tracked backfill). A 404 would cause the sensor to retry;
        # HTTP 200 with processed=False is the correct production behaviour.
        return DagsterEventResponse(processed=False, reason="unknown_run")

    # ── Check 5a: run-status event dispatch ───────────────────────────────────
    if body.event_type in ("RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED"):
        # Capture prev_status BEFORE mutation so the broker event has the `from` field.
        prev_status: str | None = run.status

        if body.event_type == "RUN_START":
            run.status = "running"
            run.started_at = body.timestamp  # type: ignore[assignment]
        elif body.event_type == "RUN_SUCCESS":
            run.status = "success"
            run.ended_at = body.timestamp  # type: ignore[assignment]
        elif body.event_type in ("RUN_FAILURE", "RUN_CANCELED"):
            run.status = "failure"
            run.ended_at = body.timestamp  # type: ignore[assignment]

        # Persist
        await session.commit()

        # Fan-out to WS subscribers (best-effort, §4.5 F-051)
        if prev_status != run.status:
            run_event: dict = {
                "type": "run.status_changed",
                "run_id": run.id,
                "kind": run.kind,
                "from": prev_status,
                "to": run.status,
                "metadata": {},
            }
            try:
                request.app.state.run_broker.publish(run_id=run.id, event=run_event)
            except Exception as exc:
                # Never break HTTP 200 due to broker errors — best-effort delivery.
                logger.warning("run_broker.publish failed for run %s: %s", run.id, exc)

    # ── Check 5b: asset materialization event dispatch (S052-F-052) ───────────
    elif body.event_type == "ASSET_MATERIALIZATION":
        # Route notification to the user who triggered the run (§6.2).
        user_id: int | None = run.triggered_by
        if user_id is None:
            # No owner — drop event (§6.2: event recognized but not routable).
            logger.warning(
                "ASSET_MATERIALIZATION: run %s has triggered_by=None; dropping notification",
                run.id,
            )
            return DagsterEventResponse(processed=True)

        # Build the appropriate notification event.
        notification_event: dict | None = _build_notification_event(body)
        if notification_event is None:
            # Malformed partition_key — drop event (T15 / OQ-6 / L1 defensive path).
            # Already logged inside _build_notification_event.
            return DagsterEventResponse(processed=True)

        # Publish to NotificationBroker — best-effort, wrapped in try/except (§8a).
        try:
            request.app.state.notification_broker.publish(
                user_id=user_id, event=notification_event
            )
        except Exception as exc:
            # Never break HTTP 200 due to broker errors — best-effort delivery.
            # T16: this except is LOAD-BEARING — DB has no commit here so no
            # session rollback needed; return 200 without propagating.
            logger.warning(
                "notification_broker.publish failed for user %s: %s", user_id, exc
            )

    # ── Respond ───────────────────────────────────────────────────────────────
    return DagsterEventResponse(processed=True)


def _build_notification_event(body: DagsterRunEventPayload) -> dict | None:
    """Build the wire-format notification event dict from an ASSET_MATERIALIZATION payload.

    Returns None if the partition_key is missing or malformed (logs WARNING).
    Callers that get None should return HTTP 200 processed=True (event was
    recognized but not routable due to bad data — not a sensor bug).
    """
    asset_key: str = body.asset_key or ""
    partition_key: str | None = body.partition_key

    if asset_key == "chunks":
        # ChunksAddedEvent — parse source_id from "src_{N}"
        if not partition_key:
            logger.warning(
                "_build_notification_event: chunks asset has null partition_key; "
                "dropping notification"
            )
            return None
        try:
            source_id = int(partition_key.removeprefix("src_"))
        except ValueError:
            logger.warning(
                "_build_notification_event: chunks asset has malformed partition_key=%r "
                "(expected 'src_<int>'); dropping notification",
                partition_key,
            )
            return None
        chunk_count: int = int(body.metadata.get("chunk_count", 0))
        return {
            "type": "chunks.added",
            "source_id": source_id,
            "count": chunk_count,
        }
    else:
        # AssetMaterializedEvent — for extract_mineru and any other asset.
        if not partition_key:
            logger.warning(
                "_build_notification_event: asset=%r has null partition_key; "
                "dropping notification",
                asset_key,
            )
            return None
        return {
            "type": "asset.materialized",
            "asset_key": asset_key,
            "partition_key": partition_key,
        }
