"""FastAPI dependency for DagsterGateway — S004-F-004.

Usage in a route:

    from dataplat_api.dagster.dependencies import get_dagster_gateway

    @router.get("/some-route")
    async def my_route(
        gateway: DagsterGateway = Depends(get_dagster_gateway),
    ) -> ...:
        ...
"""

from fastapi import Request

from dataplat_api.dagster.gateway import DagsterGateway


def get_dagster_gateway(request: Request) -> DagsterGateway:
    """Return the DagsterGateway singleton from app.state.

    The gateway is initialised once in the lifespan context manager in
    `dataplat_api.main` and stored on `app.state.dagster_gateway`.
    This dependency retrieves it for injection into route handlers.
    """
    return request.app.state.dagster_gateway  # type: ignore[no-any-return]
