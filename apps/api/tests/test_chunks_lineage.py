"""Tests for GET /api/chunks/{chunk_id}/lineage — S036-F-036.

15 test cases covering both F-036 verification criteria (V1, V2) and all
edge-case branches from agreed.md §5 and §6.

All tests use FastAPI's TestClient with the conftest.py autouse fixtures:
  - _patch_engine_begin: mocks engine.begin() so TestClient(app) doesn't need Postgres.
  - _patch_httpx_no_ssl: works around broken OpenSSL on this host.

Mock pattern (Lance):
  patch("dataplat_api.routers.chunks.get_or_create_chunks_table")

Mock pattern (Postgres session):
  app.dependency_overrides[get_session] = <async generator returning AsyncMock>
  session.execute is AsyncMock; synchronous result proxy methods (.scalar_one_or_none)
  are plain MagicMock return values.

Auth override:
  app.dependency_overrides[get_current_user] = _override_current_user
  for tests that need a valid user.  The 401 test sends no Authorization header
  and does NOT override the dependency, letting the real oauth2_scheme reject it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import DocumentVariant, Source, User
from dataplat_api.db.session import get_session
from dataplat_api.main import app

# ── Shared mock user ──────────────────────────────────────────────────────────

_MOCK_USER = User(id=1, email="tester@example.com", hashed_password="$2b$12$hash")


async def _override_current_user() -> User:
    return _MOCK_USER


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


# ── Lance mock builders ───────────────────────────────────────────────────────


def _make_mock_table_with_sequence(
    rows_sequence: list[list[dict[str, Any]]],
) -> MagicMock:
    """Build a mock Lance table that returns different rows on successive calls.

    Each element of rows_sequence is the list of rows returned by one call to
    table.search().where(...).select(...).limit(...).to_arrow().to_pylist().

    The query builder chains .where(), .select(), .limit() — all return the
    query builder itself so chaining works correctly.
    """
    mock_table = MagicMock()

    call_count = {"n": 0}

    def _to_arrow_side_effect():
        idx = call_count["n"]
        call_count["n"] += 1
        rows = rows_sequence[idx] if idx < len(rows_sequence) else []
        arrow_result = MagicMock()
        arrow_result.to_pylist.return_value = rows
        return arrow_result

    qb = MagicMock()
    qb.where.return_value = qb
    qb.select.return_value = qb
    qb.limit.return_value = qb
    qb.to_arrow.side_effect = _to_arrow_side_effect

    mock_table.search.return_value = qb
    return mock_table


def _make_mock_table_single(rows: list[dict[str, Any]]) -> MagicMock:
    """Convenience wrapper for a single Lance call."""
    return _make_mock_table_with_sequence([rows])


# ── ORM stub builders ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)


def _make_source_stub(source_id: int = 42) -> MagicMock:
    src = MagicMock(spec=Source)
    src.id = source_id
    src.collection_id = 1
    src.kind = "pdf"
    src.original_name = "test.pdf"
    src.storage_uri = f"s3://sources/{source_id}/original.pdf"
    src.sha256 = "abc" * 21 + "a"
    src.size = 1024
    src.mime_type = "application/pdf"
    src.dagster_partition_key = f"src_{source_id}"
    src.uploaded_at = _NOW
    return src


def _make_variant_stub(variant_id: int = 10, source_id: int = 42) -> MagicMock:
    dv = MagicMock(spec=DocumentVariant)
    dv.id = variant_id
    dv.extractor_name = "mineru"
    dv.extractor_version = "0.1.0"
    dv.config_hash = "dead" * 16
    dv.storage_prefix = f"s3://documents/{source_id}/extract_mineru/"
    dv.page_count = 5
    dv.image_count = 2
    dv.is_canonical = True
    dv.materialized_at = _NOW
    dv.dagster_run_id = "run-abc-123"
    return dv


# ── Full chunk row helper ─────────────────────────────────────────────────────


def _make_chunk_row(
    chunk_id: str,
    source_id: int = 42,
    augmented_from: str | None = None,
    augmenter_id: str | None = None,
    augmenter_config_hash: str | None = None,
    producer_asset: str = "chunks",
    producer_version: str = "1.0.0",
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "source_collection_id": 1,
        "producer_asset": producer_asset,
        "producer_version": producer_version,
        "text": f"Sample text for {chunk_id}",
        "token_count": 6,
        "docling_refs": '{"ref": "page-1"}',
        "source_refs": '{"page": 1}',
        "augmented_from": augmented_from,
        "augmenter_id": augmenter_id,
        "augmenter_config_hash": augmenter_config_hash,
        "attr_quality_score": 0.85,
        "attr_quality_provider": "length_heuristic",
        "attr_lang_code": "en",
        "attr_lang_confidence": 0.95,
        "attr_minhash_signature": [1, 2, 3],
        "attr_minhash_cluster_id": 1,
        "attr_minhash_is_head": True,
        "attr_pii_has_pii": False,
        "attr_pii_categories": [],
        "attr_embed_vector": [0.1, 0.2, 0.3],
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
    }


# ── Postgres session mock helpers ─────────────────────────────────────────────


def _make_session_dep(
    source_stub: MagicMock | None,
    dv_stub: MagicMock | None,
) -> Any:
    """Return a get_session override for the happy 2-query Postgres path.

    The handler calls session.execute() twice on the happy path:
      1st — select(Source)          → scalar_one_or_none() → source_stub or None
      2nd — select(DocumentVariant) → scalar_one_or_none() → dv_stub or None
    """
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = source_stub

    dv_result = MagicMock()
    dv_result.scalar_one_or_none.return_value = dv_stub

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result, dv_result])
        yield session

    return _override


def _make_session_dep_source_missing() -> Any:
    """Return a get_session override for the 404-source path (1 execute call)."""
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = None

    async def _override() -> AsyncGenerator[AsyncMock, None]:  # type: ignore[misc]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[source_result])
        yield session

    return _override


# ── Helper ────────────────────────────────────────────────────────────────────


def _get_lineage(client: TestClient, chunk_id: str) -> Any:
    return client.get(
        f"/api/chunks/{chunk_id}/lineage",
        headers={"Authorization": "Bearer faketoken"},
    )


# ── Test 1: non-augmented chunk → lineage_chain length == 1 (V1 + V2-non-augmented) ──


def test_lineage_200_non_augmented_chain_length_1(client: TestClient) -> None:
    """Non-augmented chunk (augmented_from=None) → 200, lineage_chain length == 1,
    chunk_id in chain, augmented_from of only entry is null.  Satisfies V1 + V2-non-augmented.
    """
    row = _make_chunk_row("chunk-orig-001", source_id=42, augmented_from=None)
    mock_table = _make_mock_table_single([row])
    src = _make_source_stub(source_id=42)
    dv = _make_variant_stub(variant_id=10, source_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-orig-001")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    chain = body["lineage_chain"]
    assert len(chain) == 1, f"Expected 1-entry chain, got {len(chain)}"
    assert chain[0]["chunk_id"] == "chunk-orig-001"
    assert chain[0]["augmented_from"] is None


# ── Test 2: augmented chunk, 3-deep chain (V2-augmented) ─────────────────────


def test_lineage_200_augmented_chain_length_3(client: TestClient) -> None:
    """Chain C→B→A (A is root) → 200, lineage_chain == [C, B, A] (tip-to-root),
    lineage_chain[-1].augmented_from is null.  Satisfies V2-augmented.
    """
    # C (tip) → B → A (root)
    row_c = _make_chunk_row(
        "chunk-C",
        augmented_from="chunk-B",
        augmenter_id="aug-v1",
        augmenter_config_hash="cfghash1",
    )
    row_b = _make_chunk_row(
        "chunk-B",
        augmented_from="chunk-A",
        augmenter_id="aug-v1",
        augmenter_config_hash="cfghash0",
    )
    row_a = _make_chunk_row("chunk-A", augmented_from=None)

    mock_table = _make_mock_table_with_sequence([[row_c], [row_b], [row_a]])
    src = _make_source_stub(source_id=42)
    dv = _make_variant_stub(variant_id=10, source_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-C")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    chain = body["lineage_chain"]
    assert len(chain) == 3, f"Expected 3-entry chain, got {len(chain)}"
    assert chain[0]["chunk_id"] == "chunk-C"
    assert chain[1]["chunk_id"] == "chunk-B"
    assert chain[2]["chunk_id"] == "chunk-A"
    assert chain[0]["augmented_from"] == "chunk-B"
    assert chain[1]["augmented_from"] == "chunk-A"
    assert chain[2]["augmented_from"] is None


# ── Test 3: required top-level keys present (V1 shape) ───────────────────────


def test_lineage_200_response_has_required_top_level_keys(client: TestClient) -> None:
    """Response body contains keys chunk, source, document_variant, lineage_chain.
    Satisfies V1.
    """
    row = _make_chunk_row("chunk-topkeys", augmented_from=None)
    mock_table = _make_mock_table_single([row])
    src = _make_source_stub(source_id=42)
    dv = _make_variant_stub(variant_id=10, source_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-topkeys")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    for key in ("chunk", "source", "document_variant", "lineage_chain"):
        assert key in body, f"Missing top-level key: {key!r}"


# ── Test 4: source.id and document_variant.id present when both exist ─────────


def test_lineage_200_source_and_dv_fields_present(client: TestClient) -> None:
    """source.id and document_variant.id present in body when source + canonical variant exist."""
    row = _make_chunk_row("chunk-srctest", source_id=99, augmented_from=None)
    mock_table = _make_mock_table_single([row])
    src = _make_source_stub(source_id=99)
    dv = _make_variant_stub(variant_id=55, source_id=99)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-srctest")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["id"] == 99
    assert body["document_variant"]["id"] == 55


# ── Test 5: chunk_id not found in Lance → 404 ────────────────────────────────


def test_lineage_404_chunk_not_found(client: TestClient) -> None:
    """Lance returns empty list for requested chunk_id → 404."""
    mock_table = _make_mock_table_single([])  # no matching row

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "nonexistent-chunk")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ── Test 6: no Authorization header → 401 ────────────────────────────────────


def test_lineage_401_no_token(client: TestClient) -> None:
    """Missing Authorization header → 401.

    Does NOT override get_current_user — the real oauth2_scheme raises 401.
    """
    resp = client.get("/api/chunks/some-chunk-id/lineage")
    assert resp.status_code == 401


# ── Test 7: Lance raises Exception → HTTP 400 ─────────────────────────────────


def test_lineage_400_lance_error(client: TestClient) -> None:
    """get_or_create_chunks_table raises Exception → LanceQueryError → HTTP 400
    with 'Lance query error' in detail.
    """
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            side_effect=Exception("DataFusion internal error"),
        ):
            resp = _get_lineage(client, "any-chunk")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 400
    assert "Lance query error" in resp.json()["detail"]


# ── Test 8: cycle detection → HTTP 500 ───────────────────────────────────────


def test_lineage_500_cycle_detected(client: TestClient) -> None:
    """A→B, B→A (cycle) → HTTP 500 with 'Cycle detected' in detail.

    Call sequence:
      call 0 (initial fetch): returns row_a  (chunk-A, augmented_from=chunk-B)
      call 1 (fetch parent chunk-B): returns row_b  (chunk-B, augmented_from=chunk-A)
      call 2 (fetch parent chunk-A): returns row_a  (chunk-A already in seen_ids → cycle)
    The 3rd call MUST return a real row so the handler processes it as current_row
    in the next loop iteration and then fires cycle detection.
    """
    row_a = _make_chunk_row("chunk-A", augmented_from="chunk-B")
    row_b = _make_chunk_row("chunk-B", augmented_from="chunk-A")

    # Three calls: initial-A, parent-B, parent-A-again (triggers cycle on next iteration)
    mock_table = _make_mock_table_with_sequence([[row_a], [row_b], [row_a]])

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-A")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "Cycle detected" in detail


# ── Test 9a: exactly 32-entry chain succeeds with HTTP 200 ────────────────────


def test_lineage_200_depth_boundary_32_chain_succeeds(client: TestClient) -> None:
    """Chain of exactly 32 distinct chunks (entries 0–30 have non-null augmented_from,
    entry 31 is the root with augmented_from=None) → HTTP 200, lineage_chain length == 32.

    Verifies that a max-depth valid chain is NOT falsely rejected by the depth cap.
    """
    # Build 32 distinct chunk rows: chunk-0 → chunk-1 → ... → chunk-31 (root)
    chunk_ids = [f"chain-chunk-{i:03d}" for i in range(32)]
    rows_sequence = []
    for i, cid in enumerate(chunk_ids):
        parent = chunk_ids[i + 1] if i < 31 else None
        row = _make_chunk_row(cid, augmented_from=parent)
        rows_sequence.append([row])

    mock_table = _make_mock_table_with_sequence(rows_sequence)
    src = _make_source_stub(source_id=42)
    dv = _make_variant_stub(variant_id=10, source_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, chunk_ids[0])
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200, (
        f"Expected 200 for 32-entry chain, got {resp.status_code}: {resp.json()}"
    )
    body = resp.json()
    chain = body["lineage_chain"]
    assert len(chain) == 32, f"Expected 32-entry chain, got {len(chain)}"
    assert chain[0]["chunk_id"] == chunk_ids[0]
    assert chain[-1]["chunk_id"] == chunk_ids[31]
    assert chain[-1]["augmented_from"] is None


# ── Test 9b: depth cap exceeded (33+ entries, no null augmented_from within 32) ──


def test_lineage_500_depth_cap_exceeded(client: TestClient) -> None:
    """Mock returns a chain of 33 distinct chunks (no cycle, no null augmented_from
    within the first 32 iterations) → HTTP 500 with depth-cap-exceeded message.

    Verifies the for...else branch fires.

    NIT 2: the 33rd call returns a REAL chunk dict (not None) to ensure the handler
    hits the depth cap branch rather than the broken-parent branch.  The test asserts
    on the depth-cap-exceeded detail string specifically so a regression that flips
    into the wrong 500 branch fails this test.
    """
    # Build 33 distinct chunk rows, all with non-null augmented_from
    chunk_ids = [f"deep-chunk-{i:03d}" for i in range(33)]
    rows_sequence = []
    for i, cid in enumerate(chunk_ids):
        # All 33 have a non-null augmented_from — no root in the first 32 iterations
        parent = chunk_ids[i + 1] if i < 32 else "deep-chunk-beyond"
        row = _make_chunk_row(cid, augmented_from=parent)
        rows_sequence.append([row])

    # The 33rd call (index 32, for the parent of chunk-31) returns a real chunk row.
    # This ensures the depth cap fires (not the broken-parent branch).
    rows_sequence.append([_make_chunk_row("deep-chunk-beyond", augmented_from=None)])

    mock_table = _make_mock_table_with_sequence(rows_sequence)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, chunk_ids[0])
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    # Assert on depth-cap-exceeded string specifically (NIT 2):
    # if this regresses into the broken-parent branch, the detail will say
    # "Broken augmented_from chain" instead — which would fail this assertion.
    assert "depth cap" in detail.lower() or "exceeded" in detail.lower(), (
        f"Expected depth-cap-exceeded detail, got: {detail!r}"
    )


# ── Test 10: source not found in Postgres → 404 ──────────────────────────────


def test_lineage_404_source_not_found_in_postgres(client: TestClient) -> None:
    """Lance returns valid chunk; Postgres Source query returns None → 404
    with 'not found' in detail.
    """
    row = _make_chunk_row("chunk-nosrc", source_id=9999, augmented_from=None)
    mock_table = _make_mock_table_single([row])

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep_source_missing()
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-nosrc")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ── Test 11: no canonical document_variant → document_variant is null ─────────


def test_lineage_200_null_document_variant_when_no_canonical(
    client: TestClient,
) -> None:
    """Source exists in Postgres but no canonical variant → 200 with
    document_variant: null.  Not an error (§5.7).
    """
    row = _make_chunk_row("chunk-nodv", source_id=42, augmented_from=None)
    mock_table = _make_mock_table_single([row])
    src = _make_source_stub(source_id=42)

    # dv_stub=None simulates no canonical variant found
    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-nodv")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["document_variant"] is None


# ── Test 12: single-quote in chunk_id is escaped ─────────────────────────────


def test_lineage_escapes_single_quote_in_chunk_id(client: TestClient) -> None:
    """chunk_id=\"it's\" → .where() called with \"chunk_id = 'it''s'\" (SQL-safe escape).

    Verifies the DataFusion predicate injection guard inside _fetch_chunk.
    """
    row = _make_chunk_row("it's", augmented_from=None)
    mock_table = _make_mock_table_single([row])
    qb = mock_table.search.return_value  # the query builder mock
    src = _make_source_stub(source_id=42)
    dv = _make_variant_stub(variant_id=10, source_id=42)

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(src, dv)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "it's")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 200
    # The .where() call must use doubled single-quote for SQL safety.
    qb.where.assert_called_once_with("chunk_id = 'it''s'")


# ── Test 13: broken augmented_from reference → HTTP 500 ──────────────────────


def test_lineage_500_broken_augmented_from_chain(client: TestClient) -> None:
    """Chunk A has augmented_from='B'; second _fetch_chunk call (for B) returns
    None → HTTP 500 with 'Broken augmented_from chain' in detail.  Covers §5.9.
    """
    row_a = _make_chunk_row("chunk-brokenA", augmented_from="chunk-missing-B")
    # First call returns A, second call returns [] (B doesn't exist)
    mock_table = _make_mock_table_with_sequence([[row_a], []])

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-brokenA")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "Broken augmented_from chain" in detail, (
        f"Expected 'Broken augmented_from chain' in detail, got: {detail!r}"
    )


# ── Test 14: null source_id on root chunk → HTTP 500 ─────────────────────────


def test_lineage_500_null_source_id_on_root_chunk(client: TestClient) -> None:
    """Root chunk has source_id=None (augmented_from=None) → HTTP 500 with
    'null source_id' in detail.  Covers §5.5 data-integrity sentinel.
    """
    # Build a root chunk with source_id=None
    row = _make_chunk_row("chunk-nullsrc", augmented_from=None)
    row["source_id"] = None  # override to simulate missing source_id

    mock_table = _make_mock_table_single([row])

    app.dependency_overrides[get_current_user] = _override_current_user
    app.dependency_overrides[get_session] = _make_session_dep(None, None)
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            resp = _get_lineage(client, "chunk-nullsrc")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "null source_id" in detail, (
        f"Expected 'null source_id' in detail, got: {detail!r}"
    )
