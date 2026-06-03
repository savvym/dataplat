# Verification Report — S041-F-041: POST /api/recipes/{id}/preview

**Verifier:** verifier  
**Date:** 2026-06-03  
**Commit:** e08e689  
**Status:** PASS  

---

## Layered Checks Summary

### 1. Smoke Test (`bash verify/checks.sh smoke`)

```
--- smoke: C1 API health ---
smoke C1 API health: OK
--- smoke: C2 DB connection ---
smoke C2 DB connection: OK (via FastAPI lifespan)
--- smoke: C3 MinIO connectivity ---
smoke C3 MinIO connectivity: OK
--- smoke: C4 Dagster connectivity ---
smoke C4 Dagster connectivity: OK
✓ smoke passed
```

**Exit code: 0** ✓

### 2. Backend Layer (`bash verify/checks.sh backend`)

```
▶ cd apps/api && uv run ruff check .
All checks passed!
▶ cd apps/api && uv run mypy dataplat_api
Success: no issues found in 40 source files
▶ cd apps/api && uv run pytest -q
........................................................................ [ 28%]
........................................................................ [ 56%]
........................................................................ [ 85%]
.....................................                                    [100%]
=============================== warnings summary ===============================
tests/test_auth.py::test_collections_wrong_key_returns_401
  /data/home/zhhdzhang/nta/dataplat/apps/api/.venv/lib/python3.12/site-packages/jwt/api_jwt.py:147: InsecureKeyLengthWarning: The HMAC key is 30 bytes long, which is below the minimum recommended length of 32 bytes for SHA256. See RFC 7418 Section 3.2.
    return self._jws.encode(

-- Docs: https://docs.pytest.org/en/capture-pytest.html
253 passed, 1 deselected, 1 warning in 5.11s
✓ backend passed
```

**Exit code: 0** ✓

- **Ruff:** clean (0 violations)
- **Mypy:** clean (0 issues in 40 files)
- **Pytest:** **253 passed** (baseline 236 + 17 new tests = 253) ✓

### 3. Recipe Preview Tests (`cd apps/api && uv run pytest tests/test_recipes_preview.py -v`)

All 17 tests PASSED in 2.81s:

```
tests/test_recipes_preview.py::test_preview_200_returns_samples PASSED   [  5%]
tests/test_recipes_preview.py::test_preview_sample_shape_sft_qa PASSED   [ 11%]
tests/test_recipes_preview.py::test_preview_completes_under_30s PASSED   [ 17%]
tests/test_recipes_preview.py::test_preview_n_samples_5 PASSED           [ 23%]
tests/test_recipes_preview.py::test_preview_n_samples_too_low PASSED     [ 29%]
tests/test_recipes_preview.py::test_preview_n_samples_too_high PASSED    [ 35%]
tests/test_recipes_preview.py::test_preview_requires_auth PASSED         [ 41%]
tests/test_recipes_preview.py::test_preview_wrong_owner_404 PASSED       [ 47%]
tests/test_recipes_preview.py::test_preview_nonexistent_recipe_404 PASSED [ 52%]
tests/test_recipes_preview.py::test_preview_unsupported_template_400 PASSED [ 58%]
tests/test_recipes_preview.py::test_preview_missing_schema_template_400 PASSED [ 64%]
tests/test_recipes_preview.py::test_preview_lance_error_400 PASSED       [ 70%]
tests/test_recipes_preview.py::test_preview_no_chunks_400 PASSED         [ 76%]
tests/test_recipes_preview.py::test_preview_llm_parse_fail_with_fallback PASSED [ 82%]
tests/test_recipes_preview.py::test_preview_llm_parse_fail_no_fallback_502 PASSED [ 88%]
tests/test_recipes_preview.py::test_preview_bad_prompt_template_field_returns_400 PASSED [ 94%]
tests/test_recipes_preview.py::test_preview_owner_scoping_sql PASSED     [100%]

============================== 17 passed in 2.81s ==============================
```

**Count: 17/17 tests passed** ✓

### 4. Full Backend Suite (`cd apps/api && uv run pytest -q`)

**253 passed** (236 baseline + 17 new F-041 tests) ✓

---

## Verification Criteria Mapping (agreed.md §2)

All 17 tests map precisely to the contract verification criteria:

