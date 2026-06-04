# Sprint S048-F-048 — Mode B Review (Post-Implementation)

**Commit reviewed**: `994c0f0`
**Reviewer**: reviewer (Mode B)
**Date**: 2026-06-04
**Contract**: `contracts/S048-F-048/agreed.md` (Rev 2)

---

## Blocker Checklist

### B1 — Single-query owner-scope filter ✅ GREEN

`routers/runs.py` lines 244–245:
```python
result = await session.execute(
    select(Run).where(Run.id == id).where(Run.triggered_by == current_user.id)
)
```
Both `Run.id == id` AND `Run.triggered_by == current_user.id` are combined in a **single** SELECT, chained via `.where()` calls on the same `select()` statement. No select-then-check pattern. Matches contract §2 owner-scope rule exactly.

---

### B2 — `scalar_one_or_none()` + 404 on None ✅ GREEN

`routers/runs.py` lines 247–249:
```python
row = result.scalar_one_or_none()
if row is None:
    raise HTTPException(status_code=404, detail="Run not found")
```
`scalar_one_or_none()` called on the synchronous result proxy (correct — `await` is on `session.execute()`, not on the result method). `None` raises `HTTPException(status_code=404, detail="Run not found")`. Contract-exact.

---

### B3 — Dagster-proxy path RENAMED in the actual route decorator ✅ GREEN

Diff confirms the decorator changed from:
```python
@runs_router.get("/{run_id}", ...)
```
to:
```python
@runs_router.get("/dagster/{dagster_run_id}", ...)
```
The function parameter also renamed from `run_id: str` to `dagster_run_id: str`, and the internal call updated to `gateway.get_run_status(dagster_run_id)`. This is a real path change — not a docstring-only change. The old `GET /api/runs/{run_id}` path is absent from `openapi.json` (confirmed: `old path present: False`).

Route declaration order in `runs_router`:
1. `POST ""` — trigger (existing)
2. `GET /{id}` — **new F-048** (declared first)
3. `GET /dagster/{dagster_run_id}` — renamed Dagster-proxy

Matches agreed.md §4 order exactly. No routing ambiguity.

---

### B4 — `RunDetailResponse` 14 fields, nullable correctness, `from_attributes=True` ✅ GREEN

`schemas/runs.py` lines 85–123. Field inventory (verified against ORM):

| Field | Pydantic type | Nullable match |
|---|---|---|
| `id` | `int` | No ✓ |
| `dagster_run_id` | `str` | No ✓ |
| `kind` | `str` | No ✓ |
| `asset_keys` | `list[str]` | No ✓ |
| `partition_keys` | `list[str] \| None` | Yes ✓ |
| `source_collection_id` | `int \| None` | Yes ✓ |
| `dataset_id` | `int \| None` | Yes ✓ |
| `recipe_id` | `int \| None` | Yes ✓ |
| `config` | `dict \| None` | Yes ✓ |
| `status` | `str` | No ✓ |
| `started_at` | `datetime \| None` | Yes ✓ |
| `ended_at` | `datetime \| None` | Yes ✓ |
| `triggered_by` | `int \| None` | Yes ✓ |
| `trigger_context` | `dict \| None` | Yes ✓ |

**Total: 14 fields** — exact match to ORM.  
`model_config = ConfigDict(from_attributes=True)` set at line 99.  
No extra fields invented, no ORM columns omitted.  
Uses correct column name `triggered_by` (not `owner_id` — correct, no AttributeError).

---

### B5 — 9 tests in `test_runs_get.py`; lynchpin, 422, wrong-owner verified ✅ GREEN

9 test functions confirmed:
1. `test_get_run_200_all_fields` — V1 all 14 keys + spot-checks
2. `test_get_run_not_found_returns_404` — V2 non-existent id
3. `test_get_run_wrong_owner_returns_404` — same `{"detail": "Run not found"}` detail as not-found (no enumeration leak) ✓
4. `test_get_run_no_token_returns_401` — real oauth2_scheme, no dep override
5. `test_get_run_invalid_id_returns_422` — non-integer `/api/runs/not-a-number` → 422 ✓
6. `test_get_run_triggered_by_in_query` — **M1 lynchpin**: captures `session.execute.call_args_list[0].args[0]`, compiles with `literal_binds=True`, asserts both `"triggered_by"` and `str(_MOCK_USER.id)` (== `"9"`) appear in compiled SQL ✓
7. `test_get_run_no_extra_fields_leaked` — `set(body.keys()) == _EXPECTED_KEYS` exact 14-key match
8. `test_get_run_config_is_dict_or_null` — Part A: `config={"batch_size": 100}` → `isinstance(dict)` not string; Part B: `config=None` → null
9. `test_get_run_nullable_timestamps` — `started_at=None, ended_at=None` → both null, status "pending"

