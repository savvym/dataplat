# dataplat_api/llm/router.py
# F-028: Internal LLM completions endpoint.
# F-053: Error mapping (LLMAuthenticationError→502, LLMRateLimitError→429,
#         LLMError→502) and extended LLMCompletionResponse with token/cost fields.
#
# POST /api/internal/llm/completions
#
# This endpoint bridges Dagster → FastAPI → Anthropic SDK.  It is:
#   - Internal only (reachable within the Docker network; not exposed externally).
#   - Excluded from the public OpenAPI spec via include_in_schema=False on the
#     APIRouter — mandatory per D3 agreed.md to satisfy invariant #6 (make codegen
#     must produce an empty diff after this change).
#   - Unauthenticated — JWT checks would block Dagster (no user token exists in
#     asset execution context). Network-level isolation is the security boundary.
#
# F-028 design decision D-G (agreed.md):
#   include_in_schema=False is set at the ROUTER level, not per-route.
#   This is the required approach — do NOT move it to individual @router.post calls.

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dataplat_api.llm.gateway import (
    LLMAuthenticationError,
    LLMError,
    LLMGateway,
    LLMRateLimitError,
    get_llm_gateway,
)

router = APIRouter(prefix="/api/internal/llm", include_in_schema=False)


class LLMCompletionRequest(BaseModel):
    """Request body for POST /api/internal/llm/completions."""

    messages: list[dict[str, str]]
    max_tokens: int = 16


class LLMCompletionResponse(BaseModel):
    """Response body for POST /api/internal/llm/completions."""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


@router.post("/completions", response_model=LLMCompletionResponse)
async def completions(
    body: LLMCompletionRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
) -> LLMCompletionResponse:
    """Score a text via the LLM gateway.

    Delegates to LLMGateway.complete(). In mock mode (no ANTHROPIC_API_KEY),
    returns content="0.5", model="mock" without any Anthropic SDK calls.

    No authentication required — this endpoint is only reachable within the
    Docker network (Dagster → FastAPI inter-service call).

    Error mapping (F-053):
        LLMAuthenticationError → HTTP 502 (invalid API key)
        LLMRateLimitError      → HTTP 429 (in-process rate limit exceeded)
        LLMError               → HTTP 502 (upstream or generic LLM error)
    """
    try:
        result = await gateway.complete(body.messages, body.max_tokens)
    except LLMAuthenticationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f"LLM upstream error: {exc}")
    return LLMCompletionResponse(
        content=result.content,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
    )
