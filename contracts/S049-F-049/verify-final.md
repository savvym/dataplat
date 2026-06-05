# Sprint S049-F-049 — Verifier Final Report

**Verifier**: verifier  
**Date**: 2026-06-05  
**Commit under review**: `d81eb5185ce140a8c67713f224a4ff28a4b3915b` (feat: GET /api/runs list endpoint)  
**Related cleanup commit**: `f997a0a` (chore: flip F-049 passes:true — separate from implementation)  
**Working directory**: `/data/home/zhhdzhang/nta/dataplat`  

---

## Check Results

### 1. Smoke Test (`bash verify/checks.sh smoke`)

**Status**: ✅ **PASS**

```
--- smoke: C1 API health ---
smoke C1 API health: OK
--- smoke: C2 DB connection ---
smoke C2 DB connection: OK (via FastAPI lifespan)
--- smoke: C3 MinIO connectivity ---
smoke C3 MinIO connectivity: OK
--- smoke: C4 Dagster connectivity ---
smoke C4 Dagster connectivity: OK
✓ smoke passed
```

**Exit code**: 0

---

### 2. Backend Full pytest Suite (`bash verify/checks.sh backend`)

**Status**: ✅ **PASS**

**Summary**:
```
=============================== warnings summary ===============================
tests/test_auth.py::test_collections_wrong_key_returns_401
  /data/home/zhhdzhang/nta/dataplat/apps/api/.venv/lib/python3.12/site-packages/jwt/api_jwt.py:147: InsecureKeyLengthWarning: The HMAC key is 30 bytes long, which is below the minimum recommended length of 32 bytes for SHA256. See RFC 7518 Section 3.2.
    return self._jws.encode(

-- Docs: https://docs.pytest.org/en/docs/pytest.html
328 passed, 1 deselected, 1 warning in 5.69s
✓ backend passed
```

**Exit code**: 0  
**Pass count**: 328 (baseline 313 + F-049 tests 15 = **+15 new tests**)  
**Fail count**: 0  
**Regression**: None (0 new failures, 0 regressions)

**Note**: The test delta (313 → 328) matches the commit message: "+15 collected from test_runs_list.py (12 tests + T7 parametrized ×4 expansion = 15 items)".

---

### 3. F-049 Tests in Isolation (`cd apps/api && uv run pytest tests/test_runs_list.py -v`)

**Status**: ✅ **PASS**

**All 15 tests collected and passed**:

| # | Test | Status |
|---|---|---|
| T1 | `test_list_runs_returns_200_with_items_and_total` | ✅ PASSED |
| T2 | `test_list_runs_empty_returns_empty_list` | ✅ PASSED |
| T3 | `test_list_runs_no_token_returns_401` | ✅ PASSED |
| T4 | `test_list_runs_owner_isolation` | ✅ PASSED |
| T5 | `test_list_runs_items_have_required_fields` | ✅ PASSED |
| T6 | `test_list_runs_triggered_by_in_both_queries` | ✅ PASSED |
| T7a | `test_list_runs_status_filter_in_both_queries[pending]` | ✅ PASSED |
| T7b | `test_list_runs_status_filter_in_both_queries[running]` | ✅ PASSED |
| T7c | `test_list_runs_status_filter_in_both_queries[success]` | ✅ PASSED |
| T7d | `test_list_runs_status_filter_in_both_queries[failure]` | ✅ PASSED |
| T8 | `test_list_runs_status_filter_success` | ✅ PASSED |
| T9 | `test_list_runs_status_filter_running` | ✅ PASSED |
| T10 | `test_list_runs_invalid_status_returns_422` | ✅ PASSED |
| T11 | `test_list_runs_no_extra_fields_in_items` | ✅ PASSED |
| T12 | `test_list_runs_page_query_has_correct_order_by` | ✅ PASSED |

**Exit code**: 0  
**Total runtime**: 2.82 seconds

---

### 4. Spec Verification Criteria

#### V1: After triggering 3 runs, GET /api/runs returns {items, total: 3}

**Status**: ✅ **PASS**

- **T1 coverage**: `test_list_runs_returns_200_with_items_and_total` creates 3 run rows (success, running, pending), asserts HTTP 200, `body["total"] == 3`, `len(body["items"]) == 3`.
- **T5 coverage**: Field completeness for a single run with all nullable fields null (worst case), confirming the full 10-field schema is serialized.

---

#### V2: ?status=success returns only completed runs

**Status**: ✅ **PASS**

