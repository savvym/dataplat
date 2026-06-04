"""Unit tests for hf_dataset_io_manager.py (F-043/F-044).

Tests cover HFDatasetIOManager.handle_output():
  - All five S3 objects are uploaded (train Parquet, val Parquet, recipe.json,
    README.md, dataset_infos.json)
  - Parquet column schema includes "instruction", "output", "chunk_id"
  - recipe.json content matches the serialised recipe_snapshot
  - dataset_infos.json content matches HF DatasetInfo registry schema
  - README.md uses dataset_card_md when available, falls back to stub
  - Postgres dataset row is updated to status='done' after all uploads
  - DB update is NOT called if any put_object fails
  - Empty train/val produces valid (zero-row) Parquet files
  - Correct S3 key prefix pattern: {dataset_id}_{version_tag}/...

No real S3/MinIO connections; boto3.client is mocked throughout.
No real Postgres; psycopg2 is mocked via patch("dagster_platform.sft_synthesis_qa.psycopg2").

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_hf_dataset_io_manager.py -v
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import botocore.exceptions  # type: ignore[import-untyped]
import pyarrow.parquet as pq
import pytest

from dagster_platform.hf_dataset_io_manager import (
    HFDatasetIOManager,
    _build_dataset_infos,
    _rows_to_parquet_bytes,
)
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
            {
                "instruction": "What is Python?",
                "output": "A programming language.",
                "chunk_id": "c001",
            },
            {
                "instruction": "What is Rust?",
                "output": "A systems language.",
                "chunk_id": "c002",
            },
        ]
    if val_rows is None:
        val_rows = [
            {
                "instruction": "What is Go?",
                "output": "A language by Google.",
                "chunk_id": "c003",
            },
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
    mock_update_db: bool = True,
) -> tuple[MagicMock, MagicMock]:
    """Run HFDatasetIOManager.handle_output() with mocked S3 and env vars.

    Returns (mock_s3, mock_update_dataset_row) so callers can assert on both.

    Args:
        obj:             DatasetOutput to pass to handle_output.
        monkeypatch:     pytest MonkeyPatch fixture.
        bucket:          MINIO_DATASETS_BUCKET env value.
        mock_update_db:  If True (default), patch update_dataset_row to a no-op mock.
                         Set False to let the DB call execute (or raise naturally).
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", bucket)
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    mock_s3 = MagicMock()
    ctx = _mock_output_context()
    mock_update = MagicMock()

    if mock_update_db:
        with (
            patch("boto3.client", return_value=mock_s3),
            patch(
                "dagster_platform.hf_dataset_io_manager.update_dataset_row",
                mock_update,
            ),
        ):
            manager = HFDatasetIOManager()
            manager.handle_output(ctx, obj)
    else:
        with patch("boto3.client", return_value=mock_s3):
            manager = HFDatasetIOManager()
            manager.handle_output(ctx, obj)

    return mock_s3, mock_update


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
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    # Extract all Keys from put_object calls
    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    assert "7_v1/data/train-00000.parquet" in keys, f"train key missing; got {keys}"
    assert "7_v1/data/validation-00000.parquet" in keys, f"val key missing; got {keys}"


