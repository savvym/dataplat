"""lance_io_manager.py — Dagster IOManager for the Lance chunks table (F-026 / F-031).

Row mode (F-026): delete-before-insert idempotency for the Lance 'chunks' table.
Column mode (F-031): partial-column merge_insert for tagger assets (D3a).

Design decisions:
  D1  — Separate module from chunker.py to preserve chunker's no-Dagster guarantee.
  D2  — Uses lancedb API (not raw lance.dataset()) for consistency with F-025.
  D3  — No constructor arguments; all config from os.environ.
  D5  — has_partition_key guard BEFORE accessing partition_key (C1 fix).
  D6  — producer_asset from context.asset_key.path[-1].
  D7  — Row mode (producer_asset=="chunks"); column mode (all other assets, F-031).
  D8  — load_input() raises NotImplementedError.
  D11 — Empty list early-return in handle_output(), mode-aware (F-031 F2 fix).

F-031 column mode (D3a — direct partial merge, D2 probe exit 0):
  - lancedb 0.30.2 merge_insert("chunk_id").when_matched_update_all().execute(partial_pa_table)
    preserves columns absent from partial_pa_table (D2 probe confirmed exit 0).
  - No full-row read needed: incoming partial dicts are converted directly to pa.Table.
  - Schema inferred from keys of the first dict (chunk_id + tagger columns only).
  - Naturally concurrency-safe: each tagger payload contains only its own columns.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import lancedb
import pyarrow as pa
from dagster import InputContext, IOManager, OutputContext

from dagster_platform.chunker import CHUNKS_SCHEMA, build_lance_storage_options

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column-mode write helper (D3a — direct partial merge)
# ---------------------------------------------------------------------------


def _column_mode_write(
    table: Any,
    incoming_rows: list[dict[str, Any]],
    source_id: int,
) -> None:
    """D3a: convert partial dicts directly to pa.Table, call merge_insert.

    D2 probe confirmed that lancedb 0.30.2
    merge_insert("chunk_id").when_matched_update_all().execute(partial_pa_table)
    preserves columns absent from partial_pa_table.  No full-row read needed.

    Any row missing a "chunk_id" key is skipped with a warning (defensive guard
    against stale or malformed compute_*_scores() output).

    Args:
        table:        An open lancedb Table object.
        incoming_rows: Partial dicts from compute_*_scores() — each dict contains
                       chunk_id + the tagger-specific columns only.
        source_id:    Used only for log messages.
    """
    valid_rows: list[dict[str, Any]] = []
    for row in incoming_rows:
        if "chunk_id" not in row:
            logger.warning(
                "_column_mode_write: row missing chunk_id key, skipping "
                "(source_id=%d row=%r)",
                source_id,
                row,
            )
        else:
            valid_rows.append(row)

    if not valid_rows:
        logger.info(
            "_column_mode_write: no valid rows after chunk_id filter for source_id=%d",
            source_id,
        )
        return

    # D3a: schema inferred from keys of the first dict — only incoming columns.
    pa_table = pa.Table.from_pylist(valid_rows)
    table.merge_insert("chunk_id").when_matched_update_all().execute(pa_table)
    logger.info(
        "_column_mode_write: merged %d partial row(s) for source_id=%d",
        len(valid_rows),
        source_id,
    )


# ---------------------------------------------------------------------------
# IOManager
# ---------------------------------------------------------------------------


class LanceChunksIOManager(IOManager):
    """IOManager that owns write idempotency for the Lance chunks table.

    Row mode (producer_asset == "chunks"):
        Delete existing rows for (source_id, producer_asset), then bulk-add new rows.

    Column mode (all other producer_asset values — tagger assets):
        Partial-column merge_insert via D3a (lancedb 0.30.2, D2 probe exit 0).
        Only the columns present in the incoming dicts are updated; all other
        columns (other taggers' values, lineage fields) are preserved unchanged.

    load_input() is not implemented (downstream processors read Lance directly).
    """

    def handle_output(self, context: OutputContext, obj: list[dict[str, Any]]) -> None:
        """Write rows to the Lance chunks table (row or column mode, idempotent).

        Order of operations (F-031 F2 fix — producer_asset derived BEFORE empty guard):
          1. D5 (C1 fix): Guard has_partition_key before accessing partition_key.
          2. D6 (F2 fix): Derive producer_asset from context.asset_key.path[-1].
                          MOVED ABOVE the empty-list guard so early-return is mode-aware.
          3. D11/F2: Empty-list early-return with mode-aware metadata:
                     "chunks" → "row_skipped"; anything else → "column_skipped".
          4. Connect to Lance (D2/D3).
          5. Dispatch:
             - producer_asset == "chunks" → row mode (delete + add).
             - else               → column mode (_column_mode_write, D3a).
          6. Record IO-level metadata via context.add_output_metadata().
        """
        # D5 (C1 fix): Check has_partition_key before accessing partition_key.
        # context.partition_key raises DagsterInvariantViolationError (not falsy)
        # when no partition exists, so this guard must come first.
        if not context.has_partition_key:
            raise ValueError(
                "LanceChunksIOManager requires a partitioned asset; "
                "context.has_partition_key is False"
            )
        source_id = int(context.partition_key.removeprefix("src_"))

        # D6 (F2 fix): Derive producer_asset BEFORE the empty-list guard so the
        # early-return below can emit mode-aware metadata ("row_skipped" vs
        # "column_skipped").
        producer_asset = context.asset_key.path[-1]

        # D11 / F2 fix: Empty list early-return — skip write entirely.
        # Mode-aware: chunks emits "row_skipped"; tagger assets emit "column_skipped".
        if not obj:
            mode_label = "row_skipped" if producer_asset == "chunks" else "column_skipped"
            context.log.info(
                "LanceChunksIOManager: obj is empty, skipping write (mode=%s)",
                mode_label,
            )
            context.add_output_metadata({"row_count": 0, "mode": mode_label})
            return

        # D3: All config from environment variables.
        lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
        db_uri = f"s3://{lance_bucket}/chunks"
        storage_options = build_lance_storage_options()

        db = lancedb.connect(db_uri, storage_options=storage_options)
        table = db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)

        if producer_asset == "chunks":
            # Row mode (D7): delete existing rows for (source_id, producer_asset),
            # then insert.  Lance delete() is a no-op when no matching rows exist.
            predicate = (
                f"source_id = {source_id} AND producer_asset = '{producer_asset}'"
            )
            table.delete(predicate)
            table.add(obj)
            context.log.info(
                "LanceChunksIOManager: wrote %d row(s) for source_id=%d "
                "producer_asset=%s",
                len(obj),
                source_id,
                producer_asset,
            )
            context.add_output_metadata(
                {
                    "row_count": len(obj),
                    "mode": "row",
                    "source_id": source_id,
                    "producer_asset": producer_asset,
                }
            )
        else:
            # Column mode (F-031, D3a): partial-column merge_insert.
            _column_mode_write(table, obj, source_id)
            context.log.info(
                "LanceChunksIOManager: column-mode merged %d row(s) for source_id=%d "
                "producer_asset=%s",
                len(obj),
                source_id,
                producer_asset,
            )
            context.add_output_metadata(
                {
                    "row_count": len(obj),
                    "mode": "column",
                    "merge_key": "chunk_id",
                }
            )

    def load_input(self, context: InputContext) -> None:
        """Not implemented — downstream processors connect to Lance directly.

        D8: load_input() is intentionally left unimplemented. No downstream
        Dagster asset currently reads chunks through this IO manager.
        Raises NotImplementedError with a descriptive message.
        """
        raise NotImplementedError(
            "LanceChunksIOManager.load_input() is not implemented. "
            "Downstream processors connect to Lance directly."
        )
