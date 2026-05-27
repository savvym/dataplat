# dataplat_api/llm/gateway.py
# F-028: LLMGateway — wraps Anthropic SDK.
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

import os
from dataclasses import dataclass

import anthropic


@dataclass
class LLMResponse:
    """Normalised response from the LLM gateway.

    Attributes:
        content: Raw text returned by the model (e.g. "0.85").
        model:   Model name that produced the response (e.g. "claude-3-haiku-20240307"),
                 or "mock" in CI when ANTHROPIC_API_KEY is absent.
    """

    content: str
    model: str


class LLMGateway:
    """Wraps the Anthropic Messages API; mock mode when ANTHROPIC_API_KEY absent.

    Thread-safe for async use (AsyncAnthropic manages its own connection pool).
    Reads configuration from environment at construction time:
        ANTHROPIC_API_KEY — absent/empty → mock mode
        LLM_MODEL         — default "claude-3-haiku-20240307"
    """

    def __init__(self) -> None:
        self._api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
        self._mock: bool = not self._api_key
        self._model: str = os.environ.get("LLM_MODEL", "claude-3-haiku-20240307")
        if not self._mock:
            # Only create the AsyncAnthropic client when a real API key exists.
            # Mock mode never touches Anthropic SDK code paths.
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

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
            LLMResponse with content (raw model text) and model (model name or "mock").
        """
        if self._mock:
            return LLMResponse(content="0.5", model="mock")

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=messages,  # type: ignore[arg-type]  # dict[str,str] is runtime-compatible with MessageParam
        )
        # For plain text completions (no tools/thinking), content[0] is always TextBlock.
        return LLMResponse(
            content=response.content[0].text,  # type: ignore[union-attr]
            model=response.model,
        )


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
