"""Router for POST /api/dagster/events — S050-F-050.

Receives run-status webhook events from the Dagster run_status_sensor and
updates the matching Run row in Postgres.

Auth: shared-secret header X-Dagster-Webhook-Secret (no Bearer JWT — the
Dagster daemon cannot obtain a JWT; shared-secret is the standard pattern
for service-to-service webhooks).

Security checks (order is load-bearing per agreed.md §5):
  1. Fail-closed guard: if DAGSTER_WEBHOOK_SECRET is empty → 500.
  2. secrets.compare_digest on the provided header → 401 on mismatch.
  3. Pydantic body parse → 422 on invalid payload.
  4. DB lookup by dagster_run_id → 200 processed=False if unknown.
  5. State-transition: update status (+ started_at / ended_at).
  6. await session.commit().
  7. Return 200 processed=True.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.config import settings
from dataplat_api.db.models import Run
from dataplat_api.db.session import get_session
from dataplat_api.schemas.dagster_events import DagsterEventResponse, DagsterRunEventPayload

router = APIRouter(prefix="/api/dagster", tags=["dagster"])


@router.post("/events", response_model=DagsterEventResponse)
async def post_dagster_event(
    body: DagsterRunEventPayload,
    x_dagster_webhook_secret: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> DagsterEventResponse:
    """Receive a Dagster run-status event and update the matching Run row.

    Called by the fastapi_run_status_sensor in dagster_platform/definitions.py
    whenever a Dagster run transitions to STARTED / SUCCESS / FAILURE / CANCELED.

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

    # ── Check 5: apply state transition (agreed.md §3.3) ─────────────────────
    if body.event_type == "RUN_START":
        run.status = "running"
        run.started_at = body.timestamp  # type: ignore[assignment]
    elif body.event_type == "RUN_SUCCESS":
        run.status = "success"
        run.ended_at = body.timestamp  # type: ignore[assignment]
    elif body.event_type in ("RUN_FAILURE", "RUN_CANCELED"):
        run.status = "failure"
        run.ended_at = body.timestamp  # type: ignore[assignment]

    # ── Check 6: persist ──────────────────────────────────────────────────────
    await session.commit()

    # ── Check 7: respond ──────────────────────────────────────────────────────
    return DagsterEventResponse(processed=True)