| ID | Criterion | Test Method | Status |
|----|-----------|-------------|--------|
| **V1** | HTTP 200 + 3–5 samples | `test_preview_200_returns_samples` | ✓ PASS |
| **V2** | Each sample has `"instruction"` + `"output"` (str) | `test_preview_sample_shape_sft_qa` | ✓ PASS |
| **V3** | Completes in under 30s (deterministic mock) | `test_preview_completes_under_30s` | ✓ PASS |
| **A1** | `n_samples=5` → 5 items | `test_preview_n_samples_5` | ✓ PASS |
| **A2** | `n_samples=2` → 422 | `test_preview_n_samples_too_low` | ✓ PASS |
| **A3** | `n_samples=6` → 422 | `test_preview_n_samples_too_high` | ✓ PASS |
| **A4** | No bearer token → 401 | `test_preview_requires_auth` | ✓ PASS |
| **A5** | Wrong owner → 404 (no-leak) | `test_preview_wrong_owner_404` | ✓ PASS |
| **A6** | Nonexistent recipe → 404 | `test_preview_nonexistent_recipe_404` | ✓ PASS |
| **A7** | Unsupported template → 400 | `test_preview_unsupported_template_400` | ✓ PASS |
| **A8** | Missing `schema.template` → 400 | `test_preview_missing_schema_template_400` | ✓ PASS |
| **A9** | Lance I/O error → 400 | `test_preview_lance_error_400` | ✓ PASS |
| **A10** | Zero chunks → 400 | `test_preview_no_chunks_400` | ✓ PASS |
| **A11** | LLM parse fail + fallback → 200 | `test_preview_llm_parse_fail_with_fallback` | ✓ PASS |
| **A12** | LLM parse fail, no fallback → 502 | `test_preview_llm_parse_fail_no_fallback_502` | ✓ PASS |
| **A13** | Bad prompt template field → 400 | `test_preview_bad_prompt_template_field_returns_400` | ✓ PASS |
| **A14** | SQL structural (owner-scoped) | `test_preview_owner_scoping_sql` | ✓ PASS |

---

## OpenAPI Sync Verification

```bash
$ jq '.paths["/api/recipes/{id}/preview"].post != null' packages/api-types/openapi.json
true

$ jq '.components.schemas["RecipePreviewRequest"] != null and .components.schemas["RecipePreviewResponse"] != null' packages/api-types/openapi.json
true
```

✓ OpenAPI regenerated per invariant #6  
✓ `POST /api/recipes/{id}/preview` path present  
✓ `RecipePreviewRequest` schema present (n_samples with bounds)  
✓ `RecipePreviewResponse` schema present (samples array)  

---

## Contract Conformance

All criteria from `agreed.md` §2 implemented and verified:

### Handler outline (§5)
- ✓ Owner-scoped load: `SELECT WHERE Recipe.id == id AND Recipe.owner_id == current_user.id`
- ✓ Template validation: `.get()` key extraction, 400 on absent/unsupported
- ✓ Extract filter: `filter.where` optional
- ✓ Lance query via `run_preview(where_clause, n_samples, template, config, llm)`
- ✓ Concurrent LLM calls: `asyncio.gather(*[_generate_sft_qa(...) for chunk in chunks])`
- ✓ Return `RecipePreviewResponse(samples=samples)`

### Schema (§4)
- ✓ `RecipePreviewRequest(n_samples: int = Field(default=3, ge=3, le=5))`
- ✓ `RecipePreviewResponse(samples: list[dict[str, Any]])`

### Dispatch table (§6)
- ✓ `_TEMPLATE_HANDLERS = {"sft_synthesis_qa": _generate_samples_sft_synthesis_qa}`
- ✓ `run_preview` entry-point with Lance I/O in `asyncio.to_thread`
- ✓ `_generate_sft_qa` per-chunk helper with prompt render, LLM call, JSON parse, fallback logic
- ✓ `_PREVIEW_COLUMNS` list (7 fields)
- ✓ `_FALLBACK_INSTRUCTION_MAX_CHARS = 200`

### Error matrix (§7)
- ✓ 200: success
- ✓ 400: missing/unsupported template, Lance I/O, zero chunks, bad prompt field
- ✓ 401: auth (handled by `get_current_user`)
- ✓ 404: not-found/wrong-owner (owner-scoped, no leak)
- ✓ 422: Pydantic validation (n_samples bounds)
- ✓ 502: LLM non-JSON + no fallback

### Hard invariants
- ✓ **#1 Lineage:** N/A (read-only, no Commits)
- ✓ **#2 Storage separation:** No MinIO writes, no Postgres writes; all in-memory
- ✓ **#3 Schema freeze:** N/A (read-only)
- ✓ **#4 LLM via gateway:** `await llm.complete(...)` only; no direct SDK imports
- ✓ **#5 Async SQLAlchemy:** `await session.execute(...)`, Lance I/O in `asyncio.to_thread`
- ✓ **#6 OpenAPI sync:** Regenerated in same commit

### Mode B review findings
- ✓ N1: Plain string literal (no f-prefix) on 502 detail — correct implementation
- ✓ N2: Dual exception catch (PreviewError + LanceQueryError) in router — both branches present

---

## Implementation Quality

| Check | Result |
|-------|--------|
| Ruff | ✓ All checks passed |
| Mypy | ✓ Success (40 source files) |
| Test coverage | ✓ 17/17 tests pass (all criteria covered) |
| Backend suite | ✓ 253 passed (236 baseline + 17 new) |
| OpenAPI sync | ✓ Committed in same commit |
| Commit message | ✓ Descriptive, cites N1/N2 fixes |

---

## Verdict

✅ **PASS**

All 6 verification steps exit 0. All 17 tests pass. All contract criteria (V1-V3, A1-A14) mapped to test methods and verified. Hard invariants satisfied. OpenAPI synchronized. Mode B APPROVED. Sprint S041-F-041 is complete and ready.

**F-041 passes:true ready.**
