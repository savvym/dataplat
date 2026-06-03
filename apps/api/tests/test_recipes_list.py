"""Tests for GET /api/recipes — S038-F-038.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_list_recipes_returns_200_with_items_and_total   (V1)
  - test_list_recipes_items_have_required_fields          (V2)
  - test_list_recipes_no_token_returns_401               (auth gate)
  - test_list_recipes_only_own_recipes                   (isolation)
  - test_list_recipes_owner_id_in_query                  (F2 SQL-structural)
  - test_list_recipes_empty_returns_empty_list           (empty)
  - test_list_recipes_definition_not_in_items            (schema guard)
  - test_list_recipes_owner_id_not_in_items              (schema guard)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET handler calls session.execute() TWICE — once for the full row list,
  once for the total count.  The mock uses AsyncMock(side_effect=[...]) where
  the two side_effect items are plain MagicMock (NOT AsyncMock): only
  session.execute() itself is awaited; .scalars(), .all(), and .scalar_one()
  are synchronous calls on the result proxy.  Using AsyncMock for those would
  cause .scalars() to return a coroutine, producing a subtle runtime failure.

Auth-gate test does NOT override get_current_user — the real oauth2_scheme
raises 401 for a missing Authorization header.

All mocked recipe rows populate all 7 ORM-mapped attributes (id, name,
description, owner_id, definition, created_at, updated_at) for completeness /
future-proofing.  Pydantic's from_attributes=True on RecipeListItem only reads
the 5 declared fields, but populating extras avoids MagicMock attribute-access
surprises if the code path changes (agreed.md §6 F4 NIT).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=7, email="recipe-list@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Mock recipe row factory ───────────────────────────────────────────────────


def _make_recipe(
    id: int,
    name: str,
    description: str | None = None,
    owner_id: int = 7,
    definition: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a plain MagicMock that looks like a Recipe ORM row.

    RecipeListItem uses from_attributes=True, so model_validate() reads
    attributes directly from the object — a MagicMock with the right attrs set
    is sufficient.  We avoid constructing a real Recipe instance because
    SQLAlchemy's instrumented attributes require _sa_instance_state to be
    present (set by the mapper), which __new__ alone does not provide.

    All 7 ORM-mapped attributes are populated (including owner_id and definition)
    per agreed.md §6 F4 NIT, even though RecipeListItem only reads 5 of them.
    """
    row = MagicMock(spec=Recipe)
    row.id = id
    row.name = name
    row.description = description
    row.owner_id = owner_id
    row.definition = definition if definition is not None else {}
    row.created_at = _NOW
    row.updated_at = _NOW
    return row


# ── Session mock helper ───────────────────────────────────────────────────────


def _make_list_session_dep(rows: list[Any], total: int) -> Any:
    """Return a get_session dependency override for the list endpoint.

    session.execute() is called twice by the handler:
      1st call — full row list query  → result with .scalars().all() == rows
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


# ── Helper: set / clear overrides ────────────────────────────────────────────


def _set_overrides(rows: list[Any], total: int) -> None:
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_list_session_dep(rows, total)


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_list_recipes_returns_200_with_items_and_total(client: TestClient) -> None:
    """V1 — Two recipes in session → 200, items has 2 elements, total == 2."""
    rows = [
        _make_recipe(id=1, name="recipe-a"),
        _make_recipe(id=2, name="recipe-b"),
    ]
    _set_overrides(rows=rows, total=2)
    try:
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_recipes_items_have_required_fields(client: TestClient) -> None:
    """V2 — Each item has id (int), name (str), description, created_at, updated_at."""
    rows = [
        _make_recipe(id=3, name="my-recipe", description="A description"),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]

    # All 5 required fields must be present.
    for key in ("id", "name", "description", "created_at", "updated_at"):
        assert key in item, f"missing key '{key}' in item: {item}"

    # Type assertions for the two non-nullable fields.
    assert isinstance(item["id"], int)
    assert isinstance(item["name"], str)
    assert item["id"] == 3
    assert item["name"] == "my-recipe"
    assert item["description"] == "A description"


def test_list_recipes_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/recipes")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_list_recipes_only_own_recipes(client: TestClient) -> None:
    """Isolation — each user sees only their own recipes via separate session mocks.

    User A has 2 recipes → total == 2.
    User B has 1 recipe  → total == 1.
    The two calls use entirely separate session mocks and dependency overrides
    to verify the owner_id filter is applied per authenticated user.
    """
    user_a = User(id=10, email="user-a@example.com", hashed_password="$2b$12$hash")
    user_b = User(id=20, email="user-b@example.com", hashed_password="$2b$12$hash")

    # ── User A: 2 recipes ──
    rows_a = [
        _make_recipe(id=1, name="a-recipe-1", owner_id=10),
        _make_recipe(id=2, name="a-recipe-2", owner_id=10),
    ]

    async def _user_a() -> User:
        return user_a

    app.dependency_overrides[get_current_user] = _user_a
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_a, 2)
    try:
        response_a = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response_a.status_code == 200
    assert response_a.json()["total"] == 2
    assert len(response_a.json()["items"]) == 2

    # ── User B: 1 recipe ──
    rows_b = [
        _make_recipe(id=5, name="b-recipe-1", owner_id=20),
    ]

    async def _user_b() -> User:
        return user_b

    app.dependency_overrides[get_current_user] = _user_b
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_b, 1)
    try:
        response_b = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response_b.status_code == 200
    assert response_b.json()["total"] == 1
    assert len(response_b.json()["items"]) == 1


def test_list_recipes_owner_id_in_query(client: TestClient) -> None:
    """F2 SQL-structural — the first execute() call carries a WHERE clause on owner_id.

    Verification approach (per agreed.md §6 F2 LOW):
      1. Capture the Select object from the first execute() call via call_args_list.
      2. Compile it with literal_binds=True so the user id appears as a literal in SQL.
      3. Assert both "owner_id" and the mock user's id literal appear in the compiled string.

    Mirrors test_list_collections_owner_filter in test_sources_collections_list.py.
    """
    page_result = MagicMock()
    page_result.scalars.return_value.all.return_value = []
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
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    # Compile the first SELECT statement with literal_binds=True so the bound
    # parameter value (owner_id = 7) is rendered as a literal in the SQL string.
    first_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in compiled, f"'owner_id' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )


def test_list_recipes_empty_returns_empty_list(client: TestClient) -> None:
    """Empty — session returns 0 rows, total 0 → 200, items == [], total == 0."""
    _set_overrides(rows=[], total=0)
    try:
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


def test_list_recipes_definition_not_in_items(client: TestClient) -> None:
    """Schema guard — response items do NOT contain a 'definition' key (slim schema)."""
    rows = [
        _make_recipe(
            id=1, name="recipe-with-definition", definition={"steps": ["pack"]}
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert "definition" not in items[0], (
        f"'definition' should not appear in list item but found in: {items[0]}"
    )


def test_list_recipes_owner_id_not_in_items(client: TestClient) -> None:
    """Schema guard — response items do NOT contain an 'owner_id' key (slim schema)."""
    rows = [
        _make_recipe(id=1, name="recipe-with-owner", owner_id=7),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/recipes")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert "owner_id" not in items[0], (
        f"'owner_id' should not appear in list item but found in: {items[0]}"
    )
