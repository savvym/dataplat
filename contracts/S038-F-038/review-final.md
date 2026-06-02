# Review Final — S038-F-038: List Recipes (GET /api/recipes)

**Verdict: APPROVED**

Implementation fully matches `agreed.md`, resolves all Mode A findings (F1–F4), and passes all 8 contracted tests. No blockers, no changes requested.

---

## Mode A Finding Resolution

### F1 MEDIUM — Pagination omission → PASS

Option A (no pagination for MVP) implemented as agreed. Handler returns all owner-scoped rows with no `limit`/`offset` params. `total` field included in the `RecipeListResponse` envelope for forward-compatibility. Decision documented in commit message: *"No pagination for MVP — recipe counts per user are expected to be small."*

### F2 LOW — SQL-structural test for `owner_id` filter → PASS

`test_list_recipes_owner_id_in_query` (line 261–303) is present and correct. It:
- Builds a separate capturing session dependency that appends the mock session to `captured_session[]`.
- Asserts `session_mock.execute.call_count == 2` (both queries fired).
- Extracts `session_mock.execute.call_args_list[0].args[0]` — the first `Select` statement.
- Compiles it with `literal_binds=True`.
- Asserts `"owner_id"` and `str(_MOCK_USER.id)` (`"7"`) both appear in the compiled SQL string.

Implementation is a faithful mirror of `test_list_collections_owner_filter` in `test_sources_collections_list.py`. Verified to pass.

### F3 NIT — Full `packages/api-types/` diff → PASS (with explanation)

`packages/api-types/` contains **only `openapi.json`** — no `.ts` files exist anywhere in the directory. No `Makefile` is present in the repository root (confirmed: `ls` output lists no Makefile). The `make codegen` TS generation infrastructure has not yet been set up (deferred to the web sprint). The commit therefore includes the complete codegen output that exists: the updated `openapi.json`. The commit message explicitly notes: *"No TypeScript files yet (Makefile not present; web sprint deferred per checks.sh contract) layer guard."* Hard invariant #6 is satisfied to the maximum extent possible given the project state; this is not a regression.

### F4 NIT — Mock row population reasoning → PASS

`_make_recipe()` populates all 7 ORM-mapped attributes (`id`, `name`, `description`, `owner_id`, `definition`, `created_at`, `updated_at`). The factory docstring and the module-level docstring both correctly characterise the reason as *"for completeness / future-proofing"*, not as a `model_validate` requirement. Pydantic's `from_attributes=True` reads only the 5 fields declared on `RecipeListItem`. Wording is accurate.

---

## Checklist — Mode B Items

### 1. Matches every commitment in `agreed.md`

| §  | Commitment | Status |
|----|-----------|--------|
| §2.1 | `GET /api/recipes` endpoint under existing recipes router | ✅ `@router.get("")` on `APIRouter(prefix="/api/recipes")` |
| §2.2 | Response shape `{items: [...], total: int}` | ✅ `RecipeListResponse(items=items, total=total)` |
| §2.3 | `RecipeListItem`: 5 fields only; `owner_id` and `definition` omitted | ✅ Schema has exactly `id, name, description, created_at, updated_at` |
| §3.1 | No pagination for MVP | ✅ No `limit`/`offset` params |
| §3.2 | `ORDER BY created_at DESC, id DESC` | ✅ `.order_by(Recipe.created_at.desc(), Recipe.id.desc())` |
| §4 | Four files changed (schemas, router, tests, openapi.json) | ✅ Diff touches exactly those 4 files; `main.py` correctly untouched |
| §5 | Two-query pattern: Query 1 rows via `scalars().all()`, Query 2 count via `scalar_one()` | ✅ Exact pattern implemented |
| §5 | Both queries scoped to `Recipe.owner_id == current_user.id` | ✅ Both `.where()` clauses present (see below) |
| §6 | 8 test cases, exact names as specified | ✅ All 8 present |

### 2. Owner scoping on BOTH queries

**Query 1 (item list):**
```python
select(Recipe)
    .where(Recipe.owner_id == current_user.id)
    .order_by(Recipe.created_at.desc(), Recipe.id.desc())
```
**Query 2 (count):**
```python
select(func.count())
    .select_from(Recipe)
    .where(Recipe.owner_id == current_user.id)
```
Both carry the `WHERE owner_id = :current_user_id` filter. ✅

### 3. Invariant #5 — Async SQLAlchemy throughout

- Handler signature: `session: AsyncSession = Depends(get_session)` ✅
- Both DB calls: `await session.execute(...)` ✅
- No `session.query()` anywhere in the diff ✅
- No sync session usage ✅

### 4. Invariant #6 — OpenAPI ↔ TS type sync

- `openapi.json` diff committed in same commit as schema/router changes ✅
- `GET /api/recipes` operation present at correct path ✅
- `RecipeListItem` component schema present with exactly 5 fields ✅
- `RecipeListResponse` component schema present with `items` + `total` ✅
- `required: [id, name, description, created_at, updated_at]` matches Pydantic model ✅
- Security: `OAuth2PasswordBearer` applied to the GET operation ✅

### 5. Response shape — `RecipeListItem` fields

`openapi.json` `RecipeListItem` properties: `id` (integer), `name` (string), `description` (string|null), `created_at` (date-time|null), `updated_at` (date-time|null). Exactly the 5 spec-required fields. `owner_id` and `definition` absent from both the Pydantic schema and the OpenAPI component. ✅

### 6. No N+1 / ordering correct

Exactly 2 `session.execute()` calls per request (`test_list_recipes_owner_id_in_query` asserts `call_count == 2`). No per-row queries. Ordering `created_at DESC, id DESC` confirmed in source and consistent with agreed OQ-3 resolution. ✅

### 7. Test plan §6 — full coverage

| Test name | Maps to | Pass |
|-----------|---------|------|
| `test_list_recipes_returns_200_with_items_and_total` | V1 | ✅ |
| `test_list_recipes_items_have_required_fields` | V2 | ✅ |
| `test_list_recipes_no_token_returns_401` | auth gate | ✅ |
| `test_list_recipes_only_own_recipes` | isolation | ✅ |
| `test_list_recipes_owner_id_in_query` | F2 SQL-structural | ✅ |
| `test_list_recipes_empty_returns_empty_list` | empty | ✅ |
| `test_list_recipes_definition_not_in_items` | schema guard | ✅ |
| `test_list_recipes_owner_id_not_in_items` | schema guard | ✅ |

`uv run pytest tests/test_recipes_list.py -v` → **8 passed** in isolation.
Full suite: `uv run pytest --tb=no -q` → **217 passed, 1 deselected** (same count as commit message claims). ✅

---

## Findings

None. No blockers, no high/medium/low issues identified.

---

**One-line summary:** Implementation is correct, complete, and clean — all 8 tests pass, both queries are owner-scoped, `RecipeListItem` exposes exactly the 5 spec fields, the F2 SQL-structural test faithfully mirrors the `list_collections` pattern, and the `openapi.json` diff is committed in the same change set.

