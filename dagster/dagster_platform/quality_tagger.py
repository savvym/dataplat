"""quality_tagger.py — Pure helpers for the attr_quality Dagster asset (F-027).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3 D1–D4):
- No Dagster imports — same no-Dagster guarantee as chunker.py. This allows
  fast unit tests without a Dagster runtime.
- Stub scorer: score = min(1.0, float(token_count) / 512.0), provider = "length_heuristic".
  F-028 will replace this with real LLM scoring once the gateway is built.
- Column-mode update: ZERO new rows. Updates attr_quality_score and
  attr_quality_provider columns on existing producer_asset='chunks' rows only.
  Does NOT modify lineage fields (augmented_from, augmenter_id, etc.) — taggers
  are NOT augmenters (agreed.md D3).
- DB access via raw lancedb (already in the Dagster image). Reads MINIO_* from
  os.environ (same pattern as chunker.py).
"""

from __future__ import annotations

import os
from typing import Any

import lancedb  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUALITY_PROVIDER = "length_heuristic"

# ---------------------------------------------------------------------------
# Stub scorer
# ---------------------------------------------------------------------------


def compute_quality_score(token_count: int) -> float:
    """Length-heuristic quality score (F-027 stub).

    Formula (agreed.md §1):
        score = min(1.0, float(token_count) / 512.0)

    F-028 will replace this with a real LLM-based scorer once the gateway
    (apps/api/dataplat_api/llm/) exists. Until then this pure arithmetic stub
    satisfies invariant #4 (no LLM SDK imports).

    Args:
        token_count: Number of BPE tokens in the chunk.

    Returns:
        A float in [0.0, 1.0].
    """
    return min(1.0, float(token_count) / 512.0)


# ---------------------------------------------------------------------------
# Lance column-mode update helper
# ---------------------------------------------------------------------------


def _build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (same pattern as chunker.py).
    """
    return {
        "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region":            "us-east-1",
        "allow_http":            "true",
    }


def update_quality_scores_in_lance(source_id: int) -> int:
    """Update attr_quality_score and attr_quality_provider on existing chunk rows.

    Performs a **column-mode update** on existing rows where:
        source_id = <source_id> AND producer_asset = 'chunks'

    Zero new rows are created. Lineage fields (augmented_from, augmenter_id,
    augmenter_config_hash, producer_asset, producer_version) are left untouched.

    Idempotency: re-running overwrites the same two columns — no row count change.

    Implementation tries Option A (table.update with values_sql — no Python
    read-back) which is the simplest path. If values_sql is unavailable in the
    installed lancedb version, falls back to Option B (read → compute → merge_insert).

    Args:
        source_id: The source to process.

    Returns:
        Number of rows updated (matched by the WHERE clause). Zero if no chunks
        exist for this source_id (update is a no-op — caller logs a warning).
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    # Open existing table — do NOT create; chunks must already exist (R2).
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"

    # Option A: table.update() with values_sql — no Python read-back required.
    # Tested against lancedb==0.30.2. If unavailable, falls back to Option B.
    try:
        table.update(
            where=where_clause,
            values_sql={
                "attr_quality_score": "LEAST(1.0, CAST(token_count AS FLOAT) / 512.0)",
                "attr_quality_provider": f"'{QUALITY_PROVIDER}'",
            },
        )
        # Count updated rows to return meaningful metadata.
        row_count: int = table.count_rows(where_clause)
        return row_count
    except Exception as option_a_exc:  # noqa: BLE001
        # Option A failed (e.g. values_sql not supported in this lancedb build).
        # Fall back to Option B: read rows → compute scores → merge_insert.
        _option_b_update(table, source_id, where_clause)
        row_count = table.count_rows(where_clause)
        return row_count


def _option_b_update(
    table: Any,
    source_id: int,
    where_clause: str,
) -> None:
    """Option B fallback: read rows → compute scores → merge_insert.

    Reads chunk_id and token_count for the matching rows, computes the quality
    score in Python, then uses merge_insert to update only the two attr_quality_*
    columns (keyed on chunk_id). Zero new rows created.

    Args:
        table: An open lancedb Table object.
        source_id: The source being processed (used only for error messages).
        where_clause: The SQL WHERE clause identifying rows to update.
    """
    rows = (
        table.search()
        .where(where_clause)
        .select(["chunk_id", "token_count"])
        .to_list()
    )
    if not rows:
        return

    scored: list[dict[str, Any]] = [
        {
            "chunk_id": r["chunk_id"],
            "attr_quality_score": compute_quality_score(int(r["token_count"])),
            "attr_quality_provider": QUALITY_PROVIDER,
        }
        for r in rows
    ]

    (
        table.merge_insert("chunk_id")
        .when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"])
        .execute(scored)
    )
