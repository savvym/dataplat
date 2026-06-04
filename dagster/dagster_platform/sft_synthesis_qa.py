"""sft_synthesis_qa.py — Pure helpers for the `dataset` Dagster asset (F-043).

All functions are side-effect-free where possible, or have clearly bounded I/O.
No Dagster imports — same no-Dagster guarantee as quality_tagger.py, extractor.py,
and chunker.py. This allows fast unit tests without a Dagster runtime.

Design notes (agreed.md §"Decisions" D1–D8, F-043):
- No direct LLM SDK imports (hard invariant #4 — CLAUDE.md §"Hard invariants").
  LLM synthesis calls go through the internal FastAPI gateway via requests.post
  to POST /api/internal/llm/completions. Mirrors quality_tagger.py:score_chunks_via_gateway().
- DB access via raw psycopg2 (sync — invariant #5 is scoped to apps/api/dataplat_api/).
- Lance reads via lancedb.connect() with S3 storage_options (same pattern as quality_tagger.py).
- val_ratio sourced from recipe_snapshot["output"]["splits"]["validation"], fallback 0.1 (D2).
- Deterministic split via md5(chunk_id) % 100 < int(val_ratio * 100) (D8 — fast stdlib, not
  cryptographic; collision-tolerant for dataset splitting purposes).
- Zero-row materialization is allowed (D5): two empty Parquet files are valid HF artifacts.
- chunk_id included as a third column in Parquet output for row-level traceability (D6).
- max_tokens configurable via recipe_snapshot["schema"]["config"]["max_tokens"],
  fallback 512 (D7).

Operator registration (F-092): the sft_synthesis_qa operator row is deferred to F-092.
The materializer reads config from recipe_snapshot["schema"]["config"] directly at runtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import lancedb  # type: ignore[import-untyped]
import psycopg2  # type: ignore[import-untyped]
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DatasetOutput dataclass (return type of the `dataset` asset)
# ---------------------------------------------------------------------------


@dataclass
class DatasetOutput:
    """Typed payload returned by the `dataset` asset and consumed by HFDatasetIOManager.

    Attributes:
        train_rows:      List of dicts with keys "instruction", "output", "chunk_id".
        val_rows:        List of dicts with keys "instruction", "output", "chunk_id".
        recipe_snapshot: Frozen copy from Postgres dataset.recipe_snapshot (set by F-042).
        dataset_id:      DB-assigned dataset.id (int).
        version_tag:     Version string, e.g. "v1".
    """

    train_rows: list[dict[str, Any]]
    val_rows: list[dict[str, Any]]
    recipe_snapshot: dict[str, Any]
    dataset_id: int
    version_tag: str


# ---------------------------------------------------------------------------
# Partition key parsing
# ---------------------------------------------------------------------------


def parse_dataset_partition_key(partition_key: str) -> tuple[int, str]:
    """Parse a dataset partition key into (recipe_id, version_tag).

    Format: "ds_{recipe_id}_v{n}" (design doc §5.3, line 532).

    Args:
        partition_key: String like "ds_5_v2".

    Returns:
        Tuple of (recipe_id: int, version_tag: str), e.g. (5, "v2").

    Raises:
        ValueError: If the partition key does not match the expected pattern.

    Examples:
        >>> parse_dataset_partition_key("ds_5_v2")
        (5, 'v2')
        >>> parse_dataset_partition_key("ds_100_v1")
        (100, 'v1')
    """
    pattern = r"^ds_(\d+)_(v\d+)$"
    match = re.match(pattern, partition_key)
    if not match:
        raise ValueError(
            f"Invalid dataset partition key: {partition_key!r}. "
            f"Expected format: 'ds_{{recipe_id}}_v{{n}}' (e.g. 'ds_5_v2')."
        )
    recipe_id = int(match.group(1))
    version_tag = match.group(2)
    return recipe_id, version_tag


# ---------------------------------------------------------------------------
# Postgres dataset lookup
# ---------------------------------------------------------------------------


def fetch_dataset_row(recipe_id: int, version_tag: str) -> dict[str, Any]:
    """Query Postgres for the dataset row matching (recipe_id, version_tag).

    Reads `id`, `recipe_snapshot`, and `hf_repo_uri` from the `dataset` table.
    The recipe_snapshot was frozen at INSERT time by F-042 (hard invariant #1).
    This function does NOT re-freeze the recipe — it uses the already-frozen snapshot.

    Args:
        recipe_id:   The recipe ID from the partition key.
        version_tag: The version tag string (e.g. "v1").

    Returns:
        Dict with keys: "id" (int), "recipe_snapshot" (dict), "hf_repo_uri" (str).

    Raises:
        ValueError: If no matching dataset row is found (indicates F-042 step did not
                    commit the row before this asset ran — guaranteed ordering violated).
    """
    db_url = os.environ["PLATFORM_DB_URL"]
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, recipe_snapshot, hf_repo_uri "
                "FROM dataset "
                "WHERE recipe_id = %s AND version_tag = %s",
                (recipe_id, version_tag),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        raise ValueError(
            f"No dataset row found for recipe_id={recipe_id}, version_tag={version_tag!r}. "
            "The F-042 materialize endpoint must have committed the row before this "
            "asset executes. Check that the Dagster backfill was launched after the "
            "dataset row was committed."
        )

    dataset_id: int = row[0]
    # recipe_snapshot is a JSONB column; psycopg2 returns it as a dict already.
    recipe_snapshot: dict[str, Any] = row[1]
    hf_repo_uri: str = row[2]
    return {"id": dataset_id, "recipe_snapshot": recipe_snapshot, "hf_repo_uri": hf_repo_uri}


# ---------------------------------------------------------------------------
# Lance storage helpers (mirrors quality_tagger.py)
# ---------------------------------------------------------------------------


def _build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (same pattern as quality_tagger.py).
    """
    return {
        "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region":            "us-east-1",
        "allow_http":            "true",
    }


def read_chunks_from_lance(filter_sql: str | None) -> list[dict[str, Any]]:
    """Read chunk rows from Lance, optionally filtered by a SQL predicate.

    Reads the `chunks` Lance table at s3://{MINIO_LANCE_BUCKET}/chunks/,
    selecting `chunk_id` and `text` columns (same projection as quality_tagger.py).

    Args:
        filter_sql: SQL WHERE predicate (e.g. "attr_quality_score > 0.7"), or
                    None for no filter (return all chunks).

    Returns:
        List of dicts with keys "chunk_id" (str) and "text" (str).
        Returns an empty list if no chunks match the filter.
    """
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = _build_lance_storage_options()

    db = lancedb.connect(db_uri, storage_options=storage_options)
    table = db.open_table("chunks")

    search = table.search()
    if filter_sql is not None:
        # .where() before .select() — matches quality_tagger.py call order.
        rows: list[dict[str, Any]] = (
            search.where(filter_sql).select(["chunk_id", "text"]).to_list()
        )
    else:
        rows = search.select(["chunk_id", "text"]).to_list()
    if not rows:
        logger.info(
            "read_chunks_from_lance: no chunks found (filter_sql=%r)", filter_sql
        )
    else:
        logger.info(
            "read_chunks_from_lance: found %d chunk(s) (filter_sql=%r)",
            len(rows),
            filter_sql,
        )
    return rows


# ---------------------------------------------------------------------------
# LLM gateway call (mirrors quality_tagger.py:score_chunks_via_gateway)
# ---------------------------------------------------------------------------


def call_llm_gateway(
    prompt: str,
    max_tokens: int = 512,
    fallback_on_failure: bool = True,
) -> dict[str, str] | None:
    """Call the internal LLM gateway to synthesise a Q+A pair for a chunk.

    POSTs to POST /api/internal/llm/completions (same endpoint as quality_tagger.py).
    Parses the response JSON content as a JSON object with "instruction" and "output"
    keys.

    Hard invariant #4: No direct SDK imports. Uses requests.post to the internal
    FastAPI gateway endpoint only.

    Args:
        prompt:             The full prompt string to send to the LLM.
        max_tokens:         Maximum tokens in the LLM response (default 512).
        fallback_on_failure: If True, log a warning and return None on parse/request
                             failures (skip the chunk). If False, raise on failure.

    Returns:
        Dict with "instruction" (str) and "output" (str) on success.
        None if the response could not be parsed AND fallback_on_failure is True.

    Raises:
        ValueError: If the response could not be parsed AND fallback_on_failure is False.
        requests.RequestException: If the HTTP request fails AND fallback_on_failure is False.
    """
    gateway_url = os.environ.get("LLM_GATEWAY_URL", "http://fastapi:8000")
    endpoint = f"{gateway_url}/api/internal/llm/completions"

    try:
        resp = requests.post(
            endpoint,
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["content"]
        parsed = json.loads(content)
        instruction = str(parsed["instruction"])
        output = str(parsed["output"])
        return {"instruction": instruction, "output": output}
    except requests.RequestException as exc:
        if fallback_on_failure:
            logger.warning(
                "call_llm_gateway: HTTP request failed (skipping chunk): %s", exc
            )
            return None
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        if fallback_on_failure:
            logger.warning(
                "call_llm_gateway: response parse failed (skipping chunk): %s", exc
            )
            return None
        raise ValueError(
            f"call_llm_gateway: failed to parse LLM response as JSON "
            f"{{\"instruction\": ..., \"output\": ...}}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Deterministic train/val split
# ---------------------------------------------------------------------------


def deterministic_split(
    rows: list[dict[str, Any]],
    val_ratio: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows deterministically into train and val buckets by chunk_id hash.

    Uses md5(chunk_id) % 100 < int(val_ratio * 100) to assign to val;
    otherwise to train. md5 is used for speed (not cryptographic — collision-
    tolerant for dataset splitting). Deterministic: same input always produces
    the same split.

    Args:
        rows:      List of dicts, each must have a "chunk_id" key.
        val_ratio: Fraction (0.0–1.0) to assign to the validation split.
                   0.0 → all rows in train. 1.0 → all rows in val.

    Returns:
        Tuple of (train_rows, val_rows).
    """
    threshold = int(val_ratio * 100)
    train_rows: list[dict[str, Any]] = []
    val_rows: list[dict[str, Any]] = []

    for row in rows:
        chunk_id: str = row["chunk_id"]
        bucket = int(hashlib.md5(chunk_id.encode()).hexdigest(), 16) % 100  # noqa: S324
        if bucket < threshold:
            val_rows.append(row)
        else:
            train_rows.append(row)

    return train_rows, val_rows


# ---------------------------------------------------------------------------
# Pure-function wrapper for integration tests (no Dagster runtime required)
# ---------------------------------------------------------------------------


def _run_dataset_asset(
    partition_key: str,
) -> DatasetOutput:
    """Exercise the asset body logic without a Dagster runtime.

    This is the thin wrapper used by integration tests
    (test_sft_synthesis_qa.py::test_dataset_asset_end_to_end). It calls
    the same sequence of helpers that the real `dataset` asset in
    definitions.py uses.

    Args:
        partition_key: Dagster partition key (e.g. "ds_5_v1").

    Returns:
        DatasetOutput with train_rows, val_rows, recipe_snapshot, dataset_id,
        version_tag populated.

    Raises:
        ValueError: If the partition key is malformed or the dataset row is missing.
    """
    recipe_id, version_tag = parse_dataset_partition_key(partition_key)

    db_row = fetch_dataset_row(recipe_id, version_tag)
    dataset_id: int = db_row["id"]
    recipe_snapshot: dict[str, Any] = db_row["recipe_snapshot"]

    filter_sql: str | None = recipe_snapshot.get("filter", {}).get("where")
    template_config: dict[str, Any] = recipe_snapshot.get("schema", {}).get("config", {})
    prompt_template: str = template_config.get(
        "prompt_template",
        (
            "Generate a question and answer for the following text:\n\n"
            "{chunk_text}\n\n"
            'Respond with JSON: {{"instruction": "...", "output": "..."}}'
        ),
    )
    val_ratio: float = (
        recipe_snapshot.get("output", {})
        .get("splits", {})
        .get("validation", 0.1)
    )
    fallback_on_failure: bool = template_config.get("fallback_on_failure", True)
    max_tokens: int = template_config.get("max_tokens", 512)

    chunks = read_chunks_from_lance(filter_sql)
    if not chunks:
        logger.warning(
            "_run_dataset_asset: zero chunks found for recipe_id=%d version_tag=%s "
            "(filter_sql=%r) — materializing empty dataset",
            recipe_id,
            version_tag,
            filter_sql,
        )

    qa_rows: list[dict[str, Any]] = []
    for chunk in chunks:
        prompt = prompt_template.format(chunk_text=chunk["text"])
        raw = call_llm_gateway(
            prompt,
            max_tokens=max_tokens,
            fallback_on_failure=fallback_on_failure,
        )
        if raw is not None:
            qa_rows.append(
                {
                    "instruction": raw["instruction"],
                    "output": raw["output"],
                    "chunk_id": chunk["chunk_id"],
                }
            )

    train_rows, val_rows = deterministic_split(qa_rows, val_ratio)

    return DatasetOutput(
        train_rows=train_rows,
        val_rows=val_rows,
        recipe_snapshot=recipe_snapshot,
        dataset_id=dataset_id,
        version_tag=version_tag,
    )
