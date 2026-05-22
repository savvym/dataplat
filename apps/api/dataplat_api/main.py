"""FastAPI application entry point — S004-F-004 / S005-F-005.

Lifespan initialises shared resources (DagsterGateway) once at startup and
tears them down cleanly on shutdown.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from dataplat_api.config import settings
from dataplat_api.dagster.gateway import DagsterGateway
from dataplat_api.db.session import engine
from dataplat_api.routers.admin import router as admin_router
from dataplat_api.routers.health import router as health_router
from dataplat_api.routers.runs import admin_runs_router, runs_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup; close them on shutdown.

    DagsterGateway wraps a single shared AsyncClient (connection-pool reuse).
    It is stored on app.state so the get_dagster_gateway() dependency can
    retrieve it without module-level state.
    """
    # DB liveness probe — raises and aborts startup if Postgres is unreachable.
    # This makes /healthz reachability genuinely imply DB connectivity, which
    # is what verify/checks.sh smoke C2 relies on.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    gateway = DagsterGateway(graphql_url=settings.DAGSTER_GRAPHQL_URL)
    app.state.dagster_gateway = gateway
    yield
    await gateway.aclose()


app = FastAPI(title="Dataplat API", version="0.1.0", lifespan=lifespan)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(admin_runs_router)
app.include_router(runs_router)
