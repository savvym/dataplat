"""Tests for GET /api/recipes/{id} — S039-F-039.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_get_recipe_200_returns_full_record            (V1)
  - test_get_recipe_not_found_returns_404              (V2)
  - test_get_recipe_wrong_owner_returns_404            (edge: no-enumeration-leak)
  - test_get_recipe_no_token_returns_401               (auth gate)
  - test_get_recipe_invalid_id_returns_422             (path-param validation)
  - test_get_recipe_owner_id_in_query                  (structural / owner-scope SQL check)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET /{id} handler calls session.execute() exactly ONCE and calls
  scalar_one_or_none() (synchronous) on the result proxy.  The correct mock
  shape is:
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = recipe_row_or_none
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
  Note: scalar_one_or_none() is synchronous (called on the result proxy returned
  from await session.execute()).  Use MagicMock() for the result, not AsyncMock().
  Same lesson codified in the F-038 test file's header comment.

Mock factory note:
  _make_recipe_detail is defined locally in this file for self-containment.
  The duplication relative to _make_recipe in test_recipes_list.py is intentional
  (mirrors the F-038 test file convention; do not delete as dead code).  The
  detail factory populates all 7 ORM attributes — including owner_id and
  definition — because RecipeOut reads all of them.

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
from dataplat_api.db.models import Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=7, email="recipe-get@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── Mock recipe row factory ───────────────────────────────────────────────────
# Intentional duplication: _make_recipe_detail is defined locally for
# self-containment, mirroring the F-038 convention in test_recipes_list.py.
# Do NOT delete as dead code — agreed.md Mode A NIT 2.


def _make_recipe_detail(
    id: int,
    name: str,
    description: str | None = None,
    owner_id: int = 7,
    definition: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a plain MagicMock that looks like a Recipe ORM row.

    RecipeOut uses from_attributes=True, so model_validate() reads attributes
    directly from the object.  All 7 ORM-mapped attributes are populated
    (id, name, description, owner_id, definition, created_at, updated_at).
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


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_returning(recipe: MagicMock | None) -> Any:
    """Return a get_session override whose execute().scalar_one_or_none() returns `recipe`."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = recipe
        session.execute = AsyncMock(return_value=result_mock)
        yield session

    return _override


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """TestClient with app lifespan initialised.

    Does NOT set dependency overrides — each test sets and clears its own.
    """
    with TestClient(app) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_get_recipe_200_returns_full_record(client: TestClient) -> None:
    """V1 — Session returns a recipe row → 200 with all 7 RecipeOut fields."""
    recipe_row = _make_recipe_detail(
        id=42,
        name="my-sft",
        description="SFT recipe",
        owner_id=7,
        definition={"steps": ["tokenize", "pack"]},
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(recipe_row)
    try:
        response = client.get("/api/recipes/42")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()

    # All 7 RecipeOut fields must be present.
    for key in (
        "id",
        "name",
        "description",
        "owner_id",
        "definition",
        "created_at",
        "updated_at",
    ):
        assert key in body, f"missing key '{key}' in response: {body}"

    assert body["id"] == 42
    assert body["name"] == "my-sft"
    assert body["description"] == "SFT recipe"
    assert body["owner_id"] == 7
    assert body["definition"] == {"steps": ["tokenize", "pack"]}


def test_get_recipe_not_found_returns_404(client: TestClient) -> None:
    """V2 — Session returns None (non-existent id) → 404 with detail='Recipe not found'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/recipes/99999")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


def test_get_recipe_wrong_owner_returns_404(client: TestClient) -> None:
    """Edge: recipe exists but belongs to a different owner → mock returns None → 404.

    The handler combines id == ? AND owner_id == ? in a single query, so a row
    owned by user id=99 is invisible to user id=7.  The mock returns None to
    simulate this query miss.  Both 'not found' and 'wrong owner' produce 404
    with the same detail — no information leak (mirrors get_source F-013).
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    # Session returns None — models a recipe row that exists for user id=99, not id=7.
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/recipes/1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


def test_get_recipe_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/recipes/42")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_get_recipe_invalid_id_returns_422(client: TestClient) -> None:
    """Non-integer path segment → 422 (FastAPI path param validation fires before handler).

    The `id` path parameter is typed as `int`; FastAPI rejects any non-integer
    value with a 422 Unprocessable Entity before the handler body is entered.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/recipes/not-an-int")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422


def test_get_recipe_owner_id_in_query(client: TestClient) -> None:
    """Structural — the SELECT carries WHERE clauses on both id and owner_id.

    Verification approach (mirrors test_list_recipes_owner_id_in_query in F-038):
      1. Capture the Select object from the single execute() call via call_args_list.
      2. Compile it with literal_binds=True so both bound values appear as literals.
      3. Assert "owner_id" and the mock user's id literal (7) both appear in the SQL.

    This guards against accidentally dropping the owner_id filter from the query.
    """
    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    try:
        client.get("/api/recipes/5")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    # The endpoint returns 404 (scalar_one_or_none returned None) — that's fine;
    # we care about the SQL that was sent, not the HTTP response code here.
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    # Compile the captured SELECT with literal_binds=True so bound parameter
    # values (id = 5, owner_id = 7) are rendered as literals in the SQL string.
    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in compiled, f"'owner_id' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )
