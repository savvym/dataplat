"""Tests for POST /api/chunks/query — S032-F-032.

12 test cases verifying the chunk query endpoint. All tests use FastAPI's
TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern: patch("dataplat_api.routers.chunks.get_or_create_chunks_table")
so that no live Lance/S3 is required.

Auth override: app.dependency_overrides[get_current_user] = _override_current_user
for tests that need a valid user. The 401 test sends no Authorization header and
does NOT override the dependency, letting the real oauth2_scheme reject it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="test@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Mock Lance table builder ──────────────────────────────────────────────────


def _make_mock_table(rows: list[dict[str, Any]], total: int) -> MagicMock:
    """Build a mock Lance table for unit tests (no live Lance/S3 required).

    The mock supports the full query-builder chain used in the router:
        table.search() → qb
        qb.where(...)  → qb  (chained)
        qb.select(...) → qb  (chained)
        qb.limit(...)  → qb  (chained)
        qb.offset(...) → qb  (chained)
        qb.to_arrow()  → arrow_result
        arrow_result.to_pylist() → rows
    """
    mock_table = MagicMock()
    mock_table.count_rows.return_value = total

    qb = MagicMock()  # query builder — every chain method returns qb itself
    qb.where.return_value = qb
    qb.select.return_value = qb
    qb.limit.return_value = qb
    qb.offset.return_value = qb

    arrow_result = MagicMock()
    arrow_result.to_pylist.return_value = rows
    qb.to_arrow.return_value = arrow_result

    mock_table.search.return_value = qb
    return mock_table


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────


def _query(client: TestClient, body: dict[str, Any]) -> Any:
    """POST /api/chunks/query with a Bearer auth header."""
    return client.post(
        "/api/chunks/query",
        json=body,
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_query_by_source_id(client: TestClient) -> None:
    """V1: filter 'source_id = 5', limit=10; 2 rows returned, total=2."""
    rows = [{"chunk_id": "c1", "source_id": 5}, {"chunk_id": "c2", "source_id": 5}]
    mock_table = _make_mock_table(rows, total=2)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {"filter": "source_id = 5", "limit": 10})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["chunk_id"] == "c1"
    assert body["items"][1]["chunk_id"] == "c2"


def test_query_limit_applied(client: TestClient) -> None:
    """V1 pagination: 10 rows returned but total=50 (total > limit)."""
    rows = [{"chunk_id": f"c{i}"} for i in range(10)]
    mock_table = _make_mock_table(rows, total=50)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {"filter": "source_id = 1", "limit": 10, "offset": 0})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 50
    assert len(body["items"]) == 10


def test_query_by_quality_score(client: TestClient) -> None:
    """V2: filter 'attr_quality_score > 0.8'; all returned scores > 0.8."""
    rows = [
        {"chunk_id": "c1", "attr_quality_score": 0.9},
        {"chunk_id": "c2", "attr_quality_score": 0.95},
    ]
    mock_table = _make_mock_table(rows, total=2)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {"filter": "attr_quality_score > 0.8"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["attr_quality_score"] > 0.8


def test_query_no_matches(client: TestClient) -> None:
    """V3: filter produces no matches → items=[], total=0."""
    mock_table = _make_mock_table([], total=0)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {"filter": "chunk_id = '__no_match__'"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_query_no_filter_returns_all(client: TestClient) -> None:
    """No filter supplied; mock returns 5 rows, count=5.

    Also verifies M1 fix: count_rows is called unconditionally with filter=None.
    """
    rows = [{"chunk_id": f"chunk-{i}"} for i in range(5)]
    mock_table = _make_mock_table(rows, total=5)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    # M1 fix verification: count_rows called unconditionally with filter=None
    mock_table.count_rows.assert_called_once_with(filter=None)


def test_query_with_columns_projection(client: TestClient) -> None:
    """columns=["chunk_id", "text"]; .select() called with correct argument."""
    rows = [{"chunk_id": "c1", "text": "hello"}]
    mock_table = _make_mock_table(rows, total=1)
    qb = mock_table.search.return_value  # the query builder mock
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {"columns": ["chunk_id", "text"]})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    # Verify .select() was called with the projected columns list
    qb.select.assert_called_once_with(["chunk_id", "text"])


def test_query_no_token_returns_401(client: TestClient) -> None:
    """Missing Authorization header → 401.

    Does NOT override get_current_user; relies on oauth2_scheme auto_error=True.
    """
    resp = client.post("/api/chunks/query", json={})
    assert resp.status_code == 401


def test_query_invalid_filter_too_long(client: TestClient) -> None:
    """filter with 1001 chars → 422 (Pydantic max_length=1000 constraint)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _query(client, {"filter": "x" * 1001})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 422


def test_query_invalid_limit_zero_returns_422(client: TestClient) -> None:
    """limit=0 → 422 (ge=1 constraint)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _query(client, {"limit": 0})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 422


def test_query_invalid_offset_negative_returns_422(client: TestClient) -> None:
    """offset=-1 → 422 (ge=0 constraint)."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        resp = _query(client, {"offset": -1})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 422


def test_query_lance_error_returns_400(client: TestClient) -> None:
    """Lance raises Exception inside _execute() → HTTP 400 with 'Lance query error'."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            side_effect=Exception("DataFusion parse error: unexpected token"),
        ):
            resp = _query(client, {"filter": "invalid!!filter"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]


def test_query_response_shape(client: TestClient) -> None:
    """Response has 'items' and 'total' keys; items contain 'chunk_id' field."""
    rows = [{"chunk_id": "shape-check-001"}]
    mock_table = _make_mock_table(rows, total=1)
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _query(client, {})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1
    assert "chunk_id" in body["items"][0]
    assert body["items"][0]["chunk_id"] == "shape-check-001"