def test_handle_output_correct_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """All put_object calls use the correct bucket name."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch, bucket="datasets")

    for c in mock_s3.put_object.call_args_list:
        bucket_used = c.kwargs.get("Bucket")
        assert bucket_used == "datasets", f"Unexpected bucket: {bucket_used!r}"


def test_handle_output_custom_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINIO_DATASETS_BUCKET env var overrides the default 'datasets' bucket."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch, bucket="my-custom-bucket")

    for c in mock_s3.put_object.call_args_list:
        bucket_used = c.kwargs.get("Bucket")
        assert bucket_used == "my-custom-bucket"


# ---------------------------------------------------------------------------
# V2 — Parquet columns include instruction, output, chunk_id
# ---------------------------------------------------------------------------


def test_parquet_columns_instruction_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Train and val Parquet bytes contain 'instruction', 'output', 'chunk_id' columns."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)

    for key_suffix in [
        "7_v1/data/train-00000.parquet",
        "7_v1/data/validation-00000.parquet",
    ]:
        assert key_suffix in bodies, (
            f"Expected key {key_suffix!r} not found in {list(bodies)}"
        )
        parquet_bytes = bodies[key_suffix]
        table = pq.read_table(io.BytesIO(parquet_bytes))
        schema_names = table.schema.names
        assert "instruction" in schema_names, f"'instruction' missing from {key_suffix}"
        assert "output" in schema_names, f"'output' missing from {key_suffix}"
        assert "chunk_id" in schema_names, f"'chunk_id' missing from {key_suffix}"


def test_parquet_row_count_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parquet row count equals the number of rows in train/val lists."""
    train = [
        {"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i:03d}"}
        for i in range(5)
    ]
    val = [
        {"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i + 5:03d}"}
        for i in range(2)
    ]
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=7, version_tag="v1"
    )
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)

    train_table = pq.read_table(io.BytesIO(bodies["7_v1/data/train-00000.parquet"]))
    val_table = pq.read_table(io.BytesIO(bodies["7_v1/data/validation-00000.parquet"]))

    assert train_table.num_rows == 5
    assert val_table.num_rows == 2


def test_parquet_empty_rows_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero-row train/val → valid Parquet files with the correct schema (D5 decision)."""
    obj = _make_dataset_output(
        train_rows=[], val_rows=[], dataset_id=7, version_tag="v1"
    )
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

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


def test_handle_output_uploads_readme_and_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """put_object called for README.md and recipe.json."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    assert "7_v1/README.md" in keys, f"README.md missing; got {keys}"
    assert "7_v1/recipe.json" in keys, f"recipe.json missing; got {keys}"


def test_handle_output_total_five_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exactly five S3 objects are uploaded per materialization (F-044 adds dataset_infos.json)."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    assert mock_s3.put_object.call_count == 5, (
        f"Expected 5 put_object calls, got {mock_s3.put_object.call_count}"
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
    obj = _make_dataset_output(
        recipe_snapshot=recipe_snapshot, dataset_id=7, version_tag="v1"
    )
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

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
    obj = _make_dataset_output(
        recipe_snapshot=complex_snapshot, dataset_id=3, version_tag="v2"
    )
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["3_v2/recipe.json"])
    assert parsed == complex_snapshot


# ---------------------------------------------------------------------------
# Prefix pattern tests
# ---------------------------------------------------------------------------


def test_key_prefix_uses_dataset_id_version_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3 key prefix is '{dataset_id}_{version_tag}/' for all objects."""
    obj = _make_dataset_output(dataset_id=42, version_tag="v3")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

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


# ---------------------------------------------------------------------------
# F-044 — V2: dataset_infos.json uploaded and valid
# ---------------------------------------------------------------------------


def test_dataset_infos_json_uploaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """V2a: put_object called with Key ending in '/dataset_infos.json'."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]
    assert "7_v1/dataset_infos.json" in keys, f"dataset_infos.json missing; got {keys}"


