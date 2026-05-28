"""Unit tests for LanceChunksIOManager column mode (F-031).

Tests exercise the column-mode path added in F-031:
  - Column mode is triggered when producer_asset != "chunks".
  - merge_insert("chunk_id") is called; table.delete() is NOT called.
  - Incoming tagger columns are forwarded to execute() as a partial pa.Table.
  - Empty obj -> early return with mode="column_skipped", no Lance calls.
  - Rows missing chunk_id key -> warning logged, row skipped.
  - Row mode still works for producer_asset=="chunks" (delete + add).

All external I/O (lancedb.connect, CHUNKS_SCHEMA, build_lance_storage_options,
os.environ) is mocked — no live Lance or MinIO needed.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_lance_io_manager_column_mode.py -q
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_merge_chain() -> MagicMock:
    """Build a mock chain: merge_insert → when_matched_update_all → execute."""
    mock_merge = MagicMock()
    mock_merge.when_matched_update_all.return_value = mock_merge
    mock_merge.execute.return_value = None
    return mock_merge


def _make_table_mock(merge_chain: MagicMock) -> MagicMock:
    """Build a mock lancedb Table."""
    mock_table = MagicMock()
    mock_table.merge_insert.return_value = merge_chain
    return mock_table


def _make_db_mock(mock_table: MagicMock) -> MagicMock:
    """Build a mock lancedb DB that returns mock_table on create_table()."""
    mock_db = MagicMock()
    mock_db.create_table.return_value = mock_table
    return mock_db


def _make_context(partition_key: str, asset_path: list[str]) -> MagicMock:
    """Build a minimal mock OutputContext."""
    ctx = MagicMock()
    ctx.has_partition_key = True
    ctx.partition_key = partition_key
    ctx.asset_key.path = asset_path
    return ctx


_ENV = {
    "MINIO_ROOT_USER": "testuser",
    "MINIO_ROOT_PASSWORD": "testpass",
    "MINIO_ENDPOINT": "minio:9000",
    "MINIO_LANCE_BUCKET": "lance",
}


# ---------------------------------------------------------------------------
# Test 1: V-criterion #2 — column mode calls merge_insert, not delete
# ---------------------------------------------------------------------------


def test_column_mode_calls_merge_insert_not_delete() -> None:
    """Column mode must call merge_insert('chunk_id'); must NOT call table.delete().

    V-criterion #2: merge_insert("chunk_id") present, no row deletion.
    """
    merge_chain = _make_merge_chain()
    mock_table = _make_table_mock(merge_chain)
    mock_db = _make_db_mock(mock_table)
    mock_ctx = _make_context("src_1", ["attr_lang"])

    incoming = [
        {"chunk_id": "src_1_0", "attr_lang_code": "en", "attr_lang_confidence": 0.99}
    ]

    with patch("lancedb.connect", return_value=mock_db), \
         patch.dict("os.environ", _ENV), \
         patch("dagster_platform.lance_io_manager.build_lance_storage_options",
               return_value={}):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, incoming)

    # V-criterion #2: merge_insert("chunk_id") was called
    mock_table.merge_insert.assert_called_once_with("chunk_id")
    merge_chain.when_matched_update_all.assert_called_once()
    merge_chain.execute.assert_called_once()

    # Row mode delete must NOT be called in column mode
    mock_table.delete.assert_not_called()

    # Metadata: mode="column", merge_key="chunk_id"
    mock_ctx.add_output_metadata.assert_called_once()
    meta = mock_ctx.add_output_metadata.call_args[0][0]
    assert meta["mode"] == "column"
    assert meta["merge_key"] == "chunk_id"
    assert meta["row_count"] == 1


# ---------------------------------------------------------------------------
# Test 2: column mode passes only incoming columns to execute()
# ---------------------------------------------------------------------------


def test_column_mode_passes_partial_schema_to_execute() -> None:
    """The pa.Table passed to execute() must contain only the incoming column keys.

    D3a: schema is inferred from the incoming dicts — only chunk_id +
    tagger columns are present in the pa.Table, not the full CHUNKS_SCHEMA.
    lancedb is trusted (D2 probe) to preserve absent columns.
    """
    import pyarrow as pa

    captured: list[pa.Table] = []

    merge_chain = _make_merge_chain()
    # Capture what was passed to execute()
    def capture_execute(pa_tbl: pa.Table) -> None:
        captured.append(pa_tbl)
    merge_chain.execute.side_effect = capture_execute

    mock_table = _make_table_mock(merge_chain)
    mock_db = _make_db_mock(mock_table)
    mock_ctx = _make_context("src_2", ["attr_quality"])

    incoming = [
        {
            "chunk_id": "src_2_0",
            "attr_quality_score": 0.85,
            "attr_quality_provider": "mock",
        }
    ]

    with patch("lancedb.connect", return_value=mock_db), \
         patch.dict("os.environ", _ENV), \
         patch("dagster_platform.lance_io_manager.build_lance_storage_options",
               return_value={}):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, incoming)

    assert len(captured) == 1
    pa_tbl = captured[0]
    # Schema must contain exactly the keys of the incoming dicts
    assert set(pa_tbl.column_names) == {"chunk_id", "attr_quality_score",
                                         "attr_quality_provider"}
    # Values must match
    assert pa_tbl.column("chunk_id").to_pylist() == ["src_2_0"]
    assert pa_tbl.column("attr_quality_provider").to_pylist() == ["mock"]


# ---------------------------------------------------------------------------
# Test 3: empty obj -> early return with mode="column_skipped"
# ---------------------------------------------------------------------------


def test_column_mode_empty_list_early_return() -> None:
    """Empty obj must produce mode='column_skipped' and no Lance calls.

    D6 / F2 fix: producer_asset is derived BEFORE the empty-list guard, so the
    early-return can emit "column_skipped" (not "row_skipped") for tagger assets.
    """
    merge_chain = _make_merge_chain()
    mock_table = _make_table_mock(merge_chain)
    mock_db = _make_db_mock(mock_table)
    mock_ctx = _make_context("src_3", ["attr_minhash"])

    with patch("lancedb.connect", return_value=mock_db), \
         patch.dict("os.environ", _ENV), \
         patch("dagster_platform.lance_io_manager.build_lance_storage_options",
               return_value={}):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, [])

    # No Lance connection or write — early return before lancedb.connect
    mock_db.create_table.assert_not_called()
    mock_table.merge_insert.assert_not_called()
    mock_table.delete.assert_not_called()

    # Metadata: mode="column_skipped" (not "row_skipped")
    mock_ctx.add_output_metadata.assert_called_once()
    meta = mock_ctx.add_output_metadata.call_args[0][0]
    assert meta["mode"] == "column_skipped"
    assert meta["row_count"] == 0


# ---------------------------------------------------------------------------
# Test 4: row missing chunk_id key -> warning logged, row skipped
# ---------------------------------------------------------------------------


def test_column_mode_missing_chunk_id_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A row missing the 'chunk_id' key must be skipped with a warning.

    The valid row (with chunk_id) must still be processed; the invalid row
    (missing chunk_id) must be dropped with a logged warning.
    """
    import pyarrow as pa

    captured: list[pa.Table] = []

    merge_chain = _make_merge_chain()
    merge_chain.execute.side_effect = lambda pt: captured.append(pt)

    mock_table = _make_table_mock(merge_chain)
    mock_db = _make_db_mock(mock_table)
    mock_ctx = _make_context("src_4", ["attr_lang"])

    incoming = [
        # invalid: missing chunk_id
        {"attr_lang_code": "fr", "attr_lang_confidence": 0.75},
        # valid
        {"chunk_id": "src_4_0", "attr_lang_code": "en", "attr_lang_confidence": 0.99},
    ]

    with patch("lancedb.connect", return_value=mock_db), \
         patch.dict("os.environ", _ENV), \
         patch("dagster_platform.lance_io_manager.build_lance_storage_options",
               return_value={}), \
         caplog.at_level(logging.WARNING, logger="dagster_platform.lance_io_manager"):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, incoming)

    # Warning logged for missing chunk_id
    assert any("missing chunk_id" in msg for msg in caplog.messages), (
        f"expected 'missing chunk_id' warning; got: {caplog.messages}"
    )

    # Valid row was still processed
    mock_table.merge_insert.assert_called_once_with("chunk_id")
    assert len(captured) == 1
    assert captured[0].column("chunk_id").to_pylist() == ["src_4_0"]

    # Metadata row_count counts the incoming list (before filtering)
    meta = mock_ctx.add_output_metadata.call_args[0][0]
    assert meta["mode"] == "column"


