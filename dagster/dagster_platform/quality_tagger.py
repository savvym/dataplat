"""quality_tagger.py — Pure helpers for the attr_quality Dagster asset (F-028).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3 D6, F-028):
- No Dagster imports — same no-Dagster guarantee as chunker.py and extractor.py.
  This allows fast unit tests without a Dagster runtime.
- No direct LLM SDK imports (hard invariant #4 — CLAUDE.md §"Hard invariants").
  LLM scoring is done by calling the internal FastAPI gateway via requests.post.
  Only apps/api/dataplat_api/llm/gateway.py may import anthropic.
- Two-layer architecture (agreed.md D-A):
    Dagster tagger → POST /api/internal/llm/completions → LLMGateway → Anthropic SDK
- Column-mode update: ZERO new rows. Updates attr_quality_score and
  attr_quality_provider columns on existing producer_asset='chunks' rows only.
  Does NOT modify lineage fields (augmented_from, augmenter_id, etc.) — taggers
  are NOT augmenters (agreed.md D3).
- DB access via raw lancedb (already in the Dagster image).
- Reads LLM_GATEWAY_URL from os.environ (default "http://fastapi:8000").
- Mock mode is transparent: when ANTHROPIC_API_KEY is absent in fastapi service,
  the gateway returns content="0.5", model="mock" — quality_tagger.py just parses
  whatever the gateway returns, with no special handling needed.
- Per-chunk HTTP calls (agreed.md D-E): simple but potentially slow for large sources.
  Dagster op timeout should be set generously (~5 min) for production use.
  Batching deferred to a future feature.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import lancedb  # type: ignore[import-untyped]
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring prompt (exact text per agreed.md D6 — do NOT modify)
# ---------------------------------------------------------------------------

_SCORING_PROMPT = (
    "Rate the quality of the following text chunk on a scale from 0.0 to 1.0,\n"
    "where 1.0 is high-quality, coherent, informative text and 0.0 is garbled,\n"
    "empty, or meaningless content.\n"
    "\n"
    "Text:\n"
    "{chunk_text}\n"
    "\n"
    "Respond with ONLY a single decimal number between 0.0 and 1.0. No explanation."
)


# ---------------------------------------------------------------------------
# Lance storage helpers
# ---------------------------------------------------------------------------


def _build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (same pattern as chunker.py).
    """
    return {
        "aws_access_key_id": os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint": f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region": "us-east-1",
        "allow_http": "true",
    }


# ---------------------------------------------------------------------------
# LLM scoring via internal FastAPI gateway
# ---------------------------------------------------------------------------


