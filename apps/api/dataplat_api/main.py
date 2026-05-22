"""FastAPI application entry point — S004-F-004.

Lifespan initialises shared resources (DagsterGateway) once at startup and
tears them down cleanly on shutdown.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from dataplat_api.config import settings
from dataplat_api.dagster.gateway import DagsterGateway
from dataplat_api.routers.admin import router as admin_router
from dataplat_api.routers.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup; close them on shutdown.

    DagsterGateway wraps a single shared AsyncClient (connection-pool reuse).
    It is stored on app.state so the get_dagster_gateway() dependency can
    retrieve it without module-level state.
    """
    gateway = DagsterGateway(graphql_url=settings.DAGSTER_GRAPHQL_URL)
    app.state.dagster_gateway = gateway
    yield
    await gateway.aclose()


app = FastAPI(title="Dataplat API", version="0.1.0", lifespan=lifespan)

app.include_router(health_router)
app.include_router(admin_router)
