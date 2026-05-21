---
name: llm-gateway
description: All LLM calls must go through apps/api/dataplat_api/llm/ gateway. Read whenever a feature involves calling an LLM (in processors, in API routes, anywhere).
---

# LLM gateway — single point of access

§11.7 #2: **不要把 LLM 调用散落在各个 processor。统一走 `apps/api/dataplat_api/llm/` 网关**. This enables: cost tracking, caching, retries, rate limiting, model A/B, audit logging.

## Calling from a processor

```python
def process(self, inputs, config, workspace, ctx):
    response = ctx.llm.call(
        model="claude-sonnet",  # alias resolved by gateway
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        cache_key=("qa-gen", inputs[0].commit_hash, hash(prompt)),
    )
    return response.text
```

`ctx.llm` is injected by the worker. It's a thin client to the gateway, not a direct LLM SDK.

## Calling from an API route

```python
from dataplat_api.llm import LLMGateway, get_llm_gateway

@router.post("/explain")
async def explain(
    body: ExplainRequest,
    llm: LLMGateway = Depends(get_llm_gateway),
):
    return await llm.call(model="claude-sonnet", messages=[...])
```

## Hard NOs

- `import anthropic` or `import openai` anywhere outside `apps/api/dataplat_api/llm/`.
- Setting `ANTHROPIC_API_KEY` env at processor level — credentials live in the gateway.
- Direct `httpx.post("https://api.anthropic.com...")` from app code.
- Bypassing the gateway "just for testing" — write a mock gateway instead.

## When to add a new model

Add it to the gateway's model registry. Do not hardcode raw model names in caller code; the gateway resolves aliases.
