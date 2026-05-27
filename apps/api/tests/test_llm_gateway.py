"""Unit tests for LLMGateway (F-028).

Tests cover mock mode (ANTHROPIC_API_KEY absent or empty), real mode (Anthropic
client mocked via unittest.mock), max_tokens default, and LLM_MODEL env var.

No real Anthropic API calls are made — all network interactions are mocked.

Run inside the fastapi container:
    python -m pytest /app/tests/test_llm_gateway.py -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dataplat_api.llm.gateway import LLMGateway


# ---------------------------------------------------------------------------
# Mock mode — no ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------


def test_mock_mode_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTHROPIC_API_KEY unset → content="0.5", model="mock"."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gw = LLMGateway()
    result = asyncio.run(gw.complete([{"role": "user", "content": "Rate: test"}]))
    assert result.content == "0.5"
    assert result.model == "mock"


def test_mock_mode_empty_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTHROPIC_API_KEY="" → same mock response."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    gw = LLMGateway()
    result = asyncio.run(gw.complete([{"role": "user", "content": "Rate: test"}]))
    assert result.content == "0.5"
    assert result.model == "mock"


# ---------------------------------------------------------------------------
# Real mode — Anthropic client mocked
# ---------------------------------------------------------------------------


def test_real_mode_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic client mocked; verifies complete() calls messages.create() and returns correct LLMResponse."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    mock_text_block = MagicMock()
    mock_text_block.text = "0.75"
    mock_resp = MagicMock()
    mock_resp.content = [mock_text_block]
    mock_resp.model = "claude-3-haiku-20240307"

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()
        result = asyncio.run(gw.complete([{"role": "user", "content": "Rate: hello world"}]))

    assert result.content == "0.75"
    assert result.model == "claude-3-haiku-20240307"
    mock_client.messages.create.assert_called_once()


def test_max_tokens_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default max_tokens=16 is passed to the Anthropic SDK."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    mock_text_block = MagicMock()
    mock_text_block.text = "0.5"
    mock_resp = MagicMock()
    mock_resp.content = [mock_text_block]
    mock_resp.model = "claude-3-haiku-20240307"

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()
        asyncio.run(gw.complete([{"role": "user", "content": "test"}]))

        call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs.get("max_tokens") == 16


def test_model_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM_MODEL="claude-3-opus-20240229" env var is respected."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setenv("LLM_MODEL", "claude-3-opus-20240229")

    mock_text_block = MagicMock()
    mock_text_block.text = "0.5"
    mock_resp = MagicMock()
    mock_resp.content = [mock_text_block]
    mock_resp.model = "claude-3-opus-20240229"

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()
        asyncio.run(gw.complete([{"role": "user", "content": "test"}]))

        call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs.get("model") == "claude-3-opus-20240229"
