# Mode B Review — S041-F-041: Recipe Preview Endpoint

**Commit:** e08e689  
**Reviewer:** reviewer (Mode B)  
**Date:** 2026-06-03  
**Sprint:** S041-F-041  
**Feature:** F-041 — `POST /api/recipes/{id}/preview`

---

## Hard Invariants

### Invariant #2 — Storage separation, no MinIO writes from preview

Grep for `s3://`, `minio`, `boto`, `Parquet`, `flush`, `.write(`, `.put(` in the diff:
**zero matches**. The module docstring explicitly states "without writing anything to
MinIO or Postgres". All intermediate data (chunks list, LLM responses) lives only in
process memory. **PASS.**

### Invariant #4 — LLM calls go through the gateway

Grep for `import anthropic` / `from anthropic` across all modified/new files:
**zero matches**. `preview.py` imports `LLMGateway` from `dataplat_api.llm.gateway`.
`_generate_sft_qa` accepts `llm: LLMGateway` as a parameter and calls
`await llm.complete(messages=[{"role": "user", "content": rendered}], max_tokens=512)`.
No direct SDK usage anywhere. **PASS.**

### Invariant #5 — Async SQLAlchemy

- Router: `await session.execute(select(Recipe).where(...).where(...))` +
  `result.scalar_one_or_none()` — fully async. No `session.query()`. **PASS.**
- Lance I/O: wrapped in `asyncio.to_thread(_fetch_chunks)`. The synchronous
  `get_or_create_chunks_table()` call never blocks the event loop. **PASS.**

### Invariant #6 — OpenAPI ↔ TS type sync

The `packages/api-types/openapi.json` diff (same commit) contains:
- `/api/recipes/{id}/preview` POST path ✓
- `RecipePreviewRequest` schema (n_samples, integer, minimum 3, maximum 5, default 3) ✓
- `RecipePreviewResponse` schema (samples, array of objects) ✓

**PASS.**

---

## Contract Conformance

### Owner-scoped 404

Single `SELECT … WHERE Recipe.id == id AND Recipe.owner_id == current_user.id`.
Both not-found and wrong-owner collapse to `HTTP 404 "Recipe not found"` — no
enumeration leak. Identical to `get_recipe` / `update_recipe` precedent. **PASS.**

### 400 paths

| Trigger | Detail string | Verdict |
|---------|---------------|---------|
| Missing `schema.template` (router, Step 2) | `"Recipe definition missing required field: schema.template"` | ✓ PASS |
| Unsupported template (`run_preview` Step 1) | `f"Preview supports only 'sft_synthesis_qa' in MVP; got {template!r}"` — `!r` on `'cpt_plain'` produces `"'cpt_plain'"`, rendering to `"Preview supports only 'sft_synthesis_qa' in MVP; got 'cpt_plain'"` — matches contract exactly | ✓ PASS |
| Lance I/O exception | `f"Lance query error: {exc}"` (router `except LanceQueryError`) | ✓ PASS |
| Zero chunks | `"No matching chunks for preview; check recipe filter.where"` | ✓ PASS |
| Bad prompt template field (KeyError in `str.format`) | `f"Prompt template references unknown chunk field: '{field_name}'"` | ✓ PASS |

### 502 path — N1 verification

Line 153 of `preview.py`:
```python
detail="LLM returned non-JSON output and fallback_on_failure is false",
```
Plain string literal — **no `f`-prefix**. N1 correctly applied. **PASS.**

### N2 — Dual exception catch in router

```python
except PreviewError as exc:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
except LanceQueryError as exc:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Lance query error: {exc}") from exc
```
Both branches present. `LanceQueryError` is re-exported from `routers.chunks` (single
source of truth). **N2 PASS.**

### `n_samples` Field bounds

`n_samples: int = Field(default=3, ge=3, le=5)` — 422 is free, handled by Pydantic/
FastAPI, no custom handler needed. **PASS.**

### Dispatch table

`_TEMPLATE_HANDLERS = {"sft_synthesis_qa": _generate_samples_sft_synthesis_qa}`.
Only MVP handler registered. Clean extension point documented in module docstring.
**PASS.**

### `_PREVIEW_COLUMNS` list

```python
["chunk_id", "source_id", "text", "token_count", "source_refs", "attr_quality_score", "attr_lang_code"]
```
Seven columns, exactly matching the contract (§6.0). **PASS.**

### `_FALLBACK_INSTRUCTION_MAX_CHARS`

Named constant `= 200` at module level with inline comment. Used as
`chunk.get("text", "")[:_FALLBACK_INSTRUCTION_MAX_CHARS]`. No magic literal. **PASS.**

### `asyncio.gather` for parallel LLM calls (OQ-3)

`_generate_samples_sft_synthesis_qa` returns
`list(await asyncio.gather(*[_generate_sft_qa(chunk, config, llm) for chunk in chunks]))`.
All `n_samples` LLM calls fire concurrently. **PASS.**

### `str.format` substitution (OQ-2)

`rendered = prompt_template.format(**chunk)`. `KeyError` caught and converted to
`PreviewError(400, ...)`. **PASS.**

### LLM call message shape

`llm.complete(messages=[{"role": "user", "content": rendered}], max_tokens=512)`.
Matches the `{"role": "user", "content": rendered}` shape specified in contract §6.2.
**PASS.**

---

## Test Plan Conformance

All 17 test methods present (verified by `grep -c "^def test_"` → `17`).

