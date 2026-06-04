"""hf_dataset_io_manager.py — Dagster IOManager for dataset Parquet writes (F-043/F-044).

Receives a `DatasetOutput` from the `dataset` asset, serialises train/val rows to
PyArrow Parquet tables, and uploads five objects to MinIO under
s3://datasets/{dataset_id}_{version_tag}/:
    data/train-00000.parquet      — train split
    data/validation-00000.parquet — validation split
    recipe.json                   — json.dumps(recipe_snapshot)
    README.md                     — dataset card (obj.dataset_card_md or stub)
    dataset_infos.json            — HuggingFace DatasetInfo registry (F-044)

After all five uploads succeed, updates the Postgres dataset row:
    status='done', sample_count, size_bytes, materialized_at=NOW()
    (WHERE id=%s AND status='pending' — idempotent; 0-row UPDATE on re-run)

Hard invariant #2 compliance: Parquet bytes go to MinIO under a deterministic
user-facing path (NOT CAS-addressed — these are user-facing artifacts per design
doc §4.3). recipe_snapshot metadata stays in Postgres (no blob bytes written there).

Hard invariant #4 compliance: No 'import anthropic', no 'import openai', no direct
LLM SDK usage anywhere in this file.

Deferred (F-047): MINIO_DATASETS_BUCKET in FastAPI Settings (apps/api/dataplat_api/config.py).
The Dagster layer reads MINIO_DATASETS_BUCKET from os.environ directly with a 'datasets' default.
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import boto3  # type: ignore[import-untyped]
import botocore.exceptions  # type: ignore[import-untyped]
import psycopg2  # type: ignore[import-untyped]
import pyarrow as pa
import pyarrow.parquet as pq
from dagster import InputContext, IOManager, MetadataValue, OutputContext

from dagster_platform.sft_synthesis_qa import DatasetOutput, update_dataset_row

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S3 client helper (mirrors extractor.py:build_s3_client())
# ---------------------------------------------------------------------------


def _build_s3_client() -> Any:
    """Return a boto3 S3 client configured from MINIO_* environment variables.

    Mirrors extractor.py:build_s3_client() exactly. MINIO_ENDPOINT is host:port
    (no scheme), consistent with FastAPI's s3.py pattern.
    """
    endpoint = os.environ["MINIO_ENDPOINT"]
    return boto3.client(
        "s3",
        endpoint_url=f"http://{endpoint}",
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    )


# ---------------------------------------------------------------------------
# Parquet schema (agreed.md §"DatasetOutput dataclass" + D6)
# ---------------------------------------------------------------------------

DATASET_SCHEMA = pa.schema(
    [
        ("instruction", pa.string()),
        ("output", pa.string()),
        ("chunk_id", pa.string()),
    ]
)


def _rows_to_parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Serialise a list of dicts to Parquet bytes using DATASET_SCHEMA.

    Args:
        rows: List of dicts with keys "instruction", "output", "chunk_id".
              An empty list produces a valid zero-row Parquet file (D5 decision).

    Returns:
        Parquet-encoded bytes.
    """
    pa_table = pa.Table.from_pylist(rows, schema=DATASET_SCHEMA)
    buf = io.BytesIO()
    pq.write_table(pa_table, buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# dataset_infos.json builder (F-044)
# ---------------------------------------------------------------------------


def _build_dataset_infos(
    train_bytes: bytes,
    val_bytes: bytes,
    train_count: int,
    val_count: int,
) -> bytes:
    """Build the dataset_infos.json bytes for the HuggingFace Datasets library.

    Produces a minimal subset of the HF DatasetInfo registry schema that is
    sufficient for datasets.load_dataset() to consume. Uses the 'default' config
    key (HF convention for single-config datasets).

    Args:
        train_bytes:  Parquet bytes for the train split (used for num_bytes).
        val_bytes:    Parquet bytes for the validation split (used for num_bytes).
        train_count:  Number of rows in the train split.
        val_count:    Number of rows in the validation split.

    Returns:
        UTF-8-encoded JSON bytes.
    """
    total_bytes = len(train_bytes) + len(val_bytes)
    dataset_infos: dict[str, Any] = {
        "default": {
            "description": "",
            "citation": "",
            "homepage": "",
            "license": "",
            "features": {
                "instruction": {"dtype": "string", "_type": "Value"},
                "output": {"dtype": "string", "_type": "Value"},
                "chunk_id": {"dtype": "string", "_type": "Value"},
            },
            "splits": {
                "train": {
                    "name": "train",
                    "num_bytes": len(train_bytes),
                    "num_examples": train_count,
                    "dataset_name": "default",
                },
                "validation": {
                    "name": "validation",
                    "num_bytes": len(val_bytes),
                    "num_examples": val_count,
                    "dataset_name": "default",
                },
            },
            "download_size": total_bytes,
            "dataset_size": total_bytes,
        }
    }
    return json.dumps(dataset_infos, ensure_ascii=False, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# IOManager
# ---------------------------------------------------------------------------


class HFDatasetIOManager(IOManager):
    """IOManager that writes dataset artifacts to MinIO for the `dataset` asset (F-043/F-044).

    Receives a `DatasetOutput` from the asset (train_rows, val_rows, recipe_snapshot,
    dataset_id, version_tag, dataset_card_md) and uploads five objects to:
        s3://datasets/{dataset_id}_{version_tag}/data/train-00000.parquet
        s3://datasets/{dataset_id}_{version_tag}/data/validation-00000.parquet
        s3://datasets/{dataset_id}_{version_tag}/recipe.json
        s3://datasets/{dataset_id}_{version_tag}/README.md
        s3://datasets/{dataset_id}_{version_tag}/dataset_infos.json

    After all five uploads succeed, updates the Postgres dataset row:
        status='done', sample_count=len(train_rows)+len(val_rows),
        size_bytes=len(train_parquet_bytes)+len(val_parquet_bytes),
        materialized_at=NOW()
    (WHERE id=%s AND status='pending' for idempotency — 0-row UPDATE on re-run).

    Failure semantics: if any put_object or update_dataset_row raises, the error is
    logged via context.log.error() and re-raised. The dataset row stays at
    status='pending'. Operator-level retry re-runs all 5 uploads (idempotent overwrite)
    and the UPDATE (safe with AND status='pending').

    Note: load_input() is not implemented — downstream processors read MinIO directly.
    """

    def handle_output(self, context: OutputContext, obj: DatasetOutput) -> None:
        """Write train/val Parquet files, recipe.json, README.md, and dataset_infos.json
        to MinIO, then update the Postgres dataset row to status='done'.

        Steps:
          1. Serialise obj.train_rows → Parquet bytes via _rows_to_parquet_bytes().
          2. Serialise obj.val_rows → Parquet bytes.
          3. Build dataset_infos.json bytes via _build_dataset_infos().
          4. Build boto3 S3 client from MINIO_* env (mirrors extractor.py).
          5. Compute bucket (MINIO_DATASETS_BUCKET env, default "datasets") and
             prefix ("{dataset_id}_{version_tag}").
          6. Upload five objects via put_object (wrapped in try/except ClientError):
             a. {prefix}/data/train-00000.parquet
             b. {prefix}/data/validation-00000.parquet
             c. {prefix}/recipe.json
             d. {prefix}/README.md  (uses obj.dataset_card_md if set, else stub)
             e. {prefix}/dataset_infos.json
          7. Compute sample_count and size_bytes.
          8. Call update_dataset_row(dataset_id, sample_count, size_bytes).
          9. Record IO-level metadata via context.add_output_metadata().

        On botocore.exceptions.ClientError or psycopg2.Error:
            context.log.error() is called with structured details, then the exception
            is re-raised. The dataset row stays at status='pending'.

        Args:
            context: Dagster OutputContext (provides logging + add_output_metadata).
            obj:     DatasetOutput instance from the `dataset` asset.
        """
        train_bytes = _rows_to_parquet_bytes(obj.train_rows)
        val_bytes = _rows_to_parquet_bytes(obj.val_rows)

        dataset_infos_bytes = _build_dataset_infos(
            train_bytes,
            val_bytes,
            len(obj.train_rows),
            len(obj.val_rows),
        )

        recipe_json_bytes = json.dumps(
            obj.recipe_snapshot, ensure_ascii=False, indent=2
        ).encode("utf-8")

        prefix = f"{obj.dataset_id}_{obj.version_tag}"

        # Use dataset_card_md if provided, fall back to stub string.
        if obj.dataset_card_md is not None:
            readme_content = obj.dataset_card_md
        else:
            readme_content = f"# Dataset {prefix}\n\nGenerated by sft_synthesis_qa.\n"

        bucket = os.environ.get("MINIO_DATASETS_BUCKET", "datasets")

        s3 = _build_s3_client()

        context.log.info("HFDatasetIOManager: uploading to s3://%s/%s/", bucket, prefix)

        try:
            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/data/train-00000.parquet",
                Body=train_bytes,
            )
            logger.info(
                "HFDatasetIOManager: uploaded train-00000.parquet (%d rows, %d bytes)",
                len(obj.train_rows),
                len(train_bytes),
            )

            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/data/validation-00000.parquet",
                Body=val_bytes,
            )
            logger.info(
                "HFDatasetIOManager: uploaded validation-00000.parquet (%d rows, %d bytes)",
                len(obj.val_rows),
                len(val_bytes),
            )

            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/recipe.json",
                Body=recipe_json_bytes,
            )
            logger.info(
                "HFDatasetIOManager: uploaded recipe.json (%d bytes)",
                len(recipe_json_bytes),
            )

            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/README.md",
                Body=readme_content.encode("utf-8"),
            )
            logger.info("HFDatasetIOManager: uploaded README.md")

            s3.put_object(
                Bucket=bucket,
                Key=f"{prefix}/dataset_infos.json",
                Body=dataset_infos_bytes,
            )
            logger.info(
                "HFDatasetIOManager: uploaded dataset_infos.json (%d bytes)",
                len(dataset_infos_bytes),
            )

            sample_count: int = len(obj.train_rows) + len(obj.val_rows)
            size_bytes: int = len(train_bytes) + len(val_bytes)

            update_dataset_row(obj.dataset_id, sample_count, size_bytes)
            logger.info(
                "HFDatasetIOManager: dataset row updated to status='done' "
                "(dataset_id=%d sample_count=%d size_bytes=%d)",
                obj.dataset_id,
                sample_count,
                size_bytes,
            )

        except (botocore.exceptions.ClientError, psycopg2.Error) as exc:
            context.log.error(
                "HFDatasetIOManager: upload or DB update failed "
                "(dataset_id=%d, prefix=%r): %s",
                obj.dataset_id,
                prefix,
                exc,
            )
            raise

        context.add_output_metadata(
            {
                "train_rows": MetadataValue.int(len(obj.train_rows)),
                "val_rows": MetadataValue.int(len(obj.val_rows)),
                "sample_count": MetadataValue.int(sample_count),
                "size_bytes": MetadataValue.int(size_bytes),
                "dataset_uri": MetadataValue.text(f"s3://{bucket}/{prefix}/"),
                "dataset_status": MetadataValue.text("done"),
            }
        )
        context.log.info(
            "HFDatasetIOManager: done — dataset_uri=s3://%s/%s/ "
            "train=%d val=%d sample_count=%d size_bytes=%d",
            bucket,
            prefix,
            len(obj.train_rows),
            len(obj.val_rows),
            sample_count,
            size_bytes,
        )

    def load_input(self, context: InputContext) -> None:
        """Not implemented — downstream processors read MinIO directly.

        Raises NotImplementedError with a descriptive message.
        """
        raise NotImplementedError(
            "HFDatasetIOManager.load_input() is not implemented. "
            "Downstream processors read dataset artifacts from MinIO directly."
        )
