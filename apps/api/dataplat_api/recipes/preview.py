"""Recipe preview helpers — S041-F-041.

Provides ``run_preview``: fetch candidate chunks from Lance, call the LLM
gateway once per chunk (concurrently), and return a list of synthesised
sample dicts — **without writing anything to MinIO or Postgres**.

Template dispatch
-----------------
Templates are registered in ``_TEMPLATE_HANDLERS``. MVP ships only
``sft_synthesis_qa``. Adding a new template requires:
  1. Writing ``async def _generate_samples_<template>(chunks, config, llm)``.
  2. Registering it in ``_TEMPLATE_HANDLERS``.
  No changes to the router, schemas, or ``run_preview`` are needed.

Prompt substitution
-------------------
Prompts use Python's ``str.format(**chunk)`` — zero-dependency and sufficient
for simple ``{text}``, ``{source_id}`` style substitutions.  A field name that
does not appear in the fetched chunk dict raises ``PreviewError(400, ...)``.

Invariants
----------
* Invariant #2 (no blob bytes in Postgres, no MinIO writes): all intermediate
  data lives only in process memory and is discarded after the response.
* Invariant #4 (LLM calls through gateway): ``_generate_sft_qa`` accepts
  ``LLMGateway`` as a parameter; it never imports ``anthropic`` directly.
* Invariant #5 (async SQLAlchemy + asyncio.to_thread for Lance): Lance I/O
  runs inside ``asyncio.to_thread``; the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from dataplat_api.llm.gateway import LLMGateway
from dataplat_api.routers.chunks import LanceQueryError
from dataplat_api.storage.lance import get_or_create_chunks_table

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_PREVIEW_COLUMNS: list[str] = [
    "chunk_id",
    "source_id",
    "text",
    "token_count",
    "source_refs",
    "attr_quality_score",
    "attr_lang_code",
]

_FALLBACK_INSTRUCTION_MAX_CHARS: int = 200  # truncate chunk text for fallback instruction field

_DEFAULT_PROMPT_TEMPLATE: str = (
    "Generate an instruction-response pair from the following text.\n"
    'Respond with valid JSON: {{"instruction": "...", "output": "..."}}.\n\n'
    "Text:\n{text}"
)


# ---------------------------------------------------------------------------
# PreviewError
# ---------------------------------------------------------------------------


class PreviewError(Exception):
    """Raised by preview helpers when a request-level error should become an HTTPException."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

# Type alias for a template handler coroutine:
#   (chunks: list[dict], config: dict [= definition["schema"]["config"]], gateway: LLMGateway)
#   -> Awaitable[list[dict]]
TemplateHandler = Callable[
    [list[dict], dict, LLMGateway],
    Awaitable[list[dict]],
]


# ---------------------------------------------------------------------------
# sft_synthesis_qa per-chunk helper
# ---------------------------------------------------------------------------


async def _generate_sft_qa(
    chunk: dict,
    config: dict,
    llm: LLMGateway,
) -> dict:
    """Generate one instruction/output sample from a single chunk.

    1. Renders ``prompt_template`` (from ``config``, or the module default)
       via ``str.format(**chunk)``; KeyError → PreviewError(400).
    2. Calls ``await llm.complete(...)`` with the rendered prompt.
    3. Parses the response as JSON.  On failure:
       - If ``config["fallback_on_failure"]`` is true: returns a derived sample
         using the first ``_FALLBACK_INSTRUCTION_MAX_CHARS`` chars of chunk text.
       - Otherwise: raises ``PreviewError(502, "LLM returned non-JSON output
         and fallback_on_failure is false")``.
    4. Validates that the parsed dict has both ``"instruction"`` and ``"output"``
       keys; applies the same fallback/PreviewError(502) logic for bad shapes.

    Args:
        chunk:  One row from Lance (dict with keys from ``_PREVIEW_COLUMNS``).
        config: The ``definition["schema"]["config"]`` subsection of the recipe.
        llm:    LLMGateway instance (injected; never imports Anthropic directly).

    Returns:
        dict with at minimum ``"instruction"`` (str) and ``"output"`` (str).

    Raises:
        PreviewError: on template field mismatch (400) or unusable LLM response (502).
    """
    # Step 1 — Render prompt.
    prompt_template: str = config.get("prompt_template", _DEFAULT_PROMPT_TEMPLATE)
    try:
        rendered = prompt_template.format(**chunk)
    except KeyError as exc:
        field_name = exc.args[0]
        raise PreviewError(
            status_code=400,
            detail=f"Prompt template references unknown chunk field: '{field_name}'",
        ) from exc

    # Step 2 — LLM call.
    response = await llm.complete(
        messages=[{"role": "user", "content": rendered}],
        max_tokens=512,
    )

    # Steps 3 + 4 — Parse and validate response; apply fallback if configured.
    def _apply_fallback_or_raise() -> dict:
        """Return fallback sample or raise PreviewError(502) — shared by parse + shape errors."""
        if config.get("fallback_on_failure", False):
            return {
                "instruction": chunk.get("text", "")[:_FALLBACK_INSTRUCTION_MAX_CHARS],
                "output": response.content,
            }
        raise PreviewError(
            status_code=502,
            detail="LLM returned non-JSON output and fallback_on_failure is false",
        )

    try:
        parsed = json.loads(response.content)
    except json.JSONDecodeError:
        return _apply_fallback_or_raise()

    # Validate dict shape.
    if not isinstance(parsed, dict) or "instruction" not in parsed or "output" not in parsed:
        return _apply_fallback_or_raise()

    return {"instruction": parsed["instruction"], "output": parsed["output"]}


