"""Tests for POST /api/datasets/{recipe_id}/materialize — S042-F-042.

Unit tests (run in backend layer — no live DB or compose stack required):

Route tests (V1-V4, A1-A9):
  - test_materialize_202_response                 (V1)
  - test_materialize_db_row                       (V2)
  - test_materialize_dagster_called               (V3)
  - test_materialize_401_no_auth                  (A1)
  - test_materialize_404_recipe_not_found         (A2)
  - test_materialize_404_wrong_owner              (A3)
  - test_materialize_v2_second_call_increments_version  (A4)
  - test_materialize_409_concurrent_race          (A5)
  - test_materialize_503_add_partition_fails      (A6)
  - test_materialize_503_launch_backfill_fails    (A7)
  - test_freeze_guard_excludes_failed_row         (A8 / V4)
  - test_materialize_after_failed_retry_increments_version  (A9)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures.
DagsterGateway is injected via app.dependency_overrides.

Mock session pattern:
  The POST /{recipe_id}/materialize handler calls session.execute() up to 4 times:
    1. Load recipe (scalar_one_or_none)
    2. COUNT(*) for version_tag (scalar_one)
    3. Post-commit error tombstone OR post-commit backfill_id UPDATE (execute only)
    4. (if tombstone on step 3, or if backfill write)

  After commit() the handler uses update(Dataset).where(...).values(...) directly
  (M2 pattern) — no ORM attribute access post-commit.

  session.flush is mocked to set dataset.id on the ORM object (simulating DB id
  assignment).

For the freeze-guard test (A8/V4), we reuse the PUT /api/recipes/{id} handler's
session mock and assert the handler does NOT return 409 when only failed rows exist.
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
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.dagster.gateway import DagsterGateway, DagsterGatewayError
from dataplat_api.db.models import Dataset, Recipe, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="materialize@example.com", hashed_password="$2b$12$hash")
_OTHER_USER = User(id=99, email="other@example.com", hashed_password="$2b$12$hash2")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

# ── Mock factory helpers ──────────────────────────────────────────────────────


def _make_recipe(
    id: int,
    owner_id: int = 9,
    definition: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a Recipe ORM row."""
    row = MagicMock(spec=Recipe)
    row.id = id
    row.name = f"recipe-{id}"
    row.description = None
    row.owner_id = owner_id
    row.definition = (
        definition
        if definition is not None
        else {"schema": {"template": "sft_synthesis_qa"}}
    )
    row.created_at = _PAST
    row.updated_at = _PAST
    return row