def test_dataset_infos_json_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """V2b: Body bytes of the dataset_infos.json upload are valid UTF-8 JSON."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    assert "7_v1/dataset_infos.json" in bodies, "dataset_infos.json key not found"

    # Must parse without raising
    parsed = json.loads(bodies["7_v1/dataset_infos.json"])
    assert isinstance(parsed, dict)


def test_dataset_infos_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """V2c: splits.train/validation num_examples + num_bytes match DatasetOutput values."""
    train = [
        {"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i:03d}"}
        for i in range(3)
    ]
    val = [
        {"instruction": "Qv", "output": "Av", "chunk_id": "cv001"},
    ]
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=7, version_tag="v1"
    )

    from dagster_platform.hf_dataset_io_manager import _rows_to_parquet_bytes as rpb

    expected_train_bytes = len(rpb(train))
    expected_val_bytes = len(rpb(val))

    mock_s3, _ = _run_handle_output(obj, monkeypatch)
    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["7_v1/dataset_infos.json"])

    default = parsed["default"]
    assert default["splits"]["train"]["num_examples"] == 3
    assert default["splits"]["validation"]["num_examples"] == 1
    assert default["splits"]["train"]["num_bytes"] == expected_train_bytes
    assert default["splits"]["validation"]["num_bytes"] == expected_val_bytes


def test_dataset_infos_json_features_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """V2d: features has keys instruction/output/chunk_id each with correct HF schema."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["7_v1/dataset_infos.json"])

    features = parsed["default"]["features"]
    for col in ("instruction", "output", "chunk_id"):
        assert col in features, f"Feature column {col!r} missing"
        assert features[col] == {"dtype": "string", "_type": "Value"}, (
            f"Unexpected schema for {col!r}: {features[col]}"
        )


def test_dataset_infos_download_and_dataset_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2e: download_size and dataset_size both equal len(train_bytes)+len(val_bytes)."""
    train = [{"instruction": "Q1", "output": "A1", "chunk_id": "c001"}]
    val = [{"instruction": "Q2", "output": "A2", "chunk_id": "c002"}]
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=7, version_tag="v1"
    )

    from dagster_platform.hf_dataset_io_manager import _rows_to_parquet_bytes as rpb

    expected_total = len(rpb(train)) + len(rpb(val))

    mock_s3, _ = _run_handle_output(obj, monkeypatch)
    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["7_v1/dataset_infos.json"])

    assert parsed["default"]["download_size"] == expected_total
    assert parsed["default"]["dataset_size"] == expected_total


def test_build_dataset_infos_helper() -> None:
    """V2f: Direct unit test of _build_dataset_infos without S3 mock."""
    from dagster_platform.hf_dataset_io_manager import _rows_to_parquet_bytes as rpb

    train_rows = [{"instruction": "Q", "output": "A", "chunk_id": "c001"}]
    val_rows: list[dict[str, Any]] = []
    train_b = rpb(train_rows)
    val_b = rpb(val_rows)

    result_bytes = _build_dataset_infos(train_b, val_b, len(train_rows), len(val_rows))

    # Valid UTF-8 JSON
    parsed = json.loads(result_bytes.decode("utf-8"))

    assert "default" in parsed
    default = parsed["default"]
    assert default["splits"]["train"]["num_examples"] == 1
    assert default["splits"]["train"]["num_bytes"] == len(train_b)
    assert default["splits"]["validation"]["num_examples"] == 0
    assert default["splits"]["validation"]["num_bytes"] == len(val_b)
    assert default["download_size"] == len(train_b) + len(val_b)
    assert default["dataset_size"] == len(train_b) + len(val_b)

    # Features schema
    for col in ("instruction", "output", "chunk_id"):
        assert default["features"][col] == {"dtype": "string", "_type": "Value"}


# ---------------------------------------------------------------------------
# F-044 — V1: Postgres dataset row updated to status='done'
# ---------------------------------------------------------------------------


def test_db_row_updated_to_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1a: update_dataset_row called with correct dataset_id, sample_count, size_bytes."""
    train = [
        {"instruction": f"Q{i}", "output": f"A{i}", "chunk_id": f"c{i:03d}"}
        for i in range(3)
    ]
    val = [{"instruction": "Qv", "output": "Av", "chunk_id": "cv001"}]
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=7, version_tag="v1"
    )

    from dagster_platform.hf_dataset_io_manager import _rows_to_parquet_bytes as rpb

    expected_sample_count = 4  # 3 train + 1 val
    expected_size_bytes = len(rpb(train)) + len(rpb(val))

    _, mock_update = _run_handle_output(obj, monkeypatch)

    mock_update.assert_called_once_with(7, expected_sample_count, expected_size_bytes)


