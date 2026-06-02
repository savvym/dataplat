# Sprint S039-F-039 — Agreed Contract

> Reviewer Mode A: **APPROVED** (3 NITs, no blockers).
> NIT resolutions folded in below (search "Mode A NIT").

## Goal

Add `GET /api/recipes/{id}` — a detail endpoint that returns a single recipe
(full record including `definition`) owned by the authenticated caller, with
`404` for both "does not exist" and "not yours" cases.

---

## Context: key observations from existing code

| Signal | Consequence for this sprint |
|--------|---------------------------|
| `Recipe` has `owner_id` as a direct FK column (unlike `Source`, which has no `owner_id` and needs a JOIN through `SourceCollection`) | The ownership check is a simple two-clause WHERE — no JOIN needed. |
| `list_recipes` (F-038) filters `Recipe.owner_id == current_user.id` | Mirror the same filter on the detail endpoint for consistency. |
| `get_source` (F-013) returns 404 for both "not found" and "wrong owner" | Same no-leak pattern here: combine `id == ?` AND `owner_id == ?` in one query; any miss → 404. |
| `RecipeOut` already has all 7 fields: `id, name, description, owner_id, definition, created_at, updated_at` | No schema changes. Reuse directly. |
| `RecipeListItem` deliberately omits `definition` and `owner_id` | The detail endpoint is exactly the missing counterpart — `RecipeOut` is the right response type. |

---

## Files to change

| File | Change |
|------|--------|
| `apps/api/dataplat_api/routers/recipes.py` | Add `get_recipe` handler (`GET "/{id}"`). Update module docstring to reference F-039. No other handlers touched. |
| `apps/api/tests/test_recipes_get.py` | **New file.** Six test functions (detailed below). |
| `packages/api-types/` | Updated by `make codegen` — committed in the same commit per invariant #6. No manual edits. |

**No changes to:**
- `schemas/recipes.py` — `RecipeOut` is reused as-is.
- `db/models.py` — no schema changes.
- Any migration — no DB changes.
- Any other router or schema file.

---

## Handler design

### Route declaration

```python
@router.get("/{id}", response_model=RecipeOut, summary="Get Recipe")
async def get_recipe(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> RecipeOut:
```

Path parameter `id` is typed as `int` — FastAPI validates at the path-param
level; a non-integer segment (e.g. `/api/recipes/abc`) returns `422`
automatically before the handler is entered.

### Auth

`Depends(get_current_user)` — same dependency used by `list_recipes` and
`create_recipe`. A missing/invalid token returns `401` before the handler body
runs.

### Owner-scoped query (single execute call)

```python
result = await session.execute(
    select(Recipe)
    .where(Recipe.id == id)
    .where(Recipe.owner_id == current_user.id)
)
recipe = result.scalar_one_or_none()
```

Combining both filters in one query means:
- A non-existent id → `None` → 404.
- An id that exists but belongs to another user → `None` → 404.

Both cases are indistinguishable to the caller. No information leak.

### 404 path

```python
if recipe is None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Recipe not found",
    )
```

Detail string `"Recipe not found"` — consistent with the `"Source not found"`
/ `"Collection not found"` pattern across other endpoints.

### Happy path

```python
return RecipeOut.model_validate(recipe)
```

`RecipeOut` has `model_config = ConfigDict(from_attributes=True)` — reads
directly from the ORM instance. No manual field mapping needed.

### Route ordering note

`GET "/{id}"` must be appended **after** the existing `GET ""` (list) and
`POST ""` (create) registrations. FastAPI matches `/{id}` only for paths with
a non-empty segment after `/api/recipes/`, so there is no collision with the
root-path routes. Placing it last follows the same convention used in
`sources.py` (catch-all `/{id}` registered last).

---

## Schema reuse

`RecipeOut` is returned unchanged. For reference, its 7 fields are:

```
id          : int
name        : str
description : str | None
owner_id    : int | None
definition  : dict[str, Any]     ← the field that was absent from RecipeListItem
created_at  : datetime | None
updated_at  : datetime | None
```

No new Pydantic models are introduced.

---

## Test plan — `apps/api/tests/test_recipes_get.py`

### Mock session helper for this endpoint

The handler calls `session.execute()` exactly **once** and calls
`scalar_one_or_none()` (synchronous) on the result proxy. The correct mock
shape is:

```python
result_mock = MagicMock()
result_mock.scalar_one_or_none.return_value = recipe_row_or_none

session = AsyncMock()
session.execute = AsyncMock(return_value=result_mock)
```

Note: `scalar_one_or_none()` is synchronous (called on the result proxy object
returned from `await session.execute()`). Use `MagicMock()` for the result,
not `AsyncMock()`. Same lesson codified in the F-038 test file's header comment.

### Recipe row mock factory

Mirror the `_make_recipe` factory from `test_recipes_list.py`, but populate
all 7 ORM attributes (including `owner_id` and `definition`) because
`RecipeOut` reads all of them:

```python
def _make_recipe_detail(id, name, description=None, owner_id=7,
                        definition=None) -> MagicMock:
    row = MagicMock(spec=Recipe)
    row.id = id
    row.name = name
    row.description = description
    row.owner_id = owner_id
    row.definition = definition if definition is not None else {}
    row.created_at = _NOW
    row.updated_at = _NOW
    return row
```

### Test cases

