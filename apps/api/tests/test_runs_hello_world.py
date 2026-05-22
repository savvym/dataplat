"""Tests for POST /api/admin/runs/hello-world and GET /api/runs/{run_id} — S005-F-005,
extended S008-F-008.

Uses FastAPI's TestClient (sync ASGI wrapper) with DagsterGateway methods
patched via unittest.mock.AsyncMock. No live Dagster instance required.

The conftest.py autouse `_patch_httpx_no_ssl` fixture applies to all tests here —
it patches httpx.AsyncClient to use MockTransport, bypassing SSL initialisation.
Gateway methods are additionally mocked at the method level per test, so no real
HTTP call is ever attempted against Dagster.

S008-F-008: Both routes are now protected by JWT. Tests override `get_current_user`
to bypass JWT validation — the auth enforcement itself is tested in test_auth.py.

NOTE for future test authors: if a test needs a real HTTP call through the
TestClient (e.g. to test middleware), the httpx.MockTransport from conftest.py
applies to the *gateway's* AsyncClient, not to the TestClient itself. The
TestClient uses an ASGI transport internally and is unaffected by the conftest
patch. No extra plumbing is needed.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.dagster.gateway import DagsterGatewayError, DagsterRunNotFoundError
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
    `app.state.dagster_gateway`. The conftest.py `_patch_httpx_no_ssl` autouse
    fixture ensures the httpx.AsyncClient inside DagsterGateway is constructed
    without an SSL context.

    get_current_user is overridden to bypass JWT validation for all tests in
    this module — auth enforcement is covered by test_auth.py.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── POST /api/admin/runs/hello-world ─────────────────────────────────────────


def test_launch_hello_world_201(client: TestClient) -> None:
    """Gateway returns a UUID → POST returns HTTP 201 with dagster_run_id."""
    fake_run_id = str(uuid.uuid4())
    with patch.object(
        app.state.dagster_gateway,
        "launch_hello_world",
        new=AsyncMock(return_value=fake_run_id),
    ):
        response = client.post("/api/admin/runs/hello-world")

    assert response.status_code == 201
    body = response.json()
    assert "dagster_run_id" in body
    assert body["dagster_run_id"] == fake_run_id


def test_launch_hello_world_503_on_gateway_error(client: TestClient) -> None:
    """Gateway raises DagsterGatewayError → POST returns HTTP 503."""
    with patch.object(
        app.state.dagster_gateway,
        "launch_hello_world",
        new=AsyncMock(side_effect=DagsterGatewayError("dagster is down")),
    ):
        response = client.post("/api/admin/runs/hello-world")

    assert response.status_code == 503
    body = response.json()
    assert "detail" in body
    assert "dagster is down" in body["detail"]


# ── GET /api/runs/{run_id} ────────────────────────────────────────────────────


def test_get_run_status_200_success(client: TestClient) -> None:
    """Gateway returns success status → GET returns HTTP 200 with matching body."""
    fake_run_id = str(uuid.uuid4())
    with patch.object(
        app.state.dagster_gateway,
        "get_run_status",
        new=AsyncMock(
            return_value={"dagster_run_id": fake_run_id, "status": "success"}
        ),
    ):
        response = client.get(f"/api/runs/{fake_run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["dagster_run_id"] == fake_run_id
    assert body["status"] == "success"


def test_get_run_status_404_when_not_found(client: TestClient) -> None:
    """Gateway raises DagsterRunNotFoundError → GET returns HTTP 404.

    DagsterRunNotFoundError is a subclass of DagsterGatewayError — the route
    catches the more specific error first to return 404, not 503.
    """
    fake_run_id = str(uuid.uuid4())
    with patch.object(
        app.state.dagster_gateway,
        "get_run_status",
        new=AsyncMock(
            side_effect=DagsterRunNotFoundError(f"run not found: {fake_run_id}")
        ),
    ):
        response = client.get(f"/api/runs/{fake_run_id}")

    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
    assert body["detail"] == "run not found"


def test_get_run_status_503_on_gateway_error(client: TestClient) -> None:
    """Gateway raises DagsterGatewayError → GET returns HTTP 503."""
    fake_run_id = str(uuid.uuid4())
    with patch.object(
        app.state.dagster_gateway,
        "get_run_status",
        new=AsyncMock(side_effect=DagsterGatewayError("dagster unreachable")),
    ):
        response = client.get(f"/api/runs/{fake_run_id}")

    assert response.status_code == 503
    body = response.json()
    assert "detail" in body
    assert "dagster unreachable" in body["detail"]
