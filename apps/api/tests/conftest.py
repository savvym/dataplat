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
from unittest.mock import patch

import httpx
import pytest

# Provide mandatory settings so Settings() doesn't raise on import.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql")


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
