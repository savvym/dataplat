# Reviewer Feedback — S041-F-041 (Recipe Preview Endpoint)

**Mode:** A (pre-implementation review)  
**Reviewer:** Leader (inline)  
**Date:** 2026-06-03  
**Contract reviewed:** `contracts/S041-F-041/proposed.md`

---

## Overall verdict

**CHANGES_REQUESTED**

The contract is well-structured and covers the majority of the design surface correctly — owner-scoping, dispatch-table extensibility, OQ recommendations, and test-plan breadth are all solid. Four issues need resolution before implementation begins: one HIGH (a concrete schema/config parameter mismatch that will silently break custom `prompt_template`s), two MEDIUMs (missing `run_preview` function contract, missing test for a documented 400 path), and several LOW/NITs.

---

## Findings

### F1 [HIGH] — `schema_config` vs. `config` parameter mismatch will silently swallow custom prompt templates

**Location:** §5 Step 2 + §6 dispatch table + §6 generator docstring.

**Problem:**

§5 Step 2 extracts:
```python
schema_cfg = definition.get("schema", {})   # e.g. {"template": "sft_synthesis_qa", "config": {"prompt_template": "...", "fallback_on_failure": true}}
template   = schema_cfg.get("template")
```

§6 dispatch then says: "The router calls `handler = _TEMPLATE_HANDLERS[template]` and `await handler(chunks, schema_config, llm)`" — passing `schema_cfg` (the full schema section, including the `"template"` key).

But §6 generator is declared as:
```python
async def _generate_samples_sft_synthesis_qa(
    chunks: list[dict],
    config: dict,       # schema.config from recipe.definition   ← KEY COMMENT
    llm: LLMGateway,
) -> list[dict]:
```

And the per-chunk logic reads `config.get("prompt_template", _DEFAULT_PROMPT_TEMPLATE)` and `config.get("fallback_on_failure", False)`.

If `config` is actually the **full schema section** (`{"template": "sft_synthesis_qa", "config": {...}}`), then `config.get("prompt_template")` always returns `None`, and `config.get("fallback_on_failure")` always returns `False`. The custom `prompt_template` the user stored in `recipe.definition.schema.config.prompt_template` is **silently ignored in every call** — the default template is always used. This is a concrete, hard-to-notice bug.

The design doc §7.2 confirms: `sft_synthesis_qa` config keys are `synthesizer_model`, `prompt_template`, `fallback_on_failure` — these live at `definition["schema"]["config"]`, one level below what the router currently extracts.

**Remediation:**  
Choose one of two consistent options and apply it throughout §5 and §6:

**Option A (preferred — simpler generator):** The router extracts the config subsection before dispatching:
```python
# §5 Step 2 — extract both template and config
schema_sec = definition.get("schema", {})
template   = schema_sec.get("template")
config     = schema_sec.get("config", {})     # ← add this
...
# dispatch call
await handler(chunks, config, llm)            # config is schema.config, not schema
```
The `TemplateHandler` type alias comment becomes `(chunks, schema_config_subsection, gateway)`.

**Option B:** Pass `schema_cfg` (full section) and have the generator extract `config`:
```python
async def _generate_samples_sft_synthesis_qa(chunks, schema_cfg, llm):
    config = schema_cfg.get("config", {})
    ...
```

Either option is fine; **Option A** is cleaner because the generators never need to know about `template` or `template_version`. Update the `TemplateHandler` type alias comment to make the second argument explicit: `# chunks, schema_template_config (definition["schema"]["config"]), gateway`.

---

### F2 [MEDIUM] — `run_preview` function contract is undefined; error-propagation strategy ambiguous

**Location:** §3 New files, §5 Steps 4–5.

**Problem:**

§3 lists `apps/api/dataplat_api/recipes/preview.py` as a new file and says the router imports `run_preview` from it. §5 calls `await run_preview(...)` in Step 4 and says "inside `run_preview`, call `await asyncio.gather(...)`" in Step 5. But:

1. **No signature is provided.** The implementer doesn't know whether `run_preview` receives `(where_clause, n_samples, template, config, llm)`, `(recipe, n_samples, llm)`, or something else.

2. **Error-propagation strategy is unspecified.** The handler outline says Lance errors → 400 and empty chunks → 400. If `run_preview` raises `HTTPException` directly, it is tightly coupled to FastAPI and cannot be unit-tested without a running app context. If it raises a domain exception (e.g. `LanceQueryError`, already in `chunks.py`), the router converts it. If it returns an empty list, the router checks. The chunks.py precedent uses `LanceQueryError` inside `_execute` → caught by the router. The preview code should follow the same pattern.

**Remediation:**  
Add a §6.0 sub-section (or a note in §6) specifying:

```
run_preview(
    where_clause: str | None,
    n_samples: int,
    template: str,
    config: dict,              # schema.config subsection (after F1 fix)
    llm: LLMGateway,
) -> list[dict]

Raises:
  LanceQueryError  — re-raised as HTTP 400 by the router (same pattern as chunks.py)
  HTTPException(400, "No matching chunks...")  — raised by run_preview when Lance returns 0 rows
                                                 (acceptable since preview.py is an API helper;
                                                  OR return [] and let the router check — either is
                                                  acceptable but one must be chosen and documented)

Does NOT raise HTTPException for LLM failures — those are raised by the per-chunk coroutine
and propagate through asyncio.gather to the router's try/except.
```

