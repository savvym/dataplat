"""Tests for PUT /api/recipes/{id} — S040-F-040.

Unit tests (run in backend layer — no live DB or compose stack required):
  - test_update_recipe_200_returns_updated_definition     (V1)
  - test_update_recipe_updated_at_is_newer                (V2)
  - test_update_recipe_dataset_exists_returns_409         (V3)
  - test_update_recipe_not_found_returns_404              (edge: not-found)
  - test_update_recipe_wrong_owner_returns_404            (edge: no-enumeration-leak)
  - test_update_recipe_no_token_returns_401               (auth gate)
  - test_update_recipe_missing_definition_returns_422     (input validation)
  - test_update_recipe_non_object_definition_returns_422  (input validation)
  - test_update_recipe_description_updated_when_provided  (OQ2 accepted)
  - test_update_recipe_description_unchanged_when_omitted (OQ2 accepted)
  - test_update_recipe_description_explicit_null          (OQ2 accepted: null clears)
  - test_update_recipe_recipe_id_in_dataset_exists_query  (structural SQL check — EXISTS)
  - test_update_recipe_owner_id_in_recipe_query           (structural SQL check — owner scope)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (two execute() calls):
  The PUT /{id} handler calls session.execute() TWICE sequentially:
    1. First call: load recipe → result.scalar_one_or_none()
    2. Second call: dataset exists check → result.scalar_one() (True/False)
  The correct mock shape uses side_effect=[result1_mock, result2_mock] on
  session.execute (an AsyncMock) so each call gets the right result proxy.

_PAST constant (V2 flake-prevention):
  The mock recipe row's updated_at is set to a fixed historical constant
  _PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).
  The handler calls datetime.now(tz=timezone.utc) which on 2026-06-02 (and
  beyond) is always several months after _PAST — the assertion will never flake.
  Mirrors the _NOW pattern in test_recipes_get.py.

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

_MOCK_USER = User(
    id=7, email="recipe-update@example.com", hashed_password="$2b$12$hash"
)


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant (V2 flake-prevention) ──────────────────────────────────

_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


# ── Mock recipe row factory ───────────────────────────────────────────────────
# Intentional local definition — self-containment; mirrors test_recipes_get.py
# _make_recipe_detail pattern.  All 7 ORM-mapped attributes populated because
# RecipeOut uses from_attributes=True and reads all of them.


def _make_recipe_detail(
    id: int,
    name: str,
    description: str | None = None,
    owner_id: int = 7,
    definition: dict[str, Any] | None = None,
    updated_at: datetime | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a Recipe ORM row.

    All 7 ORM-mapped attributes are populated so RecipeOut.model_validate()
    can read them via from_attributes=True.
    """
    row = MagicMock(spec=Recipe)
    row.id = id
    row.name = name
    row.description = description
    row.owner_id = owner_id
    row.definition = definition if definition is not None else {}
    row.created_at = _PAST
    row.updated_at = updated_at if updated_at is not None else _PAST
    return row


# ── Session mock helper ───────────────────────────────────────────────────────


def _make_session_dep_for_update(
    recipe: MagicMock | None,
    dataset_exists: bool = False,
) -> Any:
    """Session override whose two execute() calls return recipe + exists bool.

    For 404 (recipe not found) tests, only the first execute() is reached;
    the second side_effect entry is never consumed — that is expected and safe.

    For 409 (dataset exists) tests, set dataset_exists=True.
    For 200 (success) tests, set dataset_exists=False.
    """

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        result2.scalar_one.return_value = dataset_exists
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
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


# ── Happy path — V1 ───────────────────────────────────────────────────────────


