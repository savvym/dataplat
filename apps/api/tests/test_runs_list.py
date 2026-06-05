"""Tests for GET /api/runs — S049-F-049.

Unit tests (run in backend layer — no live DB or compose stack required):
  T1.  test_list_runs_returns_200_with_items_and_total
  T2.  test_list_runs_empty_returns_empty_list
  T3.  test_list_runs_no_token_returns_401
  T4.  test_list_runs_owner_isolation
  T5.  test_list_runs_items_have_required_fields
  T6.  test_list_runs_triggered_by_in_both_queries        (SQL-structural; M1 — both queries)
  T7.  test_list_runs_status_filter_in_both_queries       (SQL-structural; parametrized ×4)
  T8.  test_list_runs_status_filter_success
  T9.  test_list_runs_status_filter_running
  T10. test_list_runs_invalid_status_returns_422
  T11. test_list_runs_no_extra_fields_in_items            (schema boundary guard)
  T12. test_list_runs_page_query_has_correct_order_by     (SQL-structural; ORDER BY guard)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern (two execute() calls):
  The GET "" handler calls session.execute() TWICE — once for the full row list,
  once for the total count.  The mock uses AsyncMock(side_effect=[...]) where
  the two side_effect items are plain MagicMock (NOT AsyncMock): only
  session.execute() itself is awaited; .scalars(), .all(), and .scalar_one()
  are synchronous calls on the result proxy.  Using AsyncMock for those would
  cause .scalars() to return a coroutine, producing a subtle runtime failure.

Run row mocks use _make_run_list_item() with MagicMock(spec=Run).  All 14
ORM-mapped attributes are populated even though RunListItem reads only 10
of them — avoids MagicMock attribute-access surprises if code paths change
(consistent with _make_run_detail() in test_runs_get.py, agreed.md §8).

Auth-gate test (T3) does NOT override get_current_user — the real oauth2_scheme
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
from dataplat_api.db.models import Run, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="runs-list@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)

# ── Expected 10-field key set for RunListItem ─────────────────────────────────

_LIST_ITEM_KEYS = {
    "id",
    "dagster_run_id",
    "kind",
    "status",
    "started_at",
    "ended_at",
    "triggered_by",
    "dataset_id",
    "recipe_id",
    "source_collection_id",
}

# ── Detail-only fields that must NOT appear in list items ─────────────────────

_EXCLUDED_DETAIL_KEYS = {
    "asset_keys",
    "partition_keys",
    "config",
    "trigger_context",
}


# ── Mock run row factory ──────────────────────────────────────────────────────


def _make_run_list_item(
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

    RunListItem uses from_attributes=True, so model_validate() reads
    attributes directly from the object.  All 14 ORM-mapped attributes are
    populated — same discipline as _make_run_detail() in test_runs_get.py —
    even though RunListItem reads only 10 of them.
    Uses MagicMock(spec=Run) consistent with F-045/F-046/F-047/F-048 pattern.
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


def test_list_runs_returns_200_with_items_and_total(client: TestClient) -> None:
    """T1 (V1) — Three run rows → 200, total == 3, len(items) == 3."""
    rows = [
        _make_run_list_item(id=1, status="success", started_at=_NOW),
        _make_run_list_item(id=2, status="running", started_at=_NOW),
        _make_run_list_item(id=3, status="pending"),
    ]
    _set_overrides(rows=rows, total=3)
    try:
        response = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_list_runs_empty_returns_empty_list(client: TestClient) -> None:
    """T2 — Empty — session returns 0 rows, total 0 → 200, {"items": [], "total": 0}."""
    _set_overrides(rows=[], total=0)
    try:
        response = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


def test_list_runs_no_token_returns_401(client: TestClient) -> None:
    """T3 — No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/runs")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_list_runs_owner_isolation(client: TestClient) -> None:
    """T4 — Isolation — each user sees only their own runs via separate session mocks.

    User A has 2 runs → total == 2.
    User B has 1 run  → total == 1.
    The two calls use entirely separate session mocks and dependency overrides
    to verify the triggered_by filter is applied per authenticated user.
    """
    user_a = User(id=10, email="user-a@example.com", hashed_password="$2b$12$hash")
    user_b = User(id=20, email="user-b@example.com", hashed_password="$2b$12$hash")

    # ── User A: 2 runs ──
    rows_a = [
        _make_run_list_item(id=1, triggered_by=10),
        _make_run_list_item(id=2, triggered_by=10),
    ]

    async def _user_a() -> User:
        return user_a

    app.dependency_overrides[get_current_user] = _user_a
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_a, 2)
    try:
        response_a = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response_a.status_code == 200
    assert response_a.json()["total"] == 2
    assert len(response_a.json()["items"]) == 2

    # ── User B: 1 run ──
    rows_b = [
        _make_run_list_item(id=5, triggered_by=20),
    ]

    async def _user_b() -> User:
        return user_b

    app.dependency_overrides[get_current_user] = _user_b
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_b, 1)
    try:
        response_b = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response_b.status_code == 200
    assert response_b.json()["total"] == 1
    assert len(response_b.json()["items"]) == 1