| ID | Test method | Key assertion | Verdict |
|----|-------------|---------------|---------|
| V1 | `test_preview_200_returns_samples` | HTTP 200, `len(samples)==3` | ✓ |
| V2 | `test_preview_sample_shape_sft_qa` | each sample has `"instruction"`, `"output"` (str) | ✓ |
| V3 | `test_preview_completes_under_30s` | `asyncio.run(asyncio.wait_for(_run(), timeout=30))` | ✓ |
| A1 | `test_preview_n_samples_5` | 5 samples returned | ✓ |
| A2 | `test_preview_n_samples_too_low` | 422 for `n_samples=2` | ✓ |
| A3 | `test_preview_n_samples_too_high` | 422 for `n_samples=6` | ✓ |
| A4 | `test_preview_requires_auth` | 401 + `WWW-Authenticate: Bearer` | ✓ |
| A5 | `test_preview_wrong_owner_404` | `scalar_one_or_none=None` → 404 `"Recipe not found"` | ✓ |
| A6 | `test_preview_nonexistent_recipe_404` | same 404 detail | ✓ |
| A7 | `test_preview_unsupported_template_400` | exact detail string with `'cpt_plain'` | ✓ |
| A8 | `test_preview_missing_schema_template_400` | exact detail string | ✓ |
| A9 | `test_preview_lance_error_400` | `_raise_lance` → `RuntimeError` wrapped in `LanceQueryError` → 400; `"Lance query error"` in detail | ✓ |
| A10 | `test_preview_no_chunks_400` | empty Lance result → 400 exact detail | ✓ |
| A11 | `test_preview_llm_parse_fail_with_fallback` | 200, `sample["output"] == "NOT JSON AT ALL"` | ✓ |
| A12 | `test_preview_llm_parse_fail_no_fallback_502` | 502, exact detail string (no f-prefix) | ✓ |
| A13 | `test_preview_bad_prompt_template_field_returns_400` | 400 exact detail with field name | ✓ |
| A14 | `test_preview_owner_scoping_sql` | `literal_binds=True` compile asserts `"owner_id"` and `"7"` in SQL | ✓ |

**Mock patterns:**
- LLM gateway: `app.dependency_overrides[get_llm_gateway]` — correct dependency injection path. ✓
- Lance: `monkeypatch.setattr("dataplat_api.recipes.preview.get_or_create_chunks_table", ...)` — correct import-site patching. ✓
- Session: `AsyncMock` + `scalar_one_or_none` pattern consistent with existing test suite. ✓

**All 17 test cases present and correctly targeting the contract criteria.** PASS.

---

## Reported Deviations — Judgement

### Deviation (a): `_DEFAULT_PROMPT_TEMPLATE` uses `{{` / `}}` for literal braces

`str.format(**chunk)` expands `{...}` as field references; `{{` and `}}` are the
standard escape sequences to produce literal `{` and `}` in the output. This is the
**correct and only viable** implementation when using `str.format` (OQ-2 accepted).
It is NOT a deviation from the contract — the contract specifies the rendered output
shape, not the source-code escape convention. **NOT a deviation. Confirmed.**

### Deviation (b): V3 calls `run_preview(...)` directly via `asyncio.run(asyncio.wait_for(..., timeout=30))`

Agreed.md §2 says: `"assert by wrapping in asyncio.wait_for(coro, timeout=30) with mocked LLM"`.
The test does exactly that. Going through the ASGI stack would only add noise (JSON
serialisation, middleware overhead) without testing the time-critical path. The direct
coroutine invocation exercises the production codepath (`run_preview` → `asyncio.gather`
→ per-chunk LLM calls) more faithfully than an HTTP round-trip would. The spec's
intent — enforce the 30-second budget — is fully met. **NOT a deviation. Approved.**

---

## Style / Cleanup

- No `summary=` kwarg on `@router.post("/{id}/preview", ...)` decorator — FastAPI
  auto-generates "Preview Recipe" from the function name, consistent with all other
  routes in the file. ✓
- Module docstring is multi-line with three sections (Template dispatch, Prompt
  substitution, Invariants) — consistent with F-039 style. ✓
- No dead variables. No unused imports (all six imports in `preview.py` are referenced).
  Ruff + mypy clean per implementer report. ✓
- Type hints consistent with codebase: `str | None`, `list[dict[str, Any]]`,
  `Callable[..., Awaitable[...]]`, `TemplateHandler` alias. ✓
- `_apply_fallback_or_raise` inner function cleanly deduplicates the fallback/502
  logic shared between JSON parse failure and dict-shape failure — good design. ✓

---

## Findings

No blocking findings. Two informational notes for the record:

**INFO-1 (non-blocking):** The `run_preview` docstring comment at Step 1 says
"router already checked for None/absent — raises PreviewError(400)". This is accurate
but slightly imprecise: the router raises `HTTPException(400)` directly (not via
`PreviewError`) for the `None` template case. The defensive check in `run_preview`
for unsupported (non-None) templates is still the right design — belt-and-suspenders
for future callers that bypass the router. No code change needed.

**INFO-2 (non-blocking):** `A9` patches `get_or_create_chunks_table` with a function
that raises on call, not via the `.search()` chain. The `except Exception` inside
`_fetch_chunks` catches this `RuntimeError` and wraps it in `LanceQueryError`, which
propagates uncaught through `run_preview` to the router's `except LanceQueryError`
clause. The end-to-end error path is correct and the test assertion
(`"Lance query error" in detail`) is satisfied.

---

## Verdict

```
APPROVED
```

All hard invariants satisfied. All 17 contract criteria implemented and tested.
Both reported deviations are confirmed as correct implementation choices.
No blocking issues found. Sprint S041-F-041 is complete.
