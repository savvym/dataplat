# S039-F-039 Mode A Review — feedback.md

## APPROVED

All three calibration checks that apply to this sprint pass cleanly. NITs noted below; none block implementation.

---

## Calibration checklist (required, per reviewer-calibration.md)

| CAL | Applies? | Finding |
|-----|----------|---------|
| CAL-1 Async session | ✅ Yes | Handler uses `await session.execute(select(...))` + synchronous `scalar_one_or_none()` on result proxy. No `session.query()`, no sync session. PASS. |
| CAL-2 LLM gateway | N/A | No LLM calls in this endpoint. |
| CAL-3 OpenAPI sync | ✅ Yes | Proposal explicitly requires `make codegen` + `packages/api-types/` diff committed in the **same** commit as the router change. Invariant #6 acknowledged correctly. PASS (as contract commitment). |
| CAL-4 Lineage completeness | N/A | Read-only endpoint, no Commit created. |
| CAL-5 CAS path discipline | N/A | No blob writes. |
| CAL-6 Schema freeze | N/A | No schema changes, no migration. |
| CAL-7 Bronze faithfulness | N/A | Not a plugin. |
| CAL-8 MVP scope | ✅ Yes | No out-of-scope items. PASS. |
| CAL-9 Plugin isolation | N/A | Not a plugin. |
| CAL-10 Test coverage | ✅ Yes | 6 tests: 1 happy path (V1), 1 not-found 404 (V2), 1 wrong-owner 404, 1 no-token 401, 1 invalid-id 422, 1 structural query check. PASS. |

---

## Findings

### 1. NIT — `summary=` kwarg inconsistent within `recipes.py`

The proposed handler declares `summary="Get Recipe"`, but the two existing handlers in `recipes.py` (`list_recipes`, `create_recipe`) carry no `summary=` kwarg at all. Adding one to `get_recipe` only creates a visual asymmetry in the same file.

Two equally acceptable resolutions — implementer should pick one and be explicit:

**Option A (preferred):** add `summary=` strings to all three recipe handlers for consistency with `sources.py` conventions:
```python
# existing list handler
@router.get("", response_model=RecipeListResponse, summary="List Recipes")

# existing create handler
@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED, summary="Create Recipe")

# new detail handler
@router.get("/{id}", response_model=RecipeOut, summary="Get Recipe")
```

**Option B:** omit `summary=` from the new handler to match the existing recipe handlers. The auto-derived OpenAPI operation ID is adequate for MVP.

Neither choice affects verification. Pick one; note it in the commit message.

---

### 2. NIT — `_make_recipe_detail` factory is a redundant copy of `_make_recipe`

The proposed factory populates the identical 7 fields in the identical way to `_make_recipe` in `test_recipes_list.py`. The only difference is the name. Since that file's factory already satisfies the shape (`_make_recipe` explicitly populates all 7 fields including `owner_id` and `definition` — see line 82–93 of `test_recipes_list.py`), the implementer can simply import it:

```python
# test_recipes_get.py
from dataplat_api.tests.test_recipes_list import _make_recipe as _make_recipe_detail
# or just use _make_recipe directly
```

If the implementer prefers test-file isolation (acceptable), they MUST add a comment explaining why the duplicate exists — otherwise a future reader will delete it as dead code. The proposed.md's comment ("Mirror the `_make_recipe` factory…") needs to survive into the actual source file.

---

### 3. NIT — Handler docstring not specified in proposed.md

Every handler in `recipes.py` and `sources.py` carries a multi-line docstring that mirrors the agreed.md rationale for that endpoint (see `list_recipes` and `create_recipe` for examples). The proposed.md does not draft a docstring for `get_recipe`.

Suggested minimum (implementer may expand):
```python
"""Return the full recipe record for the given id.

Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
that a non-existent id and an id owned by another user both return 404
(no-enumeration-leak, mirrors get_source / list_sources_by_collection).

Returns ``RecipeOut`` (all 7 fields including ``definition``).

Auth required (F-008).
"""
```

---

## Positive notes (for the implementer)

- Owner-scope analysis is correct: `Recipe.owner_id` is a direct FK column, so no JOIN is needed — the two-clause WHERE is the right approach and mirrors `list_recipes`.
- 404-for-wrong-owner (no 403) is correctly chosen and correctly explained, consistent with `get_source` (F-013) and `list_sources_by_collection` (F-014).
- `RecipeOut` reuse is correct — it already includes all 7 fields, including `definition`, without any schema changes.
- Route placement (after `GET ""` and `POST ""`) is correct; the `/{id}` pattern cannot collide with the root-path routes.
- The `test_get_recipe_owner_id_in_query` structural test (compile SELECT with `literal_binds=True`, assert `owner_id` and the literal user-id appear) is a strong ownership guard that mirrors the F-038 pattern — keep it.
- Mock shape for single-execute handler (MagicMock result proxy, synchronous `scalar_one_or_none()`) is correctly described.
- No over-engineering: no new Pydantic models, no migrations, no unnecessary abstractions.

---

## Summary

The proposal is technically sound end-to-end. Handler design, query pattern, error codes, schema reuse, test coverage, and invariant acknowledgement are all correct. The three findings are all NITs: a summary-kwarg style inconsistency, a test factory duplication that needs a comment if kept, and a missing docstring draft. None affect correctness or verification. Implement per proposed.md, resolve the NITs at implementation time.

**APPROVED**
