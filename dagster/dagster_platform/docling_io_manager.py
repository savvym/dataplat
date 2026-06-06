"""docling_io_manager.py — Dagster IOManager for DoclingDocument MinIO writes (F-054).

Implements DoclingDocIOManager which owns all MinIO writes for the extract_mineru asset:
  - Writes doc.docling.json, then images/* (zero in MVP), then manifest.json LAST.
  - On any MinIO failure, best-effort cleans up keys written in the current call
    (tracks them in _written_keys) and re-raises.  The Postgres write
    (insert_document_variant) lives OUTSIDE the MinIO cleanup try/except so a
    Postgres failure cannot trigger MinIO cleanup — see contracts/S054-F-054/agreed.md §4.1.

Design references:
  - contracts/S054-F-054/agreed.md §3.2 / §4.1 — three-zone algorithm
  - design doc §4.3 — MinIO path layout
  - design doc §8.1 — IOManager table
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3  # type: ignore[import-untyped]
from dagster import InputContext, IOManager, MetadataValue, OutputContext

from dagster_platform.extractor import (
    CONFIG_HASH,
    DOCUMENTS_BUCKET,
    EXTRACTOR_VERSION,
    insert_document_variant,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SourceRef:
    """Reference to a source object in MinIO used as input for extraction.

    Records bucket + key + sha256 for lineage compliance (CLAUDE.md invariant #1).
    """

    bucket: str  # e.g. "sources"
    key: str  # e.g. "sources/42/original.pdf"
    sha256: str  # hex string from source.sha256 (Postgres)


@dataclass
class ImageBlob:
    """A single image file produced by an extractor (future use — empty in MVP).

    For MVP, the extract_mineru asset always returns DoclingDocOutput with images=[].
    The IOManager handles N > 0 images when later extractors supply them.
    """

    filename: str  # e.g. "0.png"
    data: bytes


@dataclass
class DoclingDocOutput:
    """Typed output dataclass returned by extract_mineru and consumed by DoclingDocIOManager.

    The IOManager performs ALL writes (MinIO + Postgres) when handle_output() is called.
    The asset body constructs and returns this object; it does NOT call any storage helpers
    directly (F-054 refactor — see definitions.py).
    """

    doc_json: str  # DoclingDocument JSON string (from build_docling_document())
    images: list[ImageBlob]  # Empty list for MVP; non-empty when extractor produces images
    source_refs: list[SourceRef]  # [{bucket, key, sha256}] — one entry for MVP
    source_id: int  # For the Postgres insert and storage key prefix
    page_count: int  # For the Postgres insert
    extractor_name: str  # e.g. "mineru" — used in the storage key prefix
    dagster_run_id: str  # context.run_id at materialization time


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_s3_client() -> Any:
    """Return a boto3 S3 client configured from MINIO_* environment variables.

    Mirrors build_s3_client() in extractor.py — kept separate here to avoid
    circular imports (docling_io_manager → extractor, never the reverse).
    """
    endpoint = os.environ["MINIO_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )


def _build_manifest(obj: DoclingDocOutput, created_at: str) -> bytes:
    """Build the manifest.json bytes from a DoclingDocOutput.

    Pure function — no side effects — kept separate so it is unit-testable
    without any S3/IOManager machinery.

    Lineage compliance (CLAUDE.md invariant #1):
      - source_refs  → records input object (SHA-256 + S3 URI)
      - extractor_name + extractor_version + config_hash  → processor identity
      - dagster_run_id  → ties the materialization to the Dagster run

    Schema version 1 (§3.5 of agreed.md); bump on any backwards-incompatible change.
    """
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "extractor_name": obj.extractor_name,
        "extractor_version": EXTRACTOR_VERSION,
        "config_hash": CONFIG_HASH,
        "dagster_run_id": obj.dagster_run_id,
        "created_at": created_at,
        "source_refs": [
            {"bucket": ref.bucket, "key": ref.key, "sha256": ref.sha256}
            for ref in obj.source_refs
        ],
        "images": [img.filename for img in obj.images],
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# IOManager
# ---------------------------------------------------------------------------


class DoclingDocIOManager(IOManager):
    """Dagster IOManager that owns all MinIO writes for extract_mineru (F-054).

    Write order within handle_output() (design doc §4.3 / agreed.md §4.1):
      1. {source_id}/extract_{name}/doc.docling.json       ← written first
      2. {source_id}/extract_{name}/images/{filename}      ← zero iterations MVP
      3. {source_id}/extract_{name}/manifest.json          ← written LAST (integrity sentinel)
      4. insert_document_variant() in Postgres             ← OUTSIDE MinIO cleanup block
      5. context.add_output_metadata()

    manifest.json written LAST is the integrity sentinel: if a consumer sees manifest.json,
    all prior objects are guaranteed to exist (barring concurrent cleanup races).

    Atomic failure semantics (Option A — see agreed.md §3.2):
      - MinIO write phase (steps 1–3) is wrapped in try/except.
      - On any exception, _written_keys tracks keys PUT in THIS call; cleanup iterates
        and calls delete_object on each (best-effort; failures are logged as WARNING).
      - insert_document_variant (step 4) is STRUCTURALLY OUTSIDE the try/except.
        A Postgres failure after successful MinIO writes does NOT trigger MinIO cleanup.
        On retry, put_object overwrites idempotently; ON CONFLICT DO NOTHING handles
        the DB row idempotency. This is intentional — see agreed.md §3.2 for rationale.
    """

    def handle_output(self, context: OutputContext, obj: DoclingDocOutput) -> None:
        """Write doc.docling.json + images/* + manifest.json to MinIO, then Postgres row.

        See class docstring for the write-order and atomic-failure guarantees.
        """
        prefix = f"{obj.source_id}/extract_{obj.extractor_name}"
        s3 = _build_s3_client()
        _written_keys: list[str] = []

        # ── Zone 1: MinIO write phase ────────────────────────────────────────
        # _written_keys is local to this call; concurrent runs' keys are never
        # in this list, so cleanup cannot affect them (agreed.md §3.2 / R10).
        try:
            # 5a: doc.docling.json — first
            key = f"{prefix}/doc.docling.json"
            s3.put_object(
                Bucket=DOCUMENTS_BUCKET,
                Key=key,
                Body=obj.doc_json.encode("utf-8"),
                ContentType="application/json",
            )
            _written_keys.append(key)
            logger.info(
                "DoclingDocIOManager: wrote doc.docling.json key=%r", key
            )

            # 5b: images/* — zero iterations for MVP; supports N > 0 for future extractors
            for img in obj.images:
                key = f"{prefix}/images/{img.filename}"
                s3.put_object(
                    Bucket=DOCUMENTS_BUCKET,
                    Key=key,
                    Body=img.data,
                )
                _written_keys.append(key)
                logger.info(
                    "DoclingDocIOManager: wrote image key=%r (%d bytes)",
                    key,
                    len(img.data),
                )

            # 5c: manifest.json — LAST (integrity sentinel; see class docstring)
            created_at = datetime.now(timezone.utc).isoformat()
            manifest_bytes = _build_manifest(obj, created_at)
            key = f"{prefix}/manifest.json"
            s3.put_object(
                Bucket=DOCUMENTS_BUCKET,
                Key=key,
                Body=manifest_bytes,
                ContentType="application/json",
            )
            _written_keys.append(key)
            logger.info(
                "DoclingDocIOManager: wrote manifest.json key=%r", key
            )

        except Exception:
            # Best-effort cleanup: delete keys PUT in this call before re-raising.
            # Failures are non-fatal — log and continue so the original exception propagates.
            # (R1/R9 in agreed.md §9 — orphans are non-canonical; future compaction handles them.)
            for written_key in _written_keys:
                try:
                    s3.delete_object(Bucket=DOCUMENTS_BUCKET, Key=written_key)
                    logger.info(
                        "DoclingDocIOManager: cleanup deleted key=%r", written_key
                    )
                except Exception as del_exc:
                    logger.warning(
                        "DoclingDocIOManager: cleanup delete failed key=%r: %s",
                        written_key,
                        del_exc,
                    )
            raise  # re-raise original — no Postgres write has occurred

        # ── Zone 2: Postgres write ────────────────────────────────────────────
        # OUTSIDE the MinIO cleanup try/except — a Postgres failure here does NOT
        # trigger MinIO cleanup.  Blobs remain; retry is idempotent via ON CONFLICT.
        insert_document_variant(
            source_id=obj.source_id,
            page_count=obj.page_count,
            run_id=obj.dagster_run_id,
        )
        logger.info(
            "DoclingDocIOManager: inserted document_variant source_id=%d run_id=%s",
            obj.source_id,
            obj.dagster_run_id,
        )

        # ── Zone 3: Dagster metadata ──────────────────────────────────────────
        context.add_output_metadata(
            {
                "source_id": MetadataValue.int(obj.source_id),
                "page_count": MetadataValue.int(obj.page_count),
                "image_count": MetadataValue.int(len(obj.images)),
                "storage_prefix": MetadataValue.text(
                    f"s3://documents/{obj.source_id}/extract_{obj.extractor_name}/"
                ),
                "manifest_key": MetadataValue.text(f"{prefix}/manifest.json"),
            }
        )

    def load_input(self, context: InputContext) -> None:
        raise NotImplementedError(
            "DoclingDocIOManager.load_input() is not implemented. "
            "Downstream processors read documents from MinIO directly."
        )
