# Mode B Review — S040-F-040

**Verdict: APPROVED**

Reviewed commit: `dfc2da4` — `feat(F-040): PUT /api/recipes/{id} with post-publish freeze guard`

---

## Findings

No blockers, no high or medium findings. Two low-severity observations and one nit follow; none require a code change before shipping.

**LOW-1 — OpenAPI `summary` auto-generated string present despite agreed "no `summary=` kwarg"**

The `@router.put("/{id}")` decorator correctly has no explicit `summary=` kwarg (F8 resolved, consistent with the other three handlers). However, FastAPI auto-generates a `summary` field in the emitted OpenAPI JSON (`"summary": "Update Recipe"`). This is normal FastAPI behaviour — auto-generation happens even when `summary=` is omitted from the decorator; the *intent* of F8 was symmetry at the *source level*, which is achieved. No action needed; this is informational only.

**LOW-2 — `# type: ignore[assignment]` on `updated_at` assignment**

```python
recipe.updated_at = datetime.now(tz=timezone.utc)  # type: ignore[assignment]
```

The `type: ignore` is presumably there because mypy infers `updated_at` as `datetime` (non-nullable) from the ORM column but the ORM column definition is typed differently in the model stubs. The implementer reports mypy clean, meaning this suppresses a genuine mypy complaint rather than hiding a real error; the runtime behaviour is correct. The agreed contract does not prohibit this suppression. Flag for the model-typing sprint to align the ORM column type annotation so the ignore becomes unnecessary.

**NIT-1 — `_make_session_dep_for_update` result unused in V1 test**

In `test_update_recipe_200_returns_updated_definition` the helper is called and assigned to `session_dep` then immediately shadowed by a fully inlined `_patched_session`. The dead assignment (`_ = session_dep`) is a code smell but does not affect correctness or coverage. Cosmetic.

---

## Invariant audit

| # | Invariant | Status | Evidence |
|---|---|---|---|
| **#3** | Schema frozen post-publish | ✅ ENFORCED | `select(exists().where(Dataset.recipe_id == recipe.id))` + `scalar_one()` — correct use of `EXISTS` (not `COUNT`). Returns bool; `if dataset_exists: raise HTTPException(409, ...)` fires before any mutation. Structural test `test_update_recipe_recipe_id_in_dataset_exists_query` independently verifies the compiled SQL contains `recipe_id` and the literal id value. |
| **#5** | Async SQLAlchemy from day one | ✅ ENFORCED | Handler signature uses `AsyncSession`; every DB operation is awaited (`await session.execute(...)` ×2, `await session.commit()`, `await session.refresh(...)`). No `session.query()` anywhere in the diff. |
| **#6** | OpenAPI ↔ TS type sync | ✅ ENFORCED | `packages/api-types/openapi.json` regenerated in the **same** commit `dfc2da4`. Spot-check confirms: path `/api/recipes/{id}` has a `"put"` operation; request body `$ref` points to `#/components/schemas/RecipeUpdate`; `RecipeUpdate` component schema is present with `definition` required and `description` optional. |

---

## Per-checklist item audit

### 1. Schema — `RecipeUpdate`
- `definition: dict[str, Any]` required — ✅ present, no default.
- `description: str | None = None` — ✅ no `_UNSET` sentinel, canonical Pydantic v2 idiom.
- `extra="ignore"` via `model_config = ConfigDict(extra="ignore")` — ✅ consistent with existing models.
- Module docstring updated — ✅.

