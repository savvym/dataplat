"""Tests for POST /api/sources/collections — S009-F-009.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_create_collection_201
  - test_create_collection_db_row_via_session_add
  - test_create_collection_duplicate_returns_409
  - test_create_collection_no_token_returns_401
  - test_create_collection_missing_name_returns_422
  - test_create_collection_empty_name_returns_422
  - test_create_collection_whitespace_name_returns_422
  - test_create_collection_name_too_long_returns_422
  - test_create_collection_no_card_md_returns_201
  - test_create_collection_extra_fields_ignored

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Dependency-override pattern (mirrors test_admin_dagster_status.py and test_auth.py):
  - get_current_user is overridden per-test or via the `client` fixture to bypass JWT.
  - get_session is overridden per-test to inject an AsyncMock session.
  - All overrides are cleaned up in finally blocks.

Auth-gate tests (test_create_collection_no_token_returns_401) do NOT override
get_current_user — they rely on the real oauth2_scheme raising 401 for a missing
Authorization header.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_with_refresh(
    refresh_id: int = 42,
    refresh_name: str = "test-coll",
    refresh_card_md: str | None = "desc",
) -> Any:
    """Return a get_session dependency override that mocks a successful add/commit/refresh.

    The refresh side_effect sets id, name, owner_id, dataset_card_md, created_at,
    updated_at on the ORM object to simulate what Postgres + SQLAlchemy would populate.
    """
    _now = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()  # synchronous in AsyncSession

        def _refresh_side_effect(obj: Any) -> None:
            obj.id = refresh_id
            obj.name = refresh_name
            obj.owner_id = 1
            obj.dataset_card_md = refresh_card_md
            obj.created_at = _now
            obj.updated_at = _now

        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    return _override


def _make_session_dep_raising(exc: Exception) -> Any:
    """Return a get_session dependency override where commit raises exc."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock(side_effect=exc)
        yield session

    return _override


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """TestClient with app lifespan initialised.

    Does NOT set a get_current_user override — tests that need auth bypass
    set their own override inside the test body using try/finally.
    """
    with TestClient(app) as c:
        yield c


# ── Happy path ────────────────────────────────────────────────────────────────


def test_create_collection_201(client: TestClient) -> None:
    """POST with valid body returns 201 with id (int) and name in response."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_with_refresh(
        refresh_id=42, refresh_name="test-coll", refresh_card_md="desc"
    )
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "test-coll", "dataset_card_md": "desc"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)
    assert body["id"] == 42
    assert body["name"] == "test-coll"


def test_create_collection_db_row_via_session_add(client: TestClient) -> None:
    """session.add() is called once with a SourceCollection having correct name and owner_id."""
    from dataplat_api.db.models import SourceCollection

    captured: list[SourceCollection] = []

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        def _add_side_effect(obj: Any) -> None:
            captured.append(obj)

        session.add = MagicMock(side_effect=_add_side_effect)

        def _refresh_side_effect(obj: Any) -> None:
            obj.id = 99
            obj.name = "test-coll-row"
            obj.owner_id = 1
            obj.dataset_card_md = None
            obj.created_at = datetime(2026, 5, 22, tzinfo=timezone.utc)
            obj.updated_at = datetime(2026, 5, 22, tzinfo=timezone.utc)

        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _override
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "test-coll-row"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 201
    assert len(captured) == 1
    added = captured[0]
    assert isinstance(added, SourceCollection)
    assert added.name == "test-coll-row"
    assert added.owner_id == 1


# ── Conflict path (409) ───────────────────────────────────────────────────────


def test_create_collection_duplicate_returns_409(client: TestClient) -> None:
    """Duplicate name (IntegrityError with source_collection_name_key) returns 409."""
    dup_exc = IntegrityError(
        "",
        {},
        Exception(
            'duplicate key value violates unique constraint "source_collection_name_key"'
        ),
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_raising(dup_exc)
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "duplicate-name"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 409
    assert response.json() == {"detail": "Collection name already exists"}


# ── Auth gate ─────────────────────────────────────────────────────────────────


def test_create_collection_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.post(
        "/api/sources/collections",
        json={"name": "test-coll"},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


# ── Validation (422) ──────────────────────────────────────────────────────────


def test_create_collection_missing_name_returns_422(client: TestClient) -> None:
    """Missing required 'name' field → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/sources/collections",
            json={"dataset_card_md": "desc"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_create_collection_empty_name_returns_422(client: TestClient) -> None:
    """Empty string 'name' → 422 (min_length=1)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": ""},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_create_collection_whitespace_name_returns_422(client: TestClient) -> None:
    """Whitespace-only 'name' is stripped to '' → 422 (min_length=1 after strip)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "   "},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_create_collection_name_too_long_returns_422(client: TestClient) -> None:
    """Name of 256 chars (> max 255) → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "a" * 256},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


# ── Optional fields / extras ──────────────────────────────────────────────────


def test_create_collection_no_card_md_returns_201(client: TestClient) -> None:
    """POST without dataset_card_md returns 201 with dataset_card_md: null."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_with_refresh(
        refresh_id=7, refresh_name="no-card", refresh_card_md=None
    )
    try:
        response = client.post(
            "/api/sources/collections",
            json={"name": "no-card"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 201
    body = response.json()
    assert body["dataset_card_md"] is None


def test_create_collection_extra_fields_ignored(client: TestClient) -> None:
    """Unknown fields in request body are silently discarded (extra='ignore')."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_with_refresh(
        refresh_id=8, refresh_name="test-extra", refresh_card_md=None
    )
    try:
        response = client.post(
            "/api/sources/collections",
            json={
                "name": "test-extra",
                "unknown_field": "garbage",
                "dataset_card_md": None,
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "test-extra"
    assert "unknown_field" not in body
