"""Tests for GET /api/sources/collections — S010-F-010.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_list_collections_empty
  - test_list_collections_total_matches_owner_count
  - test_list_collections_limit_param
  - test_list_collections_offset_param
  - test_list_collections_items_shape
  - test_list_collections_owner_filter
  - test_list_collections_no_token_returns_401
  - test_list_collections_invalid_limit_zero_returns_422
  - test_list_collections_invalid_limit_negative_returns_422
  - test_list_collections_invalid_limit_over_cap_returns_422
  - test_list_collections_invalid_offset_negative_returns_422
  - test_list_collections_default_params_accepted

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET handler calls session.execute() TWICE — once for the paginated page,
  once for the total count.  The mock uses AsyncMock(side_effect=[...]) where
  the two side_effect items are plain MagicMock (NOT AsyncMock): only
  session.execute() itself is awaited; .scalars(), .all(), and .scalar_one()
  are synchronous calls on the result proxy.  Using AsyncMock for those would
  cause .scalars() to return a coroutine, producing a subtle runtime failure.

Auth-gate test does NOT override get_current_user — the real oauth2_scheme
raises 401 for a missing Authorization header.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import SourceCollection, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Session mock helpers ──────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 25, 10, 0, 0, tzinfo=timezone.utc)


def _make_collection(
    id: int,
    name: str,
    owner_id: int = 1,
    dataset_card_md: str | None = None,
) -> MagicMock:
    """Build a plain MagicMock that looks like a SourceCollection row.

    SourceCollectionOut uses from_attributes=True, so model_validate() reads
    attributes directly from the object — a MagicMock with the right attrs set
    is sufficient.  We avoid constructing a real SourceCollection instance
    because SQLAlchemy's instrumented attributes require _sa_instance_state to
    be present (set by the mapper), which __new__ alone does not provide.
    """
    coll = MagicMock(spec=SourceCollection)
    coll.id = id
    coll.name = name
    coll.owner_id = owner_id
    coll.dataset_card_md = dataset_card_md
    coll.created_at = _NOW
    coll.updated_at = _NOW
    return coll


def _make_session_dep(rows: list[SourceCollection], total: int) -> Any:
    """Return a get_session dependency override for the list endpoint.

    session.execute() is called twice by the handler:
      1st call — paginated page query → result with .scalars().all() == rows
      2nd call — COUNT query          → result with .scalar_one()   == total

    Both result mocks are plain MagicMock (NOT AsyncMock) because .scalars(),
    .all(), and .scalar_one() are synchronous calls on the result proxy.
    """
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = rows

    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[page_result, count_result])
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


# ── Helper: set both overrides and clean up ───────────────────────────────────


def _set_overrides(rows: list[SourceCollection], total: int) -> None:
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(rows, total)


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_list_collections_empty(client: TestClient) -> None:
    """GET with no collections for the current user → items=[], total=0, status 200."""
    _set_overrides(rows=[], total=0)
    try:
        response = client.get("/api/sources/collections")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


def test_list_collections_total_matches_owner_count(client: TestClient) -> None:
    """Mock returns 3 rows with count=3 → items has 3 elements, total == 3."""
    rows = [
        _make_collection(id=1, name="coll-a"),
        _make_collection(id=2, name="coll-b"),
        _make_collection(id=3, name="coll-c"),
    ]
    _set_overrides(rows=rows, total=3)
    try:
        response = client.get("/api/sources/collections")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_collections_limit_param(client: TestClient) -> None:
    """limit=2 with count=3 → items has 2 elements, total == 3."""
    rows = [
        _make_collection(id=1, name="coll-a"),
        _make_collection(id=2, name="coll-b"),
    ]
    _set_overrides(rows=rows, total=3)
    try:
        response = client.get("/api/sources/collections?limit=2")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


def test_list_collections_offset_param(client: TestClient) -> None:
    """offset=2 of 3 rows with count=3 → items has 1 element, total == 3."""
    rows = [_make_collection(id=3, name="coll-c")]
    _set_overrides(rows=rows, total=3)
    try:
        response = client.get("/api/sources/collections?offset=2")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] == 3


def test_list_collections_items_shape(client: TestClient) -> None:
    """Each item in items contains all expected keys from SourceCollectionOut."""
    rows = [_make_collection(id=1, name="coll-a", dataset_card_md="desc")]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/sources/collections")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    item = response.json()["items"][0]
    for key in (
        "id",
        "name",
        "owner_id",
        "dataset_card_md",
        "created_at",
        "updated_at",
    ):
        assert key in item, f"missing key '{key}' in item: {item}"
    assert item["id"] == 1
    assert item["name"] == "coll-a"
    assert item["owner_id"] == 1
    assert item["dataset_card_md"] == "desc"


def test_list_collections_owner_filter(client: TestClient) -> None:
    """The first session.execute() call carries a WHERE clause scoped to current_user.id.

    Verification approach (per agreed.md §6):
      1. Capture the Select object from the first execute() call via call_args_list.
      2. Compile it with literal_binds=True so the user id appears as a literal in SQL.
      3. Assert both "owner_id" and the user id literal appear in the compiled string.
    Do NOT stringify the raw Select — compile with literal_binds so the bound
    parameter value is visible.
    """
    rows: list[SourceCollection] = []
    # We need access to the session mock to inspect calls, so build it manually.
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = rows
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0

    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[page_result, count_result])
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    try:
        response = client.get("/api/sources/collections")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    first_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in compiled, f"'owner_id' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )


def test_list_collections_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/sources/collections")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_list_collections_invalid_limit_zero_returns_422(client: TestClient) -> None:
    """limit=0 violates ge=1 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.get("/api/sources/collections?limit=0")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_collections_invalid_limit_negative_returns_422(
    client: TestClient,
) -> None:
    """limit=-1 violates ge=1 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.get("/api/sources/collections?limit=-1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_collections_invalid_limit_over_cap_returns_422(
    client: TestClient,
) -> None:
    """limit=201 violates le=200 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.get("/api/sources/collections?limit=201")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_collections_invalid_offset_negative_returns_422(
    client: TestClient,
) -> None:
    """offset=-1 violates ge=0 → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.get("/api/sources/collections?offset=-1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_list_collections_default_params_accepted(client: TestClient) -> None:
    """GET with no query params uses defaults (limit=20, offset=0) → 200."""
    _set_overrides(rows=[], total=0)
    try:
        response = client.get("/api/sources/collections")
    finally:
        _clear_overrides()

    assert response.status_code == 200
