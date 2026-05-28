"""lang_tagger.py — Pure helpers for the attr_lang Dagster asset (F-029).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3 D3, F-029):
- No Dagster imports — same no-Dagster guarantee as chunker.py, extractor.py,
  and quality_tagger.py. This allows fast unit tests without a Dagster runtime.
- fasttext is NOT an LLM SDK (agreed.md D2). CLAUDE.md invariant #4 does not apply.
  lang_tagger.py imports ftlangdetect.detect directly — no HTTP gateway call.
- Package: fasttext-langdetect==1.1.1 (PyPI). Original agreed.md specified 1.0.6
  but that version is no longer available; 1.1.1 is the closest compatible release.
  Breaking change from 1.0.6: import module is now `ftlangdetect` (was
  `fasttext_langdetect`). The lang field is already stripped of __label__ prefix
  internally; our .replace() call is a safe no-op. Agreed.md updated per D1 gate.
- lid.176.ftz model is NOT bundled in the wheel (differs from 1.0.6 spec).
  It is downloaded at Docker build time by the bake RUN step and cached at
  FTLANG_CACHE=/app/fasttext-models (set via Dockerfile ENV).
- Column-mode update: ZERO new rows. Updates attr_lang_code and
  attr_lang_confidence columns on existing producer_asset='chunks' rows only.
  Does NOT modify lineage fields (augmented_from, augmenter_id, etc.) — taggers
  are NOT augmenters (agreed.md D3).
- DB access via raw lancedb (already in the Dagster image).
- Sentinel behavior (agreed.md D5): empty/whitespace → ("und", 0.0). Any
  exception from detect() also → ("und", 0.0). Never re-raises. Ensures a
  single problematic chunk does not abort the entire batch.
- Per-chunk detection (agreed.md D7): simple and self-contained.
  No batching for MVP.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import lancedb  # type: ignore[import-untyped]
from ftlangdetect import detect  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lance storage helpers (same pattern as quality_tagger.py)
# ---------------------------------------------------------------------------


def _build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (same pattern as chunker.py and quality_tagger.py).
    """
    return {
        "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region":            "us-east-1",
        "allow_http":            "true",
    }


# ---------------------------------------------------------------------------
# Language detection (agreed.md D4, D5)
# ---------------------------------------------------------------------------


def detect_language(text: str) -> tuple[str, float]:
    """Detect the language of a text chunk using fasttext lid.176.ftz.

    Returns (iso_code, confidence) where iso_code is an ISO 639-1 two-letter
    code (e.g. "en", "fr") or ISO 639-2/3 three-letter code for languages
    without a 639-1 code (e.g. "war", "bpy"). Returns "und" (ISO 639-2
    undetermined) for empty/whitespace text or on any exception.

    Sentinel behavior (agreed.md D5): empty/whitespace → ("und", 0.0), any
    exception from detect() also → ("und", 0.0). Never re-raises. Ensures a
    single problematic chunk does not abort the entire batch.

    Args:
        text: The chunk text to detect language for.

    Returns:
        (iso_code, confidence) — confidence clamped to [0.0, 1.0].
    """
    if not text or not text.strip():
        return ("und", 0.0)
    try:
        result = detect(text, low_memory=True)
        # In ftlangdetect>=1.1.0, lang has __label__ already stripped internally.
        # The .replace() is a safe no-op for stripped codes and a fallback
        # guard for any future version regression (agreed.md D4).
        code: str = result["lang"].replace("__label__", "")
        conf: float = max(0.0, min(1.0, float(result["score"])))
        return (code, conf)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fasttext detect() failed for text (len=%d): %s", len(text), exc
        )
        return ("und", 0.0)


# ---------------------------------------------------------------------------
# Lance column-mode update
# ---------------------------------------------------------------------------


def compute_lang_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, detect language via fasttext.

    Returns partial dicts suitable for LanceChunksIOManager column mode (F-031).
    Does NOT write to Lance — the IOManager handles the write.

    Args:
        source_id: The source to process.

    Returns:
        List of dicts: [{"chunk_id": str, "attr_lang_code": str,
        "attr_lang_confidence": float}, ...].
        Returns an empty list if no chunks exist for this source.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    rows = (
        table.search()
        .where(where_clause)
        .select(["chunk_id", "text"])
        .to_list()
    )
    if not rows:
        logger.info(
            "compute_lang_scores: no rows found for source_id=%d", source_id
        )
        return []

    result: list[dict] = []
    for row in rows:
        chunk_id: str = row["chunk_id"]
        text: str = row["text"] or ""
        code, conf = detect_language(text)
        result.append(
            {
                "chunk_id": chunk_id,
                "attr_lang_code": code,
                "attr_lang_confidence": conf,
            }
        )
    return result


def update_lang_in_lance(source_id: int) -> int:
    """Update attr_lang_code and attr_lang_confidence on existing chunk rows.

    .. deprecated::
        F-031: Use ``compute_lang_scores(source_id)`` and route the result
        through ``LanceChunksIOManager`` (column mode) instead.  This function
        is kept for backward compatibility but is no longer called from Dagster
        tagger assets.

    Performs a **column-mode update** on existing rows where:
        source_id = <source_id> AND producer_asset = 'chunks'

    Zero new rows are created. Lineage fields (augmented_from, augmenter_id,
    augmenter_config_hash, producer_asset, producer_version) are left untouched.

    Idempotency: re-running overwrites the same two columns — no row count change.

    Uses the same per-row table.update(where=..., values=...) pattern as
    quality_tagger.py (amendment to agreed.md D6; see
    contracts/S028-F-028/review-final.md H1).
    merge_insert is NOT used — lancedb 0.30.2 when_matched_update_all() without
    updates= kwarg replaces the entire row, destroying lineage fields.

    Args:
        source_id: The source to process.

    Returns:
        Number of rows matched by the WHERE clause (zero if no chunks exist —
        caller logs a warning). Row count is checked AFTER the update.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    # Open existing table — do NOT create; chunks must already exist.
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    _lang_update(table, source_id, where_clause)

    row_count: int = table.count_rows(where_clause)
    return row_count


def _lang_update(
    table: Any,
    source_id: int,
    where_clause: str,
) -> None:
    """Language detection column update: read chunk texts → detect → update columns.

    .. deprecated::
        F-031: Internal helper for the deprecated ``update_lang_in_lance()``.
        New code should use ``compute_lang_scores()`` and route through
        ``LanceChunksIOManager`` column mode.

    Reads chunk_id and text for matching rows, calls detect_language() per row,
    then calls table.update() once per row to overwrite only attr_lang_code and
    attr_lang_confidence (keyed on chunk_id). Zero new rows created; all other
    columns are untouched.

    Args:
        table:        An open lancedb Table object.
        source_id:    The source being processed (used only in log messages).
        where_clause: SQL WHERE clause identifying rows to update.
    """
    rows = (
        table.search()
        .where(where_clause)
        .select(["chunk_id", "text"])
        .to_list()
    )
    if not rows:
        logger.info(
            "_lang_update: no rows found for source_id=%d — skipping", source_id
        )
        return

    for row in rows:
        chunk_id: str = row["chunk_id"]
        text: str = row["text"] or ""
        code, conf = detect_language(text)
        table.update(
            where=f"chunk_id = '{chunk_id}'",
            values={
                "attr_lang_code": code,
                "attr_lang_confidence": conf,
            },
        )