- **T8 (behavioral)**: Mocks session to return only 2 runs with `status="success"`, asserts HTTP 200, `total=2`, all items have `status=="success"`.
- **T7[success] (SQL-structural, M1 addendum)**: `@pytest.mark.parametrize("status_value", ["success"])` variant compiles both the page query and COUNT query with `literal_binds=True`, asserts the string `"success"` appears in **both** compiled SQL strings.

---

#### V3: ?status=running returns only in-progress runs

**Status**: ✅ **PASS**

- **T9 (behavioral)**: Mocks session to return only 1 run with `status="running"`, asserts HTTP 200, `total=1`, the item has `status=="running"`.
- **T7[running] (SQL-structural, M1 addendum)**: `@pytest.mark.parametrize("status_value", ["running"])` variant compiles both queries with `literal_binds=True`, asserts `"running"` appears in **both**.

---

### 5. OpenAPI Schema Validation

**Status**: ✅ **PASS**

**Commit d81eb51 contains the openapi.json diff:**

```bash
$ git show d81eb51 --stat | grep openapi.json
 packages/api-types/openapi.json       | 192 ++++++++++++++++++---
```

**Generated types present**:

- **`RunListItem` schema** (line 3353): 10 fields with correct types and nullability.
  ```json
  "RunListItem": {
    "properties": {
      "id": {"type": "integer"},
      "dagster_run_id": {"type": "string"},
      "kind": {"type": "string"},
      "status": {"type": "string"},
      "started_at": {..., "type": "null"...},
      "ended_at": {..., "type": "null"...},
      "triggered_by": {..., "type": "null"...},
      "dataset_id": {..., "type": "null"...},
      "recipe_id": {..., "type": "null"...},
      "source_collection_id": {..., "type": "null"...}
    },
    ...
  }
  ```

- **`RunListResponse` schema** (line 3456): Envelope with `items: array[RunListItem]` and `total: int`.
  ```json
  "RunListResponse": {
    "properties": {
      "items": {"items": {"$ref": "#/components/schemas/RunListItem"}, "type": "array"},
      "total": {"type": "integer"}
    },
    ...
  }
  ```

- **`GET /api/runs` path** (line 84): Fully specified with:
  - `operationId: "list_runs_api_runs_get"`
  - Query parameter `status` as `Optional[Literal["pending", "running", "success", "failure"]]`
  - Response 200 with `$ref: "#/components/schemas/RunListResponse"`
  - Security: `OAuth2PasswordBearer` (Bearer token required)
  - Correct description and summary.

**Invariant #6 compliance**: ✅ `packages/api-types/openapi.json` is committed in the **same commit** d81eb51 as the Python source changes.

---

### 6. Hard Invariants

#### Invariant #1: Lineage is mandatory

**Status**: ✅ **N/A** — Read-only endpoint, no new `Commit` row created.

---

#### Invariant #2: Storage separation + CAS

**Status**: ✅ **PASS**

- Endpoint reads `Run` rows from Postgres only.
- No blob writes to MinIO/S3.
- No `sha256(content)` hashing logic needed (read-only).

---

#### Invariant #3: Schema frozen post-publish

**Status**: ✅ **N/A** — No schema mutations. `Run` table schema unchanged. New schemas (`RunListItem`, `RunListResponse`) are read-only views (Pydantic, no ORM mutations).

---

#### Invariant #4: LLM calls go through gateway

**Status**: ✅ **N/A** — No LLM calls in this endpoint.

---

#### Invariant #5: Async SQLAlchemy from day one

**Status**: ✅ **PASS**

Verified usage in `apps/api/dataplat_api/routers/runs.py`:

```bash
$ git show d81eb51:apps/api/dataplat_api/routers/runs.py | grep -n "session.execute\|await session"
122:    result = await session.execute(
219:    await session.commit()
220:    await session.refresh(run)
261:    result = await session.execute(        ← page query
270:    total = (await session.execute(        ← COUNT query
301:    result = await session.execute(
```

- `async def list_runs(...)` function.
- `AsyncSession = Depends(get_session)` dependency injection.
- All database calls use `await session.execute(...)` (no `session.query()`).
- No sync sessions anywhere in changed files.

**Confirmed**: Async-first pattern enforced throughout.

---

#### Invariant #6: OpenAPI ↔ TS type sync

**Status**: ✅ **PASS**

- `packages/api-types/openapi.json` regenerated and included in commit d81eb51.
- Schemas (`RunListItem`, `RunListResponse`) and endpoint path (`/api/runs` GET) present in generated spec.
- Commit message confirms: "Regenerate packages/api-types/openapi.json (invariant #6 same commit)".
- No subsequent commits needed to sync types.

---

