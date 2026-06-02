# Mode B Review — S039-F-039 `GET /api/recipes/{id}`

**Commit:** `8cfc5d1`
**Reviewer:** Mode B (post-implementation)
**Against:** `contracts/S039-F-039/agreed.md`

---

## APPROVED

No blockers, no HIGH findings, no MEDIUM findings. Full verification below.

---

## Findings

### NIT 1 — `summary=` dropped from decorator ✅ APPLIED

`agreed.md` NIT 1: Drop `summary="Get Recipe"` from `@router.get("/{id}", ...)`.

**Evidence (recipes.py +109):**
```python
@router.get("/{id}", response_model=RecipeOut)
```
No `summary=` kwarg present. Symmetric with the existing `list_recipes` and `create_recipe` decorators above it. ✅

**Note:** The OpenAPI spec (`openapi.json`) still shows `"summary": "Get Recipe"` at line +15 — that is FastAPI's auto-derived summary from the function name `get_recipe`, not a kwarg on the decorator. This is correct and expected behaviour; it does not violate NIT 1.

---

### NIT 2 — `_make_recipe_detail` defined locally with duplication comment ✅ APPLIED

`agreed.md` NIT 2: Define `_make_recipe_detail` locally in `test_recipes_get.py` with a header comment explaining the intentional duplication.

**Evidence (test_recipes_get.py lines 60–61 + 70–86):**

The file-level module docstring contains at lines 28–33:
```
Mock factory note:
  _make_recipe_detail is defined locally in this file for self-containment.
  The duplication relative to _make_recipe in test_recipes_list.py is intentional
  (mirrors the F-038 test file convention; do not delete as dead code).  The
  detail factory populates all 7 ORM attributes — including owner_id and
  definition — because RecipeOut reads all of them.
```

And at lines 60–63, directly above the function definition:
```python
# Intentional duplication: _make_recipe_detail is defined locally for
# self-containment, mirroring the F-038 convention in test_recipes_list.py.
# Do NOT delete as dead code — agreed.md Mode A NIT 2.
```

Function is present, locally defined, all 7 fields set. ✅

---

### NIT 3 — Multi-line docstring on `get_recipe` matches agreed.md template ✅ APPLIED

`agreed.md` NIT 3: Add the multi-line docstring capturing owner-scope rationale, no-leak design, field count, and auth requirement.

**Evidence (recipes.py +113–122):**
```python
    """Return the full recipe record for the given id.

    Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
    that a non-existent id and an id owned by another user both return 404
    (no-enumeration-leak, mirrors get_source / list_sources_by_collection).

    Returns ``RecipeOut`` (all 7 fields including ``definition``).

    Auth required (F-008).
    """
```

Word-for-word match to the `agreed.md` template. ✅

---

## Verification criteria

### V1 — 200 returns all fields including `definition` ✅

`test_get_recipe_200_returns_full_record` (test_recipes_get.py lines 115–148):

- Overrides `get_current_user` → user `id=7`.
- Builds `recipe_row` via `_make_recipe_detail(id=42, name="my-sft", description="SFT recipe", owner_id=7, definition={"steps": ["tokenize", "pack"]})`.
- GETs `/api/recipes/42`.
- Asserts `status_code == 200`.
- Asserts all 7 keys present: `id, name, description, owner_id, definition, created_at, updated_at` (iterated in a loop with `assert key in body`).
- Asserts `body["id"] == 42`, `body["definition"] == {"steps": ["tokenize", "pack"]}`, `body["owner_id"] == 7`.

V1 is fully covered. ✅

### V2 — `99999` returns 404 ✅

`test_get_recipe_not_found_returns_404` (test_recipes_get.py lines 151–164):

- Overrides `get_current_user` → user `id=7`.
- Session `scalar_one_or_none` returns `None`.
- GETs `/api/recipes/99999`.
- Asserts `status_code == 404`.
- Asserts `response.json() == {"detail": "Recipe not found"}` (exact match).

V2 is fully covered. ✅

---

## Edge cases

### 401 — no token ✅

`test_get_recipe_no_token_returns_401` (lines 193–205):

- No `get_current_user` override — real `oauth2_scheme` with `auto_error=True`.
- GETs `/api/recipes/42` without `Authorization` header.
- Asserts `status_code == 401`.
- Asserts `response.headers.get("WWW-Authenticate") == "Bearer"`.

✅

### 422 — non-integer id ✅

`test_get_recipe_invalid_id_returns_422` (lines 208–222):

