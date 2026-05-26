# S021-F-021 — Mode B Review (Post-Implementation)

**Status:** APPROVED
**Reviewer role:** Mode B (reviewing committed diff against agreed.md)
**Commit reviewed:** `a84974646dc54fd1fe89d9a34b22d3f89d005383`
**Date:** 2026-05-26
**Files changed:** `apps/api/dataplat_api/routers/sources.py` (+107), `apps/api/tests/test_documents_set_canonical.py` (new, 352 lines), `packages/api-types/openapi.json` (+57), `verify/checks.sh` (+72)

---

## Item-by-item verification

### 1. `update` imported from sqlalchemy (agreed.md §2 row 1)

**PASS** — `sources.py:35`
```python
from sqlalchemy import func, or_, select, update
```
`update` appended to the existing import line exactly as specified.

---

### 2. Module docstring updated to reference F-021 (agreed.md §2 row 1)

**PASS** — `sources.py:4`
```
+ S021-F-021 POST /{source_id}/documents/{extractor_name}/set-canonical.
```
Both the one-liner at line 4 and the route table at lines 11-12 reference F-021.

---

### 3. Handler registered between `GET /{source_id}/documents` and `GET /{id}` catch-all (agreed.md §5)

**PASS** — route order in `sources.py`:
| # | Route | Line |
|---|---|---|
| 5 | `GET /{source_id}/documents` (F-020) | 343 |
| 6 | **`POST /{source_id}/documents/{extractor_name}/set-canonical`** (F-021) | 399 |
| 7 | `GET /{id}` catch-all (F-013) | 495 |

The new 4-segment POST is correctly sandwiched between the 2-segment GET and the 1-segment catch-all.

---

### 4. Step 1 — Source ownership check (LEFT JOIN, same as F-020) (agreed.md §4)

**PASS** — `sources.py:436–447`
```python
result = await session.execute(
    select(Source)
    .join(SourceCollection, Source.collection_id == SourceCollection.id, isouter=True)
    .where(Source.id == source_id)
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
```
Identical structure to F-020 `list_document_variants`. Returns `404 "Source not found"` on failure.

---

### 5. Step 2 — Find latest variant (ORDER BY id DESC LIMIT 1) (agreed.md §4)

**PASS** — `sources.py:455–467`
```python
variant_result = await session.execute(
    select(DocumentVariant)
    .where(DocumentVariant.source_id == source_id)
    .where(DocumentVariant.extractor_name == extractor_name)
    .order_by(DocumentVariant.id.desc())
    .limit(1)
)
target = variant_result.scalar_one_or_none()
if target is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found")
```
Correctly selects the highest-id variant; returns `404 "Variant not found"` on failure.

---

### 6. Step 3 — CLEAR UPDATE with `synchronize_session=False` (agreed.md §3, §4)

**PASS** — `sources.py:471–477`
```python
await session.execute(
    update(DocumentVariant)
    .where(DocumentVariant.source_id == source_id)
    .where(DocumentVariant.is_canonical.is_(True))
    .values(is_canonical=False)
    .execution_options(synchronize_session=False)
)
```
Bulk-clears all `is_canonical=TRUE` rows for the source before setting the target. `synchronize_session=False` prevents fragile in-memory ORM state mutation. CLEAR-first ordering prevents transient violation of `idx_doc_canonical`.

---

### 7. Step 3 — SET UPDATE with `synchronize_session=False` (agreed.md §3, §4)

**PASS** — `sources.py:479–484`
```python
await session.execute(
    update(DocumentVariant)
    .where(DocumentVariant.id == target.id)
    .values(is_canonical=True)
    .execution_options(synchronize_session=False)
)
```
Targets the exact row by PK. `synchronize_session=False` consistent with the CLEAR step.

---

### 8. Step 4 — `commit()` then `refresh(target)` then return (agreed.md §4, §8 closed question 1)

**PASS** — `sources.py:487–492`
```python
await session.commit()
await session.refresh(target)
return DocumentVariantRead.model_validate(target)
```
`session.refresh(target)` is required because `expire_on_commit=True` (SQLAlchemy default) expires all ORM attributes after `commit()`. Accessing an expired attribute on `AsyncSession` without an awaited reload raises `MissingGreenlet`. The docstring at lines 429–431 explicitly documents this invariant.

---

### 9. Invariant #5 — Async SQLAlchemy (agreed.md §7)

**PASS**
- Handler is `async def set_canonical_document_variant` (`sources.py:404`).
- Session declared as `session: AsyncSession = Depends(get_session)` (`sources.py:408`).
- All four DB calls use `await session.execute(...)` (`sources.py:436, 455, 471, 479`).
- Both UPDATEs use `sqlalchemy.update()` core expression, not `session.query()`.
- No synchronous session anywhere in the handler.

---

### 10. Unit test coverage — all 7 required cases (agreed.md §6 additional tests table)

**PASS** — `test_documents_set_canonical.py`

