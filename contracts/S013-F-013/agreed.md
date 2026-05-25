# S013-F-013 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S013-F-013

---

## §1 Goal

F-013 adds `GET /api/sources/{id}` to the existing sources router. For an authenticated caller, the endpoint returns 200 with the full source record — `id`, `original_name`, `kind`, `storage_uri`, `sha256`, `size`, `mime_type`, `collection_id`, `uploaded_at`, `dagster_partition_key` — for any source the caller owns (owner-scoped via the source's collection, or uncollected sources uploaded by the caller). Requesting a source that does not exist or is not accessible returns 404. No new table or migration is required; the `source` table and `Source` ORM model are already complete from F-011.

---

## §2 Files to Change

| Path | New / Modified | Summary |
|---|---|---|
| `apps/api/dataplat_api/schemas/sources.py` | MODIFIED | Add `SourceRead` Pydantic response schema (new class). `SourceUploadResponse` is unchanged. |
| `apps/api/dataplat_api/routers/sources.py` | MODIFIED | Add `GET /{id}` route handler using `SourceRead` as response_model. |
| `apps/api/tests/test_sources_get_detail.py` | NEW | Unit tests for the new endpoint. |
| `verify/checks.sh` | MODIFIED | Extend the existing `sources)` layer with F-013 assertions (V1 and V2). |
| `packages/api-types/openapi.json` | MODIFIED | Regenerated via `make codegen` after the new route + schema are added. Must be committed in the same commit as the router/schema change. |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — no schema change; all needed columns already exist.
- Any migration file — no DB change.
- `apps/api/dataplat_api/dagster/` — not involved in a read endpoint.
- `docs/data_platform_design.md` — read-only.

---

## §3 Endpoint Design

### Route and method

```
GET /api/sources/{id}
```

Registered on the `router = APIRouter(prefix="/api/sources", tags=["sources"])` that already exists in `routers/sources.py`. The concrete path relative to the prefix is `/{id}`.

### Route-ordering safety

The current router has three routes registered in this order:

```
GET  /collections          ← fixed path
POST /collections          ← fixed path
POST /upload               ← fixed path
```

Adding `GET /{id}` as a **fourth** route (appended at the bottom of the file) is safe. FastAPI matches routes in registration order and only dispatches to `/{id}` if no fixed path matched first. Because `GET /collections` is registered before `GET /{id}`, a request to `GET /api/sources/collections` will always hit the `list_collections` handler, never the new detail handler. The string `"collections"` would only be parsed as an integer `id`, which would fail path-param conversion (FastAPI raises 422 for a non-integer), but since the fixed route matches first this never occurs.

`/upload` is a `POST`-only route; it is invisible to `GET` requests and does not conflict.

**Conclusion:** append the new handler at the bottom of `routers/sources.py` — no reordering of existing routes is required.

### Path parameter

```python
id: int
```

FastAPI will coerce the path segment to `int`. Non-integer segments (e.g. `GET /api/sources/foo`) return 422 automatically before the handler is called. This is correct behaviour and requires no extra handling.

### Auth dependency

```python
current_user: User = Depends(get_current_user)
```

Identical to all existing handlers. Missing or invalid token → 401, exactly as in F-010 and F-011.

### Response model

New `SourceRead` schema (see §3-schema). `response_model=SourceRead`, `status_code=200` (FastAPI default).

### Owner-scoping — design decision

The `Source` model has no direct `owner_id` column. Ownership is established only when a source belongs to a collection (`collection_id` FK → `source_collection.owner_id`). Uncollected sources (`collection_id IS NULL`) have no owner column at all.

**F-010 precedent:** `GET /api/sources/collections` filters strictly by `SourceCollection.owner_id == current_user.id`. An unknown collection returns 0 results (effectively 404 from the caller's perspective), never a 403.

**Decision for F-013:** Mirror the F-010 pattern — return 404 for any source the caller cannot see, regardless of whether it exists under a different owner. This prevents information leakage (a 403 would confirm the source exists). The scoping query:

```sql
SELECT source.*
FROM source
LEFT JOIN source_collection ON source.collection_id = source_collection.id
WHERE source.id = :id
  AND (
    source_collection.owner_id = :user_id     -- collected, owned
    OR source.collection_id IS NULL            -- uncollected: visible to any authenticated user
  )
```

**Rationale for uncollected sources being world-readable among authenticated users:** The MVP design has no `owner_id` on the `source` table. Until F-014+ adds a direct ownership column, uncollected sources cannot be scoped to an uploader. Returning 404 for all uncollected sources would break the primary upload → detail round-trip for any upload done without a `collection_id`. The least-bad MVP choice is: uncollected sources are readable by any authenticated user. This is consistent with the `visibility = private|internal` model described in CLAUDE.md §scope — sources without a collection are treated as `internal` (accessible to authenticated users only).

**Note to reviewer:** if the preferred semantics is strict owner-scoping (404 for uncollected sources not owned by the caller), this requires adding `uploaded_by` / `owner_id` to the `source` table, which is a migration — out of scope for F-013. The current proposal avoids that. If reviewer prefers a stricter join-only approach (no uncollected visibility), they should request that change explicitly.

### 404 behaviour

```python
if source is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
```

This is the standard `HTTPException` pattern used across the codebase (matching collections 409 and runs 404 patterns). Both "does not exist" and "exists but belongs to another owner's collection" return the same 404/detail to prevent enumeration.

### SourceRead schema (§3-schema)

A **new** Pydantic model is needed. `SourceUploadResponse` only contains `id` and `storage_uri` — deliberately minimal per F-011 agreed.md §3-D8. F-013 requires the full record. Reusing `SourceUploadResponse` is not appropriate; a new `SourceRead` class is defined in the same file (`schemas/sources.py`):

```python
class SourceRead(BaseModel):
    """Response schema for GET /api/sources/{id} (F-013)."""

    id: int
    collection_id: int | None
    kind: str
    original_name: str
    storage_uri: str
    sha256: str
    size: int | None
    mime_type: str | None
    dagster_partition_key: str
    uploaded_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
```

Fields not included: `license`, `source_metadata`, `preferred_extractor` — these are optional extension fields not required by the F-013 verification criteria. They can be added in a later sprint without a breaking change. The fields that ARE included cover all five required by the feature spec (`storage_uri`, `sha256`, `size`, `mime_type`, `collection_id`) plus `id`, `original_name`, `kind`, `dagster_partition_key`, and `uploaded_at` for completeness.

### Full handler signature

```python
@router.get("/{id}", response_model=SourceRead)
async def get_source(
    id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceRead:
```

No S3 or Dagster dependency — this is a pure DB read.

### SQLAlchemy query (async, invariant #5)

```python
from sqlalchemy.orm import aliased  # if needed
from sqlalchemy import or_

result = await session.execute(
    select(Source)
    .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
    .where(Source.id == id)
    .where(
        or_(
            SourceCollection.owner_id == current_user.id,
            Source.collection_id.is_(None),
        )
    )
)
source = result.scalar_one_or_none()
if source is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
return SourceRead.model_validate(source)
```

No `session.query()`. No sync session. Matches the async pattern of all existing handlers.

---

## §4 Hard-Invariant Compliance

| Invariant | Assessment |
|---|---|
| #1 Lineage mandatory | NOT APPLICABLE — read-only endpoint; no Commit object. |
| #2 Storage separation + CAS | NOT APPLICABLE — no blob bytes written to Postgres; no MinIO writes. |
| #3 Schema frozen post-publish | NOT APPLICABLE — no Silver/Gold commit. |
| #4 LLM calls through gateway | NOT APPLICABLE — no LLM call. |
| #5 Async SQLAlchemy | SATISFIED — handler uses `await session.execute(select(...))` and `scalar_one_or_none()`. |
| #6 OpenAPI ↔ TS type sync | REQUIRED — `SourceRead` is a new response schema on a new route. The OpenAPI output changes. The implementer MUST run `make codegen` and commit the `packages/api-types/openapi.json` diff in the same commit as the router/schema change. |

---

## §5 Verification Plan

### Criterion V1 — GET /api/sources/{id} returns 200 with all required fields

**checks.sh assertion (appended to the existing `sources)` layer):**

After the existing UPLOAD-V1 through UPLOAD-V4 checks (which already capture `SRC_ID`), add:

```bash
echo "--- sources F013-V1: GET /api/sources/{id} returns 200 with required fields ---"
DETAIL_BODY=$(mktemp)
DETAIL_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/${SRC_ID}" \
  -H "Authorization: Bearer $SRC_TOKEN" \
  -w '%{http_code}' -o "$DETAIL_BODY")
test "$DETAIL_STATUS" = "200" \
  || { echo "FAIL: F013-V1 returned $DETAIL_STATUS: $(cat "$DETAIL_BODY")"; rm -f "$DETAIL_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$DETAIL_BODY'))
required = ['id', 'storage_uri', 'sha256', 'size', 'mime_type', 'collection_id',
            'kind', 'original_name', 'dagster_partition_key', 'uploaded_at']
for field in required:
    assert field in body, f'missing field {field}: {body}'
assert body['id'] == ${SRC_ID}, f'id mismatch: {body}'
assert body['storage_uri'] == f\"s3://sources/${SRC_ID}/original.pdf\", f'storage_uri wrong: {body}'
assert body['sha256'], f'sha256 empty: {body}'
assert body['mime_type'] == 'application/pdf', f'mime_type wrong: {body}'
assert body['kind'] == 'file', f'kind wrong: {body}'
print('  F013-V1 OK: all required fields present, id=%d' % body['id'])
" || { echo "FAIL: F013-V1 response shape incorrect"; rm -f "$DETAIL_BODY"; exit 1; }
rm -f "$DETAIL_BODY"
```

### Criterion V2 — GET /api/sources/99999 returns 404

```bash
echo "--- sources F013-V2: GET /api/sources/99999 returns 404 ---"
NOTFOUND_STATUS=$(curl -sS -X GET \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/99999" \
  -H "Authorization: Bearer $SRC_TOKEN" \
  -o /dev/null -w '%{http_code}')
test "$NOTFOUND_STATUS" = "404" \
  || { echo "FAIL: F013-V2 returned $NOTFOUND_STATUS (expected 404)"; exit 1; }
echo "  F013-V2 OK: /api/sources/99999 → 404"
```

### Applicable checks.sh layer

Both assertions are appended inside the existing `sources)` case block, after the existing `UPLOAD-V4` check. The `all)` chain already includes `bash "$0" sources` — no changes to the chain are needed.

### Unit tests (backend layer)

All tests in `apps/api/tests/test_sources_get_detail.py`. Run via `bash verify/checks.sh backend` (`uv run pytest -q`). No live DB, MinIO, or Dagster required.

| Test name | Criterion | What it asserts |
|---|---|---|
| `test_get_source_returns_200_with_all_fields` | V1 | 200 response; all required fields present with correct values. |
| `test_get_source_sha256_matches_upload` | V1 | `sha256` field matches the known hash of the uploaded bytes. |
| `test_get_source_storage_uri_matches_id` | V1 | `storage_uri` is `s3://sources/{id}/original.pdf`. |
| `test_get_source_mime_type_and_kind` | V1 | `mime_type == "application/pdf"`, `kind == "file"`. |
| `test_get_source_collection_id_is_none_when_no_collection` | V1 | Source uploaded without `collection_id` → `collection_id` is `null` in response. |
| `test_get_source_collection_id_populated` | V1 | Source with `collection_id` → field populated correctly. |
| `test_get_source_not_found_returns_404` | V2 | Mock session returns `None` → 404 with `detail="Source not found"`. |
| `test_get_source_other_owners_collection_returns_404` | Owner-scoping | Source in another user's collection → mock returns `None` (join filters it) → 404. |
| `test_get_source_no_token_returns_401` | Auth gate | No `Authorization` header → 401. |

**Mock pattern:** same as `test_sources_upload.py` — override `get_current_user`, `get_session` via `app.dependency_overrides`. The mock session's `execute` returns a `MagicMock` whose `.scalar_one_or_none()` returns either a `Source` ORM stub (happy path) or `None` (404 path).

---

## §6 Out of Scope

The following are explicitly excluded from this sprint:

- **F-014** — list sources in a collection (`GET /api/sources/collections/{id}/sources` or equivalent). Not in scope.
- **F-020** — documents / document variants. Not in scope.
- Any change to `GET /api/sources/collections` (F-010) — not touched.
- Adding `uploaded_by` / `owner_id` to the `source` table — deferred; would require a migration.
- Pagination or filtering on the detail endpoint — single-record lookup only.
- `license`, `source_metadata`, `preferred_extractor` fields in `SourceRead` — optional columns deferred; can be added as a non-breaking schema extension later.

---

## §7 Codegen Requirement

`SourceRead` is a new Pydantic model attached to a new `GET` route. This changes the FastAPI OpenAPI schema output. Invariant #6 applies.

**Required action for implementer:**

```bash
cd apps/api && uv run python -c \
  'import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))' \
  > ../../packages/api-types/openapi.json
git diff packages/api-types/openapi.json
```

The diff WILL be non-empty (new path `/api/sources/{id}` + new component schema `SourceRead`). Commit `packages/api-types/openapi.json` in the **same commit** as `routers/sources.py` and `schemas/sources.py`. If a `Makefile` is present, use `make codegen` instead.

---

## §8 Open Questions

| ID | Question | Recommendation |
|---|---|---|
| OQ-1 | Should uncollected sources be visible to any authenticated user or return 404 for non-uploaders? | Current proposal: world-readable among authenticated users (no `owner_id` on `source` table). Reviewer should flag if strict scoping is preferred — it requires a migration (out of scope here). |
| OQ-2 | Should `SourceRead` include `license`, `source_metadata`, `preferred_extractor`? | Recommendation: omit for now. None are required by F-013 verification criteria. They can be added as a non-breaking extension in a later sprint. |

---

## §9 Reviewer Mode A — APPROVED (2026-05-25)

Verdict: **APPROVED**. All factual claims verified against the codebase (Source model columns, route ordering, storage_uri format `s3://sources/{id}/original.pdf`, F-010 scoping precedent, no root Makefile → inline codegen command correct). OQ-1 accepted (uncollected sources readable by any authenticated user). OQ-2 accepted (omit license/source_metadata/preferred_extractor).

**Mandatory implementation note (must be heeded during build):**
`schemas/sources.py` currently imports only `from pydantic import BaseModel`. The new `SourceRead` class requires `ConfigDict` from pydantic AND `from datetime import datetime` (for the `uploaded_at: datetime | None` field). Add both imports or startup will raise `NameError`/`ImportError`.
