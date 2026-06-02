# Proposed — S038-F-038: List Recipes (GET /api/recipes)

## 1. Goal

Expose a `GET /api/recipes` endpoint that returns all recipes owned by the
authenticated user in the canonical `{items, total}` envelope established by
F-010 (`GET /api/sources/collections`).  The endpoint must be tenant-isolated:
a user MUST see only their own recipes, never another user's.  No new migration,
ORM model, or table is required — the `recipe` table and `Recipe` ORM model
already exist.  Work is limited to: a new Pydantic list-response schema, a new
GET handler appended to the existing recipes router, and a new test file.

---

## 2. Endpoint Signature & Response Model

### 2.1 Endpoint

```
GET /api/recipes
Authorization: Bearer <token>
```

### 2.2 Response shape

```json
{
  "items": [
    {
      "id":          <int>,
      "name":        <string>,
      "description": <string | null>,
      "created_at":  <datetime | null>,
      "updated_at":  <datetime | null>
    },
    ...
  ],
  "total": <int>
}
```

### 2.3 Schema decision: `RecipeListItem` (slim) vs. reusing `RecipeOut` (full)

**Decision: introduce a new `RecipeListItem` schema that omits `owner_id` and
`definition`.**

Rationale:

| Field | In list response? | Reason |
|---|---|---|
| `id` | **Yes** | Required by spec (V2) |
| `name` | **Yes** | Required by spec (V2) |
| `description` | **Yes** | Required by spec (V2) |
| `created_at` | **Yes** | Required by spec (V2) |
| `updated_at` | **Yes** | Required by spec (V2) |
| `owner_id` | **No** | The endpoint is already owner-scoped; exposing `owner_id` on every list item is redundant noise for the caller. Consistent with how `SourceListResponse` items (via `SourceRead`) do not re-echo the collection owner. |
| `definition` | **No** — see OQ-2 | JSONB blobs can be arbitrarily large. Returning them on a multi-item list response will cause unnecessary payload inflation and database I/O for callers who only need to browse recipe names. The detail endpoint (future F-039 / GET /api/recipes/{id}) is the correct place to retrieve the full `definition`. This mirrors the pattern used by source collections (F-010 list vs. F-013 detail). |

`RecipeOut` (7 fields) is kept as-is for the POST 201 response; it is not
reused here to avoid coupling the list contract to the full-record contract.

### 2.4 HTTP status table

| Status | Body | When |
|---|---|---|
| 200 OK | `RecipeListResponse` (see above) | Successful query (including empty list) |
| 401 Unauthorized | `{"detail": "Not authenticated"}` + `WWW-Authenticate: Bearer` | No / invalid token |

No 404 or 409 paths; the endpoint simply returns an empty list when the user
has no recipes.

---

## 3. Pagination & Ordering

### 3.1 Pagination decision: **no pagination for MVP**

The spec verification criterion is "after creating 2 recipes, total == 2".  The
MVP does not require the client to page through results.  The `total` field is
still included in the response for forward-compatibility (the client can detect
when a future paginated version would truncate results without a breaking change
to the schema).

Precedent for an unpaginated list endpoint: `GET /{source_id}/documents`
(F-020) returns a plain array with no `limit`/`offset` because the cardinality
per source is expected to be small.  Recipe counts per user are similarly
bounded in MVP usage.  See OQ-1 for the reviewer's call on whether to add
`limit`/`offset` proactively.

### 3.2 Ordering: `created_at DESC` (newest first)

Rationale: recipe lists are typically browsed with the most recently created
recipe at the top.  `created_at` has a `server_default=now()` so it is always
populated.  Tie-break on `id DESC` (implicit in Postgres for equal timestamps,
but made explicit in the query for determinism).

If the reviewer prefers `id ASC` (oldest first, matching `list_collections`),
that is also acceptable — flag as OQ-1b.

---

## 4. File-by-File Change List

| File | Action | Description |
|---|---|---|
| `apps/api/dataplat_api/schemas/recipes.py` | **Edit** | Add `RecipeListItem` (5-field slim model) and `RecipeListResponse` (`{items, total}` envelope) |
| `apps/api/dataplat_api/routers/recipes.py` | **Edit** | Append `GET ""` handler `list_recipes`; add `func`, `select` imports from sqlalchemy |
| `apps/api/tests/test_recipes_list.py` | **Create** | New test file; all test cases for F-038 (see §6) |
| `packages/api-types/openapi.json` | **Regen** | `make codegen` — regenerate and commit in the same commit per hard invariant #6 |

`apps/api/dataplat_api/main.py` — **no change needed**: the recipes router is
already wired via `app.include_router(recipes_router)` from F-037.  Adding a
new GET handler to the existing router automatically appears under the same
prefix without any `main.py` changes.

---

## 5. SQL Plan

Two async queries, both scoped to `owner_id == current_user.id`:

**Query 1 — paginated/full item list (ordered, all rows for MVP):**

```python
from sqlalchemy import func, select

result = await session.execute(
    select(Recipe)
    .where(Recipe.owner_id == current_user.id)
    .order_by(Recipe.created_at.desc(), Recipe.id.desc())
)
rows = result.scalars().all()
```

**Query 2 — total count:**

```python
count_result = await session.execute(
    select(func.count())
    .select_from(Recipe)
    .where(Recipe.owner_id == current_user.id)
)
total = count_result.scalar_one()
```

