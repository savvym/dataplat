"""Tests for GET /api/runs/{id} — S048-F-048.

Unit tests (run in backend layer — no live DB or compose stack required):
  1.  test_get_run_200_all_fields             (V1 — status='pending' row, all 14 keys)
  2.  test_get_run_not_found_returns_404      (V2 — non-existent id)
  3.  test_get_run_wrong_owner_returns_404    (no-enumeration-leak)
  4.  test_get_run_no_token_returns_401       (auth gate)
  5.  test_get_run_invalid_id_returns_422     (non-integer path segment)
  6.  test_get_run_triggered_by_in_query      (SQL-structural: owner filter present — M1 lynchpin)
  7.  test_get_run_no_extra_fields_leaked     (exact 14-key set)
  8.  test_get_run_config_is_dict_or_null     (config JSONB pass-through)
  9.  test_get_run_nullable_timestamps        (started_at=None, ended_at=None for pending run)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET /{id} handler calls session.execute() exactly ONCE and calls
  scalar_one_or_none() (synchronous) on the result proxy.  The correct mock
  shape is:
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run_row_or_none
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
  Note: scalar_one_or_none() is synchronous (called on the result proxy returned
  from await session.execute()).  Use MagicMock() for the result, not AsyncMock().

Mock factory note:
  _make_run_detail is defined locally in this file for self-containment.
  All 14 ORM attributes are populated (id, dagster_run_id, kind, asset_keys,
  partition_keys, source_collection_id, dataset_id, recipe_id, config,
  status, started_at, ended_at, triggered_by, trigger_context).

Auth-gate test (test 4) does NOT override get_current_user — the real
oauth2_scheme raises 401 for a missing Authorization header.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import Run, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="runs-get@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)

# ── Expected 14-field key set ─────────────────────────────────────────────────

_EXPECTED_KEYS = {
    "id",
    "dagster_run_id",
    "kind",
    "asset_keys",
    "partition_keys",
    "source_collection_id",
    "dataset_id",
    "recipe_id",
    "config",
    "status",
    "started_at",
    "ended_at",
    "triggered_by",
    "trigger_context",
}


# ── Mock run row factory ──────────────────────────────────────────────────────


def _make_run_detail(
    id: int = 7,
    dagster_run_id: str = "backfill-run-abc123",
    kind: str = "extract",
    asset_keys: list[str] | None = None,
    partition_keys: list[str] | None = None,
    source_collection_id: int | None = None,
    dataset_id: int | None = None,
    recipe_id: int | None = None,
    config: dict[str, Any] | None = None,
    status: str = "pending",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    triggered_by: int = 9,
    trigger_context: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a plain MagicMock that looks like a Run ORM row.

    RunDetailResponse uses from_attributes=True, so model_validate() reads
    attributes directly from the object.  All 14 ORM-mapped attributes are
    populated — same discipline as _make_dataset_detail() in test_datasets_get.py.
    Uses MagicMock(spec=Run) consistent with F-045/F-046/F-047 pattern.
    """
    row = MagicMock(spec=Run)
    row.id = id
    row.dagster_run_id = dagster_run_id
    row.kind = kind
    row.asset_keys = asset_keys if asset_keys is not None else ["extract_mineru"]
    row.partition_keys = (
        partition_keys if partition_keys is not None else ["src_1", "src_2"]
    )
    row.source_collection_id = source_collection_id
    row.dataset_id = dataset_id
    row.recipe_id = recipe_id
    row.config = config
    row.status = status
    row.started_at = started_at
    row.ended_at = ended_at
    row.triggered_by = triggered_by
    row.trigger_context = trigger_context
    return row


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_returning(run: MagicMock | None) -> Any:
    """Return a get_session override whose execute().scalar_one_or_none() returns `run`."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = run
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


def test_get_run_200_all_fields(client: TestClient) -> None:
    """Test 1 (V1) — status='pending' row → 200, all 14 keys present, correct values."""
    run_row = _make_run_detail(
        id=7,
        dagster_run_id="backfill-run-abc123",
        kind="extract",
        asset_keys=["extract_mineru"],
        partition_keys=["src_1", "src_2"],
        source_collection_id=None,
        dataset_id=None,
        recipe_id=None,
        config={"batch_size": 100},
        status="pending",
        started_at=None,
        ended_at=None,
        triggered_by=9,
        trigger_context=None,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(run_row)
    try:
        response = client.get("/api/runs/7")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()

    # All 14 fields must be present.
    for key in _EXPECTED_KEYS:
        assert key in body, f"missing key '{key}' in response: {body}"

    # Spot-check values.
    assert body["id"] == 7
    assert body["dagster_run_id"] == "backfill-run-abc123"
    assert body["kind"] == "extract"
    assert body["status"] == "pending"
    assert isinstance(body["config"], dict)
    assert body["config"] == {"batch_size": 100}
    assert body["started_at"] is None


def test_get_run_not_found_returns_404(client: TestClient) -> None:
    """Test 2 (V2) — Session returns None (non-existent id) → 404 with correct detail."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/runs/99999")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_get_run_wrong_owner_returns_404(client: TestClient) -> None:
    """Test 3 — Run exists for user id=99, not mock user id=9 → same 404.

    The handler combines id == ? AND triggered_by == ? in a single query,
    so a row owned by user id=99 is invisible to user id=9.  The mock returns
    None to simulate this query miss.  Both 'not found' and 'wrong owner'
    produce identical 404 — no information leak (no-enumeration, mirrors
    get_dataset in F-046).
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    # Session returns None — simulates a row that exists for user id=99, not id=9.
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/runs/1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def test_get_run_no_token_returns_401(client: TestClient) -> None:
    """Test 4 — No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/runs/7")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_get_run_invalid_id_returns_422(client: TestClient) -> None:
    """Test 5 — Non-integer path segment → 422 (FastAPI path param validation fires first).

    The ``id`` path parameter is typed as ``int``; FastAPI rejects any non-integer
    value with 422 Unprocessable Entity before the handler body is entered.
    Auth dependency is overridden so that 401 does not interfere with observing 422.
    No session mock call is required — path-param validation fires before dependency
    injection of the session.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/runs/not-a-number")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422


def test_get_run_triggered_by_in_query(client: TestClient) -> None:
    """Test 6 — SQL-structural: single execute() call carries both id and triggered_by.

    Verification approach (mirrors test_get_dataset_materialized_by_in_query in F-046):
      1. Capture the Select object from the single execute() call via call_args_list.
      2. Compile it with literal_binds=True so both bound values appear as literals.
      3. Assert "triggered_by" and the mock user's id (9) both appear in the SQL.

    This guards against accidentally dropping the triggered_by filter from the
    query, which would allow any authenticated user to retrieve any run by id.
    This is the M1 lynchpin pattern from F-045/F-046/F-047.
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
        client.get("/api/runs/5")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    # The endpoint returns 404 (scalar_one_or_none returned None) — that's fine;
    # we care about the SQL that was sent, not the HTTP response code here.
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    # Compile the captured SELECT with literal_binds=True so bound parameter
    # values (id = 5, triggered_by = 9) are rendered as literals in the SQL.
    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "triggered_by" in compiled, f"'triggered_by' not in compiled SQL: {compiled}"
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )


def test_get_run_no_extra_fields_leaked(client: TestClient) -> None:
    """Test 7 — Exact 14-key set in response; trigger_context IS present (detail endpoint).

    Confirms RunDetailResponse includes trigger_context, and that no unexpected
    extra fields appear beyond the 14 defined in the schema.
    """
    run_row = _make_run_detail(
        id=33,
        trigger_context=None,
        config=None,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(run_row)
    try:
        response = client.get("/api/runs/33")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()

    # trigger_context must be present in the detail response (even if null).
    assert "trigger_context" in body, (
        f"'trigger_context' missing from detail response: {body}"
    )

    # Exact key set — no more, no fewer than the 14 defined fields.
    actual_keys = set(body.keys())
    assert actual_keys == _EXPECTED_KEYS, (
        f"Response key mismatch.\n"
        f"  Extra keys:   {actual_keys - _EXPECTED_KEYS}\n"
        f"  Missing keys: {_EXPECTED_KEYS - actual_keys}"
    )


def test_get_run_config_is_dict_or_null(client: TestClient) -> None:
    """Test 8 — config in response is a dict (not a JSON-encoded string) when set.

    Guards against accidental double-serialization where the JSONB value is
    stored as a JSON string and returned as a string rather than an object.
    Also tests config=None pass-through.
    """
    # Part A: config is a non-null dict.
    run_row_with_config = _make_run_detail(
        id=10,
        config={"batch_size": 100},
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(
        run_row_with_config
    )
    try:
        response = client.get("/api/runs/10")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["config"], dict), (
        f"config should be a dict, got {type(body['config'])}: {body['config']!r}"
    )
    assert body["config"] == {"batch_size": 100}

    # Part B: config=None passes through as JSON null.
    run_row_null_config = _make_run_detail(
        id=11,
        config=None,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(
        run_row_null_config
    )
    try:
        response = client.get("/api/runs/11")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["config"] is None


def test_get_run_nullable_timestamps(client: TestClient) -> None:
    """Test 9 — Row with started_at=None, ended_at=None (pending run) → 200.

    Confirms nullable datetime fields pass through as JSON null for pending
    runs where state transitions have not yet fired.
    """
    run_row = _make_run_detail(
        id=20,
        status="pending",
        started_at=None,
        ended_at=None,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(run_row)
    try:
        response = client.get("/api/runs/20")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["started_at"] is None
    assert body["ended_at"] is None
    assert body["status"] == "pending"
