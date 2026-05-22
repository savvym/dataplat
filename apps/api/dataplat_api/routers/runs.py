"""Runs router — S005-F-005.

Provides two API surfaces for Dagster run management:

  admin_runs_router (prefix="/api/admin/runs", tags=["admin", "runs"]):
    POST /hello-world  — trigger the hello_world smoke job (HTTP 201 Created)
    TODO(F-008): require admin role on this router once JWT middleware is wired.
    TODO(F-018): add POST /api/runs (generic trigger with source_ids + auth).

  runs_router (prefix="/api/runs", tags=["runs"]):
    GET  /{run_id}     — poll current status of a Dagster run (HTTP 200)
    TODO(F-008): require auth on this router.

Deferrals:
  - Generic POST /api/runs surface: F-018 (requires auth + source context).
  - GET /api/runs (list, paginated): F-049 (requires business run table from F-018).
  - GET /api/runs/{id}/logs proxy: beyond F-049.
  - WebSocket run-status events: F-051.
  - Writing to the Postgres `run` table: F-018/F-009 (requires triggered_by FK
    from JWT auth).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import (
    DagsterGateway,
    DagsterGatewayError,
    DagsterRunNotFoundError,
)
from dataplat_api.schemas.runs import LaunchHelloWorldResponse, RunStatusResponse

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
        "TODO(F-008): require admin role."
    ),
)
async def launch_hello_world(
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> LaunchHelloWorldResponse:
    """Trigger the hello_world_job in Dagster (admin smoke test).

    Returns HTTP 201 with the Dagster run UUID. The run is queued immediately
    but may not have started yet — poll GET /api/runs/{run_id} for status.

    Returns 503 if Dagster is unreachable or the launchRun mutation fails.

    TODO(F-008): add JWT admin-role dependency here.
    """
    try:
        run_id = await gateway.launch_hello_world()
        return LaunchHelloWorldResponse(dagster_run_id=run_id)
    except DagsterGatewayError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": str(exc)},
        )


@runs_router.get(
    "/{run_id}",
    response_model=RunStatusResponse,
    summary="Get Dagster run status",
    description=(
        "Return the current status of a Dagster run. "
        "Queries Dagster directly — the local `run` Postgres table is not used "
        "(that requires auth context from F-008/F-018). "
        "Status values: 'running', 'success', 'failure'. "
        "TODO(F-008): require auth."
    ),
)
async def get_run_status(
    run_id: str,
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> RunStatusResponse:
    """Return the current Dagster run status (non-blocking poll).

    Returns HTTP 200 with the run ID and mapped status.
    Returns HTTP 404 if Dagster reports the run does not exist.
    Returns HTTP 503 if Dagster is unreachable or returns an error.

    DagsterRunNotFoundError is caught BEFORE DagsterGatewayError
    (because it is a subclass — catching the base class first would swallow 404s).

    TODO(F-008): add JWT dependency here.
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
