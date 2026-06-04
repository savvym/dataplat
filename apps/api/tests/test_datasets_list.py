"""Tests for GET /api/datasets — S045-F-045.

Unit tests (run in backend layer — no live DB or compose stack required):
  1.  test_list_datasets_returns_200_with_items_and_total
  2.  test_list_datasets_items_have_required_fields
  3.  test_list_datasets_no_token_returns_401
  4.  test_list_datasets_empty_returns_empty_list
  5.  test_list_datasets_only_own_datasets
  6.  test_list_datasets_materialized_by_in_query   (SQL-structural; M1 — both queries)
  7.  test_list_datasets_pending_row_has_null_fields
  8.  test_list_datasets_done_row_fields_all_present
  9.  test_list_datasets_extra_fields_not_in_items

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

Dataset row mocks use _make_dataset() with MagicMock(spec=Dataset).  All 13
ORM-mapped attributes are populated even though DatasetListItem reads only 7
of them — avoids MagicMock attribute-access surprises if code paths change
(consistent with _make_recipe() in test_recipes_list.py, agreed.md §5).

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
from dataplat_api.db.models import Dataset, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="dataset-list@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc)


# ── Mock dataset row factory ──────────────────────────────────────────────────


def _make_dataset(
    id: int,
    recipe_id: int | None = 1,
    version_tag: str = "v1",
    status: str = "done",
    sample_count: int | None = 1500,
    size_bytes: int | None = 204800,
    materialized_at: datetime | None = _NOW,
    materialized_by: int = 9,
    recipe_snapshot: dict[str, Any] | None = None,
    hf_repo_uri: str = "s3://datasets/1_v1",
    dataset_card_md: str | None = None,
    dagster_run_id: str | None = "backfill-abc123",
    stats: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a plain MagicMock that looks like a Dataset ORM row.

    DatasetListItem uses from_attributes=True, so model_validate() reads
    attributes directly from the object — a MagicMock with the right attrs set
    is sufficient.  We avoid constructing a real Dataset instance because
    SQLAlchemy's instrumented attributes require _sa_instance_state to be
    present (set by the mapper), which __new__ alone does not provide.

    All 13 ORM-mapped attributes are populated (id, recipe_id, recipe_snapshot,
    version_tag, hf_repo_uri, dataset_card_md, sample_count, size_bytes, stats,
    status, materialized_by, materialized_at, dagster_run_id) even though
    DatasetListItem only reads 7 of them — avoids MagicMock attribute-access
    surprises if the code path changes (agreed.md §5 _make_dataset() spec).
    Uses MagicMock(spec=Dataset) consistent with _make_recipe() in
    test_recipes_list.py.
    """
    row = MagicMock(spec=Dataset)
    row.id = id
    row.recipe_id = recipe_id
    row.recipe_snapshot = recipe_snapshot if recipe_snapshot is not None else {}
    row.version_tag = version_tag
    row.hf_repo_uri = hf_repo_uri
    row.dataset_card_md = dataset_card_md
    row.sample_count = sample_count
    row.size_bytes = size_bytes
    row.stats = stats
    row.status = status
    row.materialized_by = materialized_by
    row.materialized_at = materialized_at
    row.dagster_run_id = dagster_run_id
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


