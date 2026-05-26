"""Unit tests for dataplat_api.storage.lance — S023-F-023.

These tests run under the existing `backend` layer (no live MinIO required).
All four test functions are pure unit tests: they import the module constants
and helpers but never touch the network.
"""

import pyarrow as pa

from dataplat_api.storage.lance import CHUNKS_SCHEMA, make_lance_storage_options
from dataplat_api.config import settings


def test_chunks_schema_field_count() -> None:
    """CHUNKS_SCHEMA must contain exactly 24 fields (design doc §4.2)."""
    assert len(CHUNKS_SCHEMA) == 24, (
        f"Expected 24 fields, got {len(CHUNKS_SCHEMA)}: {CHUNKS_SCHEMA.names}"
    )


def test_chunks_schema_has_all_required_fields() -> None:
    """The 7 verification-criterion fields must be present in CHUNKS_SCHEMA."""
    required = [
        "chunk_id",
        "source_id",
        "text",
        "token_count",
        "attr_quality_score",
        "attr_lang_code",
        "attr_minhash_signature",
    ]
    for field in required:
        assert field in CHUNKS_SCHEMA.names, (
            f"Required field {field!r} missing; schema has: {CHUNKS_SCHEMA.names}"
        )


def test_chunks_schema_key_field_types() -> None:
    """Spot-check a representative sample of field types against design doc §4.2."""
    expected: dict[str, pa.DataType] = {
        "chunk_id":             pa.string(),
        "source_id":            pa.int64(),
        "text":                 pa.large_string(),
        "token_count":          pa.int32(),
        "attr_quality_score":   pa.float32(),
        "attr_minhash_signature": pa.list_(pa.uint64()),
        "attr_embed_vector":    pa.list_(pa.float32(), 1024),
        "attr_pii_has_pii":     pa.bool_(),
        "created_at":           pa.timestamp("ms"),
        "updated_at":           pa.timestamp("ms"),
    }
    for field_name, expected_type in expected.items():
        field = CHUNKS_SCHEMA.field(field_name)
        assert field.type == expected_type, (
            f"Field {field_name!r}: expected type {expected_type}, got {field.type}"
        )


def test_make_lance_storage_options_shape() -> None:
    """make_lance_storage_options() must return a dict with the required keys
    and the correct values for the running Settings instance."""
    opts = make_lance_storage_options()

    # Required keys
    required_keys = {
        "aws_access_key_id",
        "aws_secret_access_key",
        "endpoint",
        "aws_region",
        "allow_http",
    }
    for key in required_keys:
        assert key in opts, f"Missing key {key!r} in storage_options: {opts}"

    # endpoint starts with "http://" and embeds settings.MINIO_ENDPOINT
    assert opts["endpoint"].startswith("http://"), (
        f"endpoint must start with 'http://'; got: {opts['endpoint']!r}"
    )
    assert settings.MINIO_ENDPOINT in opts["endpoint"], (
        f"endpoint must embed settings.MINIO_ENDPOINT ({settings.MINIO_ENDPOINT!r}); "
        f"got: {opts['endpoint']!r}"
    )

    # allow_http must be the string "true" (not a bool)
    assert opts["allow_http"] == "true", (
        f"allow_http must be the string 'true'; got: {opts['allow_http']!r}"
    )
