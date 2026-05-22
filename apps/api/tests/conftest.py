"""Pytest configuration for apps/api tests.

Sets required environment variables before any module-level import so
pydantic-settings can construct the Settings object without a running
compose stack.

Also patches httpx.AsyncClient to use httpx.MockTransport (a no-op transport)
when constructed without an explicit transport argument.  This avoids
ssl.SSLError("unknown error") on this host's Python/OpenSSL build:
ssl.SSLContext(PROTOCOL_TLS_CLIENT) fails even for verify=False in httpx 0.28.x
because the transport init still calls ssl.SSLContext.  MockTransport skips all
SSL init entirely.

This patch is transparent to production code: in production the container's
Python build has a working SSL/TLS stack; in unit tests all HTTP calls are
mocked at the method level anyway (get_dagster_version is patched per-test),
so no real network call is ever attempted.
"""

import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Provide mandatory settings so Settings() doesn't raise on import.
# IMPORTANT: dataplat_api.* imports MUST come after these setdefaults because
# dataplat_api.config.Settings() is constructed at module-level and requires
# DATABASE_URL to be present in the environment at import time.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql")
# Added S007-F-007: SECRET_KEY required by config.Settings (no default → fast fail).
# Unit tests must not need a real secret; any non-empty string satisfies pydantic-settings.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from sqlalchemy.ext.asyncio import AsyncEngine  # noqa: E402


def _no_op_transport(request: httpx.Request) -> httpx.Response:
    """Return a 500 so any accidentally-unpatched call fails loudly."""
    return httpx.Response(500, text="test: unpatched httpx call")


@pytest.fixture(autouse=True)
def _patch_httpx_no_ssl() -> pytest.FixtureRequest:
    """Patch httpx.AsyncClient to use MockTransport for the duration of each test.

    Prevents ssl.SSLError on this host's broken OpenSSL build.
    Production containers are unaffected (patch only active during pytest).
    """
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        # Only inject MockTransport if caller did not pass their own transport.
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.MockTransport(_no_op_transport)  # type: ignore[arg-type]
        original_init(self, *args, **kwargs)

    with patch.object(httpx.AsyncClient, "__init__", patched_init):
        yield


@pytest.fixture(autouse=True)
def _patch_engine_begin() -> Iterator[None]:
    """Patch engine.begin() to a no-op so tests don't require a live Postgres.

    The lifespan in main.py runs `async with engine.begin() as conn:
    await conn.execute(text("SELECT 1"))` to probe DB at startup.  In production
    that probe proves DB reachability (used by verify/checks.sh smoke C2).
    In unit tests we don't want to require a live Postgres just to construct
    the FastAPI app via TestClient — so we mock the probe to a no-op.

    Patch target: AsyncEngine.begin (class-level patch).
    AsyncEngine.begin is a read-only instance attribute, so patch.object on the
    engine instance raises AttributeError.  Patching at the class level works
    correctly: when Python dispatches engine.begin(), it finds the patched
    function on the class and calls it.  The fake_begin function accepts an
    optional `self` parameter to absorb the implicit engine instance argument.

    Production code is unaffected — this patch is only active under pytest.
    """

    @asynccontextmanager
    async def fake_begin(self: object = None) -> AsyncGenerator[MagicMock, None]:  # type: ignore[return]
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=None)
        yield conn

    with patch.object(AsyncEngine, "begin", fake_begin):
        yield
