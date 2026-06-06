"""Unit tests for docling_io_manager.py (F-054).

Covers DoclingDocIOManager.handle_output() across 8 test cases (T1–T8):

  T1: Happy path — put_object called for doc.docling.json AND manifest.json at
      the correct keys; Body argument of doc.docling.json call equals
      obj.doc_json.encode('utf-8').
  T2: Verify manifest.json key is {source_id}/extract_mineru/manifest.json
      (not under any other prefix).
  T3: S3 failure on 2nd call (manifest.json) — assert delete_object called for
      doc.docling.json AND insert_document_variant NOT called.
  T4: S3 failure on 1st call (doc.docling.json) — assert no delete_object, and
      insert_document_variant NOT called.
  T5: Read back manifest bytes — valid JSON, all required keys present with
      correct types/values.
  T6: Two handle_output() calls with different source_ids — put_object key sets
      are disjoint, each namespaced to the correct source_id prefix.
  T7: DoclingDocOutput with 2 ImageBlob entries — put_object called for both
      image keys, Body matches img.data; manifest images list has 2 entries;
      manifest written AFTER both image writes.
  T8: Two handle_output() calls for same source_id — 4 put_object calls total
      (2 files × 2 invocations: doc.docling.json + manifest.json per call);
      insert_document_variant called twice.

No real S3/MinIO connections — boto3.client is mocked throughout.
No real Postgres — insert_document_variant is patched throughout.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_docling_io_manager.py -v
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dagster_platform.docling_io_manager import (
    DoclingDocIOManager,
    DoclingDocOutput,
    ImageBlob,
    SourceRef,
    _build_manifest,
)
from dagster_platform.extractor import CONFIG_HASH, EXTRACTOR_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_output(
    source_id: int = 42,
    extractor_name: str = "mineru",
    images: list[ImageBlob] | None = None,
) -> DoclingDocOutput:
    """Build a DoclingDocOutput with sensible defaults for testing."""
    if images is None:
        images = []
    return DoclingDocOutput(
        doc_json='{"schema_name": "DoclingDocument", "name": "source_42"}',
        images=images,
        source_refs=[
            SourceRef(
                bucket="sources",
                key=f"sources/{source_id}/original.pdf",
                sha256="abc123def456",
            )
        ],
        source_id=source_id,
        page_count=1,
        extractor_name=extractor_name,
        dagster_run_id="test-run-uuid",
    )


def _mock_output_context(partition_key: str = "src_42") -> MagicMock:
    """Build a minimal mock for Dagster OutputContext."""
    ctx = MagicMock()
    ctx.log = MagicMock()
    ctx.add_output_metadata = MagicMock()
    ctx.has_partition_key = True
    ctx.partition_key = partition_key
    return ctx


def _run_handle_output(
    obj: DoclingDocOutput,
    monkeypatch: pytest.MonkeyPatch,
    mock_insert_db: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Run DoclingDocIOManager.handle_output() with mocked S3 and env vars.

    Returns (mock_s3, mock_insert_document_variant, ctx).
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    mock_s3 = MagicMock()
    ctx = _mock_output_context()
    mock_insert = MagicMock()

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        manager.handle_output(ctx, obj)

    return mock_s3, mock_insert, ctx


def _get_put_object_calls(mock_s3: MagicMock) -> dict[str, dict[str, Any]]:
    """Extract {Key: full_kwargs} from all put_object calls on the mock S3 client."""
    result: dict[str, dict[str, Any]] = {}
    for c in mock_s3.put_object.call_args_list:
        key: str | None = c.kwargs.get("Key")
        if key is not None:
            result[key] = c.kwargs
    return result


# ---------------------------------------------------------------------------
# T1 — Happy path: correct keys + doc.docling.json Body assertion
# ---------------------------------------------------------------------------


def test_T1_happy_path_correct_keys_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """T1 (V1/spec#1): put_object called for doc.docling.json AND manifest.json at
    the correct keys. Body argument of doc.docling.json equals obj.doc_json.encode('utf-8').
    """
    obj = _make_output(source_id=42)
    mock_s3, mock_insert, ctx = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    # Correct keys are present
    assert "42/extract_mineru/doc.docling.json" in keys, (
        f"doc.docling.json missing; got {keys}"
    )
    assert "42/extract_mineru/manifest.json" in keys, (
        f"manifest.json missing; got {keys}"
    )

    # Body assertion: doc.docling.json Body == obj.doc_json.encode('utf-8') (catches body-swap)
    calls_by_key = _get_put_object_calls(mock_s3)
    doc_body = calls_by_key["42/extract_mineru/doc.docling.json"].get("Body")
    assert doc_body == obj.doc_json.encode("utf-8"), (
        f"doc.docling.json Body mismatch; got {doc_body!r}"
    )

    # insert_document_variant was called once
    mock_insert.assert_called_once_with(
        source_id=42, page_count=1, run_id="test-run-uuid"
    )

    # add_output_metadata was called
    ctx.add_output_metadata.assert_called_once()


# ---------------------------------------------------------------------------
# T2 — manifest.json key is {source_id}/extract_mineru/manifest.json
# ---------------------------------------------------------------------------


def test_T2_manifest_key_correct_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """T2 (V1/spec#1): manifest.json key is '42/extract_mineru/manifest.json'
    (not under images/ or any other prefix).
    """
    obj = _make_output(source_id=42)
    mock_s3, _, _ = _run_handle_output(obj, monkeypatch)

    keys = [c.kwargs.get("Key") for c in mock_s3.put_object.call_args_list]

    # Exactly one manifest key; it must match this exact pattern
    manifest_keys = [k for k in keys if k is not None and k.endswith("manifest.json")]
    assert len(manifest_keys) == 1, f"Expected 1 manifest.json key; got {manifest_keys}"
    assert manifest_keys[0] == "42/extract_mineru/manifest.json", (
        f"manifest.json key mismatch: {manifest_keys[0]!r}"
    )

    # Sanity: key is NOT under images/
    assert "images/" not in manifest_keys[0]


# ---------------------------------------------------------------------------
# T3 — S3 failure on 2nd call (manifest.json): cleanup + no DB insert
# ---------------------------------------------------------------------------


def test_T3_s3_failure_on_manifest_triggers_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3 (V2/spec#2): S3 failure on manifest.json write → delete_object called for
    doc.docling.json (cleanup), AND insert_document_variant NOT called.
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    obj = _make_output(source_id=42)
    mock_s3 = MagicMock()
    mock_insert = MagicMock()
    ctx = _mock_output_context()

    # 1st put_object (doc.docling.json) succeeds; 2nd (manifest.json) raises
    call_count = [0]

    def put_side_effect(**kwargs: Any) -> None:
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("simulated S3 failure on manifest.json")

    mock_s3.put_object.side_effect = put_side_effect

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        with pytest.raises(RuntimeError, match="simulated S3 failure"):
            manager.handle_output(ctx, obj)

    # Cleanup: delete_object called for doc.docling.json (the key written before failure)
    delete_keys = [
        c.kwargs.get("Key") for c in mock_s3.delete_object.call_args_list
    ]
    assert "42/extract_mineru/doc.docling.json" in delete_keys, (
        f"Expected cleanup of doc.docling.json; delete_object keys: {delete_keys}"
    )

    # Postgres write must NOT have happened
    mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# T4 — S3 failure on 1st call (doc.docling.json): no cleanup needed, no DB insert
# ---------------------------------------------------------------------------


def test_T4_s3_failure_on_first_write_no_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4 (V2/spec#2): S3 failure on doc.docling.json (1st write) → no delete_object
    (nothing was written yet), AND insert_document_variant NOT called.
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    obj = _make_output(source_id=42)
    mock_s3 = MagicMock()
    mock_insert = MagicMock()
    ctx = _mock_output_context()

    # 1st put_object raises immediately
    mock_s3.put_object.side_effect = RuntimeError("simulated S3 failure on first write")

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        with pytest.raises(RuntimeError, match="simulated S3 failure"):
            manager.handle_output(ctx, obj)

    # Nothing was written, so no delete_object calls
    mock_s3.delete_object.assert_not_called()

    # Postgres write must NOT have happened
    mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# T5 — manifest.json bytes: valid JSON + all required fields
# ---------------------------------------------------------------------------


def test_T5_manifest_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """T5 (V3/spec#3 + V4/invariant#1): Read back manifest bytes; assert valid JSON;
    assert all required keys present with correct types and values.
    """
    obj = _make_output(source_id=42)
    mock_s3, _, _ = _run_handle_output(obj, monkeypatch)

    calls_by_key = _get_put_object_calls(mock_s3)
    assert "42/extract_mineru/manifest.json" in calls_by_key, (
        "manifest.json key not found in put_object calls"
    )

    manifest_body = calls_by_key["42/extract_mineru/manifest.json"]["Body"]
    assert isinstance(manifest_body, bytes), "manifest Body should be bytes"

    manifest = json.loads(manifest_body.decode("utf-8"))

    # schema_version == 1
    assert manifest["schema_version"] == 1, (
        f"schema_version={manifest.get('schema_version')!r}"
    )

    # extractor_name matches
    assert manifest["extractor_name"] == "mineru", (
        f"extractor_name={manifest.get('extractor_name')!r}"
    )

    # extractor_version matches the module constant
    assert manifest["extractor_version"] == EXTRACTOR_VERSION, (
        f"extractor_version={manifest.get('extractor_version')!r}"
    )

    # config_hash matches the module constant
    assert manifest["config_hash"] == CONFIG_HASH, (
        "config_hash mismatch"
    )

    # dagster_run_id matches what was passed in
    assert manifest["dagster_run_id"] == "test-run-uuid", (
        f"dagster_run_id={manifest.get('dagster_run_id')!r}"
    )

    # source_refs[0].sha256 matches
    assert len(manifest["source_refs"]) == 1
    assert manifest["source_refs"][0]["sha256"] == "abc123def456", (
        f"source_refs sha256={manifest['source_refs'][0].get('sha256')!r}"
    )
    assert manifest["source_refs"][0]["bucket"] == "sources"
    assert manifest["source_refs"][0]["key"] == "sources/42/original.pdf"

    # images == [] for MVP zero-image case
    assert manifest["images"] == [], f"images={manifest.get('images')!r}"

    # created_at is parseable as ISO-8601
    created_at = manifest["created_at"]
    assert isinstance(created_at, str)
    # Should parse without raising
    parsed_dt = datetime.fromisoformat(created_at)
    assert parsed_dt.tzinfo is not None, "created_at should include timezone info"


# ---------------------------------------------------------------------------
# T6 — Two different source_ids: keys are disjoint, correctly namespaced
# ---------------------------------------------------------------------------


def test_T6_namespace_isolation_two_source_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T6 (V5): Two handle_output() calls with different source_ids; put_object key
    sets are disjoint and each namespaced to the correct source_id prefix.
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    obj_a = _make_output(source_id=10)
    obj_b = _make_output(source_id=20)
    mock_insert = MagicMock()

    # Two separate mock_s3 instances to track calls per invocation
    mock_s3_a = MagicMock()
    mock_s3_b = MagicMock()
    s3_call_count = [0]

    def make_s3_client(*args: Any, **kwargs: Any) -> MagicMock:
        s3_call_count[0] += 1
        return mock_s3_a if s3_call_count[0] == 1 else mock_s3_b

    ctx_a = _mock_output_context("src_10")
    ctx_b = _mock_output_context("src_20")

    with (
        patch("boto3.client", side_effect=make_s3_client),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        manager.handle_output(ctx_a, obj_a)
        manager.handle_output(ctx_b, obj_b)

    keys_a = {c.kwargs.get("Key") for c in mock_s3_a.put_object.call_args_list}
    keys_b = {c.kwargs.get("Key") for c in mock_s3_b.put_object.call_args_list}

    # Disjoint
    assert keys_a.isdisjoint(keys_b), f"Key sets overlap: {keys_a & keys_b}"

    # Each set is namespaced to the correct source_id
    for key in keys_a:
        assert key is not None and key.startswith("10/"), (
            f"Key {key!r} from source_id=10 does not start with '10/'"
        )
    for key in keys_b:
        assert key is not None and key.startswith("20/"), (
            f"Key {key!r} from source_id=20 does not start with '20/'"
        )


# ---------------------------------------------------------------------------
# T7 — 2 ImageBlob entries: image keys, Body assertion, manifest images list, write order
# ---------------------------------------------------------------------------


def test_T7_image_blobs_written_and_manifest_images_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7 (V6): DoclingDocOutput with 2 ImageBlobs; put_object called for both image keys;
    Body matches img.data (catches "correct key, wrong body" bugs);
    manifest images list has 2 entries; manifest written AFTER both image writes.
    """
    img0_data = b"\x89PNG\r\n"
    img1_data = b"\xff\xd8\xff\xe0"
    images = [
        ImageBlob(filename="0.png", data=img0_data),
        ImageBlob(filename="1.jpg", data=img1_data),
    ]
    obj = _make_output(source_id=42, images=images)

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    mock_s3 = MagicMock()
    mock_insert = MagicMock()
    ctx = _mock_output_context()

    # Track call order to verify manifest is last among S3 writes
    put_order: list[str] = []

    def track_put(**kwargs: Any) -> None:
        key = kwargs.get("Key", "?")
        put_order.append(key)

    mock_s3.put_object.side_effect = track_put

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        manager.handle_output(ctx, obj)

    calls_by_key = _get_put_object_calls(mock_s3)

    # Image keys exist
    assert "42/extract_mineru/images/0.png" in calls_by_key, (
        f"Image key 0.png missing; got {list(calls_by_key)}"
    )
    assert "42/extract_mineru/images/1.jpg" in calls_by_key, (
        f"Image key 1.jpg missing; got {list(calls_by_key)}"
    )

    # Body assertions (catches "correct key, wrong body" bugs — NIT-1 in feedback.md)
    body_png = calls_by_key["42/extract_mineru/images/0.png"]["Body"]
    assert body_png == img0_data, (
        f"0.png body mismatch: got {body_png!r}"
    )
    body_jpg = calls_by_key["42/extract_mineru/images/1.jpg"]["Body"]
    assert body_jpg == img1_data, (
        f"1.jpg body mismatch: got {body_jpg!r}"
    )

    # manifest images list has 2 entries
    manifest_body = calls_by_key["42/extract_mineru/manifest.json"]["Body"]
    manifest = json.loads(manifest_body.decode("utf-8"))
    assert manifest["images"] == ["0.png", "1.jpg"], (
        f"manifest images={manifest.get('images')!r}"
    )

    # manifest.json was written AFTER both image writes (write order sentinel)
    manifest_idx = put_order.index("42/extract_mineru/manifest.json")
    img0_idx = put_order.index("42/extract_mineru/images/0.png")
    img1_idx = put_order.index("42/extract_mineru/images/1.jpg")
    assert img0_idx < manifest_idx, "0.png must be written before manifest.json"
    assert img1_idx < manifest_idx, "1.jpg must be written before manifest.json"

    # Total put_object calls: 1 doc + 2 images + 1 manifest = 4
    assert mock_s3.put_object.call_count == 4, (
        f"Expected 4 put_object calls; got {mock_s3.put_object.call_count}"
    )


# ---------------------------------------------------------------------------
# T8 — Re-materialization idempotency: 4 put_object calls total, 2 DB inserts
# ---------------------------------------------------------------------------


def test_T8_rematerialization_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    """T8 (V7): Two handle_output() calls for same source_id; 4 put_object calls total
    (2 files × 2 invocations: doc.docling.json + manifest.json per call, zero images);
    no error raised; insert_document_variant called twice.

    Note: T8 anchors the zero-image contract (4 = 2×2). T7 covers image-path count
    (agreed.md §6 T8 clarifying note).
    """
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")

    obj = _make_output(source_id=42)
    mock_s3 = MagicMock()
    mock_insert = MagicMock()
    ctx = _mock_output_context()

    with (
        patch("boto3.client", return_value=mock_s3),
        patch(
            "dagster_platform.docling_io_manager.insert_document_variant",
            mock_insert,
        ),
    ):
        manager = DoclingDocIOManager()
        manager.handle_output(ctx, obj)
        manager.handle_output(ctx, obj)

    # 2 files × 2 calls = 4 total (zero images per agreed.md §6 T8 note)
    assert mock_s3.put_object.call_count == 4, (
        f"Expected 4 put_object calls total; got {mock_s3.put_object.call_count}"
    )

    # insert_document_variant called twice (idempotent via ON CONFLICT DO NOTHING)
    assert mock_insert.call_count == 2, (
        f"Expected insert_document_variant called twice; got {mock_insert.call_count}"
    )

    # No exception raised (both calls succeed)
    # (If an exception had been raised, we'd never reach this assertion)


# ---------------------------------------------------------------------------
# Additional: load_input raises NotImplementedError
# ---------------------------------------------------------------------------


def test_load_input_raises_not_implemented() -> None:
    """load_input() raises NotImplementedError (consistent with other IOManagers)."""
    manager = DoclingDocIOManager()
    ctx = MagicMock()
    with pytest.raises(NotImplementedError, match="load_input"):
        manager.load_input(ctx)


# ---------------------------------------------------------------------------
# Additional: _build_manifest pure function unit test
# ---------------------------------------------------------------------------


def test_build_manifest_pure_function() -> None:
    """_build_manifest produces correct JSON without any S3/IOManager machinery."""
    obj = _make_output(source_id=7)
    created_at = "2026-06-05T12:34:56.789012+00:00"
    result = _build_manifest(obj, created_at)

    assert isinstance(result, bytes)
    parsed = json.loads(result.decode("utf-8"))

    assert parsed["schema_version"] == 1
    assert parsed["extractor_name"] == "mineru"
    assert parsed["extractor_version"] == EXTRACTOR_VERSION
    assert parsed["config_hash"] == CONFIG_HASH
    assert parsed["dagster_run_id"] == "test-run-uuid"
    assert parsed["created_at"] == created_at
    assert parsed["source_refs"][0]["sha256"] == "abc123def456"
    assert parsed["images"] == []
