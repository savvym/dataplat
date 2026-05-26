"""chunker.py — Pure helpers for the chunks Dagster asset (F-025).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3–§6):
- CHUNKS_SCHEMA is duplicated from apps/api/dataplat_api/storage/lance.py.
  The Dagster container cannot import dataplat_api (different package,
  different virtualenv). The schema is a pure constant — duplication is safe;
  any future schema change must update BOTH files together (see R6 in agreed.md).
  CRITICAL: Do NOT add nullable=False to any field — Arrow schema comparison is
  strict on nullability and a mismatch causes lancedb to raise on
  create_table(..., exist_ok=True).
- DB access via raw psycopg2 (already in the image via dagster-postgres); sync is
  fine outside apps/api/ — invariant #5 is scoped to apps/api/dataplat_api/.
- tiktoken encoder is loaded once at module import to avoid per-chunk BPE vocab
  file fetches (agreed.md D1).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import boto3  # type: ignore[import-untyped]
import lancedb  # type: ignore[import-untyped]
import psycopg2  # type: ignore[import-untyped]
import pyarrow as pa
import tiktoken  # type: ignore[import-untyped]
from docling_core.types.doc.document import DoclingDocument  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNKER_VERSION = "0.1.0"
TOKEN_BUDGET = 512
DOCUMENTS_BUCKET = "documents"

# Load encoder once at module import (agreed.md D1 — avoid per-chunk BPE fetch).
_ENCODER = tiktoken.get_encoding("cl100k_base")

# ---------------------------------------------------------------------------
# CHUNKS_SCHEMA — design doc §4.2, all 24 fields, exact order.
# DUPLICATED from apps/api/dataplat_api/storage/lance.py (agreed.md D3 / R6).
# Do NOT add nullable=False to any field — strict Arrow nullability check in
# lancedb's create_table(exist_ok=True) would raise if schema mismatches.
# ---------------------------------------------------------------------------
CHUNKS_SCHEMA: pa.Schema = pa.schema([
    # === Identifiers ===
    ("chunk_id",                pa.string()),        # uuid or source+offset derived
    ("source_id",               pa.int64()),         # denormalized for filter efficiency
    ("source_collection_id",    pa.int64()),
    ("producer_asset",          pa.string()),        # "chunks" | "augment_translate_en" etc.
    ("producer_version",        pa.string()),

    # === Content ===
    ("text",                    pa.large_string()),  # linearized chunk text
    ("token_count",             pa.int32()),
    ("docling_refs",            pa.string()),        # NodeItem path in DoclingDocument
    ("source_refs",             pa.string()),        # JSON: {page, bbox, char_range}

    # === Provenance ===
    ("augmented_from",          pa.string()),        # parent chunk_id (NULL = original)
    ("augmenter_id",            pa.string()),        # augmenter operator id
    ("augmenter_config_hash",   pa.string()),

    # === Attribute columns (initial set; new attributes = new columns) ===
    ("attr_quality_score",      pa.float32()),
    ("attr_quality_provider",   pa.string()),        # 'gpt-4o-mini' | 'qwen-judge' etc.
    ("attr_lang_code",          pa.string()),
    ("attr_lang_confidence",    pa.float32()),
    ("attr_minhash_signature",  pa.list_(pa.uint64())),
    ("attr_minhash_cluster_id", pa.int64()),
    ("attr_minhash_is_head",    pa.bool_()),
    ("attr_pii_has_pii",        pa.bool_()),
    ("attr_pii_categories",     pa.list_(pa.string())),
    ("attr_embed_vector",       pa.list_(pa.float32(), 1024)),  # Lance native vector index

    # === Timestamps ===
    ("created_at",              pa.timestamp("ms")),
    ("updated_at",              pa.timestamp("ms")),
])


# ---------------------------------------------------------------------------
# S3 / Lance helpers
# ---------------------------------------------------------------------------


def build_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Reads MINIO_* from os.environ (Dagster containers use env vars, not Pydantic
    Settings). Keys match the object_store 0.9+ convention verified for
    lancedb==0.30.2 (same as apps/api/dataplat_api/storage/lance.py).
    """
    return {
        "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
        "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
        "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
        "aws_region":            "us-east-1",
        "allow_http":            "true",
    }


def build_s3_client() -> Any:
    """Return a boto3 S3 client configured from MINIO_* environment variables."""
    endpoint = os.environ["MINIO_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )


def read_docling_document(s3: Any, source_id: int) -> DoclingDocument:
    """Read the canonical DoclingDocument JSON from MinIO and parse it.

    Path: s3://documents/{source_id}/extract_mineru/doc.docling.json

    Raises RuntimeError if the object does not exist or cannot be fetched.
    Raises ValueError if the JSON cannot be parsed as a DoclingDocument.
    """
    key = f"{source_id}/extract_mineru/doc.docling.json"
    try:
        resp = s3.get_object(Bucket=DOCUMENTS_BUCKET, Key=key)
        json_str = resp["Body"].read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read s3://{DOCUMENTS_BUCKET}/{key}: {exc}"
        ) from exc
    return DoclingDocument.model_validate_json(json_str)


