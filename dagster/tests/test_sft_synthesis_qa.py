"""Unit tests for sft_synthesis_qa.py helpers (F-043).

Covers:
  - parse_dataset_partition_key: valid/invalid cases
  - read_chunks_from_lance: with and without filter SQL
  - call_llm_gateway: happy path, parse failure with fallback True/False
  - deterministic_split: reproducibility, ratio, zero-val
  - _run_dataset_asset: end-to-end integration with all I/O mocked
  - AST no-direct-LLM-SDK-imports check (V5)

No real HTTP calls, no real Lance DB, no real Postgres, no Dagster runtime required.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_sft_synthesis_qa.py -v
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests as real_requests

from dagster_platform.sft_synthesis_qa import (
    DatasetOutput,
    _run_dataset_asset,
    call_llm_gateway,
    deterministic_split,
    parse_dataset_partition_key,
    read_chunks_from_lance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_resp(content: str, model: str = "mock") -> MagicMock:
    """Return a MagicMock that looks like a successful requests.Response."""
    resp = MagicMock()
    resp.json.return_value = {"content": content, "model": model}
    resp.raise_for_status = MagicMock()  # no-op
    return resp


def _qa_json(instruction: str = "What is it?", output: str = "It is X.") -> str:
    return json.dumps({"instruction": instruction, "output": output})


# ---------------------------------------------------------------------------
# parse_dataset_partition_key
# ---------------------------------------------------------------------------


def test_parse_dataset_partition_key_valid() -> None:
    """'ds_5_v2' → (5, 'v2')."""
    recipe_id, version_tag = parse_dataset_partition_key("ds_5_v2")
    assert recipe_id == 5
    assert version_tag == "v2"


def test_parse_dataset_partition_key_large_ids() -> None:
    """'ds_100_v1' → (100, 'v1')."""
    recipe_id, version_tag = parse_dataset_partition_key("ds_100_v1")
    assert recipe_id == 100
    assert version_tag == "v1"


def test_parse_dataset_partition_key_v10() -> None:
    """'ds_3_v10' → (3, 'v10')."""
    recipe_id, version_tag = parse_dataset_partition_key("ds_3_v10")
    assert recipe_id == 3
    assert version_tag == "v10"


def test_parse_dataset_partition_key_invalid_no_prefix() -> None:
    """Malformed key (missing 'ds_' prefix) → ValueError."""
    with pytest.raises(ValueError, match="Invalid dataset partition key"):
        parse_dataset_partition_key("5_v2")


def test_parse_dataset_partition_key_invalid_missing_version() -> None:
    """'ds_5' (no version part) → ValueError."""
    with pytest.raises(ValueError, match="Invalid dataset partition key"):
        parse_dataset_partition_key("ds_5")


def test_parse_dataset_partition_key_invalid_text_id() -> None:
    """'ds_abc_v1' (non-numeric recipe_id) → ValueError."""
    with pytest.raises(ValueError, match="Invalid dataset partition key"):
        parse_dataset_partition_key("ds_abc_v1")


def test_parse_dataset_partition_key_invalid_empty() -> None:
    """Empty string → ValueError."""
    with pytest.raises(ValueError, match="Invalid dataset partition key"):
        parse_dataset_partition_key("")


# ---------------------------------------------------------------------------
# read_chunks_from_lance
# ---------------------------------------------------------------------------


def _build_mock_lance(rows: list[dict[str, Any]]) -> MagicMock:
    """Build a mock lancedb.connect() return value that yields `rows`."""
    mock_query = MagicMock()
    mock_query.select.return_value.to_list.return_value = rows
    mock_query.where.return_value.select.return_value.to_list.return_value = rows

    mock_table = MagicMock()
    mock_table.search.return_value = mock_query

    mock_db = MagicMock()
    mock_db.open_table.return_value = mock_table

    return mock_db, mock_table, mock_query


def test_read_chunks_from_lance_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_sql=None → .where() is NOT called; returns all rows."""
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")

    rows = [
        {"chunk_id": "c001", "text": "Hello world."},
        {"chunk_id": "c002", "text": "Second chunk."},
    ]
    mock_db, mock_table, mock_query = _build_mock_lance(rows)

    with patch("lancedb.connect", return_value=mock_db):
        result = read_chunks_from_lance(None)

    assert result == rows
    # .where() must NOT have been called when filter_sql is None
    mock_query.where.assert_not_called()


def test_read_chunks_from_lance_with_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """filter_sql provided → .where() IS called with that SQL string."""
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")

    rows = [{"chunk_id": "c001", "text": "Filtered chunk."}]
    mock_db, mock_table, mock_query = _build_mock_lance(rows)

    filter_sql = "attr_quality_score > 0.7"
    with patch("lancedb.connect", return_value=mock_db):
        result = read_chunks_from_lance(filter_sql)

    assert result == rows
    mock_query.where.assert_called_once_with(filter_sql)


