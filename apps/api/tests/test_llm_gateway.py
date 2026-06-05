"""Unit tests for LLMGateway (F-028) and LLM Gateway hardening (F-053).

Tests cover mock mode (ANTHROPIC_API_KEY absent or empty), real mode (Anthropic
client mocked via unittest.mock), max_tokens default, LLM_MODEL env var,
call-metadata log emission, authentication error path, rate-limit exceeded path,
and router HTTP error mappings.

No real Anthropic API calls are made — all network interactions are mocked.

Run inside the fastapi container:
    python -m pytest /app/tests/test_llm_gateway.py -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dataplat_api.llm.gateway import (
    LLMAuthenticationError,
    LLMGateway,
    LLMRateLimitError,
)
from dataplat_api.llm.gateway import get_llm_gateway
from dataplat_api.main import app


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
    mock_resp.usage.input_tokens = 10
    mock_resp.usage.output_tokens = 5

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()
        result = asyncio.run(
            gw.complete([{"role": "user", "content": "Rate: hello world"}])
        )

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
    mock_resp.usage.input_tokens = 5
    mock_resp.usage.output_tokens = 2

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
    mock_resp.usage.input_tokens = 5
    mock_resp.usage.output_tokens = 2

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()
        asyncio.run(gw.complete([{"role": "user", "content": "test"}]))

        call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs.get("model") == "claude-3-opus-20240229"


# ---------------------------------------------------------------------------
# F-053 T-L1: log emission in real mode
# ---------------------------------------------------------------------------


def test_llm_log_emitted_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-L1 — Real mode: logger.info called once with correct extra dict fields."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    mock_text_block = MagicMock()
    mock_text_block.text = "0.9"
    mock_resp = MagicMock()
    mock_resp.content = [mock_text_block]
    mock_resp.model = "claude-3-haiku-20240307"
    mock_resp.usage.input_tokens = 10
    mock_resp.usage.output_tokens = 5

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        gw = LLMGateway()

        with patch("dataplat_api.llm.gateway.logger") as mock_logger:
            result = asyncio.run(
                gw.complete([{"role": "user", "content": "Rate: hello"}])
            )

    # logger.info must have been called exactly once.
    mock_logger.info.assert_called_once()
    call_kwargs = mock_logger.info.call_args
    extra = call_kwargs.kwargs.get("extra") or call_kwargs[1].get("extra")
    assert extra["model"] == "claude-3-haiku-20240307"
    assert extra["input_tokens"] == 10
    assert extra["output_tokens"] == 5
    assert extra["estimated_cost_usd"] > 0.0

    # LLMResponse fields also populated.
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.estimated_cost_usd > 0.0


# ---------------------------------------------------------------------------
# F-053 T-L2: log emission in mock mode (no spurious WARNING)
# ---------------------------------------------------------------------------


def test_llm_log_emitted_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-L2 — Mock mode: logger.info called once with model='mock'; no WARNING."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gw = LLMGateway()

    with patch("dataplat_api.llm.gateway.logger") as mock_logger:
        result = asyncio.run(
            gw.complete([{"role": "user", "content": "Rate: test"}])
        )

    mock_logger.info.assert_called_once()
    call_kwargs = mock_logger.info.call_args
    extra = call_kwargs.kwargs.get("extra") or call_kwargs[1].get("extra")
    assert extra["model"] == "mock"
    assert extra["input_tokens"] == 0
    assert extra["output_tokens"] == 0
    assert extra["estimated_cost_usd"] == 0.0

    # No spurious "unknown model 'mock'" WARNING should be emitted.
    mock_logger.warning.assert_not_called()

    # LLMResponse fields correct.
    assert result.model == "mock"
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# F-053 T-L3: invalid API key raises LLMAuthenticationError
# ---------------------------------------------------------------------------


def test_invalid_api_key_raises_LLMAuthenticationError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-L3 — Anthropic raises AuthenticationError → LLMAuthenticationError surfaced."""
    import anthropic as _anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-invalid")

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        # Simulate SDK raising AuthenticationError (a subclass of APIStatusError).
        mock_client.messages.create = AsyncMock(
            side_effect=_anthropic.AuthenticationError(
                message="invalid x-api-key",
                response=MagicMock(status_code=401, headers={}),
                body={"error": {"type": "authentication_error"}},
            )
        )
        mock_cls.return_value = mock_client

        gw = LLMGateway()

        with pytest.raises(LLMAuthenticationError) as exc_info:
            asyncio.run(gw.complete([{"role": "user", "content": "test"}]))

    assert "LLM authentication failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# F-053 T-L4: rate limit exceeded raises LLMRateLimitError
# ---------------------------------------------------------------------------


def test_rate_limit_exceeded_raises_LLMRateLimitError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-L4 — Third call on _rate_limit_override=2 gateway raises LLMRateLimitError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    mock_text_block = MagicMock()
    mock_text_block.text = "0.5"
    mock_resp = MagicMock()
    mock_resp.content = [mock_text_block]
    mock_resp.model = "claude-3-haiku-20240307"
    mock_resp.usage.input_tokens = 1
    mock_resp.usage.output_tokens = 1

    with patch("dataplat_api.llm.gateway.anthropic.AsyncAnthropic") as mock_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        # Construct with a limit of 2 — third call must raise.
        gw = LLMGateway(_rate_limit_override=2)

        async def _run_calls() -> None:
            r1 = await gw.complete([{"role": "user", "content": "call 1"}])
            assert r1.content == "0.5"
            r2 = await gw.complete([{"role": "user", "content": "call 2"}])
            assert r2.content == "0.5"
            # Third call must raise LLMRateLimitError.
            with pytest.raises(LLMRateLimitError) as exc_info:
                await gw.complete([{"role": "user", "content": "call 3"}])
            assert "LLM rate limit exceeded" in str(exc_info.value)

        asyncio.run(_run_calls())


# ---------------------------------------------------------------------------
# F-053 T-L5: router returns 502 on LLMAuthenticationError
# ---------------------------------------------------------------------------


def test_router_invalid_key_returns_502() -> None:
    """T-L5 — Router maps LLMAuthenticationError → HTTP 502."""

    async def _mock_complete(*_args: object, **_kwargs: object) -> None:
        raise LLMAuthenticationError(
            "LLM authentication failed: invalid ANTHROPIC_API_KEY"
        )

    mock_gw = MagicMock()
    mock_gw.complete = _mock_complete

    app.dependency_overrides[get_llm_gateway] = lambda: mock_gw
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/internal/llm/completions",
                json={"messages": [{"role": "user", "content": "test"}]},
            )
    finally:
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 502
    assert "LLM authentication failed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# F-053 T-L6: router returns 429 on LLMRateLimitError
# ---------------------------------------------------------------------------


def test_router_rate_limit_returns_429() -> None:
    """T-L6 — Router maps LLMRateLimitError → HTTP 429."""

    async def _mock_complete(*_args: object, **_kwargs: object) -> None:
        raise LLMRateLimitError("LLM rate limit exceeded: 60 calls/minute")

    mock_gw = MagicMock()
    mock_gw.complete = _mock_complete

    app.dependency_overrides[get_llm_gateway] = lambda: mock_gw
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/internal/llm/completions",
                json={"messages": [{"role": "user", "content": "test"}]},
            )
    finally:
        app.dependency_overrides.pop(get_llm_gateway, None)

    assert response.status_code == 429
    assert "LLM rate limit exceeded" in response.json()["detail"]