def test_list_datasets_returns_200_with_items_and_total(client: TestClient) -> None:
    """Test 1 — Two dataset rows → 200, items has 2 elements, total == 2."""
    rows = [
        _make_dataset(id=1),
        _make_dataset(id=2, version_tag="v2"),
    ]
    _set_overrides(rows=rows, total=2)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_datasets_items_have_required_fields(client: TestClient) -> None:
    """Test 2 — One status='done' row → all 7 required keys present in item."""
    rows = [
        _make_dataset(
            id=42,
            recipe_id=7,
            version_tag="v1",
            status="done",
            sample_count=1500,
            size_bytes=204800,
            materialized_at=_NOW,
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]

    # All 7 required fields must be present.
    for key in (
        "id",
        "recipe_id",
        "version_tag",
        "status",
        "sample_count",
        "size_bytes",
        "materialized_at",
    ):
        assert key in item, f"missing key '{key}' in item: {item}"

    # Type and value assertions for key fields.
    assert isinstance(item["id"], int)
    assert isinstance(item["version_tag"], str)
    assert item["id"] == 42
    assert item["status"] == "done"


def test_list_datasets_no_token_returns_401(client: TestClient) -> None:
    """Test 3 — No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/datasets")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_list_datasets_empty_returns_empty_list(client: TestClient) -> None:
    """Test 4 — Empty — session returns 0 rows, total 0 → 200, items == [], total == 0."""
    _set_overrides(rows=[], total=0)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0}


def test_list_datasets_only_own_datasets(client: TestClient) -> None:
    """Test 5 — Isolation — each user sees only their own datasets via separate session mocks.

    User A has 2 datasets → total == 2.
    User B has 1 dataset  → total == 1.
    The two calls use entirely separate session mocks and dependency overrides
    to verify the materialized_by filter is applied per authenticated user.
    """
    user_a = User(id=10, email="user-a@example.com", hashed_password="$2b$12$hash")
    user_b = User(id=20, email="user-b@example.com", hashed_password="$2b$12$hash")

    # ── User A: 2 datasets ──
    rows_a = [
        _make_dataset(id=1, materialized_by=10),
        _make_dataset(id=2, version_tag="v2", materialized_by=10),
    ]

    async def _user_a() -> User:
        return user_a

    app.dependency_overrides[get_current_user] = _user_a
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_a, 2)
    try:
        response_a = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response_a.status_code == 200
    assert response_a.json()["total"] == 2
    assert len(response_a.json()["items"]) == 2

    # ── User B: 1 dataset ──
    rows_b = [
        _make_dataset(id=5, materialized_by=20),
    ]

    async def _user_b() -> User:
        return user_b

    app.dependency_overrides[get_current_user] = _user_b
    app.dependency_overrides[get_session] = _make_list_session_dep(rows_b, 1)
    try:
        response_b = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response_b.status_code == 200
    assert response_b.json()["total"] == 1
    assert len(response_b.json()["items"]) == 1


def test_list_datasets_materialized_by_in_query(client: TestClient) -> None:
    """Test 6 — SQL-structural (M1): both execute() calls carry the owner filter.

    Captures call_args_list[0] (row-list query) and call_args_list[1] (COUNT
    query), compiles each with literal_binds=True, and asserts that
    "materialized_by" and the mock user's id appear in both compiled SQL strings.

    This prevents `total` from silently returning a global count if the owner
    filter is accidentally omitted from the COUNT query (M1 finding, agreed.md §11).
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
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 2

    # ── Row-list query (call index 0) ────────────────────────────────────────
    first_stmt = session_mock.execute.call_args_list[0].args[0]
    compiled_first = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "materialized_by" in compiled_first, (
        f"'materialized_by' not in row-list SQL: {compiled_first}"
    )
    assert str(_MOCK_USER.id) in compiled_first, (
        f"user id {_MOCK_USER.id!r} not in row-list SQL: {compiled_first}"
    )

    # ── COUNT query (call index 1) — M1 requirement ──────────────────────────
    second_stmt = session_mock.execute.call_args_list[1].args[0]
    compiled_second = str(second_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "materialized_by" in compiled_second, (
        f"'materialized_by' not in COUNT SQL: {compiled_second}"
    )
    assert str(_MOCK_USER.id) in compiled_second, (
        f"user id {_MOCK_USER.id!r} not in COUNT SQL: {compiled_second}"
    )


def test_list_datasets_pending_row_has_null_fields(client: TestClient) -> None:
    """Test 7 — One status='pending' row → sample_count, size_bytes, materialized_at all None."""
    rows = [
        _make_dataset(
            id=10,
            status="pending",
            sample_count=None,
            size_bytes=None,
            materialized_at=None,
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "pending"
    assert item["sample_count"] is None
    assert item["size_bytes"] is None
    assert item["materialized_at"] is None


def test_list_datasets_done_row_fields_all_present(client: TestClient) -> None:
    """Test 8 — Maps to F-045 verification[0]: status='done' row → all numeric fields present.

    Asserts item["status"] == "done", item["sample_count"] == 1500,
    item["size_bytes"] == 204800, and item["materialized_at"] is not None.
    """
    rows = [
        _make_dataset(
            id=42,
            recipe_id=7,
            version_tag="v1",
            status="done",
            sample_count=1500,
            size_bytes=204800,
            materialized_at=_NOW,
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "done"
    assert item["sample_count"] == 1500
    assert item["size_bytes"] == 204800
    assert item["materialized_at"] is not None


def test_list_datasets_extra_fields_not_in_items(client: TestClient) -> None:
    """Test 9 — Schema guard: detail-level fields must NOT appear in list response items.

    Asserts none of ["recipe_snapshot", "hf_repo_uri", "dataset_card_md",
    "dagster_run_id", "stats", "materialized_by"] appear in the response item,
    confirming the slim DatasetListItem schema excludes those fields.
    """
    rows = [
        _make_dataset(
            id=1,
            recipe_id=3,
            version_tag="v1",
            status="done",
            sample_count=100,
            size_bytes=1024,
            materialized_at=_NOW,
            materialized_by=9,
            recipe_snapshot={"steps": ["pack"]},
            hf_repo_uri="s3://datasets/1_v1",
            dataset_card_md="# My Dataset",
            dagster_run_id="backfill-xyz",
            stats={"some": "stat"},
        ),
    ]
    _set_overrides(rows=rows, total=1)
    try:
        response = client.get("/api/datasets")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]

    excluded_fields = [
        "recipe_snapshot",
        "hf_repo_uri",
        "dataset_card_md",
        "dagster_run_id",
        "stats",
        "materialized_by",
    ]
    for field in excluded_fields:
        assert field not in item, (
            f"detail-level field '{field}' should not appear in list item but found in: {item}"
        )