Lock in the pattern (Option A: raise HTTPException for the empty-chunks case since it must return 400, raise `LanceQueryError` for Lance I/O errors so the router's existing catch applies). Add the signature and one-line docstring to §6.

---

### F3 [MEDIUM] — Missing test for "prompt template references unknown chunk field" 400 path

**Location:** §2 verification table, §7 error matrix.

**Problem:**

§7 error matrix documents `HTTP 400 "Prompt template references unknown chunk field: {field_name}"` as a distinct error path (§6 Step 1). Every other 400 path has a named test in §2. This one does not. The omission means the implementer has no specification for what the test should assert, and the verifier cannot confirm coverage.

**Remediation:**  
Add to §2 (after A12, renumber A13 to A14 or add as A13a):

| **A13a** | `prompt_template` references `{nonexistent_field}`, chunk dict lacks that field → 400 `"Prompt template references unknown chunk field: 'nonexistent_field'"` | `test_preview_bad_prompt_template_field_400` |

The mock for this test: override `definition["schema"]["config"]["prompt_template"]` to `"Text: {nonexistent_field}"`, mock Lance to return a chunk dict containing only `{"chunk_id": "c1", "text": "hello"}`, and assert 400 with the correct detail prefix.

---

### F4 [LOW] — Lance column selection for candidate chunks is unspecified

**Location:** §5 Step 4, §6 per-chunk logic Step 1.

**Problem:**

§5 says "Query Lance for candidate chunks" but does not specify which columns to fetch. The default `_DEFAULT_PROMPT_TEMPLATE` only needs `{text}`, but a custom `prompt_template` may reference `{source_id}`, `{attr_lang_code}`, etc. If only `text` is fetched, those custom references hit `KeyError` → 400 (documented and correct). However:

1. The test for A9 (Lance error) and A11 (fallback) mock the chunks list — the implementer needs to know what fields are in each chunk dict.
2. The fallback path in Step 3 calls `chunk.get("text", "")[:200]` and the 502 path calls `{chunk_id!r}` — both require `text` and `chunk_id` to be present.

**Remediation:**  
Add a single bullet to §6 (after the `_DEFAULT_PROMPT_TEMPLATE` constant definition):

> "Lance query: fetch at minimum `["chunk_id", "text", "source_id"]` — sufficient for the default template and for the fallback / 502 detail string. For simplicity the implementer MAY fetch all 24 columns (consistent with F-036 lineage handler). Document the column list as a module-level constant `_PREVIEW_COLUMNS` in `preview.py`."

This also resolves the ambiguity in what fields A11 and A12 test mocks must populate.

---

### F5 [LOW] — Per-chunk coroutine name (`_generate_sft_qa`) never formally defined

**Location:** §5 Step 5, §6.

**Problem:**

§5 Step 5 calls `asyncio.gather(*[_generate_sft_qa(chunk, config, llm) for chunk in chunks])`. This implies a private, per-chunk async function named `_generate_sft_qa`. §6 defines `_generate_samples_sft_synthesis_qa(chunks: list[dict], ...)` — a function that takes all chunks. The relationship between the two is implied but never stated.

The implementer could equally conclude:
- `_generate_sft_qa` is a private inner function inside `_generate_samples_sft_synthesis_qa`; or
- The dispatch-table handler signature should actually take a single chunk (breaking the `TemplateHandler` type alias).

**Remediation:**  
Add one sentence to §6, e.g.:

> "`_generate_samples_sft_synthesis_qa` (the registered handler) receives all `n_samples` chunks and manages the gather internally. It calls a private per-chunk helper `_generate_one_sft_qa(chunk: dict, config: dict, llm: LLMGateway) -> dict` via `asyncio.gather`. The name `_generate_sft_qa` used in §5 Step 5 refers to this private helper."

Alternatively rewrite §5 Step 5 to: "inside `_generate_samples_sft_synthesis_qa`, call `await asyncio.gather(*[_generate_one_sft_qa(chunk, config, llm) for chunk in chunks])`."

---

### F6 [NIT] — Test count in §3 says "15" but §2 table has 16 entries

**Location:** §3 New files table: "All 15 test cases listed in §2."

§2 contains: V1, V2, V3, A1–A13 = **16** entries, each with a distinct test method name. The "15" is an off-by-one that may confuse the implementer about how many tests to write.

**Remediation:** Change "all 15 test cases" to "all 16 test cases" (and to "all 17 test cases" after the F3 addition of A13a).

---

### F7 [NIT] — Invariant #1 (lineage) absent from §9 invariants checklist

**Location:** §9.

§9 lists invariants #2, #4, #5, #6 only. Per S039 agreed.md (invariants checklist) and S040 agreed.md (§10), invariant #1 is always listed explicitly — either as enforced or as N/A — so the reader can verify every invariant was considered.

**Remediation:** Add to §9:

| **#1 Lineage mandatory** | N/A — preview produces no Commit, no dataset row, and no lineage records. All intermediate data is discarded after the response. |

Also add for completeness:

| **#3 Schema frozen post-publish** | N/A — preview is read-only; no dataset row is created, so the freeze guard does not apply. |

---

### F8 [NIT] — Magic number `200` in fallback truncation should be a named constant

**Location:** §6 Step 3: `chunk.get("text", "")[:200]`.

A bare `200` is unexplained. Future contributors won't know whether it's a token budget, a character budget, or an arbitrary limit.

**Remediation:**  
Define at module level in `preview.py`:
```python
_FALLBACK_INSTRUCTION_MAX_CHARS: int = 200  # truncate chunk text for fallback instruction field
```
And use `chunk.get("text", "")[:_FALLBACK_INSTRUCTION_MAX_CHARS]` in Step 3.

---

### F9 [NIT] — `detail` string template in §6 and §7 use different notation for the same error

**Location:** §6 Step 3 vs. §7 error matrix, 502 row.

§6 Step 3: `"LLM returned unparseable output for chunk {chunk_id!r}"` (Python f-string `!r` notation).  
§7 error matrix: `"LLM returned unparseable output for chunk '{chunk_id}'"` (literal single quotes around the placeholder).

Both produce the same runtime string for string `chunk_id`s (e.g. `"LLM returned unparseable output for chunk 'abc123'"`) but the contract inconsistency may confuse the test author when writing the A12 `assert response.json()["detail"] == ...` line.

**Remediation:**  
Normalise §6 Step 3 to match §7 exactly: `f"LLM returned unparseable output for chunk '{chunk.get('chunk_id', 'unknown')}'"` (or document the exact format once with the safe `.get` fallback). The test `test_preview_llm_parse_fail_no_fallback_502` should include an exact-match assert quoted literally from §7.

---

## Items verified as correct (no findings)

The following were checked and are correct:

- **Invariant #4 (LLM gateway):** `llm: LLMGateway = Depends(get_llm_gateway)` in handler signature; `_generate_samples_sft_synthesis_qa` accepts `LLMGateway` as a parameter and never imports `anthropic` directly. ✅
- **Invariant #2 (no MinIO writes):** §5 and §9 both explicitly confirm in-memory-only. ✅
- **Invariant #5 (async SQLAlchemy):** `await session.execute(select(Recipe)...)` + `asyncio.to_thread` for Lance. ✅
- **Invariant #6 (OpenAPI sync):** `packages/api-types/openapi.json` explicitly listed in §3 modified-files table. ✅
- **Owner-scoping:** `select(Recipe).where(Recipe.id == id).where(Recipe.owner_id == current_user.id)` + `scalar_one_or_none()` → 404 for both cases. ✅
- **404 detail string:** `"Recipe not found"` — matches `get_recipe` / `update_recipe` exactly. ✅
- **SQL structural test (A13):** `literal_binds=True` owner_id assertion, mirrors F-038/F-039/F-040 precedent. ✅
- **Dispatch-table extensibility:** `dict[str, TemplateHandler]`; adding a template = one function + one dict entry; no router changes. ✅
- **MVP boundary respected:** Only `sft_synthesis_qa` registered; §10 explicitly defers all other templates. ✅
- **n_samples validation:** `Field(default=3, ge=3, le=5)` → 422 without custom handler. ✅
- **Defensive `.get()` key access:** `definition.get("schema", {}).get("template")` — no KeyError possible. ✅
- **Empty Lance result → 400:** OQ-1 decided and locked; UX rationale documented. ✅
- **502 for non-JSON with `fallback_on_failure=false`:** Semantically correct (upstream LLM failure). ✅
- **OQ-2 (`str.format` vs Jinja2):** `str.format` recommended, rationale adequate. ✅
- **OQ-3 (parallel gather):** asyncio.gather recommended, 30s budget rationale correct. ✅
- **OQ-5 (owner-scoping, no non-owner access):** Correctly deferred per §11.6. ✅
- **`summary=` kwarg:** Correctly omitted from `@router.post("/{id}/preview", ...)`. ✅
- **`packages/api-types/openapi.json` in files-changed:** Present. ✅
- **No `session.query()` anywhere:** All SQLAlchemy usage is via `await session.execute(select(...))`. ✅
- **Out-of-scope section (§10):** Other templates, rate limiting, JSON Schema validation of definition, streaming — all explicitly deferred. ✅
- **All 5 OQs answered:** Concrete recommendations given for each. ✅

---

## Summary table

| # | Severity | Location | One-line summary |
|---|----------|----------|-----------------|
| F1 | **HIGH** | §5 Step 2, §6 dispatch + generator | `schema_cfg` (full schema section) vs. `config` (nested subsection) mismatch silently breaks custom prompt templates |
| F2 | **MEDIUM** | §3, §5 Steps 4–5 | `run_preview` signature and error-propagation strategy (LanceQueryError vs. HTTPException) undefined |
| F3 | **MEDIUM** | §2, §7 error matrix | No test case for "Prompt template references unknown chunk field" 400 error despite being in the error matrix |
| F4 | **LOW** | §5 Step 4, §6 Step 1 | Lance column selection for candidate chunks unspecified; affects mock shapes in A11/A12 tests |
| F5 | **LOW** | §5 Step 5, §6 | Per-chunk helper `_generate_sft_qa` never formally named/typed; relationship to `_generate_samples_sft_synthesis_qa` only implicit |
| F6 | **NIT** | §3 | Test count says "15" but §2 has 16 entries (17 after F3) |
| F7 | **NIT** | §9 | Invariants #1 and #3 absent from checklist (precedent: S039/S040 list all 6 with N/A where not applicable) |
| F8 | **NIT** | §6 Step 3 | Magic number `200` in fallback truncation should be named constant `_FALLBACK_INSTRUCTION_MAX_CHARS` |
| F9 | **NIT** | §6 Step 3 vs §7 | `!r` notation in §6 vs. literal single-quote in §7 for the 502 detail string; normalise for exact-match test asserts |

---

## Required actions before proceeding to agreed.md

1. **F1 (HIGH):** Choose Option A or B for schema_cfg vs. config parameter; update §5 Step 2, §6 dispatch call, `TemplateHandler` comment, and generator parameter docstring to be 100% consistent.
2. **F2 (MEDIUM):** Add `run_preview` function signature (parameters + return type) and one-sentence exception-propagation contract to §6 (or a new §6.0).
3. **F3 (MEDIUM):** Add test case for bad prompt-template field reference (suggest A13a) to §2 table with exact detail string.
4. **F4 (LOW):** Add a bullet to §6 specifying the Lance column list (minimum: `chunk_id`, `text`, `source_id`; or "all 24 columns"). Recommend naming it `_PREVIEW_COLUMNS`.
5. **F5 (LOW):** Name the per-chunk helper explicitly (suggest `_generate_one_sft_qa`) and state that `_generate_samples_sft_synthesis_qa` contains the gather loop.

NITs F6–F9 should be folded into agreed.md but do not require a review round-trip.

**CHANGES_REQUESTED**

---

---

# APPENDIX — Round 2 Review (Mode A)

**Reviewer:** Leader (inline)  
**Date:** 2026-06-03  
**Contract reviewed:** `contracts/S041-F-041/proposed.md` (REVISED — addressing F1–F9)

---

## Round-2 findings: F1–F9 resolution check

### F1 [HIGH] — ✅ RESOLVED

**Evidence:**

§5 Step 2 now reads:
```python
schema_section = definition.get("schema", {})   # e.g. {"template": "sft_synthesis_qa", "config": {...}}
template       = schema_section.get("template")
config         = schema_section.get("config", {})  # inner dict passed to the per-template generator
```
This is precisely Option A from the remediation. The prose closes with: "The generator never receives the full `schema_section` — it sees only `config`."

§6.0 `run_preview` docstring: "passing `(chunks, config, llm)` — where `config` is the inner `definition["schema"]["config"]` subsection (NOT the full `schema_section`)."

§6.1 dispatch call: `return await handler(chunks, config, llm)  # config is schema.config subsection — NOT schema_section`

§6.1 `TemplateHandler` comment: `#   (chunks: list[dict], config: dict [= definition["schema"]["config"]], gateway: LLMGateway)`

§6.2 generator signature: `config: dict,  # definition["schema"]["config"] subsection`

All four locations are consistent. Bug is eliminated.

---

### F2 [MEDIUM] — ✅ RESOLVED

**Evidence:**

§6.0 now includes a full `run_preview` signature:
```python
async def run_preview(
    where_clause: str | None,
    n_samples: int,
    template: str,
    config: dict,
    llm: LLMGateway,
) -> list[dict[str, Any]]:
```
Four bullet exception-propagation rules are specified:
- Lance I/O errors → re-raise `LanceQueryError` (router catches → HTTP 400, consistent with `chunks.py`).
- Zero rows → raise `PreviewError(status_code=400, detail="No matching chunks...")`.
- LLM failures → raised as `PreviewError` by per-chunk coroutine, propagate through gather.
- Does NOT raise `HTTPException` directly.

The `PreviewError` class is fully specified with `status_code` and `detail` fields, and the router catch block is shown as a code snippet:
```python
try:
    samples = await run_preview(where_clause, body.n_samples, template, config, llm)
except PreviewError as exc:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)
```

The chosen pattern (domain exception `PreviewError` converted at the router boundary) is clear and testable without a live FastAPI app.

---

### F3 [MEDIUM] — ✅ RESOLVED

**Evidence:**

§2 row A13: `` `prompt_template = "{nonexistent_field}"`, chunk dict lacks that field → 400 `"Prompt template references unknown chunk field: 'nonexistent_field'"` `` → `test_preview_bad_prompt_template_field_returns_400`.

The former SQL structural test is now A14. The detail string matches §6.2 Step 1 and §7 exactly.

---

### F4 [LOW] — ✅ RESOLVED

**Evidence:**

§6.0 defines:
```python
_PREVIEW_COLUMNS: list[str] = [
    "chunk_id", "source_id", "text", "token_count",
    "source_refs", "attr_quality_score", "attr_lang_code",
]
```
With prose: "Any `{field_name}` in a custom `prompt_template` that is not present in the returned chunk dict raises a `KeyError` during `str.format`, which the per-chunk helper catches and converts to `PreviewError(400, ...)`."

Test mock shapes for A11/A12 (fallback and 502) now have an unambiguous set of required fields: at minimum `chunk_id` and `text` must be present.

---

### F5 [LOW] — ✅ RESOLVED

**Evidence:**

§6.2 contains an explicit name declaration:
> "The name `_generate_sft_qa` (referenced in §5 Step 5) refers to this private per-chunk function. It is **not** the same as `_generate_samples_sft_synthesis_qa` (which takes all chunks and manages the gather)."

Both function signatures are shown side-by-side in §6.2 with their full parameter lists. The dispatch-table handler (`_generate_samples_sft_synthesis_qa`) takes all chunks; the per-chunk helper (`_generate_sft_qa`) takes a single chunk. Relationship is unambiguous.

---

### F6 [NIT] — ✅ RESOLVED

**Evidence:**

§3 now reads: "All 17 test cases listed in §2."

Count verification: V1, V2, V3, A1–A14 = 3 + 14 = **17**. ✅

---

### F7 [NIT] — ✅ RESOLVED

**Evidence:**

§9 now contains all six invariants:

| # | Present | Content |
|---|---------|---------|
| #1 Lineage | ✅ | "N/A — preview produces no Commit, no dataset row..." |
| #2 Storage | ✅ | "Preview is entirely in-memory..." |
| #3 Schema freeze | ✅ | "N/A — preview is read-only; it never modifies a recipe definition..." |
| #4 LLM gateway | ✅ | "`llm: LLMGateway = Depends(get_llm_gateway)`..." |
| #5 Async SQLAlchemy | ✅ | "`await session.execute(select(Recipe).where(...))`..." |
| #6 OpenAPI sync | ✅ | "`make codegen` MUST be run..." |

All six present with appropriate N/A or enforcement rationale. ✅

---

### F8 [NIT] — ✅ RESOLVED

**Evidence:**

§6.0 defines:
```python
_FALLBACK_INSTRUCTION_MAX_CHARS: int = 200  # truncate chunk text for fallback instruction field
```

§6.2 Step 3 uses: `chunk.get("text", "")[:_FALLBACK_INSTRUCTION_MAX_CHARS]`. No bare magic number anywhere. ✅

---

### F9 [NIT] — ✅ RESOLVED

**Evidence:**

§6.2 Step 3: `detail=f"LLM returned non-JSON output and fallback_on_failure is false"`  
§7 error matrix 502 row: `"LLM returned non-JSON output and fallback_on_failure is false"`  
§2 A12 criterion: `→ 502 "LLM returned non-JSON output and fallback_on_failure is false"`

All three locations use identical string. Test author can write an exact-match assert without ambiguity. ✅

---

## Fresh sweep — new issues introduced by revisions

### N1 [NIT] — Dead `f`-prefix on §6.2 Step 3 `PreviewError` raise

**Location:** §6.2 Step 3.

`detail=f"LLM returned non-JSON output and fallback_on_failure is false"` — the string contains no `{...}` interpolation so the `f` prefix is dead code. At runtime this is harmless (Python silently accepts it), but a linter will warn and a careful implementer may wonder if a `{chunk_id}` was accidentally dropped.

**Verdict:** NIT. Does not block approval. Implementer should remove the `f` prefix in the actual source code; or if they intend to include a chunk identifier, they should do so deliberately. The exact-match test assert in A12 will catch any runtime mismatch.

---

### N2 [NIT] — Router `try/except` skeleton shows only the `PreviewError` branch; `LanceQueryError` branch is prose-only

**Location:** §6.0 router snippet vs. §5 Step 4.

The code block in §6.0 shows:
```python
try:
    samples = await run_preview(where_clause, body.n_samples, template, config, llm)
except PreviewError as exc:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail)
```

The `LanceQueryError` catch is specified in prose at §5 Step 4 ("the router catches `LanceQueryError` and converts to `HTTP 400 "Lance query error: {exc}"`") but is absent from the code snippet. An implementer who copies the snippet literally may forget the second except branch.

**Verdict:** NIT. Adequately covered by the §5 prose — a competent implementer will combine both catches. Does not block approval.

---

### N3 — `PreviewError` interaction with FastAPI exception handling: confirmed clean

`PreviewError` is a plain `Exception` subclass, not an `HTTPException` subclass. FastAPI's default exception handler will NOT catch it — it reaches the `except PreviewError` branch in the router, which then raises `HTTPException`. This is the intended path and is correctly specified. No interaction issue. ✅

### N4 — `TemplateHandler` type alias sync vs. async: confirmed clean

`TemplateHandler = Callable[[list[dict], dict, LLMGateway], Awaitable[list[dict]]]` — all registered handlers are `async def`, which return coroutines (coroutines satisfy `Awaitable`). The `await handler(...)` call in `run_preview` is correct Python. A sync callable that returned a coroutine-wrapping `Awaitable` would also satisfy the type but is not the intent; this is an inherent limitation of `Callable` + `Awaitable` typing in Python and is acceptable for MVP. ✅

---

## Round-2 summary

| Finding | Round-1 severity | Resolution status |
|---------|-----------------|------------------|
| F1 schema_cfg vs. config | HIGH | ✅ RESOLVED |
| F2 run_preview contract | MEDIUM | ✅ RESOLVED |
| F3 missing bad-field test | MEDIUM | ✅ RESOLVED |
| F4 Lance column projection | LOW | ✅ RESOLVED |
| F5 per-chunk helper name | LOW | ✅ RESOLVED |
| F6 test count off-by-one | NIT | ✅ RESOLVED |
| F7 invariants #1/#3 absent | NIT | ✅ RESOLVED |
| F8 magic number 200 | NIT | ✅ RESOLVED |
| F9 detail string inconsistency | NIT | ✅ RESOLVED |
| **N1** dead f-prefix | NIT (new) | Carry to agreed.md |
| **N2** incomplete try/except snippet | NIT (new) | Carry to agreed.md |

**No new BLOCKER or HIGH issues found.** All round-1 blockers and MEDIUMs are resolved. Two new NITs (N1, N2) are carry-forward items — they must appear in `agreed.md` as implementation notes but do not require another review round-trip.

---

## Verified correct (round-2 re-check of items already green)

- **Invariant #4 (LLM gateway):** `_generate_sft_qa` takes `LLMGateway` as parameter; §6.2 explicitly states it never imports `anthropic` directly. ✅
- **Invariant #2 (no MinIO writes):** §5, §9, and §6.0 run_preview prose all confirm in-memory-only. ✅
- **Invariant #5 (async SQLAlchemy):** `await session.execute(select(Recipe)...)` in §5 Step 1; `asyncio.to_thread` for Lance. ✅
- **Invariant #6 (OpenAPI sync):** `packages/api-types/openapi.json` in §3 modified files. ✅
- **Owner-scoping:** `where(Recipe.owner_id == current_user.id)` + `scalar_one_or_none()` pattern unchanged. ✅
- **SQL structural test A14:** `literal_binds=True` assert. ✅
- **17 tests total:** V1–V3, A1–A14 counted. ✅
- **n_samples bounds:** `Field(ge=3, le=5)`. ✅
- **MVP scope:** only `sft_synthesis_qa` registered; §10 explicit deferrals intact. ✅

---

APPROVED