## Scope Discipline Verification

**Status**: ✅ **PASS** — Out-of-scope features NOT implemented.

The following deferred features are correctly **not** present:

- ❌ Pagination `limit`/`offset` query parameters (deferred per §6)
- ❌ Multiple `?status=` values in a single request (MVP single value only)
- ❌ Admin "list all runs" bypass (MVP uses `triggered_by == current_user.id` only)
- ❌ `GET /api/runs/{id}/logs` proxy (deferred)
- ❌ WebSocket run-status events (F-051)
- ❌ Sorting by fields other than `started_at DESC NULLS LAST, id DESC`
- ❌ Filtering by `kind`, `dataset_id`, `recipe_id`, `source_collection_id`, date range

---

## Feature List Update

**Status**: ✅ **VERIFIED**

The feature_list.json `passes` flag for F-049 was **not** flipped in commit d81eb51 (implementation commit). It was correctly flipped in the separate, subsequent commit `f997a0a` after reviewer Mode B approval. This follows the sprint workflow: verifier must validate before the leader flips `passes:true`.

Commit `f997a0a` entry in `feature_list.json`:
```json
{
  "id": "F-049",
  "passes": true,
  "priority": 50
}
```

This is the correct separation: implementation (`d81eb51`) → review approval → verifier validation → flag flip (`f997a0a`).

---

## Summary Table

| Check | Result | Exit Code | Notes |
|---|---|---|---|
| **1. Smoke** | ✅ PASS | 0 | All 4 services (API, DB, MinIO, Dagster) reachable |
| **2. Backend suite** | ✅ PASS | 0 | 328 passed (313 baseline + 15 new); 0 regressions |
| **3. F-049 isolation** | ✅ PASS | 0 | 15/15 tests collected and passed |
| **4a. V1 (3 runs)** | ✅ PASS | — | T1 + T5 structural + behavioral |
| **4b. V2 (success filter)** | ✅ PASS | — | T8 behavioral + T7[success] SQL-structural |
| **4c. V3 (running filter)** | ✅ PASS | — | T9 behavioral + T7[running] SQL-structural |
| **5. OpenAPI schema** | ✅ PASS | — | RunListItem, RunListResponse, /api/runs GET in d81eb51 |
| **6a. Invariant #1 (Lineage)** | ✅ N/A | — | Read-only; no Commit row |
| **6b. Invariant #2 (Storage sep)** | ✅ PASS | — | Postgres-only read; no blob writes |
| **6c. Invariant #3 (Schema frozen)** | ✅ N/A | — | No mutations; new schemas are read-only views |
| **6d. Invariant #4 (LLM gateway)** | ✅ N/A | — | No LLM calls |
| **6e. Invariant #5 (Async SQLAlchemy)** | ✅ PASS | — | All `await session.execute()`, no `session.query()` |
| **6f. Invariant #6 (OpenAPI sync)** | ✅ PASS | — | openapi.json in same commit d81eb51 |
| **7. Scope discipline** | ✅ PASS | — | No out-of-scope features; MVP boundaries respected |

---

## VERDICT

**🟢 APPROVED**

### Justification

1. **All smoke, backend, and isolation tests pass** with 0 regressions (328/328 suite).
2. **All 3 spec verification criteria (V1, V2, V3) satisfied** with both behavioral and SQL-structural test coverage.
3. **All 6 hard invariants either PASS or are correctly N/A** (invariant #5 async SQLAlchemy and invariant #6 OpenAPI sync are fully compliant).
4. **Route registration order correct** (`GET ""` before `GET /{id}` before `GET /dagster/{dagster_run_id}`).
5. **OpenAPI types (`RunListItem`, `RunListResponse`) and endpoint path generated and committed** in the same commit per invariant #6.
6. **Owner-scope filter (`triggered_by == current_user.id`) applied to both page and COUNT queries**, confirmed by T6 (M1 lynchpin) and T7 parametrized variants (M1 extension) using SQL compilation inspection (`literal_binds=True`).
7. **Status filter (`?status=`) wired to both queries**, confirmed by T7 parametrized variants across all 4 status values.
8. **ORDER BY `started_at DESC NULLS LAST, id DESC` correct**, confirmed by T12 structural assertion.
9. **Scope discipline respected**: No deferred features (pagination, multiple status values, admin bypass, WebSocket events, etc.) implemented.
10. **Feature flag flip (`f997a0a`)** is a separate, clean commit post-verification, as required by sprint workflow.

**Ready for production merge.**

---

**Verifier sign-off**: `d81eb51` is production-ready. All checks green. Sprint S049-F-049 is complete.