def test_read_chunks_from_lance_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty Lance result → returns empty list without error."""
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")

    mock_db, mock_table, mock_query = _build_mock_lance([])

    with patch("lancedb.connect", return_value=mock_db):
        result = read_chunks_from_lance(None)

    assert result == []


# ---------------------------------------------------------------------------
# call_llm_gateway
# ---------------------------------------------------------------------------


def test_call_llm_gateway_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: gateway returns valid JSON → returns {"instruction": ..., "output": ...}."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://testgateway:8000")

    content = _qa_json("What is the capital?", "Paris.")
    with patch("requests.post", return_value=_mock_llm_resp(content)) as mock_post:
        result = call_llm_gateway("Some prompt.", max_tokens=256)

    assert result is not None
    assert result["instruction"] == "What is the capital?"
    assert result["output"] == "Paris."

    # Must call the internal endpoint, not any external SDK URL
    call_args = mock_post.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "http://testgateway:8000" in url
    assert "/api/internal/llm/completions" in url


def test_call_llm_gateway_uses_requests_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_llm_gateway() only calls requests.post — no other outbound calls."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    content = _qa_json()
    with patch("requests.post", return_value=_mock_llm_resp(content)) as mock_post:
        call_llm_gateway("prompt text")

    # Exactly one requests.post call
    assert mock_post.call_count == 1


def test_call_llm_gateway_parse_failure_fallback_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON content + fallback_on_failure=True → returns None (no exception)."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    with patch("requests.post", return_value=_mock_llm_resp("not valid json at all")):
        result = call_llm_gateway("prompt", fallback_on_failure=True)

    assert result is None


def test_call_llm_gateway_parse_failure_fallback_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON + fallback_on_failure=False → raises ValueError."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    with patch("requests.post", return_value=_mock_llm_resp("not valid json at all")):
        with pytest.raises(ValueError, match="failed to parse LLM response"):
            call_llm_gateway("prompt", fallback_on_failure=False)


def test_call_llm_gateway_missing_key_fallback_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid JSON but missing 'instruction' key + fallback=True → returns None."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    # JSON is valid but doesn't have the expected keys
    with patch(
        "requests.post",
        return_value=_mock_llm_resp(json.dumps({"answer": "something"})),
    ):
        result = call_llm_gateway("prompt", fallback_on_failure=True)

    assert result is None


def test_call_llm_gateway_request_exception_fallback_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """requests.post raises RequestException + fallback=True → returns None (no raise)."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    with patch(
        "requests.post",
        side_effect=real_requests.RequestException("connection refused"),
    ):
        result = call_llm_gateway("prompt", fallback_on_failure=True)

    assert result is None


def test_call_llm_gateway_max_tokens_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_tokens is forwarded in the request body."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    content = _qa_json()
    with patch("requests.post", return_value=_mock_llm_resp(content)) as mock_post:
        call_llm_gateway("prompt", max_tokens=1024)

    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# deterministic_split
# ---------------------------------------------------------------------------


def _make_rows(n: int) -> list[dict[str, Any]]:
    """Generate n rows with deterministic chunk_ids."""
    return [
        {
            "instruction": f"Q{i}",
            "output": f"A{i}",
            "chunk_id": f"chunk_{i:05d}",
        }
        for i in range(n)
    ]


def test_deterministic_split_reproducible() -> None:
    """Same input always produces the same train/val assignment."""
    rows = _make_rows(50)
    train1, val1 = deterministic_split(rows, 0.2)
    train2, val2 = deterministic_split(rows, 0.2)
    assert [r["chunk_id"] for r in train1] == [r["chunk_id"] for r in train2]
    assert [r["chunk_id"] for r in val1] == [r["chunk_id"] for r in val2]


def test_deterministic_split_ratio_approx() -> None:
    """1000 rows, val_ratio=0.1 → ~10% in val bucket (within ±3%)."""
    rows = _make_rows(1000)
    train, val = deterministic_split(rows, 0.1)
    assert len(train) + len(val) == 1000
    val_pct = len(val) / 1000.0
    assert 0.07 <= val_pct <= 0.13, f"val% {val_pct:.2%} not in [7%, 13%]"


def test_deterministic_split_zero_val() -> None:
    """val_ratio=0.0 → all rows in train, none in val."""
    rows = _make_rows(20)
    train, val = deterministic_split(rows, 0.0)
    assert len(train) == 20
    assert len(val) == 0


def test_deterministic_split_full_val() -> None:
    """val_ratio=1.0 → all rows in val, none in train."""
    rows = _make_rows(20)
    train, val = deterministic_split(rows, 1.0)
    assert len(train) == 0
    assert len(val) == 20


def test_deterministic_split_empty_rows() -> None:
    """Empty input → both buckets empty."""
    train, val = deterministic_split([], 0.2)
    assert train == []
    assert val == []


def test_deterministic_split_no_overlap() -> None:
    """No row appears in both train and val."""
    rows = _make_rows(100)
    train, val = deterministic_split(rows, 0.2)
    train_ids = {r["chunk_id"] for r in train}
    val_ids = {r["chunk_id"] for r in val}
    assert train_ids.isdisjoint(val_ids)
    assert len(train_ids) + len(val_ids) == 100


# ---------------------------------------------------------------------------
# V5 — No direct LLM SDK imports (AST walk)
# ---------------------------------------------------------------------------


