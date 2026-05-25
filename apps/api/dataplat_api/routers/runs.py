"""Runs router — S005-F-005, extended S008-F-008, extended S018-F-018.

Provides two API surfaces for Dagster run management:

  admin_runs_router (prefix="/api/admin/runs", tags=["admin", "runs"]):
    POST /hello-world  — trigger the hello_world smoke job (HTTP 201 Created)
    Protected by JWT Bearer auth (F-008).

  runs_router (prefix="/api/runs", tags=["runs"]):
    POST ""            — trigger a MinerU extraction backfill (HTTP 202 Accepted, F-018)
    GET  /{run_id}     — poll current status of a Dagster run (HTTP 200)
    Protected by JWT Bearer auth (F-008).

Deferrals:
  - GET /api/runs (list, paginated): F-049 (requires business run table from F-018).
  - GET /api/runs/{id}/logs proxy: beyond F-049.
  - WebSocket run-status events: F-051.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import (
    DagsterGateway,
    DagsterGatewayError,
    DagsterRunNotFoundError,
)
from dataplat_api.db.models import Run, Source, User
from dataplat_api.db.session import get_session
from dataplat_api.schemas.runs import (
    LaunchHelloWorldResponse,
    RunCreate,
    RunCreateResponse,
    RunStatusResponse,
)

# ── Admin router: admin-only run management operations ───────────────────────
# Full path: /api/admin/runs/<sub-path>
admin_runs_router = APIRouter(prefix="/api/admin/runs", tags=["admin", "runs"])

# ── Public runs router: per-run status queries ───────────────────────────────
# Full path: /api/runs/<sub-path>
runs_router = APIRouter(prefix="/api/runs", tags=["runs"])


@admin_runs_router.post(
    "/hello-world",
    response_model=LaunchHelloWorldResponse,
    status_code=201,
    summary="Trigger hello_world smoke job",
    description=(
        "Launch the hello_world_job in Dagster and return the assigned run ID. "
        "Each POST creates a new Dagster run (no idempotency / dedup). "
        "This is an admin smoke endpoint — not the generic run trigger (see F-018). "
        "Requires a valid Bearer JWT (F-008)."
    ),
)
async def launch_hello_world(
    gateway: DagsterGateway = Depends(get_dagster_gateway),
    current_user: User = Depends(get_current_user),
) -> LaunchHelloWorldResponse:
    """Trigger the hello_world_job in Dagster (admin smoke test).

    Returns HTTP 201 with the Dagster run UUID. The run is queued immediately
    but may not have started yet — poll GET /api/runs/{run_id} for status.

    Returns 503 if Dagster is unreachable or the launchRun mutation fails.
    Requires a valid Bearer JWT (F-008).
    """
    try:
        run_id = await gateway.launch_hello_world()
        return LaunchHelloWorldResponse(dagster_run_id=run_id)
    except DagsterGatewayError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": str(exc)},
        )


@runs_router.post(
    "",
    response_model=RunCreateResponse,
    status_code=202,
    summary="Trigger MinerU extraction backfill",
    description=(
        "Launch a Dagster asset backfill for extract_mineru over the given source IDs. "
        "Returns the Dagster backfillId and the Postgres run.id. "
        "Returns 404 if any source_id does not exist. "
        "Returns 503 if Dagster is unreachable or the backfill launch fails. "
        "Requires a valid Bearer JWT (F-008)."
    ),
)
async def trigger_extract_run(
    body: RunCreate,
    gateway: DagsterGateway = Depends(get_dagster_gateway),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RunCreateResponse:
    """Trigger an extract_mineru asset backfill for the given source IDs.

    Ordering (agreed.md §7):
    1. Validate source existence (404 if any missing).
    2. Convert source_ids → partition_keys ("src_{id}").
    3. Register partition keys in sources_partitions (defensive, idempotent).
    4. Launch Dagster backfill → get backfill_id (503 on DagsterGatewayError).
    5. Insert Run row into Postgres and return 202.
    """
    # Step 1: Validate source existence.
    result = await session.execute(
        select(Source.id).where(Source.id.in_(body.source_ids))
    )
    found_ids = {row[0] for row in result.fetchall()}
    missing = set(body.source_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source not found: {sorted(missing)}",
        )

    # Step 2: Convert source_ids → partition_keys.
    partition_keys = [f"src_{sid}" for sid in body.source_ids]

    # Step 3: Register partition keys in sources_partitions (defensive, idempotent).
    for pk in partition_keys:
        try:
            await gateway.add_source_partition(pk)
        except DagsterGatewayError:
            # Already-registered partitions return DuplicateDynamicPartitionError (no-op).
            # Other errors here are unexpected but should not block the backfill.
            pass

    # Step 4: Launch Dagster backfill.
    try:
        backfill_id = await gateway.launch_extract_backfill(partition_keys)
    except DagsterGatewayError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": str(exc)},
        )

    # Step 5: Insert Run row into Postgres.
    run = Run(
        dagster_run_id=backfill_id,
        kind="extract",
        asset_keys=["extract_mineru"],
        status="pending",
        partition_keys=partition_keys,
        triggered_by=current_user.id,
        config=None,
        trigger_context=None,
        source_collection_id=None,
        dataset_id=None,
        recipe_id=None,
        started_at=None,
        ended_at=None,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    return RunCreateResponse(dagster_run_id=backfill_id, run_id=run.id)


@runs_router.get(
    "/{run_id}",
    response_model=RunStatusResponse,
    summary="Get Dagster run status",
    description=(
        "Return the current status of a Dagster run. "
        "Queries Dagster directly — the local `run` Postgres table is not used "
        "(that requires auth context from F-008/F-018). "
        "Status values: 'running', 'success', 'failure'. "
        "Requires a valid Bearer JWT (F-008)."
    ),
)
async def get_run_status(
    run_id: str,
    gateway: DagsterGateway = Depends(get_dagster_gateway),
    current_user: User = Depends(get_current_user),
) -> RunStatusResponse:
    """Return the current Dagster run status (non-blocking poll).

    Returns HTTP 200 with the run ID and mapped status.
    Returns HTTP 404 if Dagster reports the run does not exist.
    Returns HTTP 503 if Dagster is unreachable or returns an error.

    DagsterRunNotFoundError is caught BEFORE DagsterGatewayError
    (because it is a subclass — catching the base class first would swallow 404s).
    Requires a valid Bearer JWT (F-008).
    """
    try:
        result = await gateway.get_run_status(run_id)
        return RunStatusResponse(**result)
    except DagsterRunNotFoundError:
        # Catch specific subclass BEFORE the base class (agreed.md §4.2).
        return JSONResponse(  # type: ignore[return-value]
            status_code=404,
            content={"detail": "run not found"},
        )
    except DagsterGatewayError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": str(exc)},
        )
