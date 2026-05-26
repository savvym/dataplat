# S020-F-020 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-26
**Author:** Leader (Claude)
**Sprint-id:** S020-F-020
**Depends on:** F-019 (passes: true) — MinerU extractor writes `document_variant` rows

---

## §1 What Is Being Built

F-020 exposes `document_variant` rows over HTTP by adding a single read-only endpoint:

```
GET /api/sources/{source_id}/documents
```

The handler returns a flat JSON array of all `document_variant` rows for the given
source. After a successful F-019 extraction there will be exactly one row; the empty
case (`[]`) is valid when no extraction has run yet. A non-existent or inaccessible
source returns 404.

No mutations, no pagination, no new DB tables, no migrations, and no LLM calls are
introduced. This sprint is purely API surface (new route + new response schema +
regenerated OpenAPI/TS types).

---

## §2 Files Changed

| # | Path | New / Modified | What changes |
|---|---|---|---|
| 1 | `apps/api/dataplat_api/schemas/sources.py` | **MODIFIED** | Add `DocumentVariantRead` Pydantic schema (see §3). Update module docstring to reference F-020. |
| 2 | `apps/api/dataplat_api/routers/sources.py` | **MODIFIED** | Add `GET /{source_id}/documents` handler (see §4). Register it between the `/upload` handler and the existing `/{id}` catch-all. Update module docstring to reference F-020. |
| 3 | `apps/api/tests/test_documents_list.py` | **NEW** | Unit tests for the new endpoint (see §6). |
| 4 | `apps/api/openapi.json` | **REGENERATED** | Updated by `make codegen` after the schema and route changes. |
| 5 | `packages/api-types/` | **REGENERATED** | TypeScript types regenerated from the updated `openapi.json` by `make codegen`. Committed in the **same commit** as `openapi.json` per invariant #6. |

No other files change. No migration is needed: `document_variant` was created by the
F-002 migration and is already in place.

---

## §3 `DocumentVariantRead` Schema

Added to `apps/api/dataplat_api/schemas/sources.py`.

Fields (all drawn directly from `DocumentVariant` model columns):

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | PK; useful for stable client-side keying |
| `extractor_name` | `str` | NOT NULL in DB |
| `extractor_version` | `str` | NOT NULL in DB |
| `config_hash` | `str` | NOT NULL in DB; SHA-256 of operator config JSON |
| `storage_prefix` | `str` | NOT NULL in DB; e.g. `s3://documents/7/extract_mineru/` |
| `page_count` | `int \| None` | Nullable in DB |
| `image_count` | `int \| None` | Nullable in DB |
| `is_canonical` | `bool \| None` | Nullable in DB (server_default false) |
| `materialized_at` | `datetime \| None` | Nullable in DB (server_default now()) |
| `dagster_run_id` | `str \| None` | Nullable in DB |

`model_config = ConfigDict(from_attributes=True)` is required so SQLAlchemy ORM
objects map directly to this schema (same pattern as `SourceRead`).

The minimum required fields per the verification criteria (extractor_name,
extractor_version, storage_prefix, is_canonical, materialized_at) are a strict subset
of the above. The additional fields (id, config_hash, page_count, image_count,
dagster_run_id) are included because they are already in the DB row, cost nothing to
expose, and are needed by downstream consumers (e.g. the SDK, the UI).

---

## §4 Handler Design — `GET /{source_id}/documents`

### 4.1 Signature

```python
@router.get(
    "/{source_id}/documents",
    response_model=list[DocumentVariantRead],
    summary="List Document Variants",
)
async def list_document_variants(
    source_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVariantRead]:
```

Return type is `list[DocumentVariantRead]` — a plain JSON array, not a paginated
envelope. The number of variants per source is small (typically 1–3), so pagination
adds unnecessary complexity in the MVP.

### 4.2 Owner-Scoping Logic (2-step, same pattern as `GET /{id}`)

**Step 1 — source existence and accessibility check:**

```sql
SELECT source.id
FROM   source
LEFT JOIN source_collection ON source.collection_id = source_collection.id
WHERE  source.id = :source_id
AND    (
           source.collection_id IS NULL          -- unclaimed; visible to all auth'd users
        OR source_collection.owner_id = :user_id -- caller owns the collection
       )
```

