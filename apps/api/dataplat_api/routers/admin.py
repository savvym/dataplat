"""Admin router — S004-F-004.

Exposes internal operational endpoints. Currently unauthenticated; JWT
middleware will be wired in F-008 — see TODO below.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError
from dataplat_api.schemas.admin import DagsterStatusResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/dagster-status", response_model=DagsterStatusResponse)
async def dagster_status(
    gateway: DagsterGateway = Depends(get_dagster_gateway),
) -> DagsterStatusResponse:
    """Return the running Dagster version.

    Calls the Dagster GraphQL endpoint via DagsterGateway. Returns 503 if
    Dagster is unreachable or returns an unexpected response.

    TODO(F-008): add JWT dependency here once auth middleware is wired.
    """
    try:
        version = await gateway.get_dagster_version()
        return DagsterStatusResponse(dagster_version=version)
    except DagsterGatewayError as exc:
        # FastAPI passes Response subclasses through without serialisation;
        # mypy sees the union of DagsterStatusResponse|JSONResponse as an
        # error, but this is the standard pattern for in-route 4xx/5xx
        # overrides (agreed.md §4 note).
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": str(exc)},
        )
