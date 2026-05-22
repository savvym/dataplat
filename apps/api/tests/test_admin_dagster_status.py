"""Tests for GET /api/admin/dagster-status — S004-F-004 (OQ-7), extended S008-F-008.

Uses FastAPI's TestClient (sync wrapper around ASGI) with DagsterGateway's
`get_dagster_version` patched via unittest.mock.AsyncMock so no live Dagster
instance is required.  SSL is bypassed by conftest.py's autouse fixture.

S008-F-008: GET /api/admin/dagster-status is now protected. Tests override
`get_current_user` to bypass JWT validation — the auth enforcement itself is
tested in test_auth.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.dagster.gateway import DagsterGatewayError
from dataplat_api.db.models import User
from dataplat_api.main import app

# Shared mock user for auth override across all tests in this module.
_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


@pytest.fixture()
def client() -> TestClient:
    """Return a test client with a fully initialised app lifespan.

    `with TestClient(app)` triggers the lifespan context manager which creates
    `app.state.dagster_gateway`.  The conftest.py `_patch_httpx_ssl` fixture
    ensures the httpx.AsyncClient inside DagsterGateway is constructed without
    an SSL context (required on this host's Python/OpenSSL build).

    get_current_user is overridden to bypass JWT validation for all tests in
    this module — auth enforcement is covered by test_auth.py.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_dagster_status_200(client: TestClient) -> None:
    """Mock get_dagster_version returning a valid string → HTTP 200."""
    with patch.object(
        app.state.dagster_gateway,
        "get_dagster_version",
        new=AsyncMock(return_value="1.11.16"),
    ):
        response = client.get("/api/admin/dagster-status")

    assert response.status_code == 200
    body = response.json()
    assert body == {"dagster_version": "1.11.16"}


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


def test_dagster_status_503_on_gateway_error(client: TestClient) -> None:
    """Mock get_dagster_version raising DagsterGatewayError → HTTP 503."""
    with patch.object(
        app.state.dagster_gateway,
        "get_dagster_version",
        new=AsyncMock(side_effect=DagsterGatewayError("connection refused")),
    ):
        response = client.get("/api/admin/dagster-status")

    assert response.status_code == 503
    body = response.json()
    assert "detail" in body
    assert "connection refused" in body["detail"]
