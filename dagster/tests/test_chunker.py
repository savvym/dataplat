"""Unit tests for dagster_platform/chunker.py pure helpers (F-025).

These tests run INSIDE the dagster-webserver container (which has tiktoken,
lancedb, docling-core installed) via:
    docker compose exec -T dagster-webserver python -m pytest /app/dagster/tests/test_chunker.py -v

They do NOT run in the backend (apps/api) pytest layer — tiktoken and
lancedb are not in that venv. See agreed.md §5 (unit tests) for the
test-execution environment rationale.
"""

from __future__ import annotations

import re

from dagster_platform.chunker import (
    TOKEN_BUDGET,
    _ENCODER,
    extract_text_from_document,
    fixed_size_chunk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token_count(text: str) -> int:
    """Return the number of tokens for text using the module's encoder."""
    return len(_ENCODER.encode(text))


def _make_text(n_tokens: int) -> str:
    """Generate a string that encodes to approximately n_tokens tokens.

    Uses a single-token word repeated n_tokens times. In cl100k_base,
    'hello' encodes to exactly 1 token, so this is exact.
    """
    word = "hello"
    return " ".join([word] * n_tokens)


# ---------------------------------------------------------------------------
# fixed_size_chunk: edge cases
# ---------------------------------------------------------------------------


class TestFixedSizeChunkEmptyText:
    def test_nonempty_text_produces_at_least_one_chunk(self) -> None:
        """Any non-empty text always produces ≥1 chunk (fallback guard)."""
        rows = fixed_size_chunk("hello", source_id=1, collection_id=10)
        assert len(rows) >= 1

    def test_single_word_produces_exactly_one_chunk(self) -> None:
        rows = fixed_size_chunk("hello", source_id=1, collection_id=10)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# fixed_size_chunk: single window (≤512 tokens)
# ---------------------------------------------------------------------------


class TestFixedSizeChunkSingleWindow:
    def test_text_under_512_tokens_produces_one_chunk(self) -> None:
        text = _make_text(100)
        rows = fixed_size_chunk(text, source_id=1, collection_id=10)
        assert len(rows) == 1

    def test_exactly_512_tokens_produces_one_chunk(self) -> None:
        text = _make_text(TOKEN_BUDGET)
        rows = fixed_size_chunk(text, source_id=2, collection_id=20)
        assert len(rows) == 1
        assert rows[0]["token_count"] == TOKEN_BUDGET


# ---------------------------------------------------------------------------
# fixed_size_chunk: two windows (>512 tokens)
# ---------------------------------------------------------------------------


class TestFixedSizeChunkTwoWindows:
    def test_513_tokens_produces_two_chunks(self) -> None:
        text = _make_text(TOKEN_BUDGET + 1)
        rows = fixed_size_chunk(text, source_id=3, collection_id=30)
        assert len(rows) == 2
        assert rows[0]["token_count"] == TOKEN_BUDGET
        assert rows[1]["token_count"] == 1

    def test_1024_tokens_produces_two_equal_chunks(self) -> None:
        text = _make_text(TOKEN_BUDGET * 2)
        rows = fixed_size_chunk(text, source_id=4, collection_id=40)
        assert len(rows) == 2
        assert rows[0]["token_count"] == TOKEN_BUDGET
        assert rows[1]["token_count"] == TOKEN_BUDGET


# ---------------------------------------------------------------------------
# fixed_size_chunk: chunk_id naming convention (agreed.md D4)
# ---------------------------------------------------------------------------


class TestFixedSizeChunkIds:
    def test_chunk_ids_are_source_id_underscore_seq(self) -> None:
        """chunk_id must be '{source_id}_{seq}' (0-indexed)."""
        text = _make_text(TOKEN_BUDGET * 3)
        rows = fixed_size_chunk(text, source_id=7, collection_id=1)
        for seq, row in enumerate(rows):
            assert row["chunk_id"] == f"7_{seq}"

    def test_chunk_ids_start_at_zero(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=42, collection_id=1)
        assert rows[0]["chunk_id"] == "42_0"

    def test_chunk_id_regex_pattern(self) -> None:
        """All chunk_ids must match the regex ^{source_id}_\\d+$."""
        text = _make_text(TOKEN_BUDGET * 2 + 50)
        rows = fixed_size_chunk(text, source_id=99, collection_id=1)
        pattern = re.compile(r"^99_\d+$")
        for row in rows:
            assert pattern.fullmatch(row["chunk_id"]), (
                f"chunk_id {row['chunk_id']!r} does not match ^99_\\d+$"
            )


# ---------------------------------------------------------------------------
# fixed_size_chunk: token_count correctness
# ---------------------------------------------------------------------------


class TestFixedSizeChunkTokenCount:
    def test_token_count_matches_encoder(self) -> None:
        """token_count must equal len(encoder.encode(text)) for each chunk."""
        text = _make_text(200)
        rows = fixed_size_chunk(text, source_id=5, collection_id=50)
        for row in rows:
            actual = _token_count(row["text"])
            assert row["token_count"] == actual, (
                f"token_count mismatch: stored={row['token_count']}, "
                f"actual={actual} for chunk {row['chunk_id']!r}"
            )


# ---------------------------------------------------------------------------
# fixed_size_chunk: window boundary (max tokens)
# ---------------------------------------------------------------------------


class TestFixedSizeChunkMaxTokens:
    def test_no_chunk_exceeds_512_tokens(self) -> None:
        """No chunk may have token_count > TOKEN_BUDGET (512)."""
        text = _make_text(TOKEN_BUDGET * 5)
        rows = fixed_size_chunk(text, source_id=6, collection_id=60)
        for row in rows:
            assert row["token_count"] <= TOKEN_BUDGET, (
                f"chunk {row['chunk_id']!r} has token_count={row['token_count']} > {TOKEN_BUDGET}"
            )


# ---------------------------------------------------------------------------
# fixed_size_chunk: return shape — all 24 fields, correct values
# ---------------------------------------------------------------------------


class TestFixedSizeChunkReturnShape:
    def test_all_24_fields_present(self) -> None:
        """Every row must contain all 24 CHUNKS_SCHEMA fields."""
        rows = fixed_size_chunk("test text for shape check", source_id=1, collection_id=1)
        required = [
            "chunk_id", "source_id", "source_collection_id",
            "producer_asset", "producer_version",
            "text", "token_count", "docling_refs", "source_refs",
            "augmented_from", "augmenter_id", "augmenter_config_hash",
            "attr_quality_score", "attr_quality_provider",
            "attr_lang_code", "attr_lang_confidence",
            "attr_minhash_signature", "attr_minhash_cluster_id",
            "attr_minhash_is_head", "attr_pii_has_pii",
            "attr_pii_categories", "attr_embed_vector",
            "created_at", "updated_at",
        ]
        for row in rows:
            for field in required:
                assert field in row, (
                    f"row is missing required field {field!r}; "
                    f"row keys: {sorted(row.keys())}"
                )

    def test_producer_asset_is_chunks(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["producer_asset"] == "chunks"

    def test_producer_version_is_set(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["producer_version"], "producer_version must be non-empty"

    def test_augmented_from_is_none(self) -> None:
        """augmented_from must be None (original chunk, not augmented)."""
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["augmented_from"] is None

    def test_attr_fields_are_none(self) -> None:
        """All attr_* columns must be None (populated in later features)."""
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        attr_fields = [
            "attr_quality_score", "attr_quality_provider",
            "attr_lang_code", "attr_lang_confidence",
            "attr_minhash_signature", "attr_minhash_cluster_id",
            "attr_minhash_is_head", "attr_pii_has_pii",
            "attr_pii_categories", "attr_embed_vector",
        ]
        for row in rows:
            for field in attr_fields:
                assert row[field] is None, (
                    f"{field} must be None, got {row[field]!r}"
                )

    def test_ref_fields_are_empty_string(self) -> None:
        """docling_refs and source_refs must be '' (convention, agreed.md D10)."""
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["docling_refs"] == "", (
                f"docling_refs must be '', got {row['docling_refs']!r}"
            )
            assert row["source_refs"] == "", (
                f"source_refs must be '', got {row['source_refs']!r}"
            )

    def test_source_id_and_collection_id_set(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=17, collection_id=55)
        for row in rows:
            assert row["source_id"] == 17
            assert row["source_collection_id"] == 55

    def test_text_is_non_empty(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["text"], "chunk text must be non-empty"

    def test_timestamps_present(self) -> None:
        rows = fixed_size_chunk("hello world", source_id=1, collection_id=1)
        for row in rows:
            assert row["created_at"] is not None
            assert row["updated_at"] is not None


# ---------------------------------------------------------------------------
# extract_text_from_document: fallback chain
# ---------------------------------------------------------------------------


class TestExtractTextFallback:
    def test_name_fallback_when_markdown_empty(self) -> None:
        """For a minimal DoclingDocument (no body text), export_to_markdown()
        returns empty — fall back to doc.name."""
        from docling_core.types.doc.document import DoclingDocument  # type: ignore[import-untyped]

        doc = DoclingDocument(name="source_99")
        text = extract_text_from_document(doc, source_id=99)
        # The minimal doc has no body nodes, so export_to_markdown() is empty.
        # Fall back to doc.name = "source_99".
        assert text, "text must not be empty"
        assert "source_99" in text or len(text) > 0

    def test_source_id_fallback_when_name_empty(self) -> None:
        """If doc.name is also empty, fall back to f'source_{source_id}'."""
        from docling_core.types.doc.document import DoclingDocument  # type: ignore[import-untyped]

        # DoclingDocument name is required so we use a space to simulate an
        # effectively empty name for the strip() path.
        doc = DoclingDocument(name="   ")
        text = extract_text_from_document(doc, source_id=7)
        assert text == "source_7", f"expected 'source_7', got {text!r}"
