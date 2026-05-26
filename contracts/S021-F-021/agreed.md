# S021-F-021 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-26
**Author:** Leader (Claude)
**Sprint-id:** S021-F-021
**Depends on:** F-019 (passes: true) — MinerU extractor writes `document_variant` rows

---

## §1 What Is Being Built

F-021 adds a single mutating endpoint that designates one `document_variant` row as the
canonical variant for a source:

```
POST /api/sources/{source_id}/documents/{extractor_name}/set-canonical
```

The handler atomically (within one DB transaction):
1. Clears `is_canonical` on whichever variant previously held the flag for this source
   (if any).
2. Sets `is_canonical = TRUE` on the latest (highest `id`) variant whose
   `extractor_name` matches the path parameter.

A successful call returns HTTP 200 with the updated `DocumentVariantRead` for the
target variant. After the call, the `idx_doc_canonical` partial unique index guarantees
exactly one row per `source_id` has `is_canonical = TRUE`.

No new DB tables, no migrations, no LLM calls.

---

## §2 Files Changed

| # | Path | New / Modified | What changes |
|---|---|---|---|
| 1 | `apps/api/dataplat_api/routers/sources.py` | **MODIFIED** | Add `POST /{source_id}/documents/{extractor_name}/set-canonical` handler (§4). Update module docstring to reference F-021. Register it between the existing `GET /{source_id}/documents` handler and the `GET /{id}` catch-all. Add `update` to the `from sqlalchemy import func, or_, select` line → `from sqlalchemy import func, or_, select, update`. |
| 2 | `apps/api/dataplat_api/schemas/sources.py` | **NOT CHANGED** | `DocumentVariantRead` (added in F-020) already covers every field needed in the response. No new schema is added. Module docstring reference to F-021 may be appended as a comment. |
| 3 | `apps/api/tests/test_documents_set_canonical.py` | **NEW** | Unit tests for the new endpoint (§6). |
| 4 | `packages/api-types/openapi.json` | **REGENERATED** | New route changes the OpenAPI spec. |
| 5 | `packages/api-types/` | **REGENERATED** | TypeScript types regenerated from updated `openapi.json` via `make codegen`. Committed in the **same commit** as `openapi.json` (invariant #6). |
| 6 | `verify/checks.sh` | **MODIFIED** | Extend the `documents)` layer with F-021 checks for V1, V2, and V3 (§6.3). |

No migration is needed: `document_variant.is_canonical` and `idx_doc_canonical` were
created in the F-002 migration and are already present.

---

## §3 Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Response shape** | `DocumentVariantRead` of the target variant (HTTP 200) | The updated row is the natural response; reuses the existing schema; richer than `{"status": "ok"}` at zero cost. Verification criterion only requires "returns 200", so this is a strict superset. |
| **Target selection when multiple variants share `extractor_name`** | Pick the variant with the **highest `id`** (latest insert) | Per the prompt: "pick the LATEST (highest id) variant with that extractor_name." One extractor_name typically maps to one row in MVP, so this is a safe tie-breaker. |
| **Atomicity** | CLEAR then SET in a single SQLAlchemy transaction; `commit()` only at the end. Both UPDATEs use `.execution_options(synchronize_session=False)` since `refresh()` provides the authoritative post-commit state. | CLEAR-first avoids ever having two `is_canonical=TRUE` rows simultaneously, which would violate the partial unique index even transiently. Both UPDATEs use `await session.execute(update(...))` — not ORM attribute mutation — for efficiency and to avoid extra SELECT round-trips. `synchronize_session=False` prevents fragile in-memory state manipulation. |
| **Idempotency** | CLEAR then SET on the same row is a safe no-op; returns 200 | The CLEAR sets `is_canonical=FALSE` on the target, then SET restores it to `TRUE`. Net DB state is unchanged. No special early-exit branch needed. |
| **Owner-scoping** | 2-step LEFT JOIN check identical to `GET /{source_id}/documents` (F-020) | Consistent with the router's established pattern; prevents source enumeration leaks. |
| **404 distinction** | "Source not found" vs "Variant not found" | Two distinct 404 messages enable clients and tests to distinguish which check failed without adding a 422 or 400 path. |
| **Route registration order** | After `GET /{source_id}/documents`; before `GET /{id}` catch-all | The new path `/{source_id}/documents/{extractor_name}/set-canonical` has four segments and a literal `set-canonical` suffix; no path overlap with the two-segment `/{source_id}/documents`. Registering before `/{id}` preserves the "catch-all last" invariant stated in the module docstring. |
| **`make codegen` required** | Yes | New route changes `openapi.json`. Invariant #6 mandates committed in the same commit. |

---

## §4 Handler Logic (Pseudocode)

```
POST /{source_id}/documents/{extractor_name}/set-canonical
  Depends: current_user (get_current_user), session (AsyncSession)

  # — Step 1: source accessibility check (same 2-join query as F-020) —
  row = await session.execute(
      SELECT source
      FROM   source
      LEFT JOIN source_collection ON source.collection_id = source_collection.id
      WHERE  source.id = source_id
        AND  (source.collection_id IS NULL
              OR source_collection.owner_id = current_user.id)
  ).scalar_one_or_none()

  if row is None:
      raise HTTPException(404, "Source not found")

  # — Step 2: find target variant (latest for this extractor_name) —
  target = await session.execute(
      SELECT document_variant
      WHERE  source_id = source_id
        AND  extractor_name = extractor_name
      ORDER BY id DESC
      LIMIT 1
  ).scalar_one_or_none()

  if target is None:
      raise HTTPException(404, "Variant not found")

  # — Step 3: atomic clear + set (within the open transaction) —
  await session.execute(
      UPDATE document_variant
      SET    is_canonical = FALSE
      WHERE  source_id = source_id
        AND  is_canonical = TRUE
  )

  await session.execute(
      UPDATE document_variant
      SET    is_canonical = TRUE
      WHERE  id = target.id
  )

  await session.commit()

  # — Step 4: refresh and return updated variant —
  await session.refresh(target)
  return DocumentVariantRead.model_validate(target)   # HTTP 200
```

Key notes on the pseudocode:
- `session.execute(update(...))` uses `sqlalchemy.update()` (core expression), not
  `session.query()` (sync ORM — forbidden by invariant #5).
- No explicit `session.rollback()` is added. If either UPDATE raises (e.g. a DB error),
  the exception propagates and the session context manager handles cleanup — consistent
  with the pattern established in `upload_source`.
- The partial unique index `idx_doc_canonical` enforces the one-canonical constraint at
  the DB level as a safety net; the CLEAR-first ordering ensures no transient violation.

---

## §5 Route Registration Order (Updated)

The six routes in `sources.py` after this sprint, in registration order:

1. `GET  /collections` — paginated collection list (F-010)
2. `POST /collections` — create collection (F-009)
3. `GET  /collections/{id}/sources` — sources in collection (F-014)
4. `POST /upload` — upload PDF source (F-011)
5. `GET  /{source_id}/documents` — list document variants (F-020)
6. **`POST /{source_id}/documents/{extractor_name}/set-canonical`** ← NEW (F-021)
7. `GET  /{id}` — full source detail, catch-all (F-013)

There is no path collision between routes 5 and 6: route 5 is a two-segment `GET` path
ending with the literal `documents`; route 6 is a four-segment `POST` path with two
variable segments bracketing the literal `documents` and ending with the literal
`set-canonical`. FastAPI will not confuse them regardless of ordering, but explicit
ordering before the catch-all preserves the stated invariant.

---

## §6 Verification Plan

### V1 — POST returns 200

**Unit test** (`test_documents_set_canonical.py`):
- Override `get_current_user` → `User` stub (id=1).
- Override `get_session` → `AsyncMock` session with:
  - 1st `execute` (source check): result whose `.scalar_one_or_none()` returns a `Source`
    stub (id=7).
  - 2nd `execute` (target SELECT): result whose `.scalar_one_or_none()` returns a
    `DocumentVariant` stub (id=3, extractor_name='mineru', is_canonical=False).
  - 3rd `execute` (CLEAR UPDATE): result with `rowcount` (not inspected; can be `MagicMock`).
  - 4th `execute` (SET UPDATE): result (not inspected).
  - `session.commit()` is an `AsyncMock` returning `None`.
  - `session.refresh(target)` is an `AsyncMock`; sets `target.is_canonical = True` as a
    side effect.
- `POST /api/sources/7/documents/mineru/set-canonical` → assert HTTP 200.
- Assert response JSON contains `extractor_name == "mineru"` and `is_canonical == true`.

**Integration** (`checks.sh` `documents)` layer extension):
- Reuse `DOC_SRC_ID` and Bearer token from the F-020 flow (variant already extracted).
- `curl -sf -X POST -H "Authorization: Bearer $TOKEN" .../api/sources/$DOC_SRC_ID/documents/mineru/set-canonical`
- Assert HTTP 200.
- Assert response JSON `extractor_name == "mineru"` and `is_canonical == true`.

---

### V2 — Exactly 1 canonical row in DB with extractor_name='mineru'

**Unit test** (`test_documents_set_canonical.py`):
- After the happy-path test (§V1 unit test above), introspect the mock calls:
  - Assert the CLEAR UPDATE was called with `WHERE source_id=7 AND is_canonical=TRUE`.
  - Assert the SET UPDATE was called with `WHERE id=3`.
  - Assert `session.commit()` was called exactly once.
  - (The unit test cannot query a real DB, so it verifies the correct SQL was issued
    rather than the resulting row count.)

**Integration** (`checks.sh` `documents)` layer extension):
- After the V1 curl, run via `docker compose exec`:
  ```bash
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT COUNT(*) FROM document_variant
       WHERE source_id=$DOC_SRC_ID AND is_canonical=TRUE"
  ```
- Assert the count equals `1` (via `grep -q '^1$'`).
- Run:
  ```bash
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT extractor_name FROM document_variant
       WHERE source_id=$DOC_SRC_ID AND is_canonical=TRUE"
  ```
- Assert output matches `mineru` (via `grep -q '^mineru$'`).

---

### V3 — Unique partial index enforces one-canonical constraint

**Unit test** (`test_documents_set_canonical.py`):
- This criterion is a DB schema guarantee, not handler logic; the handler is tested
  implicitly by always issuing CLEAR before SET. Add a dedicated "index existence" test
  at the integration layer only (see below).

**Integration** (`checks.sh` `documents)` layer extension):

*Sub-check V3a — index exists with correct definition:*
- Query `pg_indexes`:
  ```bash
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT indexdef FROM pg_indexes
       WHERE tablename='document_variant' AND indexname='idx_doc_canonical'"
  ```
- Assert the output contains `WHERE is_canonical` (via `grep -q 'is_canonical'`).

*Sub-check V3b — index rejects a second TRUE row (functional):*
- Insert a probe row with `ON CONFLICT ... DO NOTHING` for re-runnability:
  ```bash
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "INSERT INTO document_variant
         (source_id, extractor_name, extractor_version, config_hash, storage_prefix, is_canonical)
       VALUES
         ($DOC_SRC_ID, 'probe', '0.0.1', 'aabbcc', 's3://documents/probe/', FALSE)
       ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING"
  ```
- Attempt to create a second TRUE row — capture output+stderr, assert ERROR:
  ```bash
  V3B_OUT=$(docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -c \
      "UPDATE document_variant SET is_canonical=TRUE
       WHERE extractor_name='probe' AND source_id=$DOC_SRC_ID" \
    2>&1 || true)
  echo "$V3B_OUT" | grep -qi "ERROR" \
    || { echo "FAIL: V3b — unique constraint was NOT violated; $V3B_OUT"; exit 1; }
  echo "  V3b OK: unique constraint rejected second is_canonical=TRUE row"
  ```
  Note: `psql` exits 0 on SQL errors by default; we detect the error via `grep -qi "ERROR"` on captured output.
- Cleanup:
  ```bash
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "DELETE FROM document_variant WHERE extractor_name='probe' AND source_id=$DOC_SRC_ID"
  ```

---

### Additional unit test cases

| Test name | Asserts |
|---|---|
| `test_set_canonical_source_not_found_returns_404` | Source check returns `None` → 404 "Source not found"; no further `execute` calls made |
| `test_set_canonical_variant_not_found_returns_404` | Source found, variant SELECT returns `None` → 404 "Variant not found"; no UPDATE calls made |
| `test_set_canonical_idempotent_when_already_canonical` | Target variant has `is_canonical=True` already; handler still issues CLEAR + SET and returns 200 (no early-exit branch) |
| `test_set_canonical_no_token_returns_401` | No `Authorization` header → 401 (real `oauth2_scheme`; no mock needed) |
| `test_set_canonical_commit_called_once` | Happy path: assert `session.commit()` called exactly once after both UPDATEs |

---

## §7 Invariant Compliance

| Invariant | Status | Notes |
|---|---|---|
| **#1 Lineage mandatory** | N/A | This endpoint mutates `is_canonical`, a selection flag, not a lineage field. No new Commit row is created; `document_variant` rows already have `parents[]` / processor identity recorded at extraction time (F-019). The canonical flag is metadata *about* a variant, not a new provenance event. |
| **#2 Storage separation + CAS** | N/A | No blob writes. Handler only issues UPDATE to Postgres metadata. MinIO is not touched. |
| **#3 Schema frozen post-publish** | N/A | No Silver/Gold repo schema is modified. `is_canonical` is an existing nullable column toggled by this endpoint, not a schema change. |
| **#4 LLM calls via gateway** | N/A | No LLM calls. |
| **#5 Async SQLAlchemy** | **SATISFIED** | Handler is `async def`. Session is `AsyncSession` via `Depends(get_session)`. All DB calls are `await session.execute(...)`. The UPDATE expressions use `sqlalchemy.update()` (core API). No `session.query()`, no sync sessions. |
| **#6 OpenAPI ↔ TS type sync** | **MUST COMPLY** | The new POST route changes `openapi.json`. `make codegen` must run and `packages/api-types/` diff must be committed in the **same commit** as all other changes. CI will reject a mismatch. |

---

## §8 Closed Questions (Resolved)

1. **`session.refresh()` after commit: DECIDED — required, not optional.**
   After `await session.commit()`, SQLAlchemy expires all ORM attributes
   (`expire_on_commit=True` default). On `AsyncSession`, accessing an expired
   attribute without an awaited load raises `MissingGreenlet` at runtime.
   `DocumentVariantRead.model_validate(target)` would trigger this on every field.
   `await session.refresh(target)` is the only correct approach. The extra round-trip
   (one PK lookup on a single small row) is immaterial for a mutation endpoint.

2. **Partial index `WHERE is_canonical` vs `WHERE is_canonical = TRUE`:** PostgreSQL
   treats `WHERE is_canonical` and `WHERE is_canonical = TRUE` identically for a boolean
   column. The existing migration uses `text("is_canonical")`. The V3b integration sub-check
   tests the index functionally regardless of the definition form.

3. **`source_id` not on `DocumentVariantRead`:** The current schema (F-020) omits
   `source_id` from `DocumentVariantRead`. The response is the target variant object,
   which does not include `source_id`. This is acceptable for MVP — the caller already
   knows `source_id` from the URL. If a future sprint needs `source_id` in the response,
   it is a non-breaking additive change to the schema.