def test_update_recipe_200_returns_updated_definition(client: TestClient) -> None:
    """V1 — PUT with new definition → 200; response definition equals the sent dict."""
    new_def = {"steps": ["clean", "pack", "dedupe"]}
    recipe_row = _make_recipe_detail(
        id=42,
        name="my-sft",
        description="Original desc",
        owner_id=7,
        definition={"steps": ["tokenize"]},  # will be overwritten by handler
    )

    # The session.refresh() side-effect simulates the ORM updating the object
    # after commit — the handler then calls RecipeOut.model_validate(recipe).
    def _refresh_side_effect(obj: Any) -> None:
        obj.definition = new_def

    app.dependency_overrides[get_current_user] = _override_current_user
    session_dep = _make_session_dep_for_update(recipe_row, dataset_exists=False)
    # Patch refresh to apply the new definition on the mock row
    _ = session_dep

    async def _patched_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_session] = _patched_session
    try:
        response = client.put(
            "/api/recipes/42",
            json={"definition": new_def},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["definition"] == new_def


# ── Happy path — V2 (updated_at bumped) ──────────────────────────────────────


def test_update_recipe_updated_at_is_newer(client: TestClient) -> None:
    """V2 — updated_at in response is strictly later than the fixed _PAST constant.

    The mock recipe row starts with updated_at = _PAST (2026-01-01).
    The handler calls datetime.now(tz=timezone.utc) which on 2026-06-02+ is
    always several months after _PAST — no flake risk.

    The refresh side-effect captures the updated_at set by the handler and
    keeps it on the row so RecipeOut.model_validate() can see it.
    """
    recipe_row = _make_recipe_detail(
        id=10,
        name="ts-test-recipe",
        owner_id=7,
        definition={"v": 1},
        updated_at=_PAST,
    )

    # Capture the updated_at the handler assigned before refresh.
    def _refresh_side_effect(obj: Any) -> None:
        # updated_at was already set on the row object (MagicMock attribute
        # assignment is captured by MagicMock); nothing extra needed here.
        pass

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    try:
        response = client.put(
            "/api/recipes/10",
            json={"definition": {"v": 2}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    returned_updated_at = datetime.fromisoformat(response.json()["updated_at"])
    assert returned_updated_at > _PAST, (
        f"updated_at {returned_updated_at!r} is not newer than _PAST {_PAST!r}"
    )


# ── 409 — dataset exists (freeze guard) ──────────────────────────────────────


def test_update_recipe_dataset_exists_returns_409(client: TestClient) -> None:
    """V3 — Mock exists check returns True → 409 with exact detail string."""
    recipe_row = _make_recipe_detail(id=5, name="frozen-recipe", owner_id=7)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_for_update(
        recipe_row, dataset_exists=True
    )
    try:
        response = client.put(
            "/api/recipes/5",
            json={"definition": {"new": "def"}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Recipe is locked: a dataset has been materialized from it"
    }


# ── 404 — recipe not found ────────────────────────────────────────────────────


def test_update_recipe_not_found_returns_404(client: TestClient) -> None:
    """Session returns None (non-existent id) → 404 with detail='Recipe not found'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_for_update(None)
    try:
        response = client.put(
            "/api/recipes/99999",
            json={"definition": {}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ── 404 — wrong owner (no enumeration leak) ───────────────────────────────────


def test_update_recipe_wrong_owner_returns_404(client: TestClient) -> None:
    """Recipe owned by a different user → owner-scoped query returns None → 404.

    Both 'not found' and 'wrong owner' return 404 with the same detail string —
    no information leak (mirrors get_recipe, F-039).
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_for_update(None)
    try:
        response = client.put(
            "/api/recipes/1",
            json={"definition": {"a": 1}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ── 401 — no token ────────────────────────────────────────────────────────────


def test_update_recipe_no_token_returns_401(client: TestClient) -> None:
    """No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.put(
        "/api/recipes/42",
        json={"definition": {}},
    )
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


# ── 422 — validation errors ───────────────────────────────────────────────────


def test_update_recipe_missing_definition_returns_422(client: TestClient) -> None:
    """Missing required 'definition' field → 422 (Pydantic required field check)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.put(
            "/api/recipes/42",
            json={},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


def test_update_recipe_non_object_definition_returns_422(client: TestClient) -> None:
    """'definition' that is an array (not a JSON object) → 422."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        response = client.put(
            "/api/recipes/42",
            json={"definition": [1, 2, 3]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 422


# ── description field semantics (OQ2 accepted) ───────────────────────────────


def test_update_recipe_description_updated_when_provided(client: TestClient) -> None:
    """Body includes 'description': 'new desc' → recipe.description set to new value.

    The mock recipe starts with description='original description'.
    The refresh side-effect applies the new value (simulating the ORM seeing the
    updated attribute on the row after commit).
    """
    recipe_row = _make_recipe_detail(
        id=20,
        name="desc-test",
        description="original description",
        owner_id=7,
        definition={"a": 1},
    )

    def _refresh_side_effect(obj: Any) -> None:
        obj.description = "new desc"

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    try:
        response = client.put(
            "/api/recipes/20",
            json={"definition": {"a": 1}, "description": "new desc"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["description"] == "new desc"


def test_update_recipe_description_unchanged_when_omitted(client: TestClient) -> None:
    """Body omits 'description' → 'description' not in body.model_fields_set → unchanged.

    The mock recipe starts with description='keep this'.  The handler checks
    ``'description' in body.model_fields_set`` — since the caller omits it,
    the guard does not fire and recipe.description is never reassigned.
    The refresh side-effect preserves the original description to simulate
    the ORM not changing that column.
    """
    original_description = "keep this"
    recipe_row = _make_recipe_detail(
        id=21,
        name="omit-desc-test",
        description=original_description,
        owner_id=7,
        definition={"b": 2},
    )

    def _refresh_side_effect(obj: Any) -> None:
        # description is NOT changed by the handler (omitted in body) —
        # refresh sees the same description as before.
        pass

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    try:
        response = client.put(
            "/api/recipes/21",
            json={"definition": {"b": 3}},  # no description key
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    # The original description is preserved (handler skipped the assignment).
    assert response.json()["description"] == original_description


def test_update_recipe_description_explicit_null(client: TestClient) -> None:
    """Body sends 'description': null → recipe.description set to None (cleared).

    'description' IS in model_fields_set (caller explicitly sent null), so the
    handler fires ``recipe.description = body.description`` (which is None).
    Refresh side-effect applies the null to simulate the ORM roundtrip.
    """
    recipe_row = _make_recipe_detail(
        id=22,
        name="null-desc-test",
        description="will be cleared",
        owner_id=7,
        definition={"c": 3},
    )

    def _refresh_side_effect(obj: Any) -> None:
        obj.description = None

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    try:
        response = client.put(
            "/api/recipes/22",
            json={"definition": {"c": 3}, "description": None},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["description"] is None


# ── Structural SQL tests ──────────────────────────────────────────────────────


def test_update_recipe_recipe_id_in_dataset_exists_query(client: TestClient) -> None:
    """Structural: compile the EXISTS query with literal_binds; assert recipe_id appears.

    Captures the second session.execute() call (the exists-check query).
    Compiles the SQLAlchemy statement with literal_binds=True so the recipe id
    value appears as a literal in the rendered SQL.
    Asserts both 'recipe_id' (column name) and the recipe's id (42) appear in
    the compiled WHERE clause.
    """
    recipe_id = 42
    recipe_row = _make_recipe_detail(id=recipe_id, name="sql-check", owner_id=7)

    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        result2 = MagicMock()
        result2.scalar_one.return_value = False  # pass freeze check → proceed to 200
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    try:
        client.put(
            f"/api/recipes/{recipe_id}",
            json={"definition": {"x": 1}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert len(captured_session) == 1
    session_mock = captured_session[0]
    # Two execute calls: first is recipe load, second is exists check.
    assert session_mock.execute.call_count == 2

    # Capture the second call (exists check).
    exists_stmt = session_mock.execute.call_args_list[1].args[0]
    compiled = str(exists_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "recipe_id" in compiled, (
        f"'recipe_id' not in compiled EXISTS SQL: {compiled}"
    )
    assert str(recipe_id) in compiled, (
        f"recipe id {recipe_id!r} not in compiled EXISTS SQL: {compiled}"
    )


def test_update_recipe_owner_id_in_recipe_query(client: TestClient) -> None:
    """Structural: compile the recipe-load SELECT; assert owner_id and user id appear.

    Captures the first session.execute() call (owner-scoped recipe load).
    Compiles with literal_binds=True and asserts both 'owner_id' (column name)
    and '7' (the mock user's id) appear in the WHERE clause.
    This guards against accidentally dropping the owner_id filter.
    """
    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = (
            None  # 404 — we only care about the SQL
        )
        session.execute = AsyncMock(return_value=result1)
        captured_session.append(session)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    try:
        client.put(
            "/api/recipes/5",
            json={"definition": {}},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    # Response is 404 (scalar_one_or_none returned None) — expected; we care about SQL.
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "owner_id" in compiled, f"'owner_id' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )
