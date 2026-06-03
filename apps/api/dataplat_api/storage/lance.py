"""Storage module for the Lance global chunks table — S023-F-023.

Creates/opens the Lance dataset at s3://{MINIO_LANCE_BUCKET}/chunks/
using lancedb==0.30.2.

Implementation note — D1 adaptation (agreed.md §Design decisions):
  agreed.md D1 specified `import lance; lance.write_dataset()` to write to the
  exact path s3://lance/chunks/.  In lancedb 0.30.2, `import lance` raises
  ImportError — the `lance` standalone module is NOT distributed separately;
  only `lancedb` itself is available.  We therefore use lancedb's high-level API:

    db = lancedb.connect("s3://lance/chunks", storage_options=...)
    db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)

  lancedb's internal `_table_uri(base, name)` appends ".lance" to the table
  name, so the resolved dataset path is:

    s3://lance/chunks/chunks.lance/

  All S3 objects land under the key prefix "chunks/" (e.g.
  "chunks/chunks.lance/_versions/1.manifest"), which satisfies the V2
  verification criterion `list_objects_v2(Bucket='lance', Prefix='chunks/')`.

  Verified against lancedb==0.30.2 / object_store 0.9.

Implementation note — D4 exception handling:
  `db.create_table(..., exist_ok=True)` atomically creates-or-opens the table:
  it creates a new schema-only (0-row) table if absent, or opens the existing
  table if present.  This replaces the try-open / create-on-exception pattern
  from agreed.md D4.

Implementation note — D5 storage options:
  The "endpoint" key (not "aws_endpoint" or "aws_endpoint_url") is correct for
  lancedb >= 0.6 / object_store 0.9.  Verified against lancedb==0.30.2.

Implementation note — empty table:
  `db.create_table("chunks", schema=CHUNKS_SCHEMA)` with no `data=` argument
  internally calls `pa.Table.from_pylist([], schema=CHUNKS_SCHEMA)`, producing
  a zero-row table — equivalent to agreed.md D4's `CHUNKS_SCHEMA.empty_table()`.
  pyarrow==24.0.0 is the transitive version installed with lancedb==0.30.2.

Return type:
  `lancedb.table.LanceTable` (not `lance.LanceDataset` as in agreed.md D1;
  `LanceTable` has the same `.schema.names` interface used by V1 check).
"""

from typing import Any

import pyarrow as pa
import lancedb  # type: ignore[import-untyped]

from dataplat_api.config import settings


# ---------------------------------------------------------------------------
# CHUNKS_SCHEMA — design doc §4.2, all 24 fields, exact order.
# ---------------------------------------------------------------------------
CHUNKS_SCHEMA: pa.Schema = pa.schema(
    [
        # === Identifiers ===
        ("chunk_id", pa.string()),  # uuid or source+offset derived
        ("source_id", pa.int64()),  # denormalized for filter efficiency
        ("source_collection_id", pa.int64()),
        ("producer_asset", pa.string()),  # "chunks" | "augment_translate_en" etc.
        ("producer_version", pa.string()),
        # === Content ===
        ("text", pa.large_string()),  # linearized chunk text
        ("token_count", pa.int32()),
        ("docling_refs", pa.string()),  # NodeItem path in DoclingDocument
        ("source_refs", pa.string()),  # JSON: {page, bbox, char_range}
        # === Provenance ===
        ("augmented_from", pa.string()),  # parent chunk_id (NULL = original)
        ("augmenter_id", pa.string()),  # augmenter operator id
        ("augmenter_config_hash", pa.string()),
        # === Attribute columns (initial set; new attributes = new columns) ===
        ("attr_quality_score", pa.float32()),
        ("attr_quality_provider", pa.string()),  # 'gpt-4o-mini' | 'qwen-judge' etc.
        ("attr_lang_code", pa.string()),
        ("attr_lang_confidence", pa.float32()),
        ("attr_minhash_signature", pa.list_(pa.uint64())),
        ("attr_minhash_cluster_id", pa.int64()),
        ("attr_minhash_is_head", pa.bool_()),
        ("attr_pii_has_pii", pa.bool_()),
        ("attr_pii_categories", pa.list_(pa.string())),
        (
            "attr_embed_vector",
            pa.list_(pa.float32(), 1024),
        ),  # Lance native vector index
        # === Timestamps ===
        ("created_at", pa.timestamp("ms")),
        ("updated_at", pa.timestamp("ms")),
    ]
)


def make_lance_storage_options() -> dict[str, str]:
    """Build S3-compatible storage_options dict for lancedb.connect().

    Uses lowercase keys matching the object_store 0.9+ convention.
    The "endpoint" key is correct for lancedb >= 0.6 / object_store 0.9;
    verified against lancedb==0.30.2.

    allow_http must be the string "true" (not a bool) as required by object_store.
    aws_region must be present even though MinIO ignores it (the AWS SDK
    validates its presence).
    """
    return {
        "aws_access_key_id": settings.MINIO_ROOT_USER,
        "aws_secret_access_key": settings.MINIO_ROOT_PASSWORD,
        # f"http://{settings.MINIO_ENDPOINT}" — same construction as s3.py.
        "endpoint": f"http://{settings.MINIO_ENDPOINT}",
        "aws_region": "us-east-1",
        "allow_http": "true",
    }


def get_or_create_chunks_table() -> Any:
    """Open or create the Lance chunks table at s3://{MINIO_LANCE_BUCKET}/chunks/.

    Connects to s3://{MINIO_LANCE_BUCKET}/chunks (no trailing slash) so that
    lancedb's internal _table_uri() resolves to:
        s3://{MINIO_LANCE_BUCKET}/chunks/chunks.lance/
    All S3 object keys therefore start with "chunks/", satisfying the V2
    verification criterion list_objects_v2(Bucket='lance', Prefix='chunks/').

    Uses exist_ok=True to atomically create-or-open: creates a 0-row
    schema-only dataset when absent; opens the existing dataset when present.
    This is synchronous (agreed.md D3 — Lance S3 I/O is sync from Python).
    """
    storage_options = make_lance_storage_options()
    # Connect to the bucket sub-path, not the bucket root.
    # The table "chunks" will be created at chunks/chunks.lance/ inside bucket.
    db_uri = f"s3://{settings.MINIO_LANCE_BUCKET}/chunks"
    db = lancedb.connect(db_uri, storage_options=storage_options)
    # exist_ok=True: creates empty schema-only table if absent, opens if present.
    return db.create_table("chunks", schema=CHUNKS_SCHEMA, exist_ok=True)