If this returns no row → raise `HTTPException(status_code=404)`. This handles both
"source does not exist" and "source belongs to another user's collection" with the same
404, preventing enumeration leaks. This is identical in spirit to the scoping logic in
`GET /{id}`.

**Step 2 — fetch variants:**

```sql
SELECT * FROM document_variant
WHERE  source_id = :source_id
ORDER  BY id ASC
```

Return the rows (possibly an empty list) serialized as `list[DocumentVariantRead]`.

### 4.3 Route Registration Order

The new route is inserted **after** the `POST /upload` handler and **before** the
`GET /{id}` catch-all (currently the last route in the file, line 333).

`/{source_id}/documents` has **two** path segments while `/{id}` has **one**, so
there is no runtime ambiguity; FastAPI would never match `/7/documents` against `/{id}`
regardless of ordering. The explicit ordering before `/{id}` preserves the router's
stated invariant ("catch-all registered last") and keeps the file readable.

The updated registration order in `sources.py`:

1. `GET  /collections` — paginated list of caller's collections (F-010)
2. `POST /collections` — create collection (F-009)
3. `GET  /collections/{id}/sources` — paginated sources in collection (F-014)
4. `POST /upload` — upload PDF source (F-011)
5. **`GET  /{source_id}/documents`** ← new (F-020)
6. `GET  /{id}` — full source detail, catch-all (F-013)

---

## §5 Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Response shape** | Flat `list[DocumentVariantRead]` (not paginated) | Variants per source are small (1–3 in practice). Paginated envelope adds latency and client complexity with no benefit. Verification criterion explicitly says "returns array with 1 item." |
| **404 vs 200+empty on missing source** | 404 | A missing or inaccessible source is an error condition; the sub-resource cannot be enumerated. An empty array (`[]`) is only returned when the source *exists* but has no variants yet. |
| **Path parameter name** | `source_id` (not `id`) | Avoids any potential shadowing of the existing `/{id}` handler's parameter; also more semantically clear in the handler body. FastAPI path params are local to their own route so the name doesn't affect dispatch, but using a distinct name is a good practice. |
| **`DocumentVariantRead` location** | `schemas/sources.py` | `document_variant` is a child entity of `source`; grouping it in the sources schema file mirrors how the router nests it under `/sources/{id}/documents`. A separate `schemas/documents.py` would be premature for a single schema. |
| **`from_attributes=True`** | Yes, via `ConfigDict` | Needed for SQLAlchemy ORM → Pydantic model serialization. Consistent with `SourceRead`. |
| **`ORDER BY id ASC`** | Yes | Stable, deterministic ordering for clients. `id` is a monotonically increasing bigint. No use-case for reverse order in the MVP. |
| **`make codegen` required** | Yes | New route + new response schema changes the OpenAPI spec. Invariant #6 mandates the `packages/api-types/` diff be committed in the same commit. |

---

## §6 Verification Plan

### V1 — After extraction, GET returns array with 1 item containing required fields

**Unit test** (`test_documents_list.py`):
- Override `get_current_user` → `User` stub (id=1).
- Override `get_session` → `AsyncMock` session with two `execute` side effects:
  - 1st call (ownership check): `MagicMock` result whose `.scalar_one_or_none()` returns a `Source` stub with `id=7`.
  - 2nd call (variant fetch): `MagicMock` result whose `.scalars().all()` returns a list containing one `DocumentVariant` stub with all required fields populated.
- `GET /api/sources/7/documents` → assert HTTP 200.
- Assert response JSON is a list of length 1.
- Assert the single item contains keys: `extractor_name`, `extractor_version`, `storage_prefix`, `is_canonical`, `materialized_at`.
- Assert `extractor_name == "mineru"` and `extractor_version == "0.1.0"`.

**Integration** (`checks.sh documents` layer):
- Reuse the source id from the F-019 E2E flow (or run a fresh upload + extraction).
- `curl -sf -H "Authorization: Bearer $TOKEN" http://localhost:$FASTAPI_HOST_PORT/api/sources/$SRC_ID/documents`
- Assert HTTP 200.
- Use `jq` to assert: array length ≥ 1, `.[0].extractor_name == "mineru"`, `.[0].extractor_version == "0.1.0"`, `.[0].storage_prefix` is non-empty, `.[0].is_canonical` is a boolean, `.[0].materialized_at` is non-null.