def _check_no_llm_sdk_imports(filepath: pathlib.Path) -> None:
    """Assert that 'anthropic' and 'openai' do not appear as module imports."""
    src = filepath.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "anthropic" not in alias.name, (
                    f"{filepath.name}: direct 'import anthropic' found — "
                    "violates hard invariant #4"
                )
                assert "openai" not in alias.name, (
                    f"{filepath.name}: direct 'import openai' found — "
                    "violates hard invariant #4"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert "anthropic" not in module, (
                f"{filepath.name}: 'from anthropic ...' import found — "
                "violates hard invariant #4"
            )
            assert "openai" not in module, (
                f"{filepath.name}: 'from openai ...' import found — "
                "violates hard invariant #4"
            )


def test_no_direct_llm_sdk_imports_sft_synthesis_qa() -> None:
    """sft_synthesis_qa.py must not import anthropic or openai directly."""
    # Walk up from this test file's directory to the dagster_platform module.
    base = pathlib.Path(__file__).parent.parent / "dagster_platform"
    _check_no_llm_sdk_imports(base / "sft_synthesis_qa.py")


def test_no_direct_llm_sdk_imports_hf_dataset_io_manager() -> None:
    """hf_dataset_io_manager.py must not import anthropic or openai directly."""
    base = pathlib.Path(__file__).parent.parent / "dagster_platform"
    _check_no_llm_sdk_imports(base / "hf_dataset_io_manager.py")


# ---------------------------------------------------------------------------
# End-to-end integration test (_run_dataset_asset)
# ---------------------------------------------------------------------------


def _make_mock_db_cursor(dataset_id: int, recipe_snapshot: dict[str, Any]) -> MagicMock:
    """Build a mock psycopg2 connection/cursor that returns a dataset row."""
    mock_cursor = MagicMock()
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = (dataset_id, recipe_snapshot, f"s3://datasets/{dataset_id}_v1")

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.close = MagicMock()

    return mock_conn


def test_dataset_asset_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: patches all I/O, calls _run_dataset_asset, asserts DatasetOutput.

    Verifies the full chain:
      parse_dataset_partition_key → fetch_dataset_row → read_chunks_from_lance
      → call_llm_gateway (×N) → deterministic_split → DatasetOutput
    """
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    recipe_snapshot = {
        "filter": {"where": "attr_quality_score > 0.5"},
        "schema": {
            "config": {
                "prompt_template": "Q&A for: {chunk_text}\nJSON: {{\"instruction\":\"...\",\"output\":\"...\"}}",
                "max_tokens": 256,
                "fallback_on_failure": True,
            }
        },
        "output": {"splits": {"validation": 0.2}},
    }
    dataset_id = 5
    mock_conn = _make_mock_db_cursor(dataset_id, recipe_snapshot)

    lance_rows = [
        {"chunk_id": "chunk_001", "text": "The sky is blue."},
        {"chunk_id": "chunk_002", "text": "Water boils at 100°C."},
        {"chunk_id": "chunk_003", "text": "Python is a programming language."},
    ]
    mock_db, _mock_table, _mock_query = _build_mock_lance(lance_rows)
    # Set up with-filter variant to return lance_rows too
    _mock_query.where.return_value.select.return_value.to_list.return_value = lance_rows

    llm_response = _mock_llm_resp(_qa_json("What is the sky?", "It is blue."))

    with (
        patch("psycopg2.connect", return_value=mock_conn),
        patch("lancedb.connect", return_value=mock_db),
        patch("requests.post", return_value=llm_response),
    ):
        output = _run_dataset_asset("ds_5_v1")

    assert isinstance(output, DatasetOutput)
    assert output.dataset_id == dataset_id
    assert output.version_tag == "v1"
    assert output.recipe_snapshot == recipe_snapshot

    # All 3 chunks were processed → should have 3 QA rows total split between train/val
    total = len(output.train_rows) + len(output.val_rows)
    assert total == 3

    # Every row has the required keys
    for row in output.train_rows + output.val_rows:
        assert "instruction" in row
        assert "output" in row
        assert "chunk_id" in row


def test_dataset_asset_end_to_end_llm_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM failures with fallback=True → skipped chunks, no exception."""
    monkeypatch.setenv("MINIO_ROOT_USER", "user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "pass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://test:test@db/test")
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://fastapi:8000")

    recipe_snapshot: dict[str, Any] = {}
    mock_conn = _make_mock_db_cursor(7, recipe_snapshot)

    lance_rows = [
        {"chunk_id": "c001", "text": "Text one."},
        {"chunk_id": "c002", "text": "Text two."},
    ]
    mock_db, _mock_table, _mock_query = _build_mock_lance(lance_rows)

    with (
        patch("psycopg2.connect", return_value=mock_conn),
        patch("lancedb.connect", return_value=mock_db),
        patch(
            "requests.post",
            return_value=_mock_llm_resp("NOT VALID JSON"),
        ),
    ):
        output = _run_dataset_asset("ds_7_v1")

    # All chunks skipped due to parse failure with fallback=True
    assert len(output.train_rows) == 0
    assert len(output.val_rows) == 0