def score_chunks_via_gateway(texts: list[str]) -> list[tuple[float, str]]:
    """Score a list of chunk texts via the internal LLM gateway.

    For each chunk text, POSTs the scoring prompt to the internal FastAPI
    endpoint (POST /api/internal/llm/completions) via requests.post.
    Parses the response JSON and clamps the score to [0.0, 1.0].

    Uses requests (not httpx) — the Dagster image has requests as a transitive
    dep of dagster itself; httpx is NOT reliably present in the Dagster virtualenv.
    (agreed.md D-C)

    Args:
        texts: List of chunk texts to score (one HTTP call per text).

    Returns:
        List of (score, model_name) tuples, one per input text.
        On request failure or parse error: (0.0, "error") for that chunk.
        Errors are logged and do NOT abort the batch (agreed.md D6 item 3).
    """
    gateway_url = os.environ.get("LLM_GATEWAY_URL", "http://fastapi:8000")
    endpoint = f"{gateway_url}/api/internal/llm/completions"

    results: list[tuple[float, str]] = []
    for text in texts:
        prompt = _SCORING_PROMPT.format(chunk_text=text)
        try:
            resp = requests.post(
                endpoint,
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 16,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_score = float(data["content"])
            score = max(0.0, min(1.0, raw_score))
            provider: str = data["model"]
            results.append((score, provider))
        except requests.RequestException as exc:
            logger.warning("score_chunks_via_gateway: HTTP request failed: %s", exc)
            results.append((0.0, "error"))
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("score_chunks_via_gateway: response parse failed: %s", exc)
            results.append((0.0, "error"))

    return results


# ---------------------------------------------------------------------------
# Lance column-mode update
# ---------------------------------------------------------------------------


def compute_quality_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, score via LLM gateway.

    Returns partial dicts suitable for LanceChunksIOManager column mode (F-031).
    Does NOT write to Lance — the IOManager handles the write.

    Args:
        source_id: The source to process.

    Returns:
        List of dicts: [{"chunk_id": str, "attr_quality_score": float,
        "attr_quality_provider": str}, ...].
        Returns an empty list if no chunks exist for this source.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    rows = table.search().where(where_clause).select(["chunk_id", "text"]).to_list()
    if not rows:
        logger.info("compute_quality_scores: no rows found for source_id=%d", source_id)
        return []

    scored = score_chunks_via_gateway([r["text"] for r in rows])

    result: list[dict] = []
    for row, (score, provider) in zip(rows, scored):
        result.append(
            {
                "chunk_id": row["chunk_id"],
                "attr_quality_score": score,
                "attr_quality_provider": provider,
            }
        )
    return result


def update_quality_scores_in_lance(source_id: int) -> int:
    """Update attr_quality_score and attr_quality_provider on existing chunk rows.

    .. deprecated::
        F-031: Use ``compute_quality_scores(source_id)`` and route the result
        through ``LanceChunksIOManager`` (column mode) instead.  This function
        is kept for backward compatibility but is no longer called from Dagster
        tagger assets.

    Performs a **column-mode update** on existing rows where:
        source_id = <source_id> AND producer_asset = 'chunks'

    Zero new rows are created. Lineage fields (augmented_from, augmenter_id,
    augmenter_config_hash, producer_asset, producer_version) are left untouched.

    Idempotency: re-running overwrites the same two columns — no row count change.

    F-028 change (vs F-027): reads chunk text from Lance → calls internal LLM
    gateway per chunk → merge_insert updated scores. Option A (SQL values_sql) is
    no longer viable because HTTP cannot be invoked from SQL (agreed.md D-D).

    Args:
        source_id: The source to process.

    Returns:
        Number of rows matched by the WHERE clause (zero if no chunks exist —
        caller logs a warning). Note: row count is checked AFTER the update
        (not the number of HTTP calls made).
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    # Open existing table — do NOT create; chunks must already exist.
    table = db.open_table("chunks")

    where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
    _llm_update(table, source_id, where_clause)

    row_count: int = table.count_rows(where_clause)
    return row_count


def _llm_update(
    table: Any,
    source_id: int,
    where_clause: str,
) -> None:
    """LLM-based column update: read chunk texts → call gateway → update columns.

    .. deprecated::
        F-031: Internal helper for the deprecated ``update_quality_scores_in_lance()``.
        New code should use ``compute_quality_scores()`` and route through
        ``LanceChunksIOManager`` column mode.

    Reads chunk_id and text for matching rows, calls score_chunks_via_gateway()
    to get (score, provider) per row, then calls table.update() once per row to
    overwrite only attr_quality_score and attr_quality_provider (keyed on chunk_id).
    Zero new rows created; all other columns are untouched.

    Uses table.update(where=..., values=...) rather than merge_insert because
    lancedb 0.30.2 does not support when_matched_update_all(updates=[...]) —
    the `updates=` kwarg does not exist, and bare when_matched_update_all()
    replaces the entire row (destroying lineage fields). The per-row
    table.update(where=..., values=...) achieves column-mode partial update
    correctly: only attr_quality_score and attr_quality_provider are touched.
    (Amendment to agreed.md D6 item 5; see contracts/S028-F-028/review-final.md H1.)

    Args:
        table:        An open lancedb Table object.
        source_id:    The source being processed (used only in log messages).
        where_clause: SQL WHERE clause identifying rows to update.
    """
    rows = table.search().where(where_clause).select(["chunk_id", "text"]).to_list()
    if not rows:
        logger.info("_llm_update: no rows found for source_id=%d — skipping", source_id)
        return

    scored = score_chunks_via_gateway([r["text"] for r in rows])

    for row, (score, provider) in zip(rows, scored):
        chunk_id: str = row["chunk_id"]
        table.update(
            where=f"chunk_id = '{chunk_id}'",
            values={
                "attr_quality_score": score,
                "attr_quality_provider": provider,
            },
        )