- GETs `/api/recipes/not-an-int`.
- Asserts `status_code == 422` (FastAPI path-param validation fires before handler body).

✅

### Wrong owner → 404 (no-enumeration-leak) ✅

`test_get_recipe_wrong_owner_returns_404` (lines 167–190):

- `get_current_user` → user `id=7`; session returns `None` (simulates DB miss for row owned by `id=99`).
- GETs `/api/recipes/1`.
- Asserts `status_code == 404` with `{"detail": "Recipe not found"}` — indistinguishable from "not found". No 403 leak.

✅

### Structural — `owner_id` in compiled query ✅

`test_get_recipe_owner_id_in_query` (lines 225–264):

- Captures the `AsyncMock` session via `captured_session` list.
- Calls `client.get("/api/recipes/5")`.
- Asserts `session.execute.call_count == 1` (single execute per agreed.md).
- Compiles the captured `Select` with `literal_binds=True`.
- Asserts `"owner_id" in compiled` and `str(7) in compiled` (the mock user's id literal).

Mirrors the F-038 structural test pattern. ✅

---

## Invariant checks (calibration cases)

### CAL-1 / Invariant #5 — Async SQLAlchemy ✅

**Handler (recipes.py +124–127):**
```python
result = await session.execute(
    select(Recipe)
    .where(Recipe.id == id)
    .where(Recipe.owner_id == current_user.id)
)
recipe = result.scalar_one_or_none()
```

- `await session.execute(...)` — async ✅
- `result.scalar_one_or_none()` — synchronous call on the result proxy (correct; the proxy itself is not a coroutine) ✅
- No `session.query()` anywhere in the diff ✅
- No sync `session.commit()` (no write path in this endpoint) ✅

CAL-1 PASS.

### CAL-3 / Invariant #6 — OpenAPI regenerated in same commit ✅

`packages/api-types/openapi.json` appears in the diff stat of commit `8cfc5d1` alongside `routers/recipes.py`. The JSON diff adds the `/api/recipes/{id}` path block with correct `operationId`, `parameters` (`id: integer, in: path, required: true`), `response_model` (`$ref: RecipeOut`), and `security` (`OAuth2PasswordBearer`). No manual JSON edits — consistent with `make codegen` output.

CAL-3 PASS.

### CAL-2 — LLM gateway ✅

No `import anthropic`, `import openai`, or direct HTTP calls to LLM APIs anywhere in the diff. N/A for a read-only recipe endpoint. PASS.

### CAL-4 — Lineage completeness ✅

No `Commit` object is created in this endpoint (read-only). N/A. PASS.

### CAL-5 — CAS path discipline ✅

No blob storage operations. N/A. PASS.

### CAL-6 — Schema freeze ✅

No Pydantic schema files changed. `RecipeOut` is reused as-is. No migration. PASS.

### CAL-8 — MVP scope discipline ✅

No out-of-scope features introduced. PASS.

### CAL-10 — Test coverage ✅

6 new tests covering: happy path (200), two failure modes (404 not-found, 404 wrong-owner), auth gate (401), param validation (422), structural SQL correctness. Exceeds minimum. PASS.

---

## Files-changed cross-check

| File | agreed.md intent | Present in diff? | Correct? |
|------|-----------------|-----------------|---------|
| `apps/api/dataplat_api/routers/recipes.py` | Add `get_recipe`; update module docstring | ✅ | ✅ |
| `apps/api/tests/test_recipes_get.py` | New file; 6 test functions | ✅ | ✅ |
| `packages/api-types/openapi.json` | Regenerated by `make codegen`; same commit | ✅ | ✅ |
| `claude-progress.txt` | Sprint progress entries | ✅ | ✅ |
| `schemas/recipes.py` | No change | Not in diff ✅ | — |
| `db/models.py` | No change | Not in diff ✅ | — |
| Any migration | No change | Not in diff ✅ | — |

No unintended files modified.

---

## Final summary

The implementation is clean, minimal, and letter-perfect against `agreed.md`. All three Mode A NITs are applied correctly. Both verification criteria (V1, V2) are covered with precise assertions. All four edge cases (401, 422, wrong-owner→404, owner_id-in-query structural) are present and well-reasoned. Invariants #5 and #6 are satisfied — async session usage is correct throughout, and `openapi.json` is regenerated in the same commit. No scope creep, no schema changes, no migration. CAL-1 through CAL-10 checked with no violations found.

APPROVED