### V2 — Non-existent source returns 404

**Unit test** (`test_documents_list.py`):
- Override `get_current_user` → `User` stub.
- Override `get_session` → `AsyncMock` session with one `execute` side effect:
  - 1st call (ownership check): `MagicMock` result whose `.scalar_one_or_none()` returns `None` (source not found or not accessible).
- `GET /api/sources/99999/documents` → assert HTTP 404.
- Assert the second `session.execute` is never called (short-circuit).

**Integration** (`checks.sh documents` layer):
- `curl -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" http://localhost:$FASTAPI_HOST_PORT/api/sources/99999/documents`
- Assert status code is `404`.

### Additional unit test cases

| Test name | What it asserts |
|---|---|
| `test_list_documents_returns_empty_list_when_no_variants` | Source found (step 1 returns a stub), step 2 returns `[]` → 200 with `[]` body |
| `test_list_documents_no_token_returns_401` | No `Authorization` header → 401 (real `oauth2_scheme` raises it; no mock needed) |
| `test_list_documents_other_owners_source_returns_404` | Ownership check returns `None` for another user's source → 404 |
| `test_list_documents_item_fields_match_model` | Full field presence check including `id`, `config_hash`, `page_count`, `image_count`, `dagster_run_id` |

### `checks.sh` integration — new `documents)` layer

A new `documents)` case is added to `verify/checks.sh` and appended to the `all)` chain
after the `extract` layer. It:

1. Mints a Bearer token (`admin@example.com / testpassword123`).
2. Uploads a minimal PDF → captures `SRC_ID`.
3. Triggers a MinerU extraction backfill for `SRC_ID` and polls to `COMPLETED_SUCCESS`
   (reuses the same logic as the `extract` layer, condensed).
4. Calls `GET /api/sources/$SRC_ID/documents` and asserts V1 (200 + correct fields).
5. Calls `GET /api/sources/99999/documents` and asserts V2 (404).

Alternatively, if `SRC_ID` from a previously-run `extract` layer is available in the
environment, step 2–3 can be skipped. The layer is self-contained by default.

---

## §7 Invariant Compliance

| Invariant | Status | Notes |
|---|---|---|
| **#1 Lineage mandatory** | N/A | Read-only endpoint; no data is written. |
| **#2 Storage separation + CAS** | N/A | Read-only endpoint; no storage writes. Handler reads `document_variant` metadata from Postgres only — no MinIO access. |
| **#3 Schema frozen post-publish** | N/A | No schema publish occurs. `DocumentVariantRead` is a response schema, not a repo schema. |
| **#4 LLM calls via gateway** | N/A | No LLM calls. |
| **#5 Async SQLAlchemy from day one** | **SATISFIED** | Handler is `async def`. Session is `AsyncSession` via `Depends(get_session)`. Both `execute` calls are `await session.execute(...)`. No `session.query()`, no sync sessions. Consistent with all existing handlers in `sources.py`. |
| **#6 OpenAPI ↔ TS type sync** | **MUST COMPLY** | The new route and `DocumentVariantRead` schema change `openapi.json`. `make codegen` must run and the `packages/api-types/` diff must be committed in the **same commit** as all other changes. CI will reject a mismatch. |

---

## §8 Open Questions

1. **`source_id` path parameter type collision with `/{id}`:** FastAPI uses the
   parameter name in the generated OpenAPI spec, not just the path. Both `/{id}` and
   `/{source_id}/documents` will appear in `openapi.json` without conflict because they
   are on different paths. No action needed; confirmed by FastAPI routing rules.

2. **Ordering guarantee:** `ORDER BY id ASC` is chosen for determinism. If a future
   sprint wants `ORDER BY materialized_at DESC` (most recent first), that is a
   non-breaking change (no schema change required). Acceptable to defer.

3. **inaccessible-vs-no-variants ambiguity:** The handler returns `404` for inaccessible
   sources and `200 + []` for accessible sources with no variants. A caller cannot
   distinguish "source has no variants" from "source does not exist" if they receive
   `404`, but that is the intended behavior — we do not leak existence. A `200 + []` is
   the correct signal that extraction has not run yet.
