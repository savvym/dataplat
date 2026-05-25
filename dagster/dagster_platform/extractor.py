"""extractor.py — pure helpers for the extract_mineru Dagster asset (F-019).

All functions are side-effect-free where possible, or have clearly bounded I/O.
Keeping these out of definitions.py makes unit-testing straightforward.

Design notes (agreed.md §3–§6):
- DocumentOrigin / binary_hash are NOT used: the binary_hash field truncates the
  sha256 to 64 bits, making it useless. The authoritative sha256 is on source.sha256.
- DB access via raw psycopg2 (already in the image via dagster-postgres); sync is fine
  outside apps/api/ — invariant #5 is scoped to apps/api/dataplat_api/.
- config_hash is a constant: sha256(json.dumps({}, sort_keys=True, separators=(',',':')))
  because the mineru operator default_config is {}.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import boto3  # type: ignore[import-untyped]
import psycopg2  # type: ignore[import-untyped]
from docling_core.types.doc.base import Size  # type: ignore[import-untyped]
from docling_core.types.doc.document import (  # type: ignore[import-untyped]
    DoclingDocument,
    PageItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXTRACTOR_NAME = "mineru"
EXTRACTOR_VERSION = "0.1.0"

# sha256 of canonical JSON of the operator config {} (default_config for mineru).
# Computed once: sha256(json.dumps({}, sort_keys=True, separators=(',',':')))
CONFIG_HASH: str = hashlib.sha256(
    json.dumps({}, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()

SOURCES_BUCKET = "sources"
DOCUMENTS_BUCKET = "documents"


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def build_s3_client() -> Any:
    """Return a boto3 S3 client configured from MINIO_* environment variables."""
    endpoint = os.environ["MINIO_ENDPOINT"]
    # MINIO_ENDPOINT is host:port (no scheme), consistent with FastAPI's s3.py pattern.
    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )


def read_pdf_bytes(s3: Any, source_id: int) -> bytes:
    """Fetch PDF bytes from MinIO for the given source_id.

    F-011 stores files at Bucket=sources, Key=sources/{source_id}/original.pdf
    (the bucket name is also prefixed into the key — this is the established pattern).

    Raises RuntimeError if the object does not exist or cannot be fetched.
    """
    # Key includes "sources/" prefix: verified against live MinIO (F-011 upload pattern).
    key = f"sources/{source_id}/original.pdf"
    try:
        resp = s3.get_object(Bucket=SOURCES_BUCKET, Key=key)
        return resp["Body"].read()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to read s3://{SOURCES_BUCKET}/{key}: {exc}"
        ) from exc


def write_document_json(s3: Any, source_id: int, doc_json: str) -> None:
    """Write doc_json to s3://documents/{source_id}/extract_mineru/doc.docling.json."""
    key = f"{source_id}/extract_mineru/doc.docling.json"
    s3.put_object(
        Bucket=DOCUMENTS_BUCKET,
        Key=key,
        Body=doc_json.encode("utf-8"),
        ContentType="application/json",
    )


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------


def estimate_page_count(pdf_bytes: bytes) -> int:
    """Best-effort page count from PDF bytes via /Count N regex.

    Returns 0 on any failure (malformed PDF, no /Count field, etc.).
    This keeps the asset working even for the synthetic minimal PDF fixture.
    """
    try:
        match = re.search(rb"/Count\s+(\d+)", pdf_bytes)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# DoclingDocument helpers
# ---------------------------------------------------------------------------


def build_docling_document(
    source_id: int,
    pdf_bytes: bytes,  # noqa: ARG001  — kept for signature consistency; unused here
    page_count: int,
) -> str:
    """Produce a minimal schema-valid DoclingDocument JSON string.

    DocumentOrigin/binary_hash are intentionally omitted: binary_hash truncates
    the sha256 to 64 bits (useless integrity check).  The authoritative sha256
    lives on source.sha256 (F-011).

    Returns model_dump_json() output — guaranteed valid per docling-core's schema.
    """
    doc = DoclingDocument(name=f"source_{source_id}")
    for page_no in range(1, page_count + 1):
        doc.pages[page_no] = PageItem(
            page_no=page_no, size=Size(width=612.0, height=792.0)
        )
    return doc.model_dump_json()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def insert_document_variant(
    source_id: int,
    page_count: int,
    run_id: str,
) -> None:
    """Insert a document_variant row into the platform Postgres DB.

    Uses raw psycopg2 (already installed in the Dagster image via dagster-postgres).
    The full logic:
      1. Open a transaction.
      2. Check whether a canonical variant already exists for this source_id.
      3. INSERT … ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING
         so re-materialisation does not crash.
      4. Commit.

    is_canonical is set TRUE only if no canonical row exists yet for the source,
    satisfying the idx_doc_canonical partial-unique index constraint.
    """
    db_url = os.environ["PLATFORM_DB_URL"]
    storage_prefix = f"s3://documents/{source_id}/extract_mineru/"

    conn = psycopg2.connect(db_url)
    try:
        with conn:  # transaction context — commits on exit, rolls back on exception
            with conn.cursor() as cur:
                # Check for an existing canonical variant for this source.
                cur.execute(
                    "SELECT COUNT(*) FROM document_variant "
                    "WHERE source_id = %s AND is_canonical = TRUE",
                    (source_id,),
                )
                row = cur.fetchone()
                is_canonical = (row[0] == 0) if row else True

                cur.execute(
                    """
                    INSERT INTO document_variant
                        (source_id, extractor_name, extractor_version, config_hash,
                         storage_prefix, page_count, image_count, is_canonical,
                         dagster_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
                    ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING
                    """,
                    (
                        source_id,
                        EXTRACTOR_NAME,
                        EXTRACTOR_VERSION,
                        CONFIG_HASH,
                        storage_prefix,
                        page_count,
                        is_canonical,
                        run_id,
                    ),
                )
    finally:
        conn.close()
