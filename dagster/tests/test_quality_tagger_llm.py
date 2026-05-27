"""Unit tests for quality_tagger.py helpers (F-028).

Tests cover score_chunks_via_gateway() (mock requests.post), _llm_update()
(mock Lance merge_insert chain), and update_quality_scores_in_lance() (full path).
No real HTTP calls, no real Lance DB, no Dagster runtime required.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_quality_tagger_llm.py -q
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

import pytest
import requests as real_requests

from dagster_platform.quality_tagger import (
    _llm_update,
    score_chunks_via_gateway,
    update_quality_scores_in_lance,
)


# ---------------------------------------------------------------------------
# Helper: build a mock requests.Response
# ---------------------------------------------------------------------------


def _mock_resp(content: str, model: str = "mock") -> MagicMock:
    """Return a MagicMock that looks like a successful requests.Response."""
    resp = MagicMock()
    resp.json.return_value = {"content": content, "model": model}
    resp.raise_for_status = MagicMock()  # no-op (no exception)
    return resp


# ---------------------------------------------------------------------------
# score_chunks_via_gateway() — happy path and clamping
# ---------------------------------------------------------------------------


def test_score_via_gateway_mock_response() -> None:
    """Gateway returns {"content": "0.75", "model": "mock"} → score=0.75, provider="mock"."""
    with patch("requests.post", return_value=_mock_resp("0.75", "mock")):
        results = score_chunks_via_gateway(["hello world, this is a test chunk"])

    assert len(results) == 1
    score, provider = results[0]
    assert score == pytest.approx(0.75)
    assert provider == "mock"


def test_score_via_gateway_clamping_above_1() -> None:
    """Gateway returns "1.5" → clamped to 1.0."""
    with patch("requests.post", return_value=_mock_resp("1.5")):
        results = score_chunks_via_gateway(["some text"])

    assert results[0][0] == pytest.approx(1.0)


def test_score_via_gateway_clamping_below_0() -> None:
    """Gateway returns "-0.1" → clamped to 0.0."""
    with patch("requests.post", return_value=_mock_resp("-0.1")):
        results = score_chunks_via_gateway(["some text"])

    assert results[0][0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# score_chunks_via_gateway() — error handling
# ---------------------------------------------------------------------------


def test_score_via_gateway_parse_error() -> None:
    """Gateway returns non-numeric content → score=0.0, provider="error" (no exception raised)."""
    with patch("requests.post", return_value=_mock_resp("error: internal server error")):
        results = score_chunks_via_gateway(["text"])

    assert len(results) == 1
    score, provider = results[0]
    assert score == pytest.approx(0.0)
    assert provider == "error"


def test_score_via_gateway_request_exception() -> None:
    """requests.post raises RequestException → (0.0, "error") per chunk, no exception propagated."""
    with patch(
        "requests.post",
        side_effect=real_requests.RequestException("connection refused"),
    ):
        results = score_chunks_via_gateway(["chunk one", "chunk two"])

    assert len(results) == 2
    for score, provider in results:
        assert score == pytest.approx(0.0)
        assert provider == "error"


# ---------------------------------------------------------------------------
# score_chunks_via_gateway() — URL from env
# ---------------------------------------------------------------------------


def test_gateway_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_GATEWAY_URL="http://custom:8000" is used in the POST URL."""
    monkeypatch.setenv("LLM_GATEWAY_URL", "http://custom:8000")

    with patch("requests.post", return_value=_mock_resp("0.5")) as mock_post:
        score_chunks_via_gateway(["sample text"])

    call_args = mock_post.call_args
    # First positional arg is the URL
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "http://custom:8000" in url
    assert "/api/internal/llm/completions" in url


# ---------------------------------------------------------------------------
# _llm_update() — merge_insert chain verification
# ---------------------------------------------------------------------------


def test_llm_update_calls_update() -> None:
    """_llm_update calls table.update(where=..., values=...) once per chunk row."""
    mock_rows = [
        {"chunk_id": "c001", "text": "Hello world, this is a test document."},
        {"chunk_id": "c002", "text": "Another chunk with different content."},
    ]

    # Mock search chain: table.search().where(...).select([...]).to_list()
    mock_search_chain = MagicMock()
    mock_search_chain.where.return_value.select.return_value.to_list.return_value = mock_rows

    mock_table = MagicMock()
    mock_table.search.return_value = mock_search_chain

    with patch("requests.post", return_value=_mock_resp("0.6", "mock")):
        _llm_update(
            mock_table,
            source_id=1,
            where_clause="source_id = 1 AND producer_asset = 'chunks'",
        )

    # table.update() must be called once per row (2 rows → 2 calls)
    assert mock_table.update.call_count == 2

    # Verify each call targets the correct chunk_id and updates the two quality columns
    for i, row in enumerate(mock_rows):
        call_kwargs = mock_table.update.call_args_list[i].kwargs
        assert call_kwargs["where"] == f"chunk_id = '{row['chunk_id']}'"
        values = call_kwargs["values"]
        assert "attr_quality_score" in values
        assert "attr_quality_provider" in values
        assert 0.0 <= values["attr_quality_score"] <= 1.0

    # merge_insert must NOT be called (we switched to table.update)
    mock_table.merge_insert.assert_not_called()


# ---------------------------------------------------------------------------
# update_quality_scores_in_lance() — full path, no new rows
# ---------------------------------------------------------------------------


def test_update_quality_scores_no_new_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full path: 2 rows found, scored, count_rows called, no insert triggered."""
    # Required env vars for _build_lance_storage_options
    monkeypatch.setenv("MINIO_ROOT_USER", "testuser")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "testpass")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")

    mock_rows = [
        {"chunk_id": "c001", "text": "Sample text one, used for quality scoring."},
        {"chunk_id": "c002", "text": "Sample text two, also used for quality scoring."},
    ]

    mock_search_chain = MagicMock()
    mock_search_chain.where.return_value.select.return_value.to_list.return_value = mock_rows

    mock_table = MagicMock()
    mock_table.search.return_value = mock_search_chain
    # count_rows() returns 2 — same before and after (no new rows)
    mock_table.count_rows.return_value = 2

    mock_db = MagicMock()
    mock_db.open_table.return_value = mock_table

    with patch("requests.post", return_value=_mock_resp("0.5", "mock")):
        with patch("lancedb.connect", return_value=mock_db):
            result = update_quality_scores_in_lance(42)

    # count_rows should be called once (after updates)
    mock_table.count_rows.assert_called_once()
    # Result is the row count
    assert result == 2
    # table.update called for each row (2 rows → 2 calls)
    assert mock_table.update.call_count == 2
    # merge_insert must NOT be called (column-mode uses table.update, not merge_insert)
    mock_table.merge_insert.assert_not_called()
    # No call to insert, insert_many, or add that would create new rows
    mock_table.insert.assert_not_called() if hasattr(mock_table, "insert") else None
