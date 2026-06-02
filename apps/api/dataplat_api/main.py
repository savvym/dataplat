"""FastAPI application entry point — S004-F-004 / S005-F-005 / S008-F-008 / S022-F-022.

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
from dataplat_api.routers.auth import router as auth_router
from dataplat_api.routers.chunks import router as chunks_router
from dataplat_api.routers.documents import router as documents_router
from dataplat_api.routers.health import router as health_router
from dataplat_api.routers.operators import router as operators_router
from dataplat_api.routers.runs import admin_runs_router, runs_router
from dataplat_api.llm.router import router as llm_router
from dataplat_api.routers.recipes import router as recipes_router
from dataplat_api.routers.sources import router as sources_router


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
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(sources_router)
app.include_router(operators_router)
app.include_router(chunks_router)
app.include_router(recipes_router)
# F-028: Internal LLM gateway — excluded from public OpenAPI spec (include_in_schema=False on router).
app.include_router(llm_router)
