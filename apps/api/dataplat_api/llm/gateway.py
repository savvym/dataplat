# dataplat_api/llm/gateway.py
# F-028: LLMGateway — wraps Anthropic SDK.
# F-053: typed errors, rate limiter, call-metadata logging.
#
# Hard invariant #4 (CLAUDE.md §"Hard invariants"):
#   This is the ONLY file in the entire codebase that may `import anthropic`.
#   No other file — no processor, no adapter, no router, no Dagster helper — may
#   import the Anthropic or OpenAI SDKs directly. All callers must either:
#     (a) call the internal FastAPI endpoint POST /api/internal/llm/completions, or
#     (b) inject an LLMGateway instance via FastAPI Depends(get_llm_gateway).
#
# Mock mode (CI-safe):
#   When ANTHROPIC_API_KEY is absent or empty, LLMGateway operates in mock mode.
#   complete() immediately returns LLMResponse(content="0.5", model="mock")
#   without making any Anthropic SDK calls or network requests. This enables CI
#   without burning API credits and without a separate mock flag.
#
# Singleton pattern:
#   get_llm_gateway() returns a module-level singleton. Exactly one AsyncAnthropic()
#   client is created for the lifetime of the process — no resource leak under load.

from __future__ import annotations

import asyncio
import collections
import logging
import os
import time
from dataclasses import dataclass

import anthropic

from dataplat_api.config import settings

# ---------------------------------------------------------------------------
# Module-level logger (structured logging — §3.4 of agreed.md)
# ---------------------------------------------------------------------------

logger = logging.getLogger("dataplat_api.llm")


# ---------------------------------------------------------------------------
# Exception hierarchy (§3.2 of agreed.md)
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base for all gateway-raised LLM errors."""


class LLMAuthenticationError(LLMError):
    """Raised when the API key is rejected by the upstream provider."""


class LLMRateLimitError(LLMError):
    """Raised when the in-process rate limiter rejects the call."""


# ---------------------------------------------------------------------------
# Price table (§3.5 of agreed.md)
# ---------------------------------------------------------------------------

_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # model_id: (price_per_million_input_tokens_usd, price_per_million_output_tokens_usd)
    "claude-3-haiku-20240307": (0.25, 1.25),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD for a call to the given model.

    If the model is not in _PRICE_TABLE, emits a WARNING and returns 0.0.
    Only called for real (non-mock) API calls — mock mode sets cost=0.0 directly
    to avoid a spurious "unknown model 'mock'" WARNING on every CI call.
    """
    if model not in _PRICE_TABLE:
        logger.warning("llm.call unknown model %r — estimated_cost_usd=0.0", model)
        return 0.0
    inp_price, out_price = _PRICE_TABLE[model]
    return (input_tokens * inp_price + output_tokens * out_price) / 1_000_000


