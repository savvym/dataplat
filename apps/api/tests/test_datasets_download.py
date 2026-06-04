"""Tests for GET /api/datasets/{id}/download — S047-F-047.

Unit tests (run in backend layer — no live DB or compose stack required):
  1.  test_download_200_returns_json_with_files
  2.  test_download_response_shape_exact
  3.  test_download_parquet_urls_are_named_correctly
  4.  test_download_presigned_urls_are_well_formed
  5.  test_download_not_found_returns_404
  6.  test_download_wrong_owner_returns_404
  7.  test_download_no_token_returns_401
  8.  test_download_invalid_id_returns_422
  9.  test_download_owner_scope_sql_literal_binds
  10. test_download_all_five_keys_present
  11. test_download_presigned_url_keys_match_prefix

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock session pattern:
  The GET /{id}/download handler calls session.execute() exactly ONCE and calls
  scalar_one_or_none() (synchronous) on the result proxy.  The correct mock
  shape is:
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = dataset_row_or_none
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

S3 client mock pattern (OQ-1 resolved: generate_presigned_url is async def in
aiobotocore 2.25.1):
    mock_s3 = AsyncMock()
    mock_s3.generate_presigned_url = AsyncMock(return_value=<url_string>)
    async def _mock_s3_dep():
        yield mock_s3
    app.dependency_overrides[get_s3_client] = _mock_s3_dep

Test #11 uses mock_s3.generate_presigned_url.call_args_list to assert the exact
set of Key= values passed across all 5 presigned URL calls — the structural
assertion that the correct MinIO keys are built (analog of S045 M1).
"""

from __future__ import annotations

import re
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
from dataplat_api.routers.datasets import _PRESIGN_TTL_SECONDS
from dataplat_api.storage.s3 import get_s3_client

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=9, email="dataset-dl@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Timestamp constant ────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)

# ── Expected 3-field top-level key set ───────────────────────────────────────

_EXPECTED_TOP_KEYS = {"dataset_id", "files", "expires_in_seconds"}
_EXPECTED_FILE_KEYS = {"name", "presigned_url"}

# ── Expected 5 file names ─────────────────────────────────────────────────────

_EXPECTED_NAMES = {
    "data/train-00000.parquet",
    "data/validation-00000.parquet",
    "recipe.json",
    "README.md",
    "dataset_infos.json",
}


# ── Mock dataset row factory ──────────────────────────────────────────────────


