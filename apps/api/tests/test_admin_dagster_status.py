"""Tests for GET /api/admin/dagster-status — S004-F-004 (OQ-7).

Uses FastAPI's TestClient (sync wrapper around ASGI) with DagsterGateway's
`get_dagster_version` patched via unittest.mock.AsyncMock so no live Dagster
instance is required.  SSL is bypassed by conftest.py's autouse fixture.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.dagster.gateway import DagsterGatewayError
from dataplat_api.main import app


@pytest.fixture()
def client() -> TestClient:
    """Return a test client with a fully initialised app lifespan.

    `with TestClient(app)` triggers the lifespan context manager which creates
    `app.state.dagster_gateway`.  The conftest.py `_patch_httpx_ssl` fixture
    ensures the httpx.AsyncClient inside DagsterGateway is constructed without
    an SSL context (required on this host's Python/OpenSSL build).
    """
    with TestClient(app) as c:
        yield c


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
