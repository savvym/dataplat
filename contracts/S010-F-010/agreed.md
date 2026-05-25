# S010-F-010 — Proposed Contract

**Status:** AGREED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S010-F-010

---

## §1 Objective & Scope

**Goal:** Implement the real body of `GET /api/sources/collections` — replacing the F-009 stub that returns `items=[], total=0` — so authenticated users receive a paginated, owner-scoped list of their source collections.

### Dependency confirmation

| Dependency | Required state | Evidence |
|---|---|---|
| F-009 (POST /api/sources/collections) | `passes: true` | Commit `594356d` message: `feat(api): F-009 POST /api/sources/collections — 201 create, 409 dup, V1/V2/V3 verified`. `feature_list.json` confirms `"passes": true`. |

F-009 is confirmed `passes: true`. Proceeding is correct per CLAUDE.md sprint workflow.

### Explicit non-goals (out of scope for this sprint)

- `GET /api/sources/collections/{id}` — no individual-collection detail route in MVP.
- `PUT` / `PATCH` / `DELETE` on collections.
- Shared/public collections — list is always filtered to `owner_id = current_user.id`.
- Cursor-based pagination — `limit` + `offset` is sufficient for MVP.
- Full-text search or filtering beyond the ownership scope.
- Any change to the `POST /api/sources/collections` handler (F-009 deliverable — do not disturb).
- F-011 (upload PDF source) or later features.

---

## §2 Files Changed

