# dataplat_api/llm/router.py
# F-028: Internal LLM completions endpoint.
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

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dataplat_api.llm.gateway import LLMGateway, get_llm_gateway

router = APIRouter(prefix="/api/internal/llm", include_in_schema=False)


class LLMCompletionRequest(BaseModel):
    """Request body for POST /api/internal/llm/completions."""

    messages: list[dict[str, str]]
    max_tokens: int = 16


class LLMCompletionResponse(BaseModel):
    """Response body for POST /api/internal/llm/completions."""

    content: str
    model: str


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
    """
    result = await gateway.complete(body.messages, body.max_tokens)
    return LLMCompletionResponse(content=result.content, model=result.model)