# ---------------------------------------------------------------------------
# sft_synthesis_qa handler (gather loop)
# ---------------------------------------------------------------------------


async def _generate_samples_sft_synthesis_qa(
    chunks: list[dict],
    config: dict,
    llm: LLMGateway,
) -> list[dict]:
    """Generate one sample per chunk concurrently via ``asyncio.gather``.

    Delegates each chunk to ``_generate_sft_qa``.  Using ``asyncio.gather``
    keeps total latency close to a single LLM round-trip (OQ-3 accepted).

    Args:
        chunks: List of chunk dicts from Lance (already limited to ``n_samples``).
        config: The ``definition["schema"]["config"]`` subsection of the recipe.
        llm:    LLMGateway instance.

    Returns:
        List of sample dicts (same length as ``chunks``).
    """
    return list(await asyncio.gather(*[_generate_sft_qa(chunk, config, llm) for chunk in chunks]))


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_TEMPLATE_HANDLERS: dict[str, TemplateHandler] = {
    "sft_synthesis_qa": _generate_samples_sft_synthesis_qa,
}


# ---------------------------------------------------------------------------
# run_preview — public entry-point called by the router
# ---------------------------------------------------------------------------


async def run_preview(
    where_clause: str | None,
    n_samples: int,
    template: str,
    config: dict,
    llm: LLMGateway,
) -> list[dict[str, Any]]:
    """Fetch candidate chunks from Lance and run the template handler.

    Steps:
      1. Validate template is registered in ``_TEMPLATE_HANDLERS``
         (router already checked for None/absent — raises PreviewError(400)).
      2. Fetch up to ``n_samples`` chunks from Lance via ``asyncio.to_thread``.
         Any Lance exception → ``LanceQueryError`` (router converts to HTTP 400).
      3. If zero chunks match: raise ``PreviewError(400, "No matching chunks...")``.
      4. Dispatch to ``_TEMPLATE_HANDLERS[template](chunks, config, llm)`` and return.

    Args:
        where_clause: DataFusion SQL filter string (or None for no filter).
        n_samples:    Number of samples to generate (3–5, Pydantic-validated upstream).
        template:     Template name (e.g. ``"sft_synthesis_qa"``).
        config:       Inner ``definition["schema"]["config"]`` subsection.
        llm:          LLMGateway instance.

    Returns:
        List of sample dicts (one per chunk, length == ``n_samples`` unless Lance
        returned fewer rows).

    Raises:
        PreviewError:   On unsupported template, zero-row result, or LLM error.
        LanceQueryError: On Lance I/O failure (router catches → HTTP 400).
    """
    # Step 1 — Template check (router checks for None first; this catches unsupported values).
    if template not in _TEMPLATE_HANDLERS:
        raise PreviewError(
            status_code=400,
            detail=f"Preview supports only 'sft_synthesis_qa' in MVP; got {template!r}",
        )

    # Step 2 — Lance I/O in asyncio.to_thread (invariant #5).
    def _fetch_chunks() -> list[dict]:
        try:
            table = get_or_create_chunks_table()
            q = table.search()
            if where_clause:
                q = q.where(where_clause)
            q = q.select(_PREVIEW_COLUMNS).limit(n_samples)
            return q.to_arrow().to_pylist()
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

    chunks: list[dict] = await asyncio.to_thread(_fetch_chunks)

    # Step 3 — Empty result.
    if not chunks:
        raise PreviewError(
            status_code=400,
            detail="No matching chunks for preview; check recipe filter.where",
        )

    # Step 4 — Dispatch.
    handler = _TEMPLATE_HANDLERS[template]
    return await handler(chunks, config, llm)
