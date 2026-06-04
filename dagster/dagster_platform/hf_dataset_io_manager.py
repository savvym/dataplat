"""hf_dataset_io_manager.py — Dagster IOManager for dataset Parquet writes (F-043).

Receives a `DatasetOutput` from the `dataset` asset, serialises train/val rows to
PyArrow Parquet tables, and uploads four objects to MinIO under
s3://datasets/{dataset_id}_{version_tag}/:
    data/train-00000.parquet      — train split
    data/validation-00000.parquet — validation split
    recipe.json                   — json.dumps(recipe_snapshot)
    README.md                     — dataset card stub

Hard invariant #2 compliance: Parquet bytes go to MinIO under a deterministic
user-facing path (NOT CAS-addressed — these are user-facing artifacts per design
doc §4.3). recipe_snapshot metadata stays in Postgres (no blob bytes written there).

Hard invariant #4 compliance: No 'import anthropic', no 'import openai', no direct
LLM SDK usage anywhere in this file.

Deferred (F-044): dataset row status update (status='done', sample_count, size_bytes,
materialized_at). This IOManager intentionally does NOT update the dataset Postgres row.

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
import pyarrow as pa
import pyarrow.parquet as pq
from dagster import InputContext, IOManager, MetadataValue, OutputContext

from dagster_platform.sft_synthesis_qa import DatasetOutput

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
# IOManager
# ---------------------------------------------------------------------------


class HFDatasetIOManager(IOManager):
    """IOManager that writes dataset artifacts to MinIO for the `dataset` asset (F-043).

    Receives a `DatasetOutput` from the asset (train_rows, val_rows, recipe_snapshot,
    dataset_id, version_tag) and uploads four objects to:
        s3://datasets/{dataset_id}_{version_tag}/data/train-00000.parquet
        s3://datasets/{dataset_id}_{version_tag}/data/validation-00000.parquet
        s3://datasets/{dataset_id}_{version_tag}/recipe.json
        s3://datasets/{dataset_id}_{version_tag}/README.md

    Note: dataset row status update (status='done', sample_count, size_bytes) is
    intentionally deferred to F-044. This IOManager only handles MinIO writes.

    Note: load_input() is not implemented — downstream processors read MinIO directly.
    """

    def handle_output(self, context: OutputContext, obj: DatasetOutput) -> None:
        """Write train/val Parquet files, recipe.json, and README.md to MinIO.

        Steps:
          1. Serialise obj.train_rows → Parquet bytes via _rows_to_parquet_bytes().
          2. Serialise obj.val_rows → Parquet bytes.
          3. Build boto3 S3 client from MINIO_* env (mirrors extractor.py).
          4. Compute bucket (MINIO_DATASETS_BUCKET env, default "datasets") and
             prefix ("{dataset_id}_{version_tag}").
          5. Upload four objects via put_object.
          6. Record IO-level metadata via context.add_output_metadata().

        Args:
            context: Dagster OutputContext (provides logging + add_output_metadata).
            obj:     DatasetOutput instance from the `dataset` asset.
        """
        train_bytes = _rows_to_parquet_bytes(obj.train_rows)
        val_bytes = _rows_to_parquet_bytes(obj.val_rows)

        recipe_json_bytes = json.dumps(
            obj.recipe_snapshot, ensure_ascii=False, indent=2
        ).encode("utf-8")

        prefix = f"{obj.dataset_id}_{obj.version_tag}"
        readme_content = f"# Dataset {prefix}\n\nGenerated by sft_synthesis_qa.\n"

        bucket = os.environ.get("MINIO_DATASETS_BUCKET", "datasets")

        s3 = _build_s3_client()

        context.log.info(
            "HFDatasetIOManager: uploading to s3://%s/%s/", bucket, prefix
        )

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
            "HFDatasetIOManager: uploaded recipe.json (%d bytes)", len(recipe_json_bytes)
        )

        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}/README.md",
            Body=readme_content.encode("utf-8"),
        )
        logger.info("HFDatasetIOManager: uploaded README.md")

        context.add_output_metadata(
            {
                "train_rows": MetadataValue.int(len(obj.train_rows)),
                "val_rows": MetadataValue.int(len(obj.val_rows)),
                "dataset_uri": MetadataValue.text(f"s3://{bucket}/{prefix}/"),
            }
        )
        context.log.info(
            "HFDatasetIOManager: done — dataset_uri=s3://%s/%s/ "
            "train=%d val=%d",
            bucket,
            prefix,
            len(obj.train_rows),
            len(obj.val_rows),
        )

    def load_input(self, context: InputContext) -> None:
        """Not implemented — downstream processors read MinIO directly.

        Raises NotImplementedError with a descriptive message.
        """
        raise NotImplementedError(
            "HFDatasetIOManager.load_input() is not implemented. "
            "Downstream processors read dataset artifacts from MinIO directly."
        )