def test_db_update_not_called_if_minio_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1b: If put_object raises ClientError, update_dataset_row is NOT called."""
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", "datasets")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    obj = _make_dataset_output(dataset_id=7, version_tag="v1")

    mock_s3 = MagicMock()
    # Make the first put_object call raise a ClientError
    mock_s3.put_object.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}},
        "PutObject",
    )
    ctx = _mock_output_context()
    mock_update = MagicMock()

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.hf_dataset_io_manager.update_dataset_row",
            mock_update,
        ),
    ):
        manager = HFDatasetIOManager()
        with pytest.raises(botocore.exceptions.ClientError):
            manager.handle_output(ctx, obj)

    mock_update.assert_not_called()
    # Error was logged
    ctx.log.error.assert_called_once()


def test_size_bytes_equals_parquet_buffer_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    """V1c: size_bytes passed to update_dataset_row equals sum of Parquet buffer sizes."""
    train = [
        {
            "instruction": f"Instruction {i}",
            "output": f"Output {i}",
            "chunk_id": f"chunk_{i:05d}",
        }
        for i in range(5)
    ]
    val = [
        {
            "instruction": f"Val instruction {i}",
            "output": f"Val output {i}",
            "chunk_id": f"vchunk_{i:05d}",
        }
        for i in range(2)
    ]
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=99, version_tag="v2"
    )

    from dagster_platform.hf_dataset_io_manager import _rows_to_parquet_bytes as rpb

    expected_size = len(rpb(train)) + len(rpb(val))

    _, mock_update = _run_handle_output(obj, monkeypatch)

    # Third arg to update_dataset_row is size_bytes
    call_args = mock_update.call_args
    actual_size = (
        call_args.args[2] if call_args.args else call_args.kwargs["size_bytes"]
    )
    assert actual_size == expected_size


# ---------------------------------------------------------------------------
# F-044 — V3: README.md content uses dataset_card_md or falls back to stub
# ---------------------------------------------------------------------------


def test_readme_uses_dataset_card_md(monkeypatch: pytest.MonkeyPatch) -> None:
    """V3a: When obj.dataset_card_md is set, README.md Body contains that content."""
    custom_card = "# My Custom Dataset\n\nCool content about this dataset."
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    # Build with dataset_card_md set
    obj_with_card = DatasetOutput(
        train_rows=obj.train_rows,
        val_rows=obj.val_rows,
        recipe_snapshot=obj.recipe_snapshot,
        dataset_id=obj.dataset_id,
        version_tag=obj.version_tag,
        dataset_card_md=custom_card,
    )

    mock_s3, _ = _run_handle_output(obj_with_card, monkeypatch)
    bodies = _get_put_object_bodies(mock_s3)

    readme_body = bodies["7_v1/README.md"].decode("utf-8")
    assert "My Custom Dataset" in readme_body
    assert "Cool content about this dataset." in readme_body


def test_readme_fallback_when_no_card_md(monkeypatch: pytest.MonkeyPatch) -> None:
    """V3b: When obj.dataset_card_md is None, README.md falls back to stub string."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    # Explicit None (default)
    assert obj.dataset_card_md is None

    mock_s3, _ = _run_handle_output(obj, monkeypatch)
    bodies = _get_put_object_bodies(mock_s3)

    readme_body = bodies["7_v1/README.md"].decode("utf-8")
    # Stub contains the prefix
    assert "7_v1" in readme_body
    assert len(readme_body) > 0


# ---------------------------------------------------------------------------
# F-044 — A: Additional assertions
# ---------------------------------------------------------------------------


def test_total_five_objects_uploaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1: Exactly 5 put_object calls (same as test_handle_output_total_five_objects)."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)
    assert mock_s3.put_object.call_count == 5