`_EXPECTED_KEYS` constant (lines 65–77) matches all 14 agreed field names exactly. Mock factory `_make_run_detail()` populates all 14 ORM attributes per contract §7 discipline.

---

### B6 — `test_runs_hello_world.py` 3 call sites + `checks.sh` line 455 area updated ✅ GREEN

`test_runs_hello_world.py` diff shows all three call sites changed:
- Line 110: `client.get(f"/api/runs/{fake_run_id}")` → `client.get(f"/api/runs/dagster/{fake_run_id}")`
- Line 132: same → same
- Line 148: same → same

Module docstring and section header also updated to reference `GET /api/runs/dagster/{dagster_run_id}`.

`verify/checks.sh` diff shows two URL changes in the runs-layer smoke poll block:
- `curl -sS .../api/runs/${RUN_ID}` → `curl -sS .../api/runs/dagster/${RUN_ID}`
- Error message string also updated: `"GET /api/runs/dagster/$RUN_ID ->"` 
- Section echo updated: `"--- runs V1: poll GET /api/runs/dagster/{dagster_run_id} until success ---"`

No functional logic changed in either file — URL string updates only.

---

### B7 — `packages/api-types/openapi.json` in commit `994c0f0` ✅ GREEN

`git show --stat 994c0f0` confirms `packages/api-types/openapi.json` is in the commit (210-line change). **Same commit as all Python source changes — hard invariant #6 satisfied.**

OpenAPI content verified via Python inspection:
- `"/api/runs/{id}"` path present ✓ — `GET` operation with `RunDetailResponse` response schema, 200 + 422 responses
- `"/api/runs/dagster/{dagster_run_id}"` path present ✓ — `GET` operation with `RunStatusResponse`, `operationId` updated
- `"/api/runs/{run_id}"` path **absent** ✓ — old path gone
- `RunDetailResponse` schema under `components/schemas`: 14 properties, `required` array contains all 14 field names ✓
- `RunStatusResponse` schema **retained** ✓
- `RunDetailResponse` description matches agreed.md docstring verbatim

---

### B8 — Hard invariants audit ✅ GREEN

| # | Invariant | Verdict | One-line reason |
|---|---|---|---|
| 1 | Lineage mandatory | **N/A** | Read-only `GET` endpoint — no `Commit` objects created, no lineage event fires. |
| 2 | Storage separation + CAS | **N/A** | Returns Postgres metadata fields only. No MinIO/S3 interaction. `config` and `trigger_context` are JSONB metadata, not content blobs. |
| 3 | Schema frozen post-publish | **N/A** | No schema mutations. Read-only endpoint; no Silver/Gold commit touched. |
| 4 | LLM calls via gateway | **N/A** | No LLM calls. Renamed Dagster-proxy continues to call `gateway.get_run_status(dagster_run_id)` — unchanged, already compliant. |
| 5 | Async SQLAlchemy | **✓ MET** | `AsyncSession = Depends(get_session)`; `await session.execute(select(Run).where(...).where(...))` — no `session.query()`, no sync sessions. |
| 6 | OpenAPI ↔ TS type sync | **✓ MET** | `packages/api-types/openapi.json` regenerated and staged in the **same** commit `994c0f0`. `git show --stat` confirms it. |

---

### B9 — No scope deviation / F-049 leakage ✅ GREEN

Only F-048 work present. No list endpoint (`GET /api/runs`), no pagination, no filtering by status/kind/date added. No new Alembic migration (all 14 columns exist since F-002 — correct). No extraneous routes or schema additions beyond those contracted.

---

## Cosmetic NITs (non-blocking)

**NIT-1**: `launch_hello_world()` function docstring (routers/runs.py line 73) still says "poll GET /api/runs/{run_id} for status" — stale reference to the pre-rename path. This was not listed in agreed.md §4 required docstring changes; no test catches it. Worth cleaning up in a follow-on commit, but does not affect correctness.

---

## Summary

All 9 blockers are **GREEN**. The implementation faithfully executes every item in `contracts/S048-F-048/agreed.md`:

- Single-query owner-scope at `GET /api/runs/{id}` — correct filter, correct 404-collapse.
- `RunDetailResponse` schema exact (14 fields, correct nullability, `from_attributes=True`).
- Dagster-proxy properly renamed with real path change, not just docstring.
- 9 tests covering V1, V2, auth gate, 422 validation, M1 SQL lynchpin, exact key set, JSONB and datetime nullables.
- All three external touch-points updated: `test_runs_hello_world.py` (3 call sites), `checks.sh` (line 455 area), and `openapi.json` (same commit).
- Hard invariants #5 (async) and #6 (codegen) satisfied. Invariants #1–4 correctly N/A.

---

## VERDICT: APPROVED