def test_list_runs_items_have_required_fields(client: TestClient) -> None:
    """T5 — One status='pending' row (all nullable fields null) → all 10 keys present.

    Confirms all fields in _LIST_ITEM_KEYS are present and that types are correct
    for the required non-nullable fields.
    """
    rows = [
        _make_run_list_item(
            id=42,
            dagster_run_id="backfill-xyz",
            kind="extract",
            status="pending",
            started_at=None,
            ended_at=None,
            triggered_by=9,
            dataset_id=None,
            recipe_id=None,
            source_collection_id=None,
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]

    # All 10 required fields must be present.
    for key in _LIST_ITEM_KEYS:
        assert key in item, f"missing key '{key}' in item: {item}"

    # Type and value assertions for key fields.
    assert isinstance(item["id"], int)
    assert item["status"] == "pending"
    assert item["started_at"] is None
    assert item["ended_at"] is None


def test_list_runs_triggered_by_in_both_queries(client: TestClient) -> None:
    """T6 — SQL-structural (M1 lynchpin): both execute() calls carry the owner filter.

    Captures call_args_list[0] (row-list query) and call_args_list[1] (COUNT
    query), compiles each with literal_binds=True, and asserts that
    "triggered_by" and the mock user's id appear in both compiled SQL strings.

    This prevents `total` from silently returning a global count if the owner
    filter is accidentally omitted from the COUNT query (M1 lynchpin from
    F-045/F-046/F-047/F-048, applied to both queries here).
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
        response = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    # ── Row-list query (call index 0) ────────────────────────────────────────
    first_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled_first = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "triggered_by" in compiled_first, (
        f"'triggered_by' not in row-list SQL: {compiled_first}"
    )
    assert str(_MOCK_USER.id) in compiled_first, (
        f"user id {_MOCK_USER.id!r} not in row-list SQL: {compiled_first}"
    )

    # ── COUNT query (call index 1) — M1 requirement ──────────────────────────
    second_stmt = session_mock.execute.call_args_list[1].args[0]
    compiled_second = str(second_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "triggered_by" in compiled_second, (
        f"'triggered_by' not in COUNT SQL: {compiled_second}"
    )
    assert str(_MOCK_USER.id) in compiled_second, (
        f"user id {_MOCK_USER.id!r} not in COUNT SQL: {compiled_second}"
    )


@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])
def test_list_runs_status_filter_in_both_queries(
    client: TestClient, status_value: str
) -> None:
    """T7 — SQL-structural (M1 lynchpin extension): status filter wired into BOTH queries.

    Parameterized over all four valid status values.  For each variant:
      - Calls GET /api/runs?status=<status_value>
      - Captures both execute() call args
      - Compiles each with literal_binds=True
      - Asserts the literal status_value string appears in BOTH the page query
        (call index 0) AND the COUNT query (call index 1)

    This ensures the status filter is never accidentally applied to only one
    of the two queries.  Covers V2 (success) and V3 (running) structural assertions
    as well as "pending" and "failure" variants.
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
        response = client.get(f"/api/runs?status={status_value}")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    # ── Row-list query (call index 0) ─────────────────────────────────────────
    first_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled_first = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert status_value in compiled_first, (
        f"status literal {status_value!r} not in row-list SQL: {compiled_first}"
    )

    # ── COUNT query (call index 1) ────────────────────────────────────────────
    second_stmt = session_mock.execute.call_args_list[1].args[0]
    compiled_second = str(second_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert status_value in compiled_second, (
        f"status literal {status_value!r} not in COUNT SQL: {compiled_second}"
    )


def test_list_runs_status_filter_success(client: TestClient) -> None:
    """T8 (V2) — Session mock returns only status='success' rows when ?status=success.

    All items in the response must have status == 'success'; total == 2.
    """
    rows = [
        _make_run_list_item(id=1, status="success", started_at=_NOW),
        _make_run_list_item(id=2, status="success", started_at=_NOW),
    ]
    _set_overrides(rows=rows, total=2)
    try:
        response = client.get("/api/runs?status=success")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert item["status"] == "success"


def test_list_runs_status_filter_running(client: TestClient) -> None:
    """T9 (V3) — Session mock returns only status='running' rows when ?status=running.

    The single item in the response must have status == 'running'; total == 1.
    """
    rows = [
        _make_run_list_item(id=3, status="running", started_at=_NOW),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/runs?status=running")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "running"


def test_list_runs_invalid_status_returns_422(client: TestClient) -> None:
    """T10 — ?status=bogus → 422 (FastAPI Literal validation fires before handler body).

    The ``status`` parameter is typed as
    Optional[Literal["pending", "running", "success", "failure"]]; FastAPI
    rejects any other value with 422 Unprocessable Entity before the handler
    body is entered.
    Auth dependency is overridden to ensure 401 does not mask the 422.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_list_session_dep([], 0)
    try:
        response = client.get("/api/runs?status=bogus")
    finally:
        _clear_overrides()

    assert response.status_code == 422


def test_list_runs_no_extra_fields_in_items(client: TestClient) -> None:
    """T11 — Schema guard: detail-level fields must NOT appear in list response items.

    One row with all 14 ORM attributes populated → exact key set equals
    _LIST_ITEM_KEYS; none of _EXCLUDED_DETAIL_KEYS appear in the item.
    """
    rows = [
        _make_run_list_item(
            id=1,
            dagster_run_id="backfill-full",
            kind="extract",
            asset_keys=["extract_mineru"],
            partition_keys=["src_1"],
            source_collection_id=5,
            dataset_id=3,
            recipe_id=2,
            config={"batch_size": 10},
            status="success",
            started_at=_NOW,
            ended_at=_NOW,
            triggered_by=9,
            trigger_context={"extra": "data"},
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/runs")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]

    # Exact key set — must match _LIST_ITEM_KEYS exactly.
    actual_keys = set(item.keys())
    assert actual_keys == _LIST_ITEM_KEYS, (
        f"Response key mismatch.\n"
        f"  Extra keys:   {actual_keys - _LIST_ITEM_KEYS}\n"
        f"  Missing keys: {_LIST_ITEM_KEYS - actual_keys}"
    )

    # Detail-level keys must NOT be present.
    for field in _EXCLUDED_DETAIL_KEYS:
        assert field not in item, (
            f"detail-level field '{field}' should not appear in list item: {item}"
        )


def test_list_runs_page_query_has_correct_order_by(client: TestClient) -> None:
    """T12 — SQL-structural ORDER BY guard: page query must include started_at NULLS LAST.

    Captures the page query via session.execute.call_args_list[0].args[0],
    compiles with literal_binds=True, and asserts the compiled SQL contains
    "started_at", "NULLS LAST", and "id" in the ORDER BY clause.

    This prevents a handler that omits .order_by() from passing all other tests
    (T1–T11 do not inspect ORDER BY directly).
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
        client.get("/api/runs")
    finally:
        _clear_overrides()

    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    # Inspect the page query (call index 0).
    page_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(page_stmt.compile(compile_kwargs={"literal_binds": True}))

    assert "started_at" in compiled, (
        f"'started_at' not found in page query ORDER BY: {compiled}"
    )
    assert "NULLS LAST" in compiled, (
        f"'NULLS LAST' not found in page query ORDER BY: {compiled}"
    )
    assert "id" in compiled, (
        f"'id' not found in page query ORDER BY: {compiled}"
    )