| Test name | What it checks | Maps to criterion |
|-----------|---------------|-------------------|
| `test_get_recipe_200_returns_full_record` | Session returns a recipe row → 200, all 7 `RecipeOut` fields present, `definition` matches | **V1** |
| `test_get_recipe_not_found_returns_404` | Session returns `None` (id 99999) → 404 with `detail="Recipe not found"` | **V2** |
| `test_get_recipe_wrong_owner_returns_404` | Session returns `None` (simulates existing-but-wrong-owner query miss) → 404 | edge case: no-leak |
| `test_get_recipe_no_token_returns_401` | No Authorization header, no `get_current_user` override → 401 + `WWW-Authenticate: Bearer` | auth gate |
| `test_get_recipe_invalid_id_returns_422` | `GET /api/recipes/not-an-int` → 422 (FastAPI path param validation) | param validation |
| `test_get_recipe_owner_id_in_query` | Captures the compiled SELECT; asserts both `"owner_id"` and the mock user's id literal appear in it | structural / owner-scope correctness |

#### Detail on `test_get_recipe_200_returns_full_record` (V1)

```
- Override get_current_user → user id=7
- Override get_session → returns a recipe MagicMock with:
    id=42, name="my-sft", description="SFT recipe",
    owner_id=7, definition={"steps": ["tokenize", "pack"]},
    created_at=_NOW, updated_at=_NOW
- GET /api/recipes/42
- Assert status_code == 200
- Assert all 7 keys present: id, name, description, owner_id, definition,
  created_at, updated_at
- Assert body["id"] == 42
- Assert body["definition"] == {"steps": ["tokenize", "pack"]}
- Assert body["owner_id"] == 7
```

#### Detail on `test_get_recipe_not_found_returns_404` (V2)

```
- Override get_current_user → user id=7
- Override get_session → scalar_one_or_none returns None
- GET /api/recipes/99999
- Assert status_code == 404
- Assert response.json() == {"detail": "Recipe not found"}
```

#### Detail on `test_get_recipe_wrong_owner_returns_404`

```
- Override get_current_user → user id=7
- Override get_session → scalar_one_or_none returns None
  (models a recipe row that exists for user id=99, not id=7)
- GET /api/recipes/1
- Assert status_code == 404
- Assert response.json() == {"detail": "Recipe not found"}
```

Note: the mock produces the same outcome as "not found" — the test documents
the intended security property (no 403, no distinction) rather than testing a
DB-level fact.

#### Detail on `test_get_recipe_owner_id_in_query`

```
- Override get_current_user → user id=7
- Capture the AsyncMock session; execute returns empty scalar_one_or_none=None
- GET /api/recipes/5
- Compile the captured SELECT with literal_binds=True
- Assert "owner_id" in compiled_sql
- Assert str(7) in compiled_sql
```

Mirrors `test_list_recipes_owner_id_in_query` from F-038.

---

## Verification mapping

| Criterion | Test(s) |
|-----------|---------|
| V1 — 200 with all fields including definition | `test_get_recipe_200_returns_full_record` |
| V2 — 404 for non-existent id | `test_get_recipe_not_found_returns_404` |
| (edge) 404 for wrong owner — no existence leak | `test_get_recipe_wrong_owner_returns_404` |
| (edge) 401 no token | `test_get_recipe_no_token_returns_401` |
| (edge) 422 non-integer id | `test_get_recipe_invalid_id_returns_422` |
| (structural) owner_id filter in SQL | `test_get_recipe_owner_id_in_query` |

---

## Open questions

None. All design decisions are settled by the existing codebase:

1. **Owner-scope vs. world-readable?** → Owner-scoped, mirrors `list_recipes`.
   `Recipe.owner_id` is a direct column so no JOIN is required.
2. **404 vs. 403 for wrong owner?** → 404 in both cases, mirrors `get_source`
   (F-013) and `list_sources_by_collection` (F-014).
3. **Response schema?** → `RecipeOut` reused as-is. It already contains
   `definition` and all 7 fields.
4. **Path param type?** → `int`, same as `get_source(id: int)` in sources.py.

---

## Invariants checklist

- [x] **#5 Async SQLAlchemy** — single `await session.execute(select(...))`,
  `scalar_one_or_none()` called synchronously on the result proxy. No
  `session.query()`, no sync session.
- [x] **#6 OpenAPI ↔ TS type sync** — a new route is added to the OpenAPI
  spec. `make codegen` must be run and the resulting `packages/api-types/`
  diff committed in the **same commit** as the router change. CI will reject
  mismatches.
- [x] **No new migration** — read-only endpoint, no schema change.
- [x] **No LLM gateway** — not applicable.
- [x] **Lineage invariant** — not applicable (no Commit created).
- [x] **Storage separation** — not applicable (read-only, no blob ops).

---

## Mode A NIT resolutions

- **NIT 1 (`summary=` consistency).** Drop `summary="Get Recipe"` from the
  `@router.get("/{id}", ...)` decorator (Option B): keep symmetry with the
  existing `list_recipes` and `create_recipe` handlers in `recipes.py`, which
  do not carry `summary=`.
- **NIT 2 (mock factory duplication).** Define `_make_recipe_detail` locally
  in `test_recipes_get.py` for self-containment, and add a header comment in
  the test file explaining the duplication is intentional (mirrors F-038 test
  file convention; do not delete as dead code).
- **NIT 3 (handler docstring).** Add the multi-line docstring to `get_recipe`
  per reviewer's suggested template — captures the no-leak owner-scope
  rationale and the auth requirement. Final form:

  ```python
  """Return the full recipe record for the given id.

  Owner-scoping: combines ``id == ?`` AND ``owner_id == ?`` in one query so
  that a non-existent id and an id owned by another user both return 404
  (no-enumeration-leak, mirrors get_source / list_sources_by_collection).

  Returns ``RecipeOut`` (all 7 fields including ``definition``).

  Auth required (F-008).
  """
  ```