### 2. Handler logic — `update_recipe`
- `Depends(get_current_user)` auth-gate — ✅ present in function signature.
- `@router.put("/{id}")` has **no** `summary=` kwarg at source level — ✅ (F8 resolved).
- Owner-scoped SELECT with `scalar_one_or_none()` — ✅ `select(Recipe).where(Recipe.id == id).where(Recipe.owner_id == current_user.id)` + `result.scalar_one_or_none()`.
- 404 detail exactly `"Recipe not found"` for both not-found and wrong-owner — ✅ single raise path.
- Freeze check: `select(exists().where(Dataset.recipe_id == recipe.id))` + `scalar_one()` — ✅ NOT count; correct bool short-circuit.
- 409 detail exactly `"Recipe is locked: a dataset has been materialized from it"` — ✅ verified character-for-character.
- `recipe.definition = body.definition` always — ✅.
- `if "description" in body.model_fields_set: recipe.description = body.description` — ✅ correct `model_fields_set` guard.
- `recipe.updated_at = datetime.now(tz=timezone.utc)` app-side — ✅ (with `# type: ignore[assignment]`, noted LOW-2).
- `await session.commit()` + `await session.refresh(recipe)` — ✅ both present.
- Returns `RecipeOut.model_validate(recipe)` — ✅.

### 3. Tests — 13 tests, all named per contract
| Contract test name | Present | Key assertion verified |
|---|---|---|
| `test_update_recipe_200_returns_updated_definition` | ✅ | `body["definition"] == new_def` exact dict equality |
| `test_update_recipe_updated_at_is_newer` | ✅ | Uses `_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)` module constant; asserts `returned_updated_at > _PAST` (strict) |
| `test_update_recipe_dataset_exists_returns_409` | ✅ | `response.json() == {"detail": "Recipe is locked: a dataset has been materialized from it"}` full equality |
| `test_update_recipe_not_found_returns_404` | ✅ | 404 + `{"detail": "Recipe not found"}` |
| `test_update_recipe_wrong_owner_returns_404` | ✅ | Same 404 detail, no enumeration leak |
| `test_update_recipe_no_token_returns_401` | ✅ | 401 + `WWW-Authenticate: Bearer` header |
| `test_update_recipe_missing_definition_returns_422` | ✅ | 422 on `{}` body |
| `test_update_recipe_non_object_definition_returns_422` | ✅ | 422 on `{"definition": [1,2,3]}` |
| `test_update_recipe_description_updated_when_provided` | ✅ | `response.json()["description"] == "new desc"` |
| `test_update_recipe_description_unchanged_when_omitted` | ✅ | `response.json()["description"] == original_description` |
| `test_update_recipe_description_explicit_null` | ✅ | `response.json()["description"] is None` |
| `test_update_recipe_recipe_id_in_dataset_exists_query` | ✅ | `compile(literal_binds=True)` on 2nd execute call; asserts `"recipe_id"` and `str(recipe_id)` in SQL |
| `test_update_recipe_owner_id_in_recipe_query` | ✅ | `compile(literal_binds=True)` on 1st execute call; asserts `"owner_id"` and `str(_MOCK_USER.id)` in SQL |

Count: 13 tests — meets contract requirement of ≥10.

### 4. Imports
All required imports present in router diff: `from datetime import datetime, timezone`, `from sqlalchemy import exists, func, select`, `from dataplat_api.db.models import Dataset, Recipe, User`, `from dataplat_api.schemas.recipes import ..., RecipeUpdate`. ✅

### 5. No deviations from agreed.md
Implementer claim verified. The implementation follows the agreed contract exactly on every numbered point. The only discoverable difference is `func` remaining in the import line (it was pre-existing for `create_recipe`'s `func.now()` usage) — this is not a deviation. ✅

### 6. Pyright IDE diagnostics
Per project convention, not assessed (mypy is the source of truth; implementer reports mypy clean on 38 files). ✅

---

## Summary

The implementation is a faithful, complete realisation of the agreed S040-F-040 contract. Every handler step (auth gate, owner-scoped load, `EXISTS`-based freeze guard, patch semantics, app-side `updated_at` bump, commit/refresh, `RecipeOut` return) matches the spec precisely, including the exact 404 and 409 detail strings, the `model_fields_set` idiom for optional `description` updates, and the `scalar_one()` (not count) pattern for the freeze check. All 13 tests are present and well-structured, with the V2 `_PAST` flake-prevention constant, the V3 full-equality assertion on the 409 body, and the two structural SQL compilation tests giving strong regression coverage. Invariants #3, #5, and #6 all hold. The three observations (LOW-1, LOW-2, NIT-1) are informational and do not affect correctness or safety. Ready to hand off to verifier.