# ---------------------------------------------------------------------------
# LLMResponse — extended dataclass (§3.1 of agreed.md)
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Normalised response from the LLM gateway.

    Attributes:
        content:           Raw text returned by the model (e.g. "0.85").
        model:             Model name that produced the response
                           (e.g. "claude-3-haiku-20240307"), or "mock" in CI.
        input_tokens:      Number of input tokens consumed (0 in mock mode).
        output_tokens:     Number of output tokens produced (0 in mock mode).
        estimated_cost_usd: Estimated USD cost for this call (0.0 in mock mode).
    """

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# LLMGateway
# ---------------------------------------------------------------------------


class LLMGateway:
    """Wraps the Anthropic Messages API; mock mode when ANTHROPIC_API_KEY absent.

    Thread-safe for async use (AsyncAnthropic manages its own connection pool).
    Reads configuration from environment at construction time:
        ANTHROPIC_API_KEY — absent/empty → mock mode
        LLM_MODEL         — default "claude-3-haiku-20240307"

    Rate limiting (real mode only):
        Enforces settings.LLM_RATE_LIMIT_PER_MINUTE (default 60) via a
        fixed-window sliding deque protected by asyncio.Lock.
        Raises LLMRateLimitError when the limit is exceeded.
        Bypassed entirely in mock mode (mock calls are free/instant).

    Error mapping (real mode only):
        anthropic.AuthenticationError     → LLMAuthenticationError
        anthropic.APIStatusError(401)     → LLMAuthenticationError
        any other anthropic.APIError      → LLMError
    """

    def __init__(self, _rate_limit_override: int | None = None) -> None:
        self._api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self._mock: bool = not self._api_key
        self._model: str = os.environ.get("LLM_MODEL", "claude-3-haiku-20240307")
        if not self._mock:
            # Only create the AsyncAnthropic client when a real API key exists.
            # Mock mode never touches Anthropic SDK code paths.
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

        # Rate limiter — only used in real mode; bypassed in mock mode.
        # _rate_limit_override is a test-only constructor parameter; when set it
        # takes precedence over settings.LLM_RATE_LIMIT_PER_MINUTE so tests can
        # exercise the limiter without mutating global Settings state.
        self._rate_limit: int = (
            _rate_limit_override
            if _rate_limit_override is not None
            else settings.LLM_RATE_LIMIT_PER_MINUTE
        )
        # asyncio.Lock() construction is safe outside event loop in Python 3.12+
        self._rate_lock: asyncio.Lock = asyncio.Lock()
        self._rate_window: collections.deque[float] = collections.deque()

    async def complete(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 16,
    ) -> LLMResponse:
        """Call the LLM and return a normalised LLMResponse.

        Args:
            messages:   Anthropic Messages API shape: [{"role": "user", "content": "..."}].
            max_tokens: Maximum tokens in the completion (default 16 — sufficient for a score).

        Returns:
            LLMResponse with content, model, input_tokens, output_tokens, and
            estimated_cost_usd.

        Raises:
            LLMRateLimitError:       When the in-process rate limit is exceeded (real mode).
            LLMAuthenticationError:  When the API key is rejected by Anthropic (real mode).
            LLMError:                For all other Anthropic SDK errors (real mode).
        """
        if self._mock:
            # Mock mode: return deterministic response, emit log, bypass rate limiter.
            # estimated_cost_usd is set to 0.0 directly — _estimate_cost() is NOT
            # called to avoid a spurious "unknown model 'mock'" WARNING on every CI run.
            input_tokens = 0
            output_tokens = 0
            estimated_cost_usd = 0.0
            logger.info(
                "llm.call model=%s input_tokens=%d output_tokens=%d estimated_cost_usd=%.6f",
                "mock",
                input_tokens,
                output_tokens,
                estimated_cost_usd,
                extra={
                    "model": "mock",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "estimated_cost_usd": estimated_cost_usd,
                },
            )
            return LLMResponse(
                content="0.5",
                model="mock",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
            )

        # Real mode — enforce rate limit before touching the Anthropic SDK.
        await self._check_rate_limit()

        # Call the Anthropic SDK; map exceptions to typed LLM errors.
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=messages,  # type: ignore[arg-type]  # dict[str,str] is runtime-compatible with MessageParam
            )
        except anthropic.AuthenticationError as exc:
            raise LLMAuthenticationError(
                "LLM authentication failed: invalid ANTHROPIC_API_KEY"
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 401:
                raise LLMAuthenticationError(
                    "LLM authentication failed: invalid ANTHROPIC_API_KEY"
                ) from exc
            raise LLMError(f"LLM upstream error: {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"LLM upstream error: {exc}") from exc

        # Extract token counts — usage is always present for non-streaming responses
        # on Anthropic SDK ≥ 0.20 (OQ-2: container has 0.104.1; fallback to 0 for safety).
        try:
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        except AttributeError:
            logger.warning(
                "llm.call usage field absent on response (SDK < 0.20?); "
                "input_tokens and output_tokens defaulting to 0"
            )
            input_tokens = 0
            output_tokens = 0

        # Cost estimation — only for real API calls (not mock mode).
        estimated_cost_usd = _estimate_cost(response.model, input_tokens, output_tokens)

        # For plain text completions (no tools/thinking), content[0] is always TextBlock.
        result = LLMResponse(
            content=response.content[0].text,  # type: ignore[union-attr]
            model=response.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )

        logger.info(
            "llm.call model=%s input_tokens=%d output_tokens=%d estimated_cost_usd=%.6f",
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.estimated_cost_usd,
            extra={
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "estimated_cost_usd": result.estimated_cost_usd,
            },
        )
        return result

    async def _check_rate_limit(self) -> None:
        """Enforce the per-minute fixed-window sliding rate limit.

        Acquires asyncio.Lock, prunes expired timestamps, raises LLMRateLimitError
        if at capacity, otherwise records the current call timestamp.

        Only called in real mode; mock mode bypasses this entirely.
        """
        now = time.monotonic()
        async with self._rate_lock:
            # Prune entries older than 60 seconds from the left.
            while self._rate_window and now - self._rate_window[0] >= 60.0:
                self._rate_window.popleft()
            # Check capacity.
            if len(self._rate_window) >= self._rate_limit:
                raise LLMRateLimitError(
                    f"LLM rate limit exceeded: {self._rate_limit} calls/minute"
                )
            # Record this call.
            self._rate_window.append(now)


# ---------------------------------------------------------------------------
# Module-level singleton factory (D-I from agreed.md)
# ---------------------------------------------------------------------------
# One AsyncAnthropic() client per process lifetime — prevents resource leaks
# (open file descriptors, connection pools, TLS state) under per-chunk calling
# patterns (~200 HTTP calls per source materialization).

_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    """Return the module-level LLMGateway singleton.

    FastAPI uses this as a dependency via Depends(get_llm_gateway).
    The singleton is created on first call and reused thereafter.
    """
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