def _make_dataset(
    id: int,
    recipe_id: int,
    version_tag: str = "v1",
    status: str = "pending",
    dagster_run_id: str | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like a Dataset ORM row."""
    row = MagicMock(spec=Dataset)
    row.id = id
    row.recipe_id = recipe_id
    row.version_tag = version_tag
    row.status = status
    row.dagster_run_id = dagster_run_id
    row.hf_repo_uri = f"s3://datasets/{id}_{version_tag}"
    row.recipe_snapshot = {}
    return row


def _mock_gateway(
    add_partition_error: Exception | None = None,
    launch_backfill_error: Exception | None = None,
    backfill_id: str = "backfill-dataset-test-123",
) -> MagicMock:
    """Build a mock DagsterGateway with configurable error injection."""
    gw = MagicMock(spec=DagsterGateway)
    if add_partition_error is not None:
        gw.add_dataset_partition = AsyncMock(side_effect=add_partition_error)
    else:
        gw.add_dataset_partition = AsyncMock(return_value=None)
    if launch_backfill_error is not None:
        gw.launch_dataset_backfill = AsyncMock(side_effect=launch_backfill_error)
    else:
        gw.launch_dataset_backfill = AsyncMock(return_value=backfill_id)
    return gw


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """TestClient with app lifespan initialised."""
    with TestClient(app) as c:
        yield c


# ── Happy path session helper ─────────────────────────────────────────────────


def _make_materialize_session(
    recipe: MagicMock | None,
    existing_count: int = 0,
    assigned_id: int = 42,
    flush_side_effect: Any = None,
    execute_extra_calls: int = 2,  # post-commit execute calls (backfill_id UPDATE + any errors)
) -> Any:
    """Session override for the materialize happy path.

    execute() call sequence:
      1. Load recipe (scalar_one_or_none)
      2. COUNT(*) (scalar_one → existing_count)
      3+. Post-commit UPDATE calls (execute only, no result needed)
    flush() sets dataset.id = assigned_id on the added object.
    """

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe

        result2 = MagicMock()
        result2.scalar_one.return_value = existing_count

        # Post-commit execute calls return a MagicMock (no result needed)
        extra_results = [MagicMock() for _ in range(execute_extra_calls)]

        session.execute = AsyncMock(side_effect=[result1, result2, *extra_results])
        session.commit = AsyncMock()

        async def _flush_side_effect() -> None:
            # Simulate DB assigning id to the added object (dataset row)
            for obj in session.add.call_args_list:
                arg = obj[0][0]
                if hasattr(arg, "id"):
                    arg.id = assigned_id

        session.flush = AsyncMock(side_effect=_flush_side_effect)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    return _override


# ─────────────────────────────────────────────────────────────────────────────
# V1 — 202 response with dataset_id + dagster_run_id
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_202_response(client: TestClient) -> None:
    """V1 — Authenticated user, valid recipe owner, all mocks succeed → 202.

    Response body has dataset_id: int and dagster_run_id: str (non-empty).
    """
    recipe = _make_recipe(id=1)
    gw = _mock_gateway(backfill_id="backfill-v1-abc")
    session_dep = _make_materialize_session(recipe, existing_count=0, assigned_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/1/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202
    body = response.json()
    assert isinstance(body["dataset_id"], int)
    assert isinstance(body["dagster_run_id"], str)
    assert body["dagster_run_id"] != ""


# ─────────────────────────────────────────────────────────────────────────────
# V2 — DB row written with correct attributes
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_db_row(client: TestClient) -> None:
    """V2 — Inspect the Dataset row written to DB.

    Asserts: status='pending', recipe_snapshot == recipe.definition,
    version_tag == 'v1', hf_repo_uri starts with 's3://datasets/',
    dagster_run_id is set (via post-commit UPDATE in agreed.md §4 Step 9).
    """
    recipe_def = {"schema": {"template": "sft_synthesis_qa"}, "filter": {}}
    recipe = _make_recipe(id=5, definition=recipe_def)
    backfill_id = "backfill-db-row-xyz"
    gw = _mock_gateway(backfill_id=backfill_id)

    captured_dataset: list[MagicMock] = []

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        result2.scalar_one.return_value = 0  # no existing datasets

        session.execute = AsyncMock(
            side_effect=[result1, result2, MagicMock(), MagicMock()]
        )
        session.commit = AsyncMock()

        async def _flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if isinstance(obj, Dataset) or hasattr(obj, "recipe_id"):
                    obj.id = 7
                    obj.recipe_id = 5
                    captured_dataset.append(obj)

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/5/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202
    assert len(captured_dataset) >= 1
    ds = captured_dataset[0]
    assert ds.status == "pending"
    assert ds.recipe_snapshot == recipe_def
    assert ds.version_tag == "v1"
    assert ds.hf_repo_uri.startswith("s3://datasets/")
    # dagster_run_id written via UPDATE in Step 9 — confirmed by the gateway mock call
    assert gw.launch_dataset_backfill.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# V3 — Dagster gateway called with correct args
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_dagster_called(client: TestClient) -> None:
    """V3 — Assert on mock call signatures.

    add_dataset_partition called with 'ds_3_v1'.
    launch_dataset_backfill called with ['ds_3_v1'] and payload contains
    assetSelection=[{"path": ["dataset"]}].
    """
    recipe = _make_recipe(id=3)
    backfill_id = "backfill-v3-test"
    gw = _mock_gateway(backfill_id=backfill_id)
    session_dep = _make_materialize_session(recipe, existing_count=0, assigned_id=10)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/3/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202

    # Assert add_dataset_partition called with expected partition key
    gw.add_dataset_partition.assert_called_once_with("ds_3_v1")

    # Assert launch_dataset_backfill called with expected partition keys list
    gw.launch_dataset_backfill.assert_called_once_with(["ds_3_v1"])


# ─────────────────────────────────────────────────────────────────────────────
# A1 — 401 no auth
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_401_no_auth(client: TestClient) -> None:
    """Request without Authorization header → 401."""
    response = client.post("/api/datasets/1/materialize")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


# ─────────────────────────────────────────────────────────────────────────────
# A2 — 404 recipe not found
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_404_recipe_not_found(client: TestClient) -> None:
    """recipe_id does not exist → 404."""
    session_dep = _make_materialize_session(recipe=None)
    gw = _mock_gateway()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/999/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ─────────────────────────────────────────────────────────────────────────────
# A3 — 404 wrong owner (no enumeration leak)
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_404_wrong_owner(client: TestClient) -> None:
    """recipe_id exists but belongs to another user → 404 (no enumeration leak).

    The owner-scoped query collapses not-found and wrong-owner into the same 404.
    """
    # Session returns None because owner_id filter excludes the recipe.
    session_dep = _make_materialize_session(recipe=None)
    gw = _mock_gateway()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/1/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Recipe not found"}


# ─────────────────────────────────────────────────────────────────────────────
# A4 — Second call increments version (stateful mock)
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_v2_second_call_increments_version(client: TestClient) -> None:
    """Two successive materialize calls on the same recipe produce v1 then v2.

    Session mock is stateful: first call's INSERT is committed (visible in the
    mock DB state) before second call's COUNT(*) executes — the mock returns
    count=1 on the second call so that version_tag='v2', not 'v1' (which would
    cause an IntegrityError in production).
    """
    recipe = _make_recipe(id=7)
    gw = _mock_gateway(backfill_id="backfill-v1")

    call_count = [0]
    assigned_ids = [101, 102]

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        call_count[0] += 1
        current_call = call_count[0]

        session = AsyncMock()
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        # First call: count=0 → v1; second call: count=1 → v2
        result2.scalar_one.return_value = current_call - 1

        session.execute = AsyncMock(
            side_effect=[result1, result2, MagicMock(), MagicMock()]
        )
        session.commit = AsyncMock()

        async def _flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if hasattr(obj, "id"):
                    obj.id = assigned_ids[current_call - 1]

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    # First call
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response1 = client.post("/api/datasets/7/materialize")
        assert response1.status_code == 202
        assert response1.json()["dataset_id"] == 101

        # Second call
        gw2 = _mock_gateway(backfill_id="backfill-v2")
        app.dependency_overrides[get_dagster_gateway] = lambda: gw2
        response2 = client.post("/api/datasets/7/materialize")
        assert response2.status_code == 202
        assert response2.json()["dataset_id"] == 102
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    # Verify first call used ds_7_v1 and second used ds_7_v2
    gw.add_dataset_partition.assert_called_once_with("ds_7_v1")
    gw2.add_dataset_partition.assert_called_once_with("ds_7_v2")


# ─────────────────────────────────────────────────────────────────────────────
# A5 — 409 concurrent race (IntegrityError)
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_409_concurrent_race(client: TestClient) -> None:
    """IntegrityError on INSERT (uq_dataset_recipe_version race) → 409 Conflict."""

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = _make_recipe(id=2)
        result2 = MagicMock()
        result2.scalar_one.return_value = 0

        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()

        async def _flush() -> None:
            raise IntegrityError(
                "INSERT failed",
                params={},
                orig=Exception(
                    "duplicate key value violates unique constraint uq_dataset_recipe_version"
                ),
            )

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    gw = _mock_gateway()
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/2/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# A6 — 503 add_dataset_partition fails (tombstone)
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_503_add_partition_fails(client: TestClient) -> None:
    """gateway.add_dataset_partition raises DagsterGatewayError → 503.

    The dataset row must exist in DB with status='failed', dagster_run_id=None.
    """
    recipe = _make_recipe(id=4)
    gw = _mock_gateway(
        add_partition_error=DagsterGatewayError("Dagster is down"),
    )

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        result2.scalar_one.return_value = 0

        # After Step 6 commit, Step 7 fails → tombstone UPDATE execute
        tombstone_result = MagicMock()

        session.execute = AsyncMock(side_effect=[result1, result2, tombstone_result])
        session.commit = AsyncMock()

        async def _flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if hasattr(obj, "id"):
                    obj.id = 55

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/4/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 503
    # Dagster launch NOT called (failed at add_partition step)
    gw.launch_dataset_backfill.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# A7 — 503 launch_dataset_backfill fails (tombstone)
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_503_launch_backfill_fails(client: TestClient) -> None:
    """gateway.launch_dataset_backfill raises DagsterGatewayError → 503.

    add_dataset_partition succeeds; launch_dataset_backfill fails.
    The dataset row must exist with status='failed', dagster_run_id=None.
    """
    recipe = _make_recipe(id=6)
    gw = _mock_gateway(
        launch_backfill_error=DagsterGatewayError("backfill launch failed"),
    )

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        result2.scalar_one.return_value = 0

        session.execute = AsyncMock(side_effect=[result1, result2, MagicMock()])
        session.commit = AsyncMock()

        async def _flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if hasattr(obj, "id"):
                    obj.id = 66

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/6/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 503
    # add_dataset_partition WAS called
    gw.add_dataset_partition.assert_called_once()
    # launch_dataset_backfill WAS called and raised
    gw.launch_dataset_backfill.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# A8 / V4 — freeze guard excludes status='failed' rows
# ─────────────────────────────────────────────────────────────────────────────


def test_freeze_guard_excludes_failed_row(client: TestClient) -> None:
    """V4/A8 — Recipe with only status='failed' dataset rows accepts PUT.

    Create recipe; seed a Dataset row with status='failed', recipe_id=rid
    referencing that recipe; call PUT /api/recipes/{rid} with a valid update payload.
    Asserts 200 (not 409) — the freeze guard does NOT block a recipe that has
    only failed dataset rows. Validates the H1 fix in recipes.py.
    """
    recipe_row = MagicMock(spec=Recipe)
    recipe_row.id = 88
    recipe_row.name = "recipe-with-failed-ds"
    recipe_row.description = "desc"
    recipe_row.owner_id = 9
    recipe_row.definition = {"schema": {"template": "sft_synthesis_qa"}}
    recipe_row.created_at = _PAST
    recipe_row.updated_at = _PAST

    new_def = {"schema": {"template": "sft_synthesis_qa"}, "updated": True}

    def _refresh_side_effect(obj: Any) -> None:
        obj.definition = new_def

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        # execute call 1: load recipe
        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe_row
        # execute call 2: freeze check — only failed rows → exists() returns False
        # (because the query now includes .where(Dataset.status != 'failed'))
        result2 = MagicMock()
        result2.scalar_one.return_value = False  # no non-failed datasets → not locked
        session.execute = AsyncMock(side_effect=[result1, result2])
        session.commit = AsyncMock()
        session.refresh = AsyncMock(side_effect=_refresh_side_effect)
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    try:
        response = client.put(
            "/api/recipes/88",
            json={"definition": new_def},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    # Must be 200 (not 409) — failed rows do NOT lock the recipe
    assert response.status_code == 200, (
        f"Expected 200 (not 409), got {response.status_code}: {response.json()}"
    )
    assert response.json()["definition"] == new_def


# ─────────────────────────────────────────────────────────────────────────────
# A9 — Retry after failed materialization increments version_tag
# ─────────────────────────────────────────────────────────────────────────────


def test_materialize_after_failed_retry_increments_version(client: TestClient) -> None:
    """A9 — Setup: recipe has a failed v1 row (count=1 includes it).

    Call POST /api/datasets/{rid}/materialize with mocks succeeding.
    Asserts: 202; new dataset row has version_tag='v2' (count=1 → n=2);
    both gateway methods called with 'ds_11_v2' (not v1).

    This validates that COUNT(*) includes status='failed' rows so v1 is never reused.
    """
    recipe = _make_recipe(id=11)
    backfill_id = "backfill-retry-v2"
    gw = _mock_gateway(backfill_id=backfill_id)

    captured_version: list[str] = []

    async def _session_dep() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()

        result1 = MagicMock()
        result1.scalar_one_or_none.return_value = recipe
        result2 = MagicMock()
        # count=1: one existing row (the failed v1) → n=2 → version_tag='v2'
        result2.scalar_one.return_value = 1

        session.execute = AsyncMock(
            side_effect=[result1, result2, MagicMock(), MagicMock()]
        )
        session.commit = AsyncMock()

        async def _flush() -> None:
            for call in session.add.call_args_list:
                obj = call[0][0]
                if hasattr(obj, "id"):
                    obj.id = 200
                    # Capture the version_tag on the new dataset row
                    if hasattr(obj, "version_tag"):
                        captured_version.append(obj.version_tag)

        session.flush = AsyncMock(side_effect=_flush)
        session.add = MagicMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[get_dagster_gateway] = lambda: gw
    try:
        response = client.post("/api/datasets/11/materialize")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_dagster_gateway, None)

    assert response.status_code == 202
    body = response.json()
    assert body["dagster_run_id"] == backfill_id

    # version_tag captured from the flushed object should be 'v2'
    assert "v2" in captured_version, (
        f"Expected 'v2' in captured_version {captured_version}"
    )

    # Both gateway methods called with ds_11_v2 (not v1)
    gw.add_dataset_partition.assert_called_once_with("ds_11_v2")
    gw.launch_dataset_backfill.assert_called_once_with(["ds_11_v2"])