def test_dataset_infos_key_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2: dataset_infos.json key starts with correct '{dataset_id}_{version_tag}/' prefix."""
    obj = _make_dataset_output(dataset_id=42, version_tag="v3")
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]
    assert "42_v3/dataset_infos.json" in keys


def test_dataset_infos_zero_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A3: Zero-row splits → dataset_infos.json still valid JSON with num_examples=0."""
    obj = _make_dataset_output(
        train_rows=[], val_rows=[], dataset_id=7, version_tag="v1"
    )
    mock_s3, _ = _run_handle_output(obj, monkeypatch)

    bodies = _get_put_object_bodies(mock_s3)
    parsed = json.loads(bodies["7_v1/dataset_infos.json"])

    assert parsed["default"]["splits"]["train"]["num_examples"] == 0
    assert parsed["default"]["splits"]["validation"]["num_examples"] == 0
    assert parsed["default"]["download_size"] >= 0
    assert parsed["default"]["dataset_size"] >= 0


def test_db_update_called_after_all_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A4: update_dataset_row is called exactly once, after all 5 put_object calls."""
    obj = _make_dataset_output(dataset_id=7, version_tag="v1")

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", "datasets")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    call_order: list[str] = []
    mock_s3 = MagicMock()

    def track_put_object(**kwargs: Any) -> None:
        call_order.append(f"put_object:{kwargs.get('Key', '?')}")

    mock_s3.put_object.side_effect = track_put_object
    ctx = _mock_output_context()

    def track_update(dataset_id: int, sample_count: int, size_bytes: int) -> None:
        call_order.append("update_dataset_row")

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.hf_dataset_io_manager.update_dataset_row",
            side_effect=track_update,
        ),
    ):
        manager = HFDatasetIOManager()
        manager.handle_output(ctx, obj)

    # update_dataset_row must come after all 5 put_object calls
    assert call_order[-1] == "update_dataset_row", (
        f"Last call was not update_dataset_row: {call_order}"
    )
    assert call_order.count("update_dataset_row") == 1
    put_calls = [e for e in call_order if e.startswith("put_object:")]
    assert len(put_calls) == 5


def test_db_error_logged_and_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A5: If update_dataset_row raises psycopg2.Error, error is logged and re-raised."""
    import psycopg2 as pg2

    obj = _make_dataset_output(dataset_id=7, version_tag="v1")

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", "datasets")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    mock_s3 = MagicMock()
    ctx = _mock_output_context()

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.hf_dataset_io_manager.update_dataset_row",
            side_effect=pg2.OperationalError("DB connection refused"),
        ),
    ):
        manager = HFDatasetIOManager()
        with pytest.raises(pg2.OperationalError):
            manager.handle_output(ctx, obj)

    # All 5 put_object calls were made (DB error happens after uploads)
    assert mock_s3.put_object.call_count == 5
    # Error was logged
    ctx.log.error.assert_called_once()


def test_metadata_includes_sample_count_and_size_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A6: context.add_output_metadata includes sample_count, size_bytes, dataset_status."""
    train = [{"instruction": "Q", "output": "A", "chunk_id": "c001"}]
    val: list[dict[str, Any]] = []
    obj = _make_dataset_output(
        train_rows=train, val_rows=val, dataset_id=7, version_tag="v1"
    )

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_DATASETS_BUCKET", "datasets")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    mock_s3 = MagicMock()
    ctx = _mock_output_context()

    with (
        patch("boto3.client", return_value=mock_s3),
        patch("dagster_platform.hf_dataset_io_manager.update_dataset_row"),
    ):
        manager = HFDatasetIOManager()
        manager.handle_output(ctx, obj)

    # add_output_metadata was called
    ctx.add_output_metadata.assert_called_once()
    metadata = ctx.add_output_metadata.call_args.args[0]
    assert "sample_count" in metadata
    assert "size_bytes" in metadata
    assert "dataset_status" in metadata
