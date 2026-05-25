# S014-F-014 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S014-F-014

---

## §1 Goal

F-014 adds `GET /api/sources/collections/{id}/sources` to the existing sources router. For an authenticated caller, the endpoint returns a paginated list of all `Source` records that belong to the specified collection — but only if that collection exists AND is owned by the caller. If the collection does not exist or belongs to another user, the endpoint returns 404 (no enumeration leak). The response shape mirrors F-010: `{"items": [...], "total": N}` with `limit`/`offset` pagination.

No new table or migration is required; the `source` and `source_collection` tables are complete from prior sprints.

---

## §2 Files to Change

| Path | New / Modified | Summary |
|---|---|---|
| `apps/api/dataplat_api/schemas/sources.py` | MODIFIED | Add `SourceListResponse` Pydantic response schema (`items: list[SourceRead]`, `total: int`). `SourceRead` and `SourceUploadResponse` are unchanged. |
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Add `GET /collections/{id}/sources` route handler before the existing `GET /{id}` catch-all. Update module docstring. |
| `apps/api/tests/test_sources_list_by_collection.py` | NEW | Unit tests for the new endpoint. |
| `verify/checks.sh` | MODIFIED | Extend the existing `sources)` layer with F-014 assertions (V1 and V2). |
| `packages/api-types/openapi.json` | MODIFIED | Regenerated via `make codegen` (or inline `uv run python` command) after the new route + schema are added. Committed in the same commit as the router/schema changes. |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — no schema change; all needed columns already exist.
- Any migration file — no DB change.
- `apps/api/dataplat_api/dagster/` — not involved in a read endpoint.
- `apps/api/dataplat_api/schemas/collections.py` — not changed.
- `docs/data_platform_design.md` — read-only.

---

## §3 Endpoint Design

### §3.1 Path, method, and route registration

```
GET /api/sources/collections/{id}/sources
```

Registered on `router = APIRouter(prefix="/api/sources", tags=["sources"])`. The path relative to the prefix is `/collections/{id}/sources`.

**Route-ordering safety.** The current router registers routes in this order:

```
GET  /collections             ← fixed path, F-010
POST /collections             ← fixed path, F-009
POST /upload                  ← fixed path, F-011
GET  /{id}                    ← catch-all, F-013 (must remain LAST)
```

The new route `/collections/{id}/sources` has three path segments. The existing catch-all `GET /{id}` has one path segment. FastAPI's router matches on the full path after prefix, so a three-segment path cannot collide with a one-segment catch-all — they are structurally distinct and FastAPI dispatches by segment count before evaluating wildcards.

The new route MUST be inserted **before** `GET /{id}` (the catch-all) and **after** the other `/collections` routes. Recommended placement: immediately after `POST /collections` (create collection handler, F-009) and before `POST /upload`. This preserves the invariant that `GET /{id}` is the last `GET` route registered.

**Explicit placement in `routers/sources.py`:**

1. `GET  /collections`              — list collections (F-010), unchanged
2. `POST /collections`              — create collection (F-009), unchanged
3. **`GET  /collections/{id}/sources`** — new F-014 handler, inserted here
4. `POST /upload`                   — upload source (F-011), unchanged
5. `GET  /{id}`                     — source detail (F-013), LAST (unchanged)

The string `"collections"` cannot be parsed as an integer `id` for the catch-all, so there is no collision risk even if ordering were wrong — but correct placement is required for maintainability and to satisfy the route-ordering invariant documented in F-013.

### §3.2 Path parameter

```python
id: int
```

FastAPI coerces the `{id}` segment to `int`. Non-integer values (e.g. `GET /api/sources/collections/foo/sources`) return 422 automatically.

### §3.3 Auth dependency

```python
current_user: User = Depends(get_current_user)
```

Identical to all existing handlers. Missing or invalid token → 401.

### §3.4 Response model

`response_model=SourceListResponse`, `status_code=200` (default). See §3.6 for the schema definition.

### §3.5 Owner-scoping and error semantics — design decision

The `{id}` is a **collection id**, not a source id. Ownership is established via `SourceCollection.owner_id`. The scoping strategy is:

