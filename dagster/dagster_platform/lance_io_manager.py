"""lance_io_manager.py — Dagster IOManager for the Lance chunks table (F-026).

Implements delete-before-insert idempotency for the Lance 'chunks' table.
Row mode only; column mode (tagger category dispatch) is deferred to F-028.

Design decisions (see contracts/S026-F-026/agreed.md):
  D1  — Separate module from chunker.py to preserve chunker's no-Dagster guarantee.
  D2  — Uses lancedb API (not raw lance.dataset()) for consistency with F-025.
  D3  — No constructor arguments; all config from os.environ.
  D5  — has_partition_key guard BEFORE accessing partition_key (C1 fix).
  D6  — producer_asset from context.asset_key.path[-1].
  D7  — Row mode only; column mode deferred to F-028 (TODO comment marks dispatch point).
  D8  — load_input() raises NotImplementedError.
  D11 — Empty list early-return in handle_output().
"""
from __future__ import annotations

import os
from typing import Any

import lancedb
from dagster import InputContext, IOManager, OutputContext

from dagster_platform.chunker import CHUNKS_SCHEMA, build_lance_storage_options


class LanceChunksIOManager(IOManager):
    """IOManager that owns write idempotency for the Lance chunks table.

    handle_output() accepts a list[dict] from a partitioned asset, deletes
    existing rows for (source_id, producer_asset), then inserts the new rows.
    load_input() is not implemented (downstream processors read Lance directly).
    """

    def handle_output(self, context: OutputContext, obj: list[dict[str, Any]]) -> None:
        """Write chunk rows to the Lance chunks table (row mode, idempotent).

        Steps:
          1. D11: Early-return if obj is empty — nothing to write or delete.
          2. D5 (C1 fix): Guard has_partition_key before accessing partition_key.
          3. D6: Derive producer_asset from context.asset_key.path[-1].
          4. Connect to Lance via lancedb (D2/D3).
          5. D7: Delete existing rows for (source_id, producer_asset), then add new rows.
          6. Record IO-level metadata via context.add_output_metadata().
        """
        # D11: Empty list early-return — skip write entirely.
        if not obj:
            context.log.info("LanceChunksIOManager: obj is empty, skipping write")
            context.add_output_metadata({"row_count": 0, "mode": "row_skipped"})
            return

        # D5 (C1 fix): Check has_partition_key before accessing partition_key.
        # context.partition_key raises DagsterInvariantViolationError (not falsy)
        # when no partition exists, so the guard must come first.
        if not context.has_partition_key:
            raise ValueError(
                "LanceChunksIOManager requires a partitioned asset; "
                "context.has_partition_key is False"
            )
        source_id = int(context.partition_key.removeprefix("src_"))

        # D6: Derive producer_asset from the last segment of the asset key path.
        # e.g. context.asset_key.path[-1] == "chunks" for the chunks asset.
        producer_asset = context.asset_key.path[-1]

        # D3: All config from environment variables.
        lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
        db_uri = f"s3://{lance_bucket}/chunks"
        storage_options = build_lance_storage_options()

        db = lancedb.connect(db_uri, storage_options=storage_options)
        table = db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)

        # D7: Row mode — delete existing rows for this (source_id, producer_asset)
        # then insert the new rows.  Lance delete() is a no-op when no matching rows
        # exist, so this is always safe even on a fresh table.
        # TODO F-028: dispatch column mode vs. row mode based on operator category.
        predicate = f"source_id = {source_id} AND producer_asset = '{producer_asset}'"
        table.delete(predicate)
        table.add(obj)

        context.log.info(
            "LanceChunksIOManager: wrote %d rows for source_id=%d producer_asset=%s",
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
