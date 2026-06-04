"""Unit tests for hf_dataset_io_manager.py (F-043).

Tests cover HFDatasetIOManager.handle_output():
  - All four S3 objects are uploaded (train Parquet, val Parquet, recipe.json, README.md)
  - Parquet column schema includes "instruction", "output", "chunk_id"
  - recipe.json content matches the serialised recipe_snapshot
  - Empty train/val produces valid (zero-row) Parquet files
  - Correct S3 key prefix pattern: {dataset_id}_{version_tag}/...

No real S3/MinIO connections; boto3.client is mocked throughout.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_hf_dataset_io_manager.py -v
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow.parquet as pq
import pytest

from dagster_platform.hf_dataset_io_manager import HFDatasetIOManager, _rows_to_parquet_bytes
from dagster_platform.sft_synthesis_qa import DatasetOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dataset_output(
    train_rows: list[dict[str, Any]] | None = None,
    val_rows: list[dict[str, Any]] | None = None,
    recipe_snapshot: dict[str, Any] | None = None,
    dataset_id: int = 7,
    version_tag: str = "v1",
) -> DatasetOutput:
    """Build a DatasetOutput with sensible defaults for testing."""
    if train_rows is None:
        train_rows = [
            {"instruction": "What is Python?", "output": "A programming language.", "chunk_id": "c001"},
            {"instruction": "What is Rust?", "output": "A systems language.", "chunk_id": "c002"},
        ]
    if val_rows is None:
        val_rows = [
            {"instruction": "What is Go?", "output": "A language by Google.", "chunk_id": "c003"},
        ]
    if recipe_snapshot is None:
        recipe_snapshot = {
            "filter": {"where": "attr_quality_score > 0.7"},
            "schema": {"template": "sft_synthesis_qa"},
        }
    return DatasetOutput(
        train_rows=train_rows,
        val_rows=val_rows,
        recipe_snapshot=recipe_snapshot,
        dataset_id=dataset_id,
        version_tag=version_tag,
    )


def _mock_output_context() -> MagicMock:
    """Build a minimal mock for Dagster OutputContext."""
    ctx = MagicMock()
    ctx.log = MagicMock()
    ctx.add_output_metadata = MagicMock()
    return ctx


def _run_handle_output(
    obj: DatasetOutput,
    monkeypatch: pytest.MonkeyPatch,
    bucket: str = "datasets",
) -> MagicMock:
    """Run HFDatasetIOManager.handle_output() with mocked S3 and env vars.

    Returns the mock S3 client so callers can assert on put_object calls.
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", bucket)

    mock_s3 = MagicMock()
    ctx = _mock_output_context()

    with patch("boto3.client", return_value=mock_s3):
        manager = HFDatasetIOManager()
        manager.handle_output(ctx, obj)

    return mock_s3


def _get_put_object_bodies(mock_s3: MagicMock) -> dict[str, bytes]:
    """Extract {Key: Body} from all put_object calls on the mock S3 client."""
    result: dict[str, bytes] = {}
    for c in mock_s3.put_object.call_args_list:
        # All put_object calls use keyword arguments only (Bucket=, Key=, Body=).
        key: str | None = c.kwargs.get("Key")
        if key is None and len(c.args) > 1:
            key = c.args[1]
        body = c.kwargs.get("Body")
        if body is None and len(c.args) > 2:
            body = c.args[2]
        if key is not None and body is not None:
            result[key] = body if isinstance(body, bytes) else body.encode("utf-8")
    return result


# ---------------------------------------------------------------------------
# V1 — Parquet files exist in MinIO at the expected path
# ---------------------------------------------------------------------------


def test_handle_output_uploads_parquet(monkeypatch: pytest.MonkeyPatch) -> None:
    """put_object called for train-00000.parquet and validation-00000.parquet."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    # Extract all Keys from put_object calls
    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    assert "7_v1/data/train-00000.parquet" in keys, f"train key missing; got {keys}"
    assert "7_v1/data/validation-00000.parquet" in keys, f"val key missing; got {keys}"


def test_handle_output_correct_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """All put_object calls use the correct bucket name."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch, bucket="datasets")

    for c in mock_s3.put_object.call_args_list:
        bucket_used = c.kwargs.get("Bucket")
        assert bucket_used == "datasets", f"Unexpected bucket: {bucket_used!r}"


def test_handle_output_custom_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIO_DATASETS_BUCKET env var overrides the default 'datasets' bucket."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch, bucket="my-custom-bucket")

    for c in mock_s3.put_object.call_args_list:
        bucket_used = c.kwargs.get("Bucket")
        assert bucket_used == "my-custom-bucket"


# ---------------------------------------------------------------------------
# V2 — Parquet columns include instruction, output, chunk_id
# ---------------------------------------------------------------------------


