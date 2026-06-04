"""Tests for GET /api/datasets/{id} — S046-F-046.

Unit tests (run in backend layer — no live DB or compose stack required):
  1.  test_get_dataset_200_all_fields          (V1 — status='done' row, all 13 keys)
  2.  test_get_dataset_not_found_returns_404   (V2 — non-existent id)
  3.  test_get_dataset_wrong_owner_returns_404 (no-enumeration-leak)
  4.  test_get_dataset_no_token_returns_401    (auth gate)
  5.  test_get_dataset_recipe_snapshot_is_dict (guard against double-serialization)
  6.  test_get_dataset_stats_nullable          (stats=None passes through)
  7.  test_get_dataset_materialized_by_in_query (SQL-structural: owner filter present)
  8.  test_get_dataset_no_extra_fields_leaked  (exact 13-key set; dataset_card_md IS included)
  9.  test_get_dataset_invalid_id_returns_422  (non-integer path segment)

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET /{id} handler calls session.execute() exactly ONCE and calls
  scalar_one_or_none() (synchronous) on the result proxy.  The correct mock
  shape is:
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = dataset_row_or_none
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
  Note: scalar_one_or_none() is synchronous (called on the result proxy returned
  from await session.execute()).  Use MagicMock() for the result, not AsyncMock().

Mock factory note:
  _make_dataset_detail is defined locally in this file for self-containment.
  All 13 ORM attributes are populated (id, recipe_id, recipe_snapshot,
  version_tag, hf_repo_uri, dataset_card_md, sample_count, size_bytes, stats,
  status, materialized_by, materialized_at, dagster_run_id).

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
from dataplat_api.db.models import Dataset, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="dataset-get@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)

# ── Expected 13-field key set ─────────────────────────────────────────────────

_EXPECTED_KEYS = {
    "id",
    "recipe_id",
    "version_tag",
    "hf_repo_uri",
    "recipe_snapshot",
    "sample_count",
    "size_bytes",
    "stats",
    "dataset_card_md",
    "status",
    "materialized_by",
    "materialized_at",
    "dagster_run_id",
}


# ── Mock dataset row factory ──────────────────────────────────────────────────


def _make_dataset_detail(
    id: int = 42,
    recipe_id: int | None = 3,
    version_tag: str = "v1",
    hf_repo_uri: str = "s3://datasets/3_v1",
    recipe_snapshot: dict[str, Any] | None = None,
    sample_count: int | None = 1500,
    size_bytes: int | None = 204800,
    stats: dict[str, Any] | None = None,
    dataset_card_md: str | None = None,
    status: str = "done",
    materialized_by: int = 9,
    materialized_at: datetime | None = _NOW,
    dagster_run_id: str | None = "backfill-abc456",
) -> MagicMock:
    """Build a plain MagicMock that looks like a Dataset ORM row.

    DatasetDetailResponse uses from_attributes=True, so model_validate() reads
    attributes directly from the object.  All 13 ORM-mapped attributes are
    populated — same discipline as _make_dataset() in test_datasets_list.py.
    Uses MagicMock(spec=Dataset) consistent with _make_recipe_detail() in
    test_recipes_get.py.
    """
    row = MagicMock(spec=Dataset)
    row.id = id
    row.recipe_id = recipe_id
    row.recipe_snapshot = (
        recipe_snapshot if recipe_snapshot is not None else {"steps": ["pack"]}
    )
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


# ── Session mock helpers ──────────────────────────────────────────────────────


def _make_session_dep_returning(dataset: MagicMock | None) -> Any:
    """Return a get_session override whose execute().scalar_one_or_none() returns `dataset`."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = dataset
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