**Step 1 — Verify collection ownership (single query).** Execute a SELECT against `source_collection` filtered by both `id == <path_param>` AND `owner_id == current_user.id`. If the result is `None` (collection not found OR found but not owned by caller), raise HTTP 404 immediately.

**Step 2 — List sources (two queries, paginated + count).** Once the collection is confirmed to exist and be owned by the caller, execute the paginated SELECT and COUNT against `source` filtered by `collection_id == <path_param>`.

**Why 404 (not 403) for unowned/missing collection:** Mirrors F-013 pattern — returning 403 would confirm the collection exists, leaking existence information. Both "does not exist" and "exists but owned by another user" return the same 404 to prevent enumeration.

**Why separate the ownership check from the source queries:** The ownership check is a scalar query that short-circuits cleanly to 404 before the potentially-larger paginated queries are issued. The alternative — embedding ownership as a JOIN condition in the source queries — would silently return `{"items": [], "total": 0}` for unowned collections (since no sources would match), which would be misleading and inconsistent with the F-013 pattern. Explicit 404 on the collection is the correct behavior.

**Query sequence (async, invariant #5):**

```python
# Step 1: verify the collection exists and is owned by the caller.
coll_result = await session.execute(
    select(SourceCollection)
    .where(SourceCollection.id == id)
    .where(SourceCollection.owner_id == current_user.id)
)
collection = coll_result.scalar_one_or_none()
if collection is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                        detail="Collection not found")

# Step 2a: paginated source list.
result = await session.execute(
    select(Source)
    .where(Source.collection_id == id)
    .order_by(Source.id.asc())
    .limit(limit)
    .offset(offset)
)
rows = result.scalars().all()

# Step 2b: total count (same filter, no limit/offset).
count_result = await session.execute(
    select(func.count())
    .select_from(Source)
    .where(Source.collection_id == id)
)
total = count_result.scalar_one()
```

No `session.query()`. No sync session.

### §3.6 Response schema — design decision

**Decision: reuse `SourceRead` as the item type; add a new `SourceListResponse` in `schemas/sources.py`.**

Rationale:
- `SourceRead` already contains all 5 fields required by the F-014 verification criteria (`id`, `original_name`, `storage_uri`, `sha256`, `uploaded_at`) plus additional fields (`collection_id`, `kind`, `size`, `mime_type`, `dagster_partition_key`). A superset is acceptable — it requires no new schema and no migration risk.
- A leaner `SourceListItem` with only the 5 required fields would introduce an additional schema and diverge from the detail endpoint without adding value at MVP scale. Adding a lean type now would also make it harder to correlate items with their detail view.
- `SourceListResponse` mirrors `CollectionListResponse` (from F-010) exactly: `items: list[SourceRead]`, `total: int`. This is the established pagination shape for this codebase.

New class to add in `schemas/sources.py`:

```python
class SourceListResponse(BaseModel):
    """Response for GET /api/sources/collections/{id}/sources (F-014)."""

    items: list[SourceRead]
    total: int
```

No `model_config` needed on `SourceListResponse` itself — only on `SourceRead` (already has `from_attributes=True`).

### §3.7 Pagination

Same convention as F-010:

| Param | Default | Constraint | Source |
|---|---|---|---|
| `limit` | 20 | `ge=1, le=200` | `Query(default=20, ge=1, le=200)` |
| `offset` | 0 | `ge=0` | `Query(default=0, ge=0)` |

`total` = COUNT of ALL sources in the specified collection (not just the current page). Ordering: `Source.id ASC`.

### §3.8 Full handler signature

```python
@router.get(
    "/collections/{id}/sources",
    response_model=SourceListResponse,
    summary="List Sources in Collection",
)
async def list_sources_by_collection(
    id: int,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceListResponse:
```

No S3 or Dagster dependency — this is a pure DB read.

### §3.9 Serialization

```python
items = [SourceRead.model_validate(row) for row in rows]
return SourceListResponse(items=items, total=total)
```

`SourceRead` has `model_config = ConfigDict(from_attributes=True)`, so `model_validate` on an ORM `Source` instance works correctly.

---

## §4 Hard-Invariant Compliance

| Invariant | Assessment |
|---|---|
| #1 Lineage mandatory | NOT APPLICABLE — read-only endpoint; no Commit object. |
| #2 Storage separation + CAS | NOT APPLICABLE — no blob bytes written to Postgres; no MinIO writes. |
| #3 Schema frozen post-publish | NOT APPLICABLE — no Silver/Gold commit. |
| #4 LLM calls through gateway | NOT APPLICABLE — no LLM call. |
| #5 Async SQLAlchemy | SATISFIED — handler uses three `await session.execute(select(...))` calls, all with `select()` and async result accessors. No `session.query()`. |
| #6 OpenAPI ↔ TS type sync | REQUIRED — `SourceListResponse` is a new response schema on a new route. The OpenAPI output changes. The implementer MUST run `make codegen` (or the inline regen command) and commit `packages/api-types/openapi.json` in the same commit as `routers/sources.py` and `schemas/sources.py`. |

---

## §5 Verification Plan

### §5.1 Criterion V1 — After uploading 3 PDFs to a collection, GET returns `{"items": [<3 items>], "total": 3}`

Each item must include `id`, `original_name`, `storage_uri`, `sha256`, `uploaded_at`.

**checks.sh assertion (appended to the existing `sources)` layer, after the F-013 checks):**

Setup: create a collection for the test user, upload 3 PDFs to it, then call the new endpoint.

```bash
echo "--- sources F014-V1: create collection and upload 3 sources to it ---"
# Create a collection dedicated to F-014 tests.
F014_COLL_BODY=$(mktemp)
F014_COLL_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections" \
  -H "Authorization: Bearer $SRC_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "f014-test-collection"}' \
  -w '%{http_code}' -o "$F014_COLL_BODY")
# Accept 201 or 409 (idempotent across reruns).
[[ "$F014_COLL_STATUS" == "201" || "$F014_COLL_STATUS" == "409" ]] \
  || { echo "FAIL: F014-V1 collection create returned $F014_COLL_STATUS: $(cat "$F014_COLL_BODY")"; rm -f "$F014_COLL_BODY"; exit 1; }
# If 201, grab the new id; if 409 (already exists), query the DB.
if [[ "$F014_COLL_STATUS" == "201" ]]; then
  F014_COLL_ID=$(python3 -c "import json; print(json.load(open('$F014_COLL_BODY'))['id'])")
else
  F014_COLL_ID=$(docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT id FROM source_collection WHERE name='f014-test-collection'" \
    | tr -d '[:space:]')
fi
rm -f "$F014_COLL_BODY"
echo "  F014 collection id=$F014_COLL_ID"

# Upload 3 minimal PDFs to the collection.
F014_SRC_IDS=()
for i in 1 2 3; do
  F014_PDF=$(mktemp /tmp/f014-XXXXXX.pdf)
  python3 -c "
pdf = (b'%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type /Pages /Kids[3 0 R] /Count 1>>endobj\n'
       b'3 0 obj<</Type /Page /MediaBox[0 0 612 792] /Parent 2 0 R>>endobj\n'
       b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
       b'0000000058 00000 n \n0000000115 00000 n \n'
       b'trailer<</Size 4 /Root 1 0 R>>\nstartxref\n182\n%%EOF\n')
open('$F014_PDF', 'wb').write(pdf)
"
  F014_UP_BODY=$(mktemp)
  F014_UP_STATUS=$(curl -sS -X POST \
    "http://localhost:${FASTAPI_HOST_PORT}/api/sources/upload" \
    -H "Authorization: Bearer $SRC_TOKEN" \
    -F "file=@${F014_PDF};type=application/pdf" \
    -F "collection_id=${F014_COLL_ID}" \
    -w '%{http_code}' -o "$F014_UP_BODY")
  rm -f "$F014_PDF"
  test "$F014_UP_STATUS" = "201" \
    || { echo "FAIL: F014-V1 upload $i returned $F014_UP_STATUS: $(cat "$F014_UP_BODY")"; rm -f "$F014_UP_BODY"; exit 1; }
  F014_SRC_ID=$(python3 -c "import json; print(json.load(open('$F014_UP_BODY'))['id'])")
  F014_SRC_IDS+=("$F014_SRC_ID")
  rm -f "$F014_UP_BODY"
  echo "  uploaded source $i: id=$F014_SRC_ID"
done

echo "--- sources F014-V1: GET /api/sources/collections/{id}/sources returns 200, total>=3 ---"
F014_LIST_BODY=$(mktemp)
F014_LIST_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections/${F014_COLL_ID}/sources" \
  -H "Authorization: Bearer $SRC_TOKEN" \
  -w '%{http_code}' -o "$F014_LIST_BODY")
test "$F014_LIST_STATUS" = "200" \
  || { echo "FAIL: F014-V1 GET returned $F014_LIST_STATUS: $(cat "$F014_LIST_BODY")"; rm -f "$F014_LIST_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$F014_LIST_BODY'))
assert 'items' in body, f'missing items key: {body}'
assert 'total' in body, f'missing total key: {body}'
assert isinstance(body['total'], int), f'total not int: {body}'
assert body['total'] >= 3, f'expected total >= 3, got {body[\"total\"]}'
assert len(body['items']) >= 3, f'expected >= 3 items, got {len(body[\"items\"])}'
required = ['id', 'original_name', 'storage_uri', 'sha256', 'uploaded_at']
for item in body['items']:
    for field in required:
        assert field in item, f'item missing field {field}: {item}'
print('  F014-V1 OK: total =', body['total'], 'items =', len(body['items']))
" || { echo "FAIL: F014-V1 response shape incorrect"; rm -f "$F014_LIST_BODY"; exit 1; }
rm -f "$F014_LIST_BODY"
```

### §5.2 Criterion V2 — Response shape: `{"items": [<3 items>], "total": 3}`

This is satisfied by V1 above (same request, same assertions for both shape and total). No additional integration step is needed for V2 — the V1 check asserts both `total >= 3` and that each item includes the 5 required fields.

**checks.sh assertion for 404 on unknown/unowned collection:**

```bash
echo "--- sources F014-V2: GET on non-existent collection returns 404 ---"
F014_NOTFOUND_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections/999999/sources" \
  -H "Authorization: Bearer $SRC_TOKEN" \
  -o /dev/null -w '%{http_code}')
test "$F014_NOTFOUND_STATUS" = "404" \
  || { echo "FAIL: F014-V2 returned $F014_NOTFOUND_STATUS (expected 404)"; exit 1; }
echo "  F014-V2 OK: /api/sources/collections/999999/sources -> 404"
```

### §5.3 Applicable checks.sh layer

Both assertions are appended inside the existing `sources)` case block, after the existing F-013 checks (F013-V1 and F013-V2). No new layer is added; the `all)` chain already includes `bash "$0" sources`.

The F014 setup block creates a collection named `f014-test-collection` and uploads 3 PDFs to it. A 409 on the collection create (idempotent reruns) falls back to a DB query for the collection id. This keeps the checks self-contained and rerunnable.

### §5.4 Unit tests

All tests live in `apps/api/tests/test_sources_list_by_collection.py`. Run via `bash verify/checks.sh backend` (`uv run pytest -q`). No live DB, MinIO, or Dagster required.

**Mock pattern:** The new handler calls `session.execute()` three times on the happy path (collection check + page query + count query). The mock session uses `AsyncMock(side_effect=[...])` as an iterable of three `MagicMock` result objects:

1. First call: `.scalar_one_or_none()` returns a `SourceCollection` ORM stub (happy path) or `None` (404 path).
2. Second call: `.scalars().all()` returns a list of `Source` ORM stubs.
3. Third call: `.scalar_one()` returns an integer count.

`side_effect` items MUST be plain `MagicMock`, NOT `AsyncMock`. Only `session.execute(...)` itself is awaited; `.scalar_one_or_none()`, `.scalars()`, `.all()`, `.scalar_one()` are synchronous calls on the result proxy (per established pattern from F-010 and F-013 test files).

For the 404 path, only one `execute()` call is made (the collection check). The `side_effect` list can have just one entry in those tests.

| Test name | Criterion | What it asserts |
|---|---|---|
| `test_list_sources_by_collection_returns_200_with_items` | V1 | 200; `items` has 3 elements; `total == 3`. |
| `test_list_sources_by_collection_items_have_required_fields` | V1 | Each item contains `id`, `original_name`, `storage_uri`, `sha256`, `uploaded_at`. |
| `test_list_sources_by_collection_total_is_full_count_not_page` | V1 | `limit=2` page returns 2 items but `total == 3`. |
| `test_list_sources_by_collection_offset_works` | V1 | `offset=2` of 3 returns 1 item, `total == 3`. |
| `test_list_sources_by_collection_collection_not_found_returns_404` | V2 | Session returns `None` for collection check → 404 with `detail="Collection not found"`. |
| `test_list_sources_by_collection_other_owners_collection_returns_404` | Owner-scoping | Collection owned by another user → session returns `None` for collection check → 404. |
| `test_list_sources_by_collection_empty_collection_returns_zero` | Edge case | Collection exists and is owned, but has no sources → `{"items": [], "total": 0}`. |
| `test_list_sources_by_collection_no_token_returns_401` | Auth gate | No `Authorization` header → 401. |
| `test_list_sources_by_collection_invalid_limit_zero_returns_422` | Pagination | `?limit=0` → 422. |
| `test_list_sources_by_collection_invalid_limit_over_cap_returns_422` | Pagination | `?limit=201` → 422. |
| `test_list_sources_by_collection_invalid_offset_negative_returns_422` | Pagination | `?offset=-1` → 422. |

---

## §6 Edge Cases

| Case | Behavior |
|---|---|
| Collection does not exist | 404, `detail="Collection not found"` |
| Collection exists, owned by a different user | 404, `detail="Collection not found"` (same response — no enumeration) |
| Collection exists and is owned, but has zero sources | 200, `{"items": [], "total": 0}` |
| `limit=0` | 422 (FastAPI `ge=1` validation) |
| `limit=201` | 422 (FastAPI `le=200` validation) |
| `offset=-1` | 422 (FastAPI `ge=0` validation) |
| Non-integer collection id (e.g. `/api/sources/collections/foo/sources`) | 422 (FastAPI int path-param coercion) |
| No `Authorization` header | 401 |
| Invalid / expired token | 401 |

---

## §7 Out of Scope

- Any change to `GET /api/sources/collections` (F-010), `GET /api/sources/{id}` (F-013), or any other existing handler.
- Filtering sources within a collection by any field (search, mime_type, etc.).
- Cursor-based pagination — `limit`/`offset` is sufficient for MVP.
- Sources with `collection_id IS NULL` — they have no collection and this endpoint is scoped to a concrete collection id.
- Adding `uploaded_by` / `owner_id` to the `source` table — deferred; would require a migration.
- Including `license`, `source_metadata`, `preferred_extractor` in `SourceListResponse` items — these are optional extension fields deferred per F-013.

---

## §8 Codegen Requirement

`SourceListResponse` is a new Pydantic model attached to a new `GET` route. This changes the FastAPI OpenAPI schema output. Hard invariant #6 applies.

**Required action for implementer:**

If a root `Makefile` with a `codegen` target exists:

```bash
make codegen
git diff packages/api-types/openapi.json
```

Otherwise (current state — no Makefile per `checks.sh contract)` guard):

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
git diff packages/api-types/openapi.json
```

The diff WILL be non-empty (new path `/api/sources/collections/{id}/sources` and new component schema `SourceListResponse`). Commit `packages/api-types/openapi.json` in the **same commit** as `routers/sources.py` and `schemas/sources.py`.

---

## §9 Open Questions for Reviewer

| ID | Question | Recommendation |
|---|---|---|
| OQ-1 | Should a 404 on an unowned collection use `detail="Collection not found"` or a more generic `detail="Not found"`? | Recommend `"Collection not found"` for clarity — it matches the F-009/F-010 pattern where collections return collection-specific messages. Both prevent enumeration equally. |
| OQ-2 | Should the total count in the unit tests assert `== 3` (exact) or `>= 3`? | Recommend `== 3` in unit tests (mocked session, exact control) and `>= 3` in integration checks (live DB may accumulate sources from repeated runs). This is the same pattern as F-010. |
| OQ-3 | Is three separate `session.execute()` calls on the happy path acceptable? | Yes — this is consistent with F-010 (two calls) extended by one ownership-check call. A combined JOIN query could merge ownership check + source list but would complicate the empty-collection case and obscure the 404 logic. Three calls is clearer and sufficiently performant for MVP. Reviewer may override if they prefer a JOIN approach. |