def _make_dataset(
    id: int = 42,
    recipe_id: int | None = 3,
    version_tag: str = "v1",
    hf_repo_uri: str = "s3://datasets/42_v1",
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

    DatasetDownloadResponse reads row.id and row.version_tag to construct
    the MinIO prefix.  All 13 ORM-mapped attributes are populated for
    completeness (mirrors _make_dataset_detail in test_datasets_get.py).
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


# ── S3 mock helpers ───────────────────────────────────────────────────────────

_FAKE_URL = (
    "http://minio:9000/datasets/42_v1/data/train-00000.parquet"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
)


def _make_s3_dep(mock_s3: AsyncMock) -> Any:
    """Return a get_s3_client override that yields the provided AsyncMock."""

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        yield mock_s3

    return _override


def _make_mock_s3(return_value: str = _FAKE_URL) -> AsyncMock:
    """Return an AsyncMock S3 client whose generate_presigned_url returns return_value."""
    mock_s3 = AsyncMock()
    mock_s3.generate_presigned_url = AsyncMock(return_value=return_value)
    return mock_s3


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """TestClient with app lifespan initialised.

    Does NOT set dependency overrides — each test sets and clears its own.
    """
    with TestClient(app) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_download_200_returns_json_with_files(client: TestClient) -> None:
    """Test 1 (V1) — 200 happy path: 5 files, correct Content-Type and expires_in_seconds."""
    dataset_row = _make_dataset(id=42, version_tag="v1")
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")

    body = response.json()
    assert body["dataset_id"] == 42
    assert body["expires_in_seconds"] == _PRESIGN_TTL_SECONDS
    assert len(body["files"]) == 5
    for entry in body["files"]:
        assert entry["presigned_url"] != ""
        assert isinstance(entry["presigned_url"], str)


def test_download_response_shape_exact(client: TestClient) -> None:
    """Test 2 (V1, schema guard) — Exact key sets in response: no extra fields leaked."""
    dataset_row = _make_dataset(id=42, version_tag="v1")
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200
    body = response.json()

    # Top-level key set — exactly 3 fields.
    assert set(body.keys()) == _EXPECTED_TOP_KEYS, (
        f"Top-level key mismatch.\n"
        f"  Extra:   {set(body.keys()) - _EXPECTED_TOP_KEYS}\n"
        f"  Missing: {_EXPECTED_TOP_KEYS - set(body.keys())}"
    )

    # Each file entry — exactly 2 fields.
    assert len(body["files"]) > 0
    assert set(body["files"][0].keys()) == _EXPECTED_FILE_KEYS, (
        f"File entry key mismatch: {set(body['files'][0].keys())!r}"
    )


def test_download_parquet_urls_are_named_correctly(client: TestClient) -> None:
    """Test 3 (V2) — files list includes entries for both Parquet split names."""
    dataset_row = _make_dataset(id=42, version_tag="v1")
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200
    names = {f["name"] for f in response.json()["files"]}
    assert "data/train-00000.parquet" in names, (
        f"train split not in file names: {names!r}"
    )
    assert "data/validation-00000.parquet" in names, (
        f"validation split not in file names: {names!r}"
    )


def test_download_presigned_urls_are_well_formed(client: TestClient) -> None:
    """Test 4 (V2) — Each presigned_url matches a well-formed signed URL pattern."""
    dataset_row = _make_dataset(id=42, version_tag="v1")
    # Return a URL that matches the expected signed-URL pattern.
    signed_url = (
        "http://minio:9000/datasets/42_v1/data/train-00000.parquet"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
    )
    mock_s3 = _make_mock_s3(return_value=signed_url)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200
    for entry in response.json()["files"]:
        url = entry["presigned_url"]
        assert re.match(r"^https?://.+\?X-Amz", url), (
            f"presigned_url does not look like a signed URL: {url!r}"
        )


def test_download_not_found_returns_404(client: TestClient) -> None:
    """Test 5 (V3) — Session returns None (non-existent id) → 404 with correct detail."""
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/99999/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_download_wrong_owner_returns_404(client: TestClient) -> None:
    """Test 6 (V3, no enumeration leak) — Dataset exists for another user → same 404.

    The handler combines id == ? AND materialized_by == ? in a single query,
    so a row owned by user id=99 is invisible to user id=9.  The mock returns
    None to simulate this query miss.  Both 'not found' and 'wrong owner'
    produce identical 404 — no information leak.
    """
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    # Session returns None — simulates a row that exists for user id=99, not id=9.
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/1/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 404
    assert response.json() == {"detail": "Dataset not found"}


def test_download_no_token_returns_401(client: TestClient) -> None:
    """Test 7 (auth gate) — No Authorization header → 401 with WWW-Authenticate: Bearer.

    No dependency override — real oauth2_scheme (auto_error=True) raises 401
    automatically when the Authorization header is absent.
    """
    response = client.get("/api/datasets/42/download")
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_download_invalid_id_returns_422(client: TestClient) -> None:
    """Test 8 — Non-integer path segment → 422 (FastAPI path param validation fires first).

    The ``id`` path parameter is typed as ``int``; FastAPI rejects any non-integer
    value with 422 Unprocessable Entity before the handler body is entered.
    Auth dependency is overridden so that 401 does not interfere with observing 422.
    """
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(None)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/not-a-number/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 422


def test_download_owner_scope_sql_literal_binds(client: TestClient) -> None:
    """Test 9 — SQL-structural: execute() carries both id and materialized_by.

    Verification approach (mirrors test_get_dataset_materialized_by_in_query in F-046):
      1. Capture the Select object from the single execute() call via call_args_list.
      2. Compile it with literal_binds=True so both bound values appear as literals.
      3. Assert "materialized_by" and the mock user's id (9) both appear in the SQL.

    This guards against accidentally dropping the materialized_by filter from the
    query, which would allow any authenticated user to download any dataset by id.
    """
    captured_session: list[AsyncMock] = []

    async def _capturing_session() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        captured_session.append(session)
        yield session

    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _capturing_session
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        client.get("/api/datasets/5/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    # The endpoint returns 404 (scalar_one_or_none returned None) — that's fine;
    # we care about the SQL that was sent, not the HTTP response code here.
    assert len(captured_session) == 1
    session_mock = captured_session[0]
    assert session_mock.execute.call_count == 1

    stmt = session_mock.execute.call_args_list[0].args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "materialized_by" in compiled, (
        f"'materialized_by' not in compiled SQL: {compiled}"
    )
    assert str(_MOCK_USER.id) in compiled, (
        f"user id {_MOCK_USER.id!r} not in compiled SQL: {compiled}"
    )


def test_download_all_five_keys_present(client: TestClient) -> None:
    """Test 10 — files list has exactly 5 entries with the expected names."""
    dataset_row = _make_dataset(id=42, version_tag="v1")
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200
    body = response.json()
    assert len(body["files"]) == 5, (
        f"Expected 5 file entries, got {len(body['files'])}: {body['files']!r}"
    )
    names = {f["name"] for f in body["files"]}
    assert names == _EXPECTED_NAMES, (
        f"File name set mismatch.\n"
        f"  Extra:   {names - _EXPECTED_NAMES}\n"
        f"  Missing: {_EXPECTED_NAMES - names}"
    )


def test_download_presigned_url_keys_match_prefix(client: TestClient) -> None:
    """Test 11 — Structural: exact set of Key= args passed to generate_presigned_url.

    Extracts mock_s3.generate_presigned_url.call_args_list and asserts that the
    Key= values passed across all 5 calls are exactly the 5 expected fully-prefixed
    MinIO object keys.  This catches a handler that builds wrong keys (e.g., bare
    relative names without the prefix) — which would pass all other tests because
    the mock returns a constant URL regardless of Key= argument.

    This is the structural analog of S045 M1 (ensuring the SQL owner filter was
    actually applied, not just present in the code).
    """
    dataset_row = _make_dataset(id=42, version_tag="v1")
    mock_s3 = _make_mock_s3()

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_returning(dataset_row)
    app.dependency_overrides[get_s3_client] = _make_s3_dep(mock_s3)
    try:
        response = client.get("/api/datasets/42/download")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_s3_client, None)

    assert response.status_code == 200

    calls = mock_s3.generate_presigned_url.call_args_list
    assert len(calls) == 5, (
        f"Expected 5 calls to generate_presigned_url, got {len(calls)}"
    )
    keys = {c.kwargs["Params"]["Key"] for c in calls}
    assert keys == {
        "42_v1/data/train-00000.parquet",
        "42_v1/data/validation-00000.parquet",
        "42_v1/recipe.json",
        "42_v1/README.md",
        "42_v1/dataset_infos.json",
    }, (
        f"MinIO key set mismatch.\n"
        f"  Actual:   {keys!r}\n"
        f"  Expected: 42_v1/... (5 prefixed keys)"
    )