def test_get_dataset_200_all_fields(client: TestClient) -> None:
    """Test 1 (V1) — status='done' row → 200, all 13 keys present, correct values."""
    dataset_row = _make_dataset_detail(
        id=42,
        recipe_id=3,
        version_tag="v1",
        hf_repo_uri="s3://datasets/3_v1",
        recipe_snapshot={"steps": ["tokenize", "pack"]},
        sample_count=1500,
        size_bytes=204800,
        stats={"token_count": 3_000_000},
        status="done",
        materialized_by=9,
        materialized_at=_NOW,
        dagster_run_id="backfill-abc456",
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    try:
        response = client.get("/api/datasets/42")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()

    # All 13 fields must be present.
    for key in _EXPECTED_KEYS:
        assert key in body, f"missing key '{key}' in response: {body}"

    # Spot-check values.
    assert body["id"] == 42
    assert body["recipe_id"] == 3
    assert body["version_tag"] == "v1"
    assert body["hf_repo_uri"] == "s3://datasets/3_v1"
    assert body["status"] == "done"
    assert isinstance(body["recipe_snapshot"], dict)
    assert body["recipe_snapshot"] == {"steps": ["tokenize", "pack"]}
    assert body["stats"] == {"token_count": 3_000_000}


def test_get_dataset_not_found_returns_404(client: TestClient) -> None:
    """Test 2 (V2) — Session returns None (non-existent id) → 404 with correct detail."""
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/datasets/99999")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_get_dataset_wrong_owner_returns_404(client: TestClient) -> None:
    """Test 3 — Dataset exists for user id=99, not mock user id=9 → same 404.

    The handler combines id == ? AND materialized_by == ? in a single query,
    so a row owned by user id=99 is invisible to user id=9.  The mock returns
    None to simulate this query miss.  Both 'not found' and 'wrong owner'
    produce identical 404 — no information leak (no-enumeration, mirrors
    get_recipe in F-039).
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    # Session returns None — simulates a row that exists for user id=99, not id=9.
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/datasets/1")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_get_dataset_no_token_returns_401(client: TestClient) -> None:
    """Test 4 — No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/datasets/42")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_get_dataset_recipe_snapshot_is_dict(client: TestClient) -> None:
    """Test 5 — recipe_snapshot in response is a dict, not a JSON-encoded string.

    Guards against accidental double-serialization where the JSONB value is
    stored as a JSON string and returned as a string rather than an object.
    """
    dataset_row = _make_dataset_detail(
        id=7,
        recipe_snapshot={"sources": ["web_crawl"], "filters": ["dedup"]},
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    try:
        response = client.get("/api/datasets/7")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["recipe_snapshot"], dict), (
        f"recipe_snapshot should be a dict, got {type(body['recipe_snapshot'])}: "
        f"{body['recipe_snapshot']!r}"
    )
    assert body["recipe_snapshot"] == {"sources": ["web_crawl"], "filters": ["dedup"]}


def test_get_dataset_stats_nullable(client: TestClient) -> None:
    """Test 6 — Row with stats=None → 200, response body stats is None.

    Confirms nullable JSONB field passes through as JSON null for status='pending'
    and status='running' rows where the IO manager has not yet written stats.
    """
    dataset_row = _make_dataset_detail(
        id=10,
        status="pending",
        sample_count=None,
        size_bytes=None,
        stats=None,
        materialized_at=None,
        dagster_run_id=None,
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    try:
        response = client.get("/api/datasets/10")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["stats"] is None
    assert body["status"] == "pending"


def test_get_dataset_materialized_by_in_query(client: TestClient) -> None:
    """Test 7 — SQL-structural: single execute() call carries both id and materialized_by.

    Verification approach (mirrors test_get_recipe_owner_id_in_query in F-039):
      1. Capture the Select object from the single execute() call via call_args_list.
      2. Compile it with literal_binds=True so both bound values appear as literals.
      3. Assert "materialized_by" and the mock user's id (9) both appear in the SQL.

    This guards against accidentally dropping the materialized_by filter from the
    query, which would allow any authenticated user to retrieve any dataset by id.
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
        client.get("/api/datasets/5")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    # The endpoint returns 404 (scalar_one_or_none returned None) — that's fine;
    # we care about the SQL that was sent, not the HTTP response code here.
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    # Compile the captured SELECT with literal_binds=True so bound parameter
    # values (id = 5, materialized_by = 9) are rendered as literals in the SQL.
    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "materialized_by" in compiled, (
        f"'materialized_by' not in compiled SQL: {compiled}"
    )
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )


def test_get_dataset_no_extra_fields_leaked(client: TestClient) -> None:
    """Test 8 — Exact 13-key set in response; dataset_card_md IS present (detail endpoint).

    Confirms DatasetDetailResponse includes dataset_card_md (which DatasetListItem
    explicitly excludes), and that no unexpected extra fields appear beyond the 13
    defined in the schema.

    Key distinction vs list endpoint: list endpoint excludes dataset_card_md
    (deferred to F-046 per F-045 docstring); detail endpoint MUST include it.
    """
    dataset_row = _make_dataset_detail(
        id=33,
        dataset_card_md="# My Dataset\nSome description.",
        stats={"token_count": 1_000},
    )
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    try:
        response = client.get("/api/datasets/33")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()

    # dataset_card_md must be present in the detail response.
    assert "dataset_card_md" in body, (
        f"'dataset_card_md' missing from detail response: {body}"
    )

    # Exact key set — no more, no fewer than the 13 defined fields.
    actual_keys = set(body.keys())
    assert actual_keys == _EXPECTED_KEYS, (
        f"Response key mismatch.\n"
        f"  Extra keys:   {actual_keys - _EXPECTED_KEYS}\n"
        f"  Missing keys: {_EXPECTED_KEYS - actual_keys}"
    )


def test_get_dataset_invalid_id_returns_422(client: TestClient) -> None:
    """Test 9 — Non-integer path segment → 422 (FastAPI path param validation fires first).

    The ``id`` path parameter is typed as ``int``; FastAPI rejects any non-integer
    value with 422 Unprocessable Entity before the handler body is entered.
    Auth dependency is overridden so that 401 does not interfere with observing 422.
    No session mock call is required — path-param validation fires before dependency
    injection of the session.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    try:
        response = client.get("/api/datasets/not-a-number")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 422
