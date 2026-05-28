"""Tests for GET /api/chunks/{chunk_id} — S035-F-035.

6 test cases covering both verification criteria plus edge cases. All tests
use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern: patch("dataplat_api.routers.chunks.get_or_create_chunks_table")
so that no live Lance/S3 is required.

Auth override: app.dependency_overrides[get_current_user] = _override_current_user
for tests that need a valid user. The 401 test sends no Authorization header and
does NOT override the dependency, letting the real oauth2_scheme reject it.

Note: _make_mock_table() is defined module-locally; do NOT import it from other
test files.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="tester@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Module-local mock Lance table builder ─────────────────────────────────────


def _make_mock_table(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a mock Lance table for unit tests (no live Lance/S3 required).

    Supports the full query-builder chain used by get_chunk_by_id:
        table.search()           → qb
        qb.where(...)            → qb  (chained)
        qb.limit(...)            → qb  (chained)
        qb.to_arrow()            → arrow_result
        arrow_result.to_pylist() → rows
    """
    mock_table = MagicMock()

    qb = MagicMock()  # query builder — every chain method returns qb itself
    qb.where.return_value = qb
    qb.limit.return_value = qb

    arrow_result = MagicMock()
    arrow_result.to_pylist.return_value = rows
    qb.to_arrow.return_value = arrow_result

    mock_table.search.return_value = qb
    return mock_table


# ── All 24 ChunkRead fields ───────────────────────────────────────────────────

_FULL_ROW: dict[str, Any] = {
    # Identifiers
    "chunk_id": "chunk-full-001",
    "source_id": 42,
    "source_collection_id": 7,
    "producer_asset": "my_processor",
    "producer_version": "1.0.0",
    # Content
    "text": "Hello world sample text for testing.",
    "token_count": 6,
    "docling_refs": '{"ref": "page-1"}',
    "source_refs": '{"page": 1}',
    # Provenance
    "augmented_from": None,
    "augmenter_id": None,
    "augmenter_config_hash": None,
    # Attribute columns
    "attr_quality_score": 0.95,
    "attr_quality_provider": "quality_scorer_v1",
    "attr_lang_code": "en",
    "attr_lang_confidence": 0.99,
    "attr_minhash_signature": [1, 2, 3, 4],
    "attr_minhash_cluster_id": 101,
    "attr_minhash_is_head": True,
    "attr_pii_has_pii": False,
    "attr_pii_categories": [],
    "attr_embed_vector": [0.1, 0.2, 0.3],
    # Timestamps
    "created_at": datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    "updated_at": datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
}

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_get_chunk_200_all_fields(client: TestClient) -> None:
    """V-criterion 1: valid chunk_id → 200 with all 24 ChunkRead fields present."""
    mock_table = _make_mock_table([_FULL_ROW])
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = client.get(
                "/api/chunks/chunk-full-001",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    body = resp.json()

    # All 24 ChunkRead field names must be present in the response body.
    expected_fields = [
        "chunk_id",
        "source_id",
        "source_collection_id",
        "producer_asset",
        "producer_version",
        "text",
        "token_count",
        "docling_refs",
        "source_refs",
        "augmented_from",
        "augmenter_id",
        "augmenter_config_hash",
        "attr_quality_score",
        "attr_quality_provider",
        "attr_lang_code",
        "attr_lang_confidence",
        "attr_minhash_signature",
        "attr_minhash_cluster_id",
        "attr_minhash_is_head",
        "attr_pii_has_pii",
        "attr_pii_categories",
        "attr_embed_vector",
        "created_at",
        "updated_at",
    ]
    for field in expected_fields:
        assert field in body, f"Missing field in response: {field!r}"

    # Spot-check a few values.
    assert body["chunk_id"] == "chunk-full-001"
    assert body["source_id"] == 42
    assert body["attr_quality_score"] == pytest.approx(0.95)
    assert body["attr_lang_code"] == "en"
    assert body["attr_minhash_signature"] == [1, 2, 3, 4]
    assert body["attr_pii_has_pii"] is False


def test_get_chunk_404_not_found(client: TestClient) -> None:
    """V-criterion 2: chunk_id not in Lance → 404 with 'not found' in detail."""
    mock_table = _make_mock_table([])  # empty list → no match
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = client.get(
                "/api/chunks/nonexistent-id",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_get_chunk_401_no_token(client: TestClient) -> None:
    """Missing Authorization header → 401.

    Does NOT override get_current_user; relies on oauth2_scheme auto_error=True.
    """
    resp = client.get("/api/chunks/some-chunk-id")
    assert resp.status_code == 401


def test_get_chunk_lance_error_returns_400(client: TestClient) -> None:
    """get_or_create_chunks_table raises Exception → wrapped in LanceQueryError → HTTP 400."""
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            side_effect=Exception("DataFusion internal error: table not found"),
        ):
            resp = client.get(
                "/api/chunks/any-chunk",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]


def test_get_chunk_where_called_with_escaped_id(client: TestClient) -> None:
    """chunk_id='abc-123' → .where() called with \"chunk_id = 'abc-123'\" exactly."""
    mock_table = _make_mock_table([{"chunk_id": "abc-123"}])
    qb = mock_table.search.return_value  # the query builder mock
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = client.get(
                "/api/chunks/abc-123",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    qb.where.assert_called_once_with("chunk_id = 'abc-123'")


def test_get_chunk_where_escapes_single_quote(client: TestClient) -> None:
    """F2 fix: chunk_id=\"it's\" → .where() called with \"chunk_id = 'it''s'\" (SQL escaping)."""
    mock_table = _make_mock_table([{"chunk_id": "it's"}])
    qb = mock_table.search.return_value  # the query builder mock
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = client.get(
                "/api/chunks/it's",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    qb.where.assert_called_once_with("chunk_id = 'it''s'")
