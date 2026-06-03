"""Tests for POST /api/chunks/distribution — S034-F-034.

13 test cases verifying the chunk distribution endpoint. All tests use FastAPI's
TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern for distribution-correctness tests: build a real pa.Table (not a
MagicMock) so PyArrow type introspection, drop_null(), group_by().aggregate(),
sort_by(), and numpy.histogram() execute on real data.
Patch "dataplat_api.routers.chunks.get_or_create_chunks_table" to return a mock
Lance table whose query builder chain returns the real pa.Table at .to_arrow().

Error-path tests (401, 400, 422) use MagicMock or minimal pa.Table since they
never reach (or only partially reach) the distribution code.

Auth override: app.dependency_overrides[get_current_user] = _override_current_user
for tests that need a valid user. The 401 test sends no Authorization header and
does NOT override the dependency.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Mock Lance table builder ───────────────────────────────────────────────────


def _make_dist_mock_table(real_pa_table: pa.Table) -> MagicMock:
    """Build a mock Lance table that returns a real pa.Table at .to_arrow().

    Supports the query-builder chain used in the distribution handler:
        table.search() → qb
        qb.where(...)  → qb  (chained, optional)
        qb.select(...) → qb  (chained)
        qb.to_arrow()  → real_pa_table   ← real PyArrow table, NOT MagicMock
    """
    mock_table = MagicMock()
    qb = MagicMock()
    qb.where.return_value = qb
    qb.select.return_value = qb
    qb.to_arrow.return_value = real_pa_table
    mock_table.search.return_value = qb
    return mock_table


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _distribution(client: TestClient, body: dict[str, Any]) -> Any:
    """POST /api/chunks/distribution with a Bearer auth header."""
    return client.post(
        "/api/chunks/distribution",
        json=body,
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_distribution_numeric_with_filter(client: TestClient) -> None:
    """V1: float column + filter → 10 buckets; type='numeric'; column echoed;
    each bucket has 'range' (2-float list) and 'count' (int); counts sum to row total.
    """
    import random

    random.seed(42)
    scores = [random.uniform(0.0, 1.0) for _ in range(100)]
    real_table = pa.table({"attr_quality_score": pa.array(scores, type=pa.float64())})
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(
                client,
                {
                    "filter": "source_id = 5",
                    "column": "attr_quality_score",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert body["column"] == "attr_quality_score"
    assert len(body["buckets"]) == 10

    total_count = 0
    for bucket in body["buckets"]:
        assert "range" in bucket
        assert "count" in bucket
        assert len(bucket["range"]) == 2
        assert isinstance(bucket["range"][0], float)
        assert isinstance(bucket["range"][1], float)
        assert isinstance(bucket["count"], int)
        total_count += bucket["count"]

    assert total_count == 100


def test_distribution_categorical_no_filter(client: TestClient) -> None:
    """V2: string column, no filter → type='categorical'; buckets have 'value'/'count'
    keys; known value counts present; ordered by count descending;
    qb.where.assert_not_called() [N4].
    """
    lang_codes = ["en"] * 150 + ["zh"] * 42 + [None] * 3
    real_table = pa.table({"attr_lang_code": pa.array(lang_codes, type=pa.string())})
    mock_table = _make_dist_mock_table(real_table)
    qb = mock_table.search.return_value

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(
                client,
                {"column": "attr_lang_code"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "categorical"
    assert body["column"] == "attr_lang_code"

    # All buckets must have 'value' and 'count' keys
    for bucket in body["buckets"]:
        assert "value" in bucket
        assert "count" in bucket

    # Known value counts
    by_value = {b["value"]: b["count"] for b in body["buckets"]}
    assert by_value["en"] == 150
    assert by_value["zh"] == 42
    assert by_value.get(None) == 3

    # Ordered by count descending: "en"(150) > "zh"(42) > null(3)
    counts = [b["count"] for b in body["buckets"]]
    assert counts == sorted(counts, reverse=True)

    # No filter supplied → .where() must NOT have been called [N4]
    qb.where.assert_not_called()


def test_distribution_numeric_default_bins(client: TestClient) -> None:
    """No 'bins' in request → exactly 10 buckets; all bucket counts sum to non-null row count."""
    values = list(range(50))  # 50 distinct integers, all non-null
    real_table = pa.table({"token_count": pa.array(values, type=pa.int64())})
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "token_count"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert len(body["buckets"]) == 10
    assert sum(b["count"] for b in body["buckets"]) == 50


def test_distribution_numeric_custom_bins(client: TestClient) -> None:
    """bins=5 → exactly 5 buckets returned."""
    values = [float(i) for i in range(100)]
    real_table = pa.table({"attr_quality_score": pa.array(values, type=pa.float64())})
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "attr_quality_score", "bins": 5})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert len(body["buckets"]) == 5


def test_distribution_numeric_all_null(client: TestClient) -> None:
    """Column of all-null floats → {"type": "numeric", "buckets": []}."""
    real_table = pa.table(
        {"attr_quality_score": pa.array([None, None, None], type=pa.float64())}
    )
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "attr_quality_score"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert body["buckets"] == []


def test_distribution_numeric_all_same_value(client: TestClient) -> None:
    """All rows have identical float value → single bucket {"range": [v, v], "count": N}."""
    real_table = pa.table(
        {"attr_quality_score": pa.array([0.5, 0.5, 0.5, 0.5, 0.5], type=pa.float64())}
    )
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "attr_quality_score"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "numeric"
    assert len(body["buckets"]) == 1
    bucket = body["buckets"][0]
    assert bucket["range"] == [0.5, 0.5]
    assert bucket["count"] == 5


def test_distribution_categorical_with_null_value(client: TestClient) -> None:
    """String column containing null rows → bucket {"value": null, "count": N} is present."""
    lang_codes = ["en"] * 10 + [None] * 4
    real_table = pa.table({"attr_lang_code": pa.array(lang_codes, type=pa.string())})
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "attr_lang_code"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "categorical"

    by_value = {b["value"]: b["count"] for b in body["buckets"]}
    assert by_value.get(None) == 4
    assert by_value.get("en") == 10


def test_distribution_empty_table(client: TestClient) -> None:
    """Lance returns 0-row table → makes 2 API calls (one float-typed column,
    one string-typed column) and asserts buckets == [] for both [N3].
    """
    # 0-row table with a float column
    float_table = pa.table({"attr_quality_score": pa.array([], type=pa.float64())})
    # 0-row table with a string column
    string_table = pa.table({"attr_lang_code": pa.array([], type=pa.string())})

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        # --- Call 1: numeric column with 0 rows ---
        mock_float = _make_dist_mock_table(float_table)
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_float,
        ):
            resp_float = _distribution(
                client,
                {"filter": "source_id = 999999", "column": "attr_quality_score"},
            )

        # --- Call 2: categorical column with 0 rows ---
        mock_string = _make_dist_mock_table(string_table)
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_string,
        ):
            resp_string = _distribution(
                client,
                {"filter": "source_id = 999999", "column": "attr_lang_code"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp_float.status_code == 200
    assert resp_float.json()["buckets"] == []
    assert resp_float.json()["type"] == "numeric"

    assert resp_string.status_code == 200
    assert resp_string.json()["buckets"] == []
    assert resp_string.json()["type"] == "categorical"


def test_distribution_invalid_column_returns_400(client: TestClient) -> None:
    """get_or_create_chunks_table raises on unknown column → HTTP 400,
    detail contains 'Lance query error'.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            side_effect=Exception("Column 'nonexistent_col' not found in schema"),
        ):
            resp = _distribution(client, {"column": "nonexistent_col"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]


def test_distribution_unsupported_type_returns_400(client: TestClient) -> None:
    """Real Arrow table with a bool-typed column → HTTP 400, detail contains 'unsupported type'."""
    real_table = pa.table(
        {"attr_pii_has_pii": pa.array([True, False, True], type=pa.bool_())}
    )
    mock_table = _make_dist_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _distribution(client, {"column": "attr_pii_has_pii"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "unsupported type" in resp.json()["detail"]


def test_distribution_no_token_returns_401(client: TestClient) -> None:
    """Missing Authorization header → 401.

    Does NOT override get_current_user; relies on oauth2_scheme auto_error=True.
    """
    resp = client.post(
        "/api/chunks/distribution",
        json={"column": "attr_quality_score"},
    )
    assert resp.status_code == 401


def test_distribution_filter_too_long_returns_422(client: TestClient) -> None:
    """[M1] filter of 1001 chars → 422 (Pydantic max_length=1000 constraint)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _distribution(
            client,
            {
                "filter": "x" * 1001,
                "column": "attr_quality_score",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 422


def test_distribution_bins_out_of_range_returns_422(client: TestClient) -> None:
    """[M1] bins=0 and bins=101 → 422 (Pydantic ge=1, le=100 constraints)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        # bins=0 (below minimum ge=1)
        resp_zero = _distribution(
            client,
            {"column": "attr_quality_score", "bins": 0},
        )
        # bins=101 (above maximum le=100)
        resp_over = _distribution(
            client,
            {"column": "attr_quality_score", "bins": 101},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp_zero.status_code == 422
    assert resp_over.status_code == 422
