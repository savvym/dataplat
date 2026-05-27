"""Unit tests for quality_tagger.py helpers (F-027).

Tests cover compute_quality_score() and the QUALITY_PROVIDER constant.
No Dagster runtime required — quality_tagger.py has no Dagster imports.

Run inside the dagster-webserver container:
    python -m pytest /app/dagster/tests/test_quality_tagger.py -q
"""

from __future__ import annotations

import pytest

from dagster_platform.quality_tagger import QUALITY_PROVIDER, compute_quality_score


# ---------------------------------------------------------------------------
# compute_quality_score() tests
# ---------------------------------------------------------------------------


def test_score_zero_tokens() -> None:
    """token_count=0 → score=0.0"""
    assert compute_quality_score(0) == 0.0


def test_score_at_budget() -> None:
    """token_count=512 → score=1.0 (exactly at the cap boundary)"""
    assert compute_quality_score(512) == 1.0


def test_score_above_budget() -> None:
    """token_count=1024 → score=1.0 (capped at 1.0)"""
    assert compute_quality_score(1024) == 1.0


def test_score_range() -> None:
    """Any token_count → 0.0 ≤ score ≤ 1.0"""
    for tc in [0, 1, 100, 256, 511, 512, 513, 1000, 10000]:
        score = compute_quality_score(tc)
        assert 0.0 <= score <= 1.0, f"score={score} out of range for token_count={tc}"


def test_provider_string() -> None:
    """QUALITY_PROVIDER constant must be 'length_heuristic'"""
    assert QUALITY_PROVIDER == "length_heuristic"