def test_parquet_columns_instruction_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Train and val Parquet bytes contain 'instruction', 'output', 'chunk_id' columns."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)

    for key_suffix in ["7_v1/data/train-00000.parquet", "7_v1/data/validation-00000.parquet"]:
        assert key_suffix in bodies, f"Expected key {key_suffix!r} not found in {list(bodies)}"
        parquet_bytes = bodies[key_suffix]
        table = pq.read_table(io.BytesIO(parquet_bytes))
        schema_names = table.schema.names
        assert "instruction" in schema_names, f"'instruction' missing from {key_suffix}"
        assert "output" in schema_names, f"'output' missing from {key_suffix}"
        assert "chunk_id" in schema_names, f"'chunk_id' missing from {key_suffix}"


def test_parquet_row_count_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parquet row count equals the number of rows in train/val lists."""
    train = [{"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i:03d}"} for i in range(5)]
    val = [{"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i+5:03d}"} for i in range(2)]
    obj = _make_dataset_output(train_rows=train, val_rows=val, dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)

    train_table = pq.read_table(io.BytesIO(bodies["7_v1/data/train-00000.parquet"]))
    val_table = pq.read_table(io.BytesIO(bodies["7_v1/data/validation-00000.parquet"]))

    assert train_table.num_rows == 5
    assert val_table.num_rows == 2


def test_parquet_empty_rows_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-row train/val → valid Parquet files with the correct schema (D5 decision)."""
    obj = _make_dataset_output(train_rows=[], val_rows=[], dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)

    for key in ["7_v1/data/train-00000.parquet", "7_v1/data/validation-00000.parquet"]:
        table = pq.read_table(io.BytesIO(bodies[key]))
        assert table.num_rows == 0
        assert "instruction" in table.schema.names
        assert "output" in table.schema.names
        assert "chunk_id" in table.schema.names


# ---------------------------------------------------------------------------
# V3 — README.md and recipe.json exist alongside the Parquet files
# ---------------------------------------------------------------------------


def test_handle_output_uploads_readme_and_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """put_object called for README.md and recipe.json."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    assert "7_v1/README.md" in keys, f"README.md missing; got {keys}"
    assert "7_v1/recipe.json" in keys, f"recipe.json missing; got {keys}"


def test_handle_output_total_four_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly four S3 objects are uploaded per materialization."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    assert mock_s3.put_object.call_count == 4, (
        f"Expected 4 put_object calls, got {mock_s3.put_object.call_count}"
    )


# ---------------------------------------------------------------------------
# V4 — recipe.json content matches the serialised recipe_snapshot
# ---------------------------------------------------------------------------


def test_recipe_json_matches_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """recipe.json Body equals json.dumps(recipe_snapshot)."""
    recipe_snapshot = {
        "filter": {"where": "attr_quality_score > 0.7"},
        "schema": {"template": "sft_synthesis_qa"},
        "output": {"splits": {"validation": 0.15}},
    }
    obj = _make_dataset_output(recipe_snapshot=recipe_snapshot, dataset_id=7, version_tag="v1")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    assert "7_v1/recipe.json" in bodies

    parsed = json.loads(bodies["7_v1/recipe.json"])
    assert parsed == recipe_snapshot


def test_recipe_json_is_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """recipe.json is valid UTF-8 JSON (not truncated, not double-encoded)."""
    complex_snapshot = {
        "filter": None,
        "unicode": "日本語テスト",
        "nested": {"a": [1, 2, 3]},
    }
    obj = _make_dataset_output(recipe_snapshot=complex_snapshot, dataset_id=3, version_tag="v2")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["3_v2/recipe.json"])
    assert parsed == complex_snapshot


# ---------------------------------------------------------------------------
# Prefix pattern tests
# ---------------------------------------------------------------------------


def test_key_prefix_uses_dataset_id_version_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3 key prefix is '{dataset_id}_{version_tag}/' for all objects."""
    obj = _make_dataset_output(dataset_id=42, version_tag="v3")
    mock_s3 = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]
    for key in keys:
        assert key.startswith("42_v3/"), f"Key {key!r} does not start with '42_v3/'"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_rows_to_parquet_bytes_schema() -> None:
    """_rows_to_parquet_bytes produces bytes with the correct schema."""
    rows = [
        {"instruction": "Q1", "output": "A1", "chunk_id": "c001"},
        {"instruction": "Q2", "output": "A2", "chunk_id": "c002"},
    ]
    parquet_bytes = _rows_to_parquet_bytes(rows)
    table = pq.read_table(io.BytesIO(parquet_bytes))
    assert set(table.schema.names) == {"instruction", "output", "chunk_id"}
    assert table.num_rows == 2


def test_rows_to_parquet_bytes_empty() -> None:
    """_rows_to_parquet_bytes with empty list → zero-row Parquet with schema."""
    parquet_bytes = _rows_to_parquet_bytes([])
    table = pq.read_table(io.BytesIO(parquet_bytes))
    assert table.num_rows == 0
    assert "instruction" in table.schema.names
    assert "output" in table.schema.names
    assert "chunk_id" in table.schema.names


# ---------------------------------------------------------------------------
# load_input not implemented
# ---------------------------------------------------------------------------


def test_load_input_raises() -> None:
    """load_input() raises NotImplementedError."""
    manager = HFDatasetIOManager()
    ctx = MagicMock()
    with pytest.raises(NotImplementedError, match="load_input"):
        manager.load_input(ctx)