# ---------------------------------------------------------------------------
# Test 5: row mode still uses delete + add for producer_asset="chunks"
# ---------------------------------------------------------------------------


def test_row_mode_still_uses_delete_add() -> None:
    """producer_asset='chunks' must use row mode: delete() then add(), no merge_insert."""
    merge_chain = _make_merge_chain()
    mock_table = _make_table_mock(merge_chain)
    mock_db = _make_db_mock(mock_table)
    mock_ctx = _make_context("src_5", ["chunks"])

    incoming = [
        {
            "chunk_id": "src_5_0",
            "source_id": 5,
            "collection_id": 1,
            "text": "hello world",
            "producer_asset": "chunks",
            "producer_version": "v1",
            "augmented_from": None,
            "augmenter_id": None,
            "augmenter_config_hash": None,
            "attr_quality_score": None,
            "attr_quality_provider": None,
            "attr_lang_code": None,
            "attr_lang_confidence": None,
            "attr_minhash_signature": None,
            "attr_minhash_cluster_id": None,
            "attr_minhash_is_head": None,
        }
    ]

    with patch("lancedb.connect", return_value=mock_db), \
         patch.dict("os.environ", _ENV), \
         patch("dagster_platform.lance_io_manager.build_lance_storage_options",
               return_value={}):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, incoming)

    # Row mode: delete was called, add was called
    mock_table.delete.assert_called_once()
    mock_table.add.assert_called_once_with(incoming)

    # Column mode must NOT be called
    mock_table.merge_insert.assert_not_called()

    # Metadata: mode="row"
    meta = mock_ctx.add_output_metadata.call_args[0][0]
    assert meta["mode"] == "row"
    assert "merge_key" not in meta
