"""Tests for POST /api/chunks/aggregate — S033-F-033.

10 test cases verifying the chunk aggregate endpoint. All tests use FastAPI's
TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern for aggregation-correctness tests: build a real pa.Table (not a
MagicMock) so PyArrow's group_by().aggregate() actually executes on real data.
Patch "dataplat_api.routers.chunks.get_or_create_chunks_table" to return a mock
Lance table whose query builder chain returns the real pa.Table at .to_arrow().

Error-path tests (401, 400, 422) can use MagicMock since they never reach
the aggregation code.

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


# ── Mock Lance table builders ─────────────────────────────────────────────────


def _make_agg_mock_table(real_pa_table: pa.Table) -> MagicMock:
    """Build a mock Lance table that returns a real pa.Table at .to_arrow().

    Supports the query-builder chain used in the aggregate handler:
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


def _aggregate(client: TestClient, body: dict[str, Any]) -> Any:
    """POST /api/chunks/aggregate with a Bearer auth header."""
    return client.post(
        "/api/chunks/aggregate",
        json=body,
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_aggregate_count_by_lang_code(client: TestClient) -> None:
    """V1: filter + group_by 'attr_lang_code' + metrics=['count'] → correct groups.

    42 rows with lang_code='zh', 17 rows with lang_code='en'.
    Response groups must contain exactly those two entries with correct counts.
    """
    real_table = pa.table(
        {"attr_lang_code": pa.array(["zh"] * 42 + ["en"] * 17, type=pa.string())}
    )
    mock_table = _make_agg_mock_table(real_table)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {
                    "filter": "source_id = 5",
                    "group_by": "attr_lang_code",
                    "metrics": ["count"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert "groups" in body
    groups = body["groups"]
    assert len(groups) == 2

    by_lang = {g["attr_lang_code"]: g for g in groups}
    assert by_lang["zh"]["count"] == 42
    assert by_lang["en"]["count"] == 17


def test_aggregate_count_consistent_with_direct_count(client: TestClient) -> None:
    """V2: sum of all per-group counts equals the total row count in the table.

    3 groups: 10 zh + 20 en + 12 ja = 42 total rows.
    The sum of all groups' 'count' values must equal 42.
    """
    lang_codes = ["zh"] * 10 + ["en"] * 20 + ["ja"] * 12
    real_table = pa.table({"attr_lang_code": pa.array(lang_codes, type=pa.string())})
    mock_table = _make_agg_mock_table(real_table)
    mock_table.count_rows.return_value = (
        42  # not called by aggregate, but set for clarity
    )

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {"group_by": "attr_lang_code", "metrics": ["count"]},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    groups = resp.json()["groups"]
    assert len(groups) == 3
    total_from_groups = sum(g["count"] for g in groups)
    assert total_from_groups == 42


def test_aggregate_no_filter(client: TestClient) -> None:
    """No filter field supplied; groups are computed over all rows correctly."""
    real_table = pa.table(
        {"attr_lang_code": pa.array(["zh"] * 5 + ["en"] * 3, type=pa.string())}
    )
    mock_table = _make_agg_mock_table(real_table)
    qb = mock_table.search.return_value

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {"group_by": "attr_lang_code", "metrics": ["count"]},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    groups = resp.json()["groups"]
    # .where() must NOT have been called (no filter)
    qb.where.assert_not_called()
    by_lang = {g["attr_lang_code"]: g for g in groups}
    assert by_lang["zh"]["count"] == 5
    assert by_lang["en"]["count"] == 3


def test_aggregate_empty_result(client: TestClient) -> None:
    """Filter matches zero rows → groups=[]."""
    real_table = pa.table({"attr_lang_code": pa.array([], type=pa.string())})
    mock_table = _make_agg_mock_table(real_table)

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {
                    "filter": "source_id = 999999",
                    "group_by": "attr_lang_code",
                    "metrics": ["count"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    assert resp.json()["groups"] == []


def test_aggregate_numeric_metric(client: TestClient) -> None:
    """metrics=['sum:attr_quality_score'] → groups contain 'sum_attr_quality_score' key."""
    real_table = pa.table(
        {
            "source_id": pa.array([1, 1, 2, 2], type=pa.int64()),
            "attr_quality_score": pa.array([0.8, 0.9, 0.5, 0.7], type=pa.float64()),
        }
    )
    mock_table = _make_agg_mock_table(real_table)

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {
                    "group_by": "source_id",
                    "metrics": ["sum:attr_quality_score"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    groups = resp.json()["groups"]
    assert len(groups) == 2

    by_source = {g["source_id"]: g for g in groups}
    # source_id=1: 0.8 + 0.9 = 1.7
    assert "sum_attr_quality_score" in by_source[1]
    assert abs(by_source[1]["sum_attr_quality_score"] - 1.7) < 1e-9
    # source_id=2: 0.5 + 0.7 = 1.2
    assert abs(by_source[2]["sum_attr_quality_score"] - 1.2) < 1e-9


def test_aggregate_multiple_metrics(client: TestClient) -> None:
    """metrics=['count', 'mean:attr_quality_score'] → groups contain both keys."""
    real_table = pa.table(
        {
            "attr_lang_code": pa.array(["zh", "zh", "en"], type=pa.string()),
            "attr_quality_score": pa.array([0.8, 0.9, 0.7], type=pa.float64()),
        }
    )
    mock_table = _make_agg_mock_table(real_table)

    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _aggregate(
                client,
                {
                    "group_by": "attr_lang_code",
                    "metrics": ["count", "mean:attr_quality_score"],
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    groups = resp.json()["groups"]
    assert len(groups) == 2

    by_lang = {g["attr_lang_code"]: g for g in groups}
    # Both metric keys must be present
    assert "count" in by_lang["zh"]
    assert "mean_attr_quality_score" in by_lang["zh"]
    assert "count" in by_lang["en"]
    assert "mean_attr_quality_score" in by_lang["en"]

    # zh: count=2, mean=(0.8+0.9)/2=0.85
    assert by_lang["zh"]["count"] == 2
    assert abs(by_lang["zh"]["mean_attr_quality_score"] - 0.85) < 1e-9
    # en: count=1, mean=0.7
    assert by_lang["en"]["count"] == 1
    assert abs(by_lang["en"]["mean_attr_quality_score"] - 0.7) < 1e-9


def test_aggregate_no_token_returns_401(client: TestClient) -> None:
    """Missing Authorization header → 401.

    Does NOT override get_current_user; relies on oauth2_scheme auto_error=True.
    """
    resp = client.post(
        "/api/chunks/aggregate",
        json={"group_by": "attr_lang_code", "metrics": ["count"]},
    )
    assert resp.status_code == 401


def test_aggregate_invalid_metric_returns_400(client: TestClient) -> None:
    """metrics=['badop:col'] → 400 with 'Lance query error' in detail.

    Metric validation runs before Lance I/O, so no mock table is needed.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _aggregate(
            client,
            {"group_by": "attr_lang_code", "metrics": ["badop:col"]},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]


def test_aggregate_filter_too_long_returns_422(client: TestClient) -> None:
    """filter of 1001 chars → 422 (Pydantic max_length=1000 constraint)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _aggregate(
            client,
            {
                "filter": "x" * 1001,
                "group_by": "attr_lang_code",
                "metrics": ["count"],
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 422


def test_aggregate_lance_error_returns_400(client: TestClient) -> None:
    """get_or_create_chunks_table raises Exception → HTTP 400 with 'Lance query error'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            side_effect=Exception("LanceDB connection failed"),
        ):
            resp = _aggregate(
                client,
                {"group_by": "attr_lang_code", "metrics": ["count"]},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]