def extract_text_from_document(doc: DoclingDocument, source_id: int) -> str:
    """Extract plain text from a DoclingDocument with graceful fallbacks.

    Strategy (agreed.md D2):
    1. doc.export_to_markdown() — linearised full text.
    2. doc.name — typically "source_{source_id}" for F-019 minimal docs.
    3. f"source_{source_id}" — final deterministic fallback (R2 mitigation).

    Returns a non-empty string in all cases. The fallback is a dev-mode safety
    net; real extraction (F-026) will replace the stub.
    """
    text = doc.export_to_markdown().strip()
    if text:
        return text
    name = (doc.name or "").strip()
    if name:
        return name
    return f"source_{source_id}"


def fixed_size_chunk(
    text: str,
    source_id: int,
    collection_id: int,
) -> list[dict[str, Any]]:
    """Split text into ≤512-token windows and return a list of chunk dicts.

    Each dict has all 24 fields required by CHUNKS_SCHEMA.
    - chunk_id = f"{source_id}_{seq}" (0-indexed, agreed.md D4)
    - token_count = exact token count for the window
    - All attr_* = None (agreed.md D10)
    - docling_refs, source_refs = "" (convention, agreed.md D10)
    - augmented_from, augmenter_id, augmenter_config_hash = None
    """
    now = datetime.now(timezone.utc)
    token_ids = _ENCODER.encode(text)

    # Guard: if text somehow encodes to 0 tokens, fall back to source_{id}.
    if not token_ids:
        token_ids = _ENCODER.encode(f"source_{source_id}")

    rows: list[dict[str, Any]] = []
    seq = 0
    for start in range(0, len(token_ids), TOKEN_BUDGET):
        window = token_ids[start : start + TOKEN_BUDGET]
        chunk_text = _ENCODER.decode(window)
        rows.append({
            "chunk_id":               f"{source_id}_{seq}",
            "source_id":              source_id,
            "source_collection_id":   collection_id,
            "producer_asset":         "chunks",
            "producer_version":       CHUNKER_VERSION,
            "text":                   chunk_text,
            "token_count":            len(window),
            "docling_refs":           "",
            "source_refs":            "",
            "augmented_from":         None,
            "augmenter_id":           None,
            "augmenter_config_hash":  None,
            "attr_quality_score":     None,
            "attr_quality_provider":  None,
            "attr_lang_code":         None,
            "attr_lang_confidence":   None,
            "attr_minhash_signature": None,
            "attr_minhash_cluster_id": None,
            "attr_minhash_is_head":   None,
            "attr_pii_has_pii":       None,
            "attr_pii_categories":    None,
            "attr_embed_vector":      None,
            "created_at":             now,
            "updated_at":             now,
        })
        seq += 1
    return rows


def write_chunks_to_lance(rows: list[dict[str, Any]]) -> None:
    """Write chunk rows to the Lance chunks table (idempotent, agreed.md D5).

    Connects to s3://{MINIO_LANCE_BUCKET}/chunks — same URI pattern as
    apps/api/dataplat_api/storage/lance.py but reads from os.environ.
    Steps:
      1. Delete existing rows for (source_id, producer_asset='chunks').
      2. Add the new rows.
    Lance delete() is a no-op when no matching rows exist (R3 — safe).
    """
    if not rows:
        return
    source_id = rows[0]["source_id"]
    lance_bucket = os.environ.get("MINIO_LANCE_BUCKET", "lance")
    db_uri = f"s3://{lance_bucket}/chunks"
    storage_options = build_lance_storage_options()
    db = lancedb.connect(db_uri, storage_options=storage_options)
    table = db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)
    # D5 idempotency: delete existing rows for this source+producer before insert.
    table.delete(f"source_id = {source_id} AND producer_asset = 'chunks'")
    table.add(rows)


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------


def lookup_source_collection_id(source_id: int) -> int:
    """Look up source_collection_id from Postgres for the given source_id.

    Uses raw psycopg2 with PLATFORM_DB_URL (same pattern as extractor.py).
    Raises ValueError if the source is not found (agreed.md D7, R5).
    """
    db_url = os.environ["PLATFORM_DB_URL"]
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT collection_id FROM source WHERE id = %s",
                (source_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(
            f"source {source_id} not found in Postgres — cannot resolve collection_id"
        )
    return int(row[0])
