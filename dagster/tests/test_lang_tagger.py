"""Unit tests for lang_tagger.py (F-029).

Tests are designed to run without a Dagster runtime or a live LanceDB instance.
ftlangdetect.detect and lancedb.connect are mocked throughout.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_lang_tagger.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers shared by the Lance update tests
# ---------------------------------------------------------------------------


def _make_table_mock(rows: list[dict]) -> MagicMock:
    """Build a mock lancedb Table that returns the given rows list."""
    table = MagicMock()
    search_chain = MagicMock()
    search_chain.where.return_value.select.return_value.to_list.return_value = rows
    table.search.return_value = search_chain
    table.count_rows.return_value = len(rows)
    return table


def _make_db_mock(table: MagicMock) -> MagicMock:
    """Build a mock lancedb db that returns the given table on open_table()."""
    db = MagicMock()
    db.open_table.return_value = table
    return db


# ---------------------------------------------------------------------------
# detect_language tests
# ---------------------------------------------------------------------------


def test_detect_language_happy_path() -> None:
    """detect returns {"lang": "__label__en", "score": 0.9999} → ("en", 0.9999)."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.return_value = {"lang": "__label__en", "score": 0.9999}
        from dagster_platform.lang_tagger import detect_language

        code, conf = detect_language("hello world")

    assert code == "en"
    assert abs(conf - 0.9999) < 1e-6


def test_detect_language_label_prefix_stripped() -> None:
    """__label__ prefix is removed from zh, fr, de codes."""
    from dagster_platform.lang_tagger import detect_language

    for raw_label, expected in [
        ("__label__zh", "zh"),
        ("__label__fr", "fr"),
        ("__label__de", "de"),
    ]:
        with patch("dagster_platform.lang_tagger.detect") as mock_detect:
            mock_detect.return_value = {"lang": raw_label, "score": 0.8}
            code, _ = detect_language("some text")
        assert code == expected, f"expected {expected!r}, got {code!r}"


def test_detect_language_confidence_clamped_above_1() -> None:
    """score 1.5 → clamped to 1.0."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.return_value = {"lang": "__label__en", "score": 1.5}
        from dagster_platform.lang_tagger import detect_language

        _, conf = detect_language("hello world")

    assert conf == 1.0


def test_detect_language_confidence_clamped_below_0() -> None:
    """score -0.1 → clamped to 0.0."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.return_value = {"lang": "__label__en", "score": -0.1}
        from dagster_platform.lang_tagger import detect_language

        _, conf = detect_language("hello world")

    assert conf == 0.0


def test_detect_language_empty_text() -> None:
    """text="" → ("und", 0.0) without calling detect."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        from dagster_platform.lang_tagger import detect_language

        code, conf = detect_language("")

    assert code == "und"
    assert conf == 0.0
    mock_detect.assert_not_called()


def test_detect_language_whitespace_only() -> None:
    """text="   " → ("und", 0.0) without calling detect."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        from dagster_platform.lang_tagger import detect_language

        code, conf = detect_language("   ")

    assert code == "und"
    assert conf == 0.0
    mock_detect.assert_not_called()


def test_detect_language_detect_raises() -> None:
    """When detect() raises ValueError, returns ("und", 0.0) sentinel (no re-raise)."""
    with patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.side_effect = ValueError("fasttext internal error")
        from dagster_platform.lang_tagger import detect_language

        code, conf = detect_language("hello world")

    assert code == "und"
    assert conf == 0.0


# ---------------------------------------------------------------------------
# update_lang_in_lance tests
# ---------------------------------------------------------------------------


def test_lang_update_calls_table_update() -> None:
    """update_lang_in_lance calls table.update(where=..., values=...) once per row;
    merge_insert is NOT called."""
    rows = [
        {"chunk_id": "cid1", "text": "hello world"},
        {"chunk_id": "cid2", "text": "bonjour monde"},
    ]
    table = _make_table_mock(rows)
    db = _make_db_mock(table)

    with patch("dagster_platform.lang_tagger.lancedb.connect", return_value=db), \
         patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.side_effect = [
            {"lang": "__label__en", "score": 0.99},
            {"lang": "__label__fr", "score": 0.95},
        ]
        from dagster_platform.lang_tagger import update_lang_in_lance

        update_lang_in_lance(42)

    assert table.update.call_count == 2
    table.merge_insert.assert_not_called()


def test_lang_update_updates_correct_columns() -> None:
    """Each table.update call sets only attr_lang_code and attr_lang_confidence."""
    rows = [{"chunk_id": "abc123", "text": "hello world"}]
    table = _make_table_mock(rows)
    db = _make_db_mock(table)

    with patch("dagster_platform.lang_tagger.lancedb.connect", return_value=db), \
         patch("dagster_platform.lang_tagger.detect") as mock_detect:
        mock_detect.return_value = {"lang": "__label__en", "score": 0.9}
        from dagster_platform.lang_tagger import update_lang_in_lance

        update_lang_in_lance(1)

    table.update.assert_called_once_with(
        where="chunk_id = 'abc123'",
        values={"attr_lang_code": "en", "attr_lang_confidence": 0.9},
    )


def test_lang_update_no_rows() -> None:
    """Zero rows from to_list() → returns 0 immediately; table.update not called."""
    table = _make_table_mock([])
    db = _make_db_mock(table)

    with patch("dagster_platform.lang_tagger.lancedb.connect", return_value=db), \
         patch("dagster_platform.lang_tagger.detect") as mock_detect:
        from dagster_platform.lang_tagger import update_lang_in_lance

        result = update_lang_in_lance(99)

    assert result == 0
    table.update.assert_not_called()
    mock_detect.assert_not_called()