Then:

```python
items = [RecipeListItem.model_validate(row) for row in rows]
return RecipeListResponse(items=items, total=total)
```

This is the exact same two-query pattern used by `list_collections` in
`routers/sources.py` (lines 84–102), with `SourceCollection` replaced by
`Recipe` and the owner filter unchanged.

**No JOIN needed.** `Recipe.owner_id` is a direct column on the `recipe` table;
there is no intermediate collection table to join through (unlike `Source →
SourceCollection`).

---

## 6. Test Plan

File: `apps/api/tests/test_recipes_list.py`

All tests use `FastAPI.dependency_overrides` with `AsyncMock` sessions (no live
DB), following the identical pattern established in `test_recipes_create.py` and
`test_sources_collections_create.py`.

### Session mock helper

`_make_list_session_dep(rows, total)` — returns a `get_session` dependency
override whose `execute` side-effect returns the mock rows on the first call and
the mock total on the second call, matching the two-query execution order.

### Test cases

| Test name | Maps to | Description |
|---|---|---|
| `test_list_recipes_returns_200_with_items_and_total` | **V1** | Two recipes in session → 200, `items` has 2 elements, `total == 2` |
| `test_list_recipes_items_have_required_fields` | **V2** | Each item in response has `id`, `name`, `description`, `created_at`, `updated_at` (types checked) |
| `test_list_recipes_no_token_returns_401` | auth gate | No Authorization header → 401 with `WWW-Authenticate: Bearer`; no dependency overrides |
| `test_list_recipes_only_own_recipes` | isolation | Session returns 2 rows for user A and 1 row for user B; override `get_current_user` to user A → `total == 2`; then switch to user B → `total == 1` (via separate client calls with separate session mocks) |
| `test_list_recipes_empty_returns_empty_list` | empty | Session returns 0 rows, total 0 → 200, `items == []`, `total == 0` |
| `test_list_recipes_definition_not_in_items` | schema guard | Response items do NOT contain a `definition` key (slim schema enforced) |
| `test_list_recipes_owner_id_not_in_items` | schema guard | Response items do NOT contain an `owner_id` key |

### Notes

- `test_list_recipes_no_token_returns_401` does NOT override `get_current_user` —
  relies on the real `oauth2_scheme` (auto_error=True) raising 401 for a missing
  Authorization header, same as `test_create_recipe_no_token_returns_401`.
- `test_list_recipes_only_own_recipes` uses two separate session mock setups
  (one per user) to verify the `owner_id` filter is applied per authenticated
  user; no shared state between the two client calls.
- All mocked recipe rows must populate all 7 ORM-mapped attributes (including
  `definition`) to satisfy `model_validate` even though `RecipeListItem` omits
  `definition`.  This ensures the ORM-level mapping path is exercised correctly.

---

## 7. Verification Criteria (verbatim from feature spec)

> After creating 2 recipes, GET /api/recipes returns `{"items": [<2 items>], "total": 2}`

→ Covered by `test_list_recipes_returns_200_with_items_and_total` (V1)

> Each item includes id, name, description, created_at, updated_at

→ Covered by `test_list_recipes_items_have_required_fields` (V2)

---

## 8. Open Questions

**OQ-1: Should MVP support `limit`/`offset` pagination?**

The spec verification only checks "2 recipes → total == 2" — no pagination is
exercised.  The existing `list_collections` (F-010) and `list_sources_by_collection`
(F-014) endpoints both support `limit`/`offset` with `Query(default=20, ge=1,
le=200)`.  Two options:

- **Option A (proposed):** No pagination for MVP. Return all rows. Include
  `total` in response for forward-compat. This is simpler and the spec does not
  require it; recipe counts per user are expected to be small (tens, not
  thousands) in MVP.
- **Option B:** Add `limit`/`offset` now, matching `list_collections` exactly.
  Minor extra complexity but already a proven pattern; avoids a later breaking
  change if recipe lists grow.

Reviewer to decide. If Option B is chosen, the SQL plan adds `.limit(limit).offset(offset)`
to Query 1 and the test file adds a `test_list_recipes_pagination` test case.

**OQ-2: Should `definition` be included in list items?**

Proposal is **NO** — `definition` can be an arbitrarily large JSONB object.
Returning it on every list item is wasteful for callers who only need to browse
names and pick one.  The full record (including `definition`) belongs on a
future GET /api/recipes/{id} detail endpoint.  If the reviewer disagrees and
wants `definition` in the list response for MVP simplicity, `RecipeListItem`
can be replaced with `RecipeOut` directly (removing the need for a new schema
class).

**OQ-3: Ordering — `created_at DESC` vs. `id ASC`?**

Proposal is `created_at DESC` (newest first), with `id DESC` as tie-breaker.
`list_collections` uses `id ASC` (oldest first).  There is no stated user
preference in the spec.  Reviewer to confirm or override.

---

## 9. Invariant Compliance

| Invariant | Status |
|---|---|
| **#2 Storage separation + CAS** | Compliant — read-only query against Postgres; no blob storage interaction. |
| **#5 Async SQLAlchemy** | Compliant — handler uses `AsyncSession`, both queries are `await session.execute(...)`. No `session.query()`, no sync sessions. |
| **#6 OpenAPI ↔ TS type sync** | Compliant — `make codegen` must be run after schemas change; resulting `packages/api-types/openapi.json` diff committed in the same commit. |