| Path | New / Modified | Summary of change |
|---|---|---|
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Replace `list_collections` stub body with two async queries (paginated SELECT + COUNT); add `limit: int` and `offset: int` Query params. Keep `Depends(get_current_user)` and add `Depends(get_session)`. |
| `apps/api/dataplat_api/schemas/collections.py` | MODIFIED | Narrow `CollectionListResponse.items` from `list[Any]` to `list[SourceCollectionOut]`. Remove `Any` from imports if no longer used. |
| `apps/api/tests/test_sources_collections_list.py` | NEW | Unit tests for F-010 (listed in §6). Mirrors style of `test_sources_collections_create.py`. |
| `verify/checks.sh` | MODIFIED | Extend the existing `collections)` layer with three new steps: create 3 collections (list-V1/V2 setup), then GET V1 (total=3), then GET V2 (limit=2, items=2, total=3). No new layer or `all)` change needed — `collections` is already in the `all)` chain after `auth`. |
| `packages/api-types/openapi.json` | MODIFIED | Regenerated in the same commit as schema + router changes (hard invariant #6). See §7 for exact command. |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — no ORM model change required.
- `apps/api/dataplat_api/db/session.py` — no change.
- `apps/api/dataplat_api/auth/dependencies.py` — no change.
- `apps/api/tests/test_sources_collections_create.py` — F-009 tests unchanged.
- `apps/api/tests/conftest.py` — existing `_patch_engine_begin` and `_patch_httpx_no_ssl` autouse fixtures are sufficient.

---

## §3 Design Decisions

### D1 — Pagination parameters

```python
@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CollectionListResponse:
```

| Param | Default | Constraint | Rationale |
|---|---|---|---|
| `limit` | 20 | `ge=1, le=200` | 20 rows is a useful page for a UI list; 200 is a safety cap preventing runaway responses. FastAPI's `Query(ge=1)` returns 422 for `limit=0` or negative — appropriate, since a zero-limit page is meaningless. |
| `offset` | 0 | `ge=0` | Standard skip-based pagination. FastAPI's `Query(ge=0)` returns 422 for negative offsets. |

V2 only exercises `limit=2`; the default and cap are conservative choices for MVP and do not need to be exercised by the verifier.

**Invalid value handling:** FastAPI's `Query(ge=..., le=...)` produces a 422 Unprocessable Entity automatically. No manual validation needed in the handler body.

### D2 — Owner filter

The list query MUST be scoped to `SourceCollection.owner_id == current_user.id`. A user must never see another user's collections. Both the paginated SELECT and the COUNT query carry this filter.

Rationale: the feature spec says "owned by the authenticated user"; ownership is recorded via `owner_id` FK (established by F-009).

### D3 — `total` semantics: count of ALL owner's collections, not page size

`total` is the result of a separate `SELECT COUNT(*)` over the full owner-filtered set, ignoring `limit`/`offset`. This means:

- After 3 collections are created, `GET /api/sources/collections?limit=2` returns `{"items": [<2 items>], "total": 3}`.
- `total` enables the client to compute the number of pages without a second round-trip.

Implementation: two independent async queries — one for the page, one for the count. No ORM relationship or subquery join is needed.

### D4 — Ordering: deterministic `ORDER BY id ASC`

The paginated SELECT uses `ORDER BY source_collection.id ASC`. Rationale:

- `id` is a `BigInteger IDENTITY` (monotonically increasing) — stable and unique, so the sort is deterministic.
- `created_at` has a `server_default=now()` but is nullable in the ORM, making it slightly less reliable as a sort key. `id` is simpler and equally correct.
- `ASC` returns oldest-first, which is conventional for append-ordered lists.

### D5 — `items` narrowing in `CollectionListResponse`

`CollectionListResponse.items` changes from `list[Any]` to `list[SourceCollectionOut]`. This is the obligation carried from F-009 (S009-F-009 §8, D7).

```python
# Before (F-009 stub):
class CollectionListResponse(BaseModel):
    items: list[Any]
    total: int

# After (F-010):
class CollectionListResponse(BaseModel):
    items: list[SourceCollectionOut]
    total: int
```

The `response_model=CollectionListResponse` decorator on the GET route already exists in the stub — no change to the decorator is needed. FastAPI will serialize via `SourceCollectionOut.from_attributes=True`.

Removing `Any` from the `typing` import: check if `Any` is still used elsewhere in the file. Based on the current file, `Any` is only used in `CollectionListResponse.items`, so remove it from the import.

### D6 — Two async queries vs. one

The handler issues two independent queries:

```python
from sqlalchemy import func, select

# Query 1: paginated page
result = await session.execute(
    select(SourceCollection)
    .where(SourceCollection.owner_id == current_user.id)
    .order_by(SourceCollection.id.asc())
    .limit(limit)
    .offset(offset)
)
rows = result.scalars().all()

# Query 2: total count (same filter, no limit/offset)
count_result = await session.execute(
    select(func.count()).select_from(SourceCollection)
    .where(SourceCollection.owner_id == current_user.id)
)
total = count_result.scalar_one()
```

Rationale: straightforward, readable, and sufficient for MVP scale. A single query with a window function (`COUNT(*) OVER ()`) would avoid a second round-trip but adds complexity. Two queries with separate `await` calls is the established pattern in this codebase.

### D7 — Response serialization

ORM rows returned by `result.scalars().all()` are `SourceCollection` instances. `SourceCollectionOut` has `model_config = ConfigDict(from_attributes=True)` (established by F-009), so serialization is:

```python
items = [SourceCollectionOut.model_validate(row) for row in rows]
return CollectionListResponse(items=items, total=total)
```

### D8 — `get_session` dependency added to GET handler

The current stub only has `Depends(get_current_user)`. F-010 adds `Depends(get_session)`. The import of `get_session` is already present at line 20 of `sources.py` (used by the POST handler) — no new import needed.

---

## §4 Deviations & Open Questions

### Open question OQ-1 — `limit` upper bound — CLOSED

Decision: `le=200` accepted by reviewer. No change required.

### Open question OQ-2 — `owner_id IS NULL` rows

The DB allows `owner_id = NULL` (the FK is nullable per `models.py` line 55). The list query filters by `owner_id == current_user.id`, which will exclude any orphaned rows (NULL `owner_id`). This is the correct behavior — orphaned rows are an integrity anomaly per F-009 §D2 and should not appear in any user's list. No special handling is needed.

### No deviations from F-009 agreed.md

F-009's agreed.md §8 explicitly assigned F-010 two obligations: (1) narrow `CollectionListResponse.items`, (2) run OpenAPI regen in the same commit. Both are addressed here.

---

## §5 Verification Plan

### Mapping feature_list.json criteria to concrete checks

| Criterion | Unit test | `checks.sh collections)` step |
|---|---|---|
| V1: After creating 3 collections, GET returns `{"items": [...], "total": 3}` | `test_list_collections_total_matches_owner_count` | `collections LIST-V1`: POST 3 collections, GET without params → assert `total == 3`, `len(items) == 3` |
| V2: `GET ?limit=2` returns `{"items": [<2 items>], "total": 3}` | `test_list_collections_limit_param` | `collections LIST-V2`: same 3 collections, GET `?limit=2` → assert `len(items) == 2`, `total == 3` |

### `collections)` layer extension (new steps appended after existing V1/V2/V3)

The existing `collections)` layer (lines 444-500 of `checks.sh`) creates one collection (`test-coll-checks`). The list checks need 3 collections total. The extension:

1. Attempts to create `test-coll-list-1` and `test-coll-list-2` (a third collection `test-coll-checks` already exists from F-009 V1 step earlier in the same layer run). If the layer is run in isolation, `test-coll-checks` may not exist — so the extension creates all three: `test-coll-list-a`, `test-coll-list-b`, `test-coll-list-c`, independently of the F-009 V1 step. This keeps list-V1/V2 self-contained.

```bash
echo "--- collections LIST-V1/V2 setup: create 3 deterministic collections ---"
for COLL_NAME in test-coll-list-a test-coll-list-b test-coll-list-c; do
  SETUP_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
    -H "Authorization: Bearer $COLL_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLL_NAME\"}" \
    -o /dev/null -w '%{http_code}')
  # Accept 201 (created) or 409 (already exists from a previous run) — both are OK.
  [[ "$SETUP_STATUS" == "201" || "$SETUP_STATUS" == "409" ]] \
    || { echo "FAIL: collections LIST setup for $COLL_NAME returned $SETUP_STATUS"; exit 1; }
  echo "  setup $COLL_NAME: $SETUP_STATUS"
done

echo "--- collections LIST-V1: GET returns total reflecting all owner collections ---"
LIST_BODY=$(mktemp)
LIST_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
  -H "Authorization: Bearer $COLL_TOKEN" \
  -w '%{http_code}' -o "$LIST_BODY")
test "$LIST_STATUS" = "200" \
  || { echo "FAIL: collections LIST-V1 returned $LIST_STATUS: $(cat "$LIST_BODY")"; rm -f "$LIST_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$LIST_BODY'))
assert 'items' in body, f'missing items key: {body}'
assert 'total' in body, f'missing total key: {body}'
assert isinstance(body['total'], int), f'total not int: {body}'
assert body['total'] >= 3, f'expected total >= 3, got {body[\"total\"]}: {body}'
assert len(body['items']) >= 3, f'expected >= 3 items, got {len(body[\"items\"])}'
assert body['total'] == len(body['items']), \
  f'with no limit param, total should equal items count; got total={body[\"total\"]}, items={len(body[\"items\"])}'
print('  LIST-V1 OK: total =', body['total'], 'items count =', len(body['items']))
" || { echo "FAIL: collections LIST-V1 response shape incorrect"; rm -f "$LIST_BODY"; exit 1; }
rm -f "$LIST_BODY"

echo "--- collections LIST-V2: GET ?limit=2 returns 2 items but total >= 3 ---"
LIST2_BODY=$(mktemp)
LIST2_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections?limit=2" \
  -H "Authorization: Bearer $COLL_TOKEN" \
  -w '%{http_code}' -o "$LIST2_BODY")
test "$LIST2_STATUS" = "200" \
  || { echo "FAIL: collections LIST-V2 returned $LIST2_STATUS: $(cat "$LIST2_BODY")"; rm -f "$LIST2_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$LIST2_BODY'))
assert len(body.get('items', [])) == 2, f'expected 2 items with limit=2, got {len(body.get(\"items\", []))}: {body}'
assert body.get('total', 0) >= 3, f'expected total >= 3, got {body.get(\"total\")}: {body}'
print('  LIST-V2 OK: items =', len(body['items']), 'total =', body['total'])
" || { echo "FAIL: collections LIST-V2 response shape incorrect"; rm -f "$LIST2_BODY"; exit 1; }
rm -f "$LIST2_BODY"
```

**Note on `total >= 3` vs `total == 3`:** The checks.sh layer runs after the F-009 `collections V1` step, which may have already created `test-coll-checks`. The admin user may thus own 4+ collections. The spec's V1/V2 say "after creating 3 collections … total: 3" — this applies to a clean-state scenario. In the integration environment where the layer may run multiple times, `>= 3` is the only reliable assertion. The unit tests use exact `== 3` semantics with a mocked session (see §6).

**Why `total == len(items)` is safe for LIST-V1 in the dev env:** The GET is issued without a `limit` param, so the default `limit=20` applies. The dev environment is not expected to accumulate 20+ collections for the admin user, so the default page will always contain ALL of the user's collections. Under that condition, `total` (the COUNT query over all owner rows) must equal `len(items)` (the page). If a broken implementation set `total = len(items)` to fake the count, the separate `total >= 3` assertion would still catch a zero-count bug, and the LIST-V2 check (which sends `limit=2` and asserts `total >= 3` while `len(items) == 2`) would expose any implementation that ties `total` to the page size.

---

## §6 Test Table

All tests live in `apps/api/tests/test_sources_collections_list.py`. All are pure unit tests using `TestClient(app)` with the `conftest.py` autouse fixtures. No live Postgres required.

**Mock session pattern for list tests:**

The GET handler calls `session.execute()` twice (once for the page, once for the count). The mock session must return the appropriate result objects for each call. The test helper uses `AsyncMock` with `side_effect` as an iterable: the first `execute()` call returns a mock that `.scalars().all()` yields the row list; the second returns a mock that `.scalar_one()` yields the count integer.

The two `side_effect` items (page_result_mock, count_result_mock) MUST be plain `MagicMock`, NOT `AsyncMock`. Only `session.execute(...)` itself is awaited; `.scalars()`, `.all()`, and `.scalar_one()` are synchronous calls on the result proxy. Using `AsyncMock` for those would cause `.scalars()` to return a coroutine instead of a result object, producing a subtle runtime failure rather than a clear assertion error.

| Test name | What it asserts |
|---|---|
| `test_list_collections_empty` | GET with no collections for user → `{"items": [], "total": 0}`, status 200 |
| `test_list_collections_total_matches_owner_count` | Mock session returns 3 rows, count=3 → `items` has 3 elements, `total == 3` |
| `test_list_collections_limit_param` | Mock session returns page of 2 rows (limit=2), count=3 → `len(items)==2`, `total==3` |
| `test_list_collections_offset_param` | Mock session returns page of 1 row (offset=2 of 3), count=3 → `len(items)==1`, `total==3` |
| `test_list_collections_items_shape` | Each item in `items` has keys: `id`, `name`, `owner_id`, `dataset_card_md`, `created_at`, `updated_at` |
| `test_list_collections_owner_filter` | Asserts the first `session.execute` call carries a WHERE clause scoped to the current user. Use this exact approach: `first_stmt = session_mock.execute.call_args_list[0].args[0]`; `compiled = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))`; `assert "owner_id" in compiled`; `assert str(_MOCK_USER.id) in compiled`. Do NOT stringify the raw Select object — compile with literal binds so the user id appears as a literal value in the SQL string. |
| `test_list_collections_no_token_returns_401` | GET without Authorization header → 401, `WWW-Authenticate: Bearer` |
| `test_list_collections_invalid_limit_zero_returns_422` | `GET ?limit=0` → 422 (ge=1 violated) |
| `test_list_collections_invalid_limit_negative_returns_422` | `GET ?limit=-1` → 422 (ge=1 violated) |
| `test_list_collections_invalid_limit_over_cap_returns_422` | `GET ?limit=201` → 422 (le=200 violated) |
| `test_list_collections_invalid_offset_negative_returns_422` | `GET ?offset=-1` → 422 (ge=0 violated) |
| `test_list_collections_default_params_accepted` | GET with no query params → 200 (defaults limit=20, offset=0 work without 422) |

---

## §7 Hard-Invariant Compliance Checklist

### Invariant #5 — Async SQLAlchemy only

- The handler MUST use `await session.execute(select(...))`. No `session.query()`.
- Both the paginated SELECT and the COUNT use `await session.execute(...)`.
- `result.scalars().all()` and `count_result.scalar_one()` are synchronous calls on the result proxy (correct; `execute` is the async step).
- The ruff + mypy checks in `checks.sh backend)` will catch any sync session usage.

### Invariant #6 — OpenAPI ↔ TS type sync

Narrowing `CollectionListResponse.items` from `list[Any]` to `list[SourceCollectionOut]` changes the OpenAPI schema for `GET /api/sources/collections`. The `packages/api-types/openapi.json` MUST be regenerated and committed in the **same commit** as the schema and router changes.

**Exact regen command** (from S009-F-009 agreed.md §6 / commit `594356d` precedent, also confirmed by S008 commit `91a2651`):

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
```

Run from the repo root as: `cd apps/api && uv run python -c '...' > ../../packages/api-types/openapi.json`

The `contract)` layer in `checks.sh` has the guard `[[ -f Makefile ]] || { echo "no Makefile yet ..."; exit 0; }` which prevents CI failure while the Makefile is absent. This guard remains in place — no Makefile change is needed in this sprint.

After running the regen command, confirm the diff includes the narrowed `items` schema (the `$ref` to `SourceCollectionOut` replacing the generic `anyOf`/empty array type):

```bash
git diff packages/api-types/openapi.json
```

Commit the updated `openapi.json` in the same commit as `schemas/collections.py` and `routers/sources.py`.

---

## §8 Hand-off Notes to Downstream Features

### F-014 — List sources in a collection

F-014 will likely implement `GET /api/sources/collections/{id}/sources` or `GET /api/sources?collection_id={id}` with a paginated list of `Source` objects. The pagination shape established here (`{"items": [...], "total": N}` with `limit`/`offset` Query params, `ge`/`le` constraints, two async queries) is the canonical pattern for MVP list endpoints. F-014 MUST reuse this shape for consistency.

Specifically:
- Use `limit: int = Query(default=20, ge=1, le=200)` and `offset: int = Query(default=0, ge=0)`.
- Return a response schema with `items: list[<ItemOut>]` and `total: int`.
- Issue two separate async `session.execute()` calls (page query + count query), both filtered by the relevant scope (collection_id + ownership).
- Order by `id ASC` unless there is a domain reason to prefer another column.

### `SourceCollectionOut` is stable

F-010 does not change `SourceCollectionOut` — it was finalized in F-009. Future features that need to surface collection metadata (e.g., a collection detail page, if added post-MVP) can import `SourceCollectionOut` from `dataplat_api.schemas.collections` directly.

### `CollectionListResponse` is stable post-F-010

After F-010, `CollectionListResponse` has its final shape. Any future additions (e.g., a `next_cursor` field for cursor-based pagination) would require a schema version bump and a new `make codegen` cycle. Do not add fields to `CollectionListResponse` without a corresponding OpenAPI regen commit.

---

## §9 Risks & Open Questions (recap)

| ID | Risk / Question | Mitigation |
|---|---|---|
| OQ-1 | `limit` upper bound: 200 or 100? | CLOSED — reviewer (Mode A) accepted `le=200` for MVP. |
| R1 | `session.execute()` mock complexity for two calls | Use `AsyncMock(side_effect=[page_result_mock, count_result_mock])` — a list side_effect is consumed call by call. Document this in test helpers. |
| R2 | Integration layer `total` may be `>= 3` not `== 3` | Unit tests use `== 3` (mocked); integration checks use `>= 3` (live DB may accumulate collections from repeated runs). Documented in §5. |
| R3 | `Any` import removal from `schemas/collections.py` | After narrowing `items`, confirm `Any` is unused. If it is, remove from `from typing import Annotated, Any`. ruff will flag an unused import if left in. |