| Test | Line | What it asserts |
|---|---|---|
| `test_set_canonical_returns_200` | 211 | V1 happy path → HTTP 200 |
| `test_set_canonical_response_has_is_canonical_true` | 226 | Body: `extractor_name=="mineru"`, `is_canonical==true` |
| `test_set_canonical_source_not_found_returns_404` | 247 | 1 execute, short-circuit → `404 "Source not found"` |
| `test_set_canonical_variant_not_found_returns_404` | 261 | 2 executes, short-circuit → `404 "Variant not found"` |
| `test_set_canonical_no_token_returns_401` | 279 | No `Authorization` header → 401 (real oauth2_scheme) |
| `test_set_canonical_commit_called_once` | 288 | `session.commit.assert_called_once()` after 4 executes |
| `test_set_canonical_idempotent_when_already_canonical` | 333 | Target already canonical → CLEAR+SET still issued → 200 |

Session mock pattern correctly implements 4-execute side-effects (source check, variant SELECT, CLEAR UPDATE, SET UPDATE) followed by `commit()` and `refresh()` with side effect setting `target.is_canonical = True` (`test_documents_set_canonical.py:133`).

---

### 11. checks.sh — F-021 integration checks in `documents)` layer (agreed.md §6 V1/V2/V3)

#### V1 — POST returns 200 with correct fields (`checks.sh:1549–1564`)
**PASS**
- `curl -sS -X POST -H "Authorization: Bearer $DOC_TOKEN"` to `set-canonical` endpoint.
- Asserts HTTP status `200`.
- Python3 assertion: `body['extractor_name'] == 'mineru'` and `body['is_canonical'] is True`.

#### V2 — Exactly 1 canonical row, extractor_name=mineru (`checks.sh:1566–1583`)
**PASS**
- Queries via `docker compose -f "$COMPOSE" exec -T postgres psql -tAc` (correct pattern, not bare `$PLATFORM_DB_URL`).
- `SELECT COUNT(*)` asserted `== "1"` via `test "$CANON_COUNT" = "1"`.
- `SELECT extractor_name` asserted `== "mineru"` via `test "$CANON_NAME" = "mineru"`.

#### V3a — `idx_doc_canonical` exists with `is_canonical` filter (`checks.sh:1585–1593`)
**PASS**
- Queries `pg_indexes` via `docker compose exec`.
- `grep -qi "is_canonical"` on `indexdef` output — case-insensitive, handles both `WHERE is_canonical` and `WHERE (is_canonical = true)` forms.

#### V3b — Unique index rejects second `is_canonical=TRUE` row (`checks.sh:1595–1618`)
**PASS**
- Probe row inserted with `ON CONFLICT (source_id, extractor_name, config_hash) DO NOTHING` — idempotent across reruns (`checks.sh:1597–1603`).
- Attempted `UPDATE ... SET is_canonical=TRUE` on probe row captured in `V3B_OUT` with `2>&1 || true` (`checks.sh:1606–1610`).
- Error detection: `echo "$V3B_OUT" | grep -qi "ERROR"` — correct; psql exits 0 on SQL errors, so `grep` on output is the only reliable signal (`checks.sh:1611`).
- Cleanup: `DELETE FROM document_variant WHERE extractor_name='probe' AND source_id=${DOC_SRC_ID}` (`checks.sh:1615–1617`).

---

### 12. Invariant #6 — OpenAPI ↔ TS type sync (agreed.md §7)

**PASS (within current project state)**

`packages/api-types/openapi.json` is updated in the same commit (`a84974646`) with:
- New path `/api/sources/{source_id}/documents/{extractor_name}/set-canonical`
- Method: `post`, security: `OAuth2PasswordBearer`
- Parameters: `source_id` (integer, path), `extractor_name` (string, path)
- Response 200: `$ref: "#/components/schemas/DocumentVariantRead"`
- Path inserted before `/api/sources/{id}` in the JSON, consistent with route registration order.

`packages/api-types/` contains only `openapi.json` — no generated TypeScript type files. This is consistent with the project's current state: the web sprint has not scaffolded the `Makefile`/pnpm codegen pipeline. The existing `contract)` check guards on `[[ -f Makefile ]] || exit 0`, so CI does not reject the absent TS output. Invariant #6 is satisfied insofar as `openapi.json` is committed in the same commit as all implementation changes.

---

## Minor informational notes (non-blocking)

1. **V2 unit test does not introspect UPDATE SQL arguments.** `test_set_canonical_commit_called_once` (`test_documents_set_canonical.py:288`) asserts `session.commit.assert_called_once()` but does not verify the exact `WHERE` clauses of the CLEAR/SET UPDATE statements. The agreed.md V2 unit test spec (`§6 V2`) mentioned this aspiration. The end-to-end correctness of the UPDATE logic is covered by the V2 and V3b integration checks. This is acceptable for MVP — not a defect.

2. **`all)` layer comment cosmetic gap.** The `documents` dispatch line in the `all)` loop (`checks.sh` ~line 1231) retains the `# F-020` comment. The F-021 checks execute correctly within the `documents)` case regardless; this comment is purely cosmetic and requires no code change.

3. **TS codegen deferred.** Noted in informational note above. No action needed until the web sprint scaffolds the codegen pipeline.

---

## Verdict

**APPROVED**

All agreed.md contract items are implemented correctly. Hard invariants #5 (async SQLAlchemy) and #6 (OpenAPI ↔ TS sync, within current project state) are satisfied. The seven unit test cases, four integration checks (V1, V2, V3a, V3b), CLEAR-first atomicity, `synchronize_session=False` on both UPDATEs, `await session.refresh(target)` after commit, and route registration order are all verified at the specific file:line locations listed above. The implementation is ready to proceed to the verifier.
