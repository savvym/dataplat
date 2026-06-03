# Sprint S042-F-042 — Mode B Review (Post-Implementation)

**Sprint**: S042-F-042 — Materialize dataset  
**Commit reviewed**: `96cfff5`  
**Compared against**: `53f3cde`  
**Date**: 2026-06-03  
**Reviewer**: Mode B

---

## ONE-LINE VERDICT

All agreed.md requirements are implemented correctly; all hard invariants satisfied; 22 new tests (12 route + 10 gateway) present and well-formed.

---

## Compliance Check

### §3 File-by-file change list

| agreed.md item | Expected | Found in diff | Status |
|---|---|---|---|
| `routers/datasets.py` (new) | POST /api/datasets/{recipe_id}/materialize | Present, 186 lines, full 10-step flow | ✅ |
| `schemas/datasets.py` (new) | `MaterializeResponse(dataset_id: int, dagster_run_id: str)` | Exactly two fields, both required, no extras | ✅ |
| `tests/test_datasets_materialize.py` (new) | ≥12 unit tests V1/V2/V3 + A1-A9 | 12 test functions, all IDs present | ✅ |
| `dagster/gateway.py` (modified) | `_ADD_DATASET_PARTITION_MUTATION`, `_LAUNCH_DATASET_BACKFILL_MUTATION`, two new methods, docstring updated | All four items present at lines 282-330 (constants) and 1264-1443 (methods); docstring updated lines 23-24 | ✅ |
| `main.py` (modified) | `import datasets_router` + `app.include_router(datasets_router)` | Both lines added | ✅ |
| `routers/recipes.py` (modified) | `Dataset.status != "failed"` added to freeze-check exists() predicate | `.where(Dataset.status != "failed")` at line 195 | ✅ |
| `dagster/definitions.py` (modified) | `dataset_versions = DynamicPartitionsDefinition(name="dataset_versions")` + stub `@asset def dataset(...)` + added to `Definitions(assets=[...])` | All three additions present | ✅ |
| `packages/api-types/openapi.json` (modified) | Regenerated in same commit 96cfff5 | `POST /api/datasets/{recipe_id}/materialize` path + `MaterializeResponse` schema component added; same commit confirmed by `--stat` | ✅ |
| `tests/test_gateway_dataset_backfill.py` (new) | Gateway unit tests, 5 per method (10 total) | 10 async test functions present | ✅ |

### §4 Ten-step route flow

| Step | Contract requirement | Code evidence |
|---|---|---|
| 1 Auth | `Depends(get_current_user)` — 401 if absent | `current_user: User = Depends(get_current_user)` in function signature | ✅ |
| 2 Owner-scoped load | `SELECT recipe WHERE id=? AND owner_id=?` → 404 both cases, same message | `select(Recipe).where(Recipe.id == recipe_id).where(Recipe.owner_id == current_user.id)` → `raise HTTPException(404, "Recipe not found")` | ✅ |
| 3 Count + version | `COUNT(*)` including failed rows; `v{n}`, `ds_{rid}_v{n}` as plain locals | `select(func.count())...where(Dataset.recipe_id == recipe_id)`; `version_tag: str = f"v{n}"`; `partition_key: str = f"ds_{recipe_id}_v{n}"` | ✅ |
| 4 INSERT | `status='pending'`, `recipe_snapshot=copy(recipe.definition)`, `hf_repo_uri='__pending__'`, `dagster_run_id=None` | `Dataset(recipe_id=..., recipe_snapshot=copy.deepcopy(recipe.definition), version_tag=version_tag, hf_repo_uri="__pending__", status="pending", materialized_by=current_user.id, dagster_run_id=None)` | ✅ |
| 5 flush + hf_repo_uri + capture locals | `await session.flush()`; `hf_repo_uri = f"s3://datasets/{dataset.id}_{version_tag}"`; capture `dataset_id` before commit | Lines 117-123: flush, set `hf_repo_uri`, `dataset_id: int = dataset.id` | ✅ |
| 6 commit | `await session.commit()` — row durable | Line 126 | ✅ |
| 7 add_dataset_partition | `await gateway.add_dataset_partition(partition_key)`; on error: UPDATE status='failed' via `update(Dataset).where(Dataset.id == dataset_id)`, commit, return 503 | Lines 152-160: correct pattern, uses `dataset_id` local | ✅ |
| 8 launch_dataset_backfill | Same error pattern | Lines 163-172: correct pattern, uses `dataset_id` local | ✅ |
| 9 write backfill_id | `update(Dataset).where(Dataset.id == dataset_id).values(dagster_run_id=backfill_id)` + commit | Lines 175-180: correct | ✅ |
| 10 return 202 | `MaterializeResponse(dataset_id=dataset_id, dagster_run_id=backfill_id)` using captured locals | Line 185: uses locals, not ORM attributes | ✅ |

IntegrityError → 409 path: `session.flush()` raises `IntegrityError` → `session.rollback()` → 409 (lines 128-135). ✅

### §6 Stub asset constraints

| Constraint | Required | Found |
|---|---|---|
| `partitions_def=dataset_versions` | FROZEN | Present on `@asset(partitions_def=dataset_versions, ...)` decorator |
| Asset key `dataset` | FROZEN | `def dataset(context: AssetExecutionContext)` — function name is asset key |
| Returns `MaterializeResult(metadata={})` | no-op body | `return MaterializeResult(metadata={})` |
| No `io_manager_key` | Per §6 ADDABLE by F-043 | Absent from decorator — confirmed by grep (no `io_manager_key` on stub) |
| Added to `Definitions(assets=[...])` | Required | `assets=[..., dataset]` in defs |

All stub constraints satisfied. ✅

### §7 Gateway additions

**`_ADD_DATASET_PARTITION_MUTATION`**:
- Separate named constant (not shared with source variant) ✅
- GraphQL operation name: `AddDatasetPartition` ✅
- Variables: `$partitionKey`, `$partitionsDefName`, `$repositorySelector` ✅
- All four union types in fragment spread: `AddDynamicPartitionSuccess`, `DuplicateDynamicPartitionError`, `UnauthorizedError`, `PythonError` ✅

**`_LAUNCH_DATASET_BACKFILL_MUTATION`**:
- Separate named constant ✅
- GraphQL operation name: `LaunchDatasetBackfill` ✅
- Union types: `LaunchBackfillSuccess`, `PartitionSetNotFoundError`, `PartitionKeysNotFoundError`, `PythonError`, `UnauthorizedError`, `InvalidSubsetError`, `RunConflict` — all 7 per agreed.md §7.1 ✅

**`add_dataset_partition()`**:
- Variables: `partitionKey=partition_key`, `partitionsDefName="dataset_versions"`, `repositorySelector` with location/name constants ✅
- `DuplicateDynamicPartitionError` → `logger.debug(...)`, `return None` (idempotent) ✅
- `AddDynamicPartitionSuccess` → `return None` ✅
- `UnauthorizedError`, `PythonError`, unexpected typename → `raise DagsterGatewayError` ✅
- Network errors: `TimeoutException`, `ConnectError`, `HTTPError` all covered ✅
- HTTP non-2xx, non-JSON, GraphQL top-level errors all covered ✅

**`launch_dataset_backfill()`**:
- `assetSelection=[{"path": ["dataset"]}]`, `partitionNames=partition_keys`, `title="F-042 dataset"` ✅
- Returns `backfillId` str ✅
- Empty backfillId check: `if not backfill_id: raise DagsterGatewayError(...)` ✅
- All non-`LaunchBackfillSuccess` typenames raise `DagsterGatewayError` ✅
- 7 error paths covered ✅

**Module docstring update**: `add_dataset_partition` and `launch_dataset_backfill` added at lines 23-24. ✅

### §8 Test plan

**Route tests (test_datasets_materialize.py)**:

| ID | Test name | Present | Assertions correct |
|---|---|---|---|
| V1 | `test_materialize_202_response` | ✅ | 202, `dataset_id: int`, `dagster_run_id` non-empty str |
| V2 | `test_materialize_db_row` | ✅ | `status='pending'`, `recipe_snapshot==recipe.definition`, `version_tag=='v1'`, `hf_repo_uri.startswith('s3://datasets/')`, gateway called |
| V3 | `test_materialize_dagster_called` | ✅ | `add_dataset_partition.assert_called_once_with("ds_3_v1")`, `launch_dataset_backfill.assert_called_once_with(["ds_3_v1"])` |
| A1 | `test_materialize_401_no_auth` | ✅ | 401 + `WWW-Authenticate: Bearer` |
| A2 | `test_materialize_404_recipe_not_found` | ✅ | 404, `{"detail": "Recipe not found"}` |
| A3 | `test_materialize_404_wrong_owner` | ✅ | 404, same message as A2 (no enumeration leak) |
| A4 | `test_materialize_v2_second_call_increments_version` | ✅ | Stateful mock; v1 partition on first call, v2 on second; `gw.add_dataset_partition.assert_called_once_with("ds_7_v1")`, `gw2.add_dataset_partition.assert_called_once_with("ds_7_v2")` |
| A5 | `test_materialize_409_concurrent_race` | ✅ | `IntegrityError` raised on flush → 409 |
| A6 | `test_materialize_503_add_partition_fails` | ✅ | 503; `launch_dataset_backfill.assert_not_called()` |
| A7 | `test_materialize_503_launch_backfill_fails` | ✅ | 503; both gateway methods called |
| A8/V4 | `test_freeze_guard_excludes_failed_row` | ✅ | PUT /api/recipes/88 returns 200 (not 409); `exists()` returns False when only failed rows |
| A9 | `test_materialize_after_failed_retry_increments_version` | ✅ | count=1 → version_tag='v2'; `add_dataset_partition.assert_called_once_with("ds_11_v2")`; `launch_dataset_backfill.assert_called_once_with(["ds_11_v2"])` |

**Note on V3 assetSelection assertion**: The agreed.md V3 criterion says `launch_dataset_backfill` must be "called with `assetSelection=[{"path": ["dataset"]}]`". The route test asserts `gw.launch_dataset_backfill.assert_called_once_with(["ds_3_v1"])` — i.e. it asserts the call to the gateway mock, not the internal payload. The gateway unit test `test_launch_dataset_backfill_success` asserts `backfill_params["assetSelection"] == [{"path": ["dataset"]}]` against the actual HTTP payload. Between the two tests the full stack is covered. ✅

**Gateway tests (test_gateway_dataset_backfill.py)**:

| Test | Description | Present |
|---|---|---|
| `test_add_dataset_partition_success` | `AddDynamicPartitionSuccess` → None; `partitionsDefName='dataset_versions'` verified in payload | ✅ |
| `test_add_dataset_partition_duplicate` | `DuplicateDynamicPartitionError` → None (idempotent) | ✅ |
| `test_add_dataset_partition_unauthorized` | `UnauthorizedError` → `DagsterGatewayError` matching "UnauthorizedError" | ✅ |
| `test_add_dataset_partition_python_error` | `PythonError` → `DagsterGatewayError` matching "PythonError" | ✅ |
| `test_add_dataset_partition_network_error` | `httpx.ConnectError` → `DagsterGatewayError("Cannot connect to Dagster")` | ✅ |
| `test_launch_dataset_backfill_success` | `LaunchBackfillSuccess` → backfillId; `assetSelection=[{"path": ["dataset"]}]`, `title="F-042 dataset"` verified | ✅ |
| `test_launch_dataset_backfill_python_error` | `PythonError` → `DagsterGatewayError("launchPartitionBackfill failed")` | ✅ |
| `test_launch_dataset_backfill_unauthorized` | `UnauthorizedError` → `DagsterGatewayError("launchPartitionBackfill failed")` | ✅ |
| `test_launch_dataset_backfill_network_error` | `httpx.ConnectError` → `DagsterGatewayError("Cannot connect to Dagster")` | ✅ |
| `test_launch_dataset_backfill_invalid_subset` | `InvalidSubsetError` → `DagsterGatewayError("launchPartitionBackfill failed")` | ✅ |

### §11 Hard invariant checklist

| # | Invariant | Assessment |
|---|---|---|
| #1 Lineage | N/A — no Commit created in this sprint | ✅ N/A |
| #2 Storage separation | `recipe_snapshot` = JSONB metadata copy; no file bytes in Postgres | ✅ |
| #3 Schema frozen post-publish | `recipe_snapshot = copy.deepcopy(recipe.definition)` at INSERT time; H1 fix ensures failed rows don't lock the recipe | ✅ |
| #4 LLM through gateway | No LLM calls in this sprint | ✅ N/A |
| #5 Async SQLAlchemy | Every `session.execute`, `session.flush`, `session.commit` is `await`-ed; no `session.query()`; `AsyncSession` typed throughout; post-commit updates use direct `update(Dataset).where(...).values(...)` with captured `dataset_id` int (M2 pattern fully applied) | ✅ |
| #6 OpenAPI ↔ TS type sync | `packages/api-types/openapi.json` regenerated and present in commit `96cfff5` (confirmed via `--stat` listing the file in the same commit) | ✅ |

---

## Findings

### H1 freeze guard (VERIFIED ✅)

`recipes.py` update_recipe handler now reads:
```python
select(
    exists()
    .where(Dataset.recipe_id == recipe.id)
    .where(Dataset.status != "failed")
)
```
Test A8 (`test_freeze_guard_excludes_failed_row`) seeds `result2.scalar_one.return_value = False` (only failed rows → guard returns False) and asserts `response.status_code == 200`. ✅

### H2 stub asset (VERIFIED ✅)

`definitions.py` stub has:
- `partitions_def=dataset_versions` ✅
- Asset key `dataset` (function name) ✅
- `return MaterializeResult(metadata={})` no-op body ✅
- **No `io_manager_key`** ✅ (grep confirms absent from stub decorator; F-043 will add it)
- Added to `Definitions(assets=[..., dataset])` ✅

### M2 locals-before-commit (VERIFIED ✅)

`dataset_id: int = dataset.id` captured at line 123 (after `flush()`, before `commit()`). All three post-commit writes (Steps 7 error path, 8 error path, 9 success path) use `update(Dataset).where(Dataset.id == dataset_id)` with the captured local integer. No ORM attribute access after Step 6 commit.

### M1 retry-after-failure test (VERIFIED ✅)

`test_materialize_after_failed_retry_increments_version` (A9):
- Seeds `result2.scalar_one.return_value = 1` (one existing failed v1 row)
- Asserts 202
- Captures `version_tag` on flushed object; asserts `"v2" in captured_version`
- `gw.add_dataset_partition.assert_called_once_with("ds_11_v2")` ✅
- `gw.launch_dataset_backfill.assert_called_once_with(["ds_11_v2"])` ✅
- Note: the test does not assert a separate "old failed v1 row still present" DB-level check, but that assertion is not testable in unit tests with mock sessions; the important invariants (count includes failed rows, correct partition key used) are fully covered.

### Tombstone semantics (VERIFIED ✅)

Steps 7 and 8 error paths both:
1. `await session.execute(update(Dataset).where(Dataset.id == dataset_id).values(status="failed"))`
2. `await session.commit()` — durable before returning 503
3. `return JSONResponse(status_code=503, content={"detail": str(exc)})`

No DELETE. No orphan `status='pending'` on the Dagster-failure path. ✅

### Owner-scoped 404 collapse (VERIFIED ✅)

Both "recipe not found" and "wrong owner" produce `{"detail": "Recipe not found"}`. Tests A2 and A3 both assert `response.json() == {"detail": "Recipe not found"}`, confirming the same error string. ✅

### OpenAPI invariant #6 (VERIFIED ✅)

`packages/api-types/openapi.json` appears in `git show 96cfff5 --stat` alongside all other feature files. The diff adds `POST /api/datasets/{recipe_id}/materialize` and `MaterializeResponse` schema component in the same commit. ✅

### Async SQLAlchemy invariant #5 (VERIFIED ✅)

All session operations in `routers/datasets.py` are `async def` with `await`:
- `await session.execute(...)` (recipe load, count, tombstone updates, backfill_id update)
- `await session.flush()`
- `await session.commit()` (three separate commit points)
- `await session.rollback()` (IntegrityError path)

No `session.query()`. No sync sessions. ✅

### Auth on new endpoint (VERIFIED ✅)

`current_user: User = Depends(get_current_user)` in the function signature. Test A1 confirms 401 + `WWW-Authenticate: Bearer` when no token is provided. ✅

### Test count delta (VERIFIED ✅)

New test functions:
- `test_datasets_materialize.py`: 12 functions
- `test_gateway_dataset_backfill.py`: 10 functions
- **Total new: 22 tests** — matches implementer's reported "+22 tests (253 → 275)". ✅

### Gateway constant accessibility (VERIFIED ✅)

`_ADD_DATASET_PARTITION_MUTATION` is accessed at `gateway.py` line 1288 (`"query": _ADD_DATASET_PARTITION_MUTATION`). `_LAUNCH_DATASET_BACKFILL_MUTATION` is accessed at line 1384 (`"query": _LAUNCH_DATASET_BACKFILL_MUTATION`). Both constants are genuinely used; any Pyright "not accessed" warning is a false positive from running outside the uv venv (per CLAUDE.md non-blocking note). ✅

---

## Advisory / NIT (non-blocking)

**NIT-B-1** (advisory): Test V3 (`test_materialize_dagster_called`) asserts `gw.launch_dataset_backfill.assert_called_once_with(["ds_3_v1"])` — this verifies the mock call but not the internal `assetSelection` payload. The gateway unit test `test_launch_dataset_backfill_success` covers the payload shape. Coverage is complete across the two layers, but a future refactor that changes the route's call site argument to `gateway.launch_dataset_backfill(["ds_3_v1"])` while somehow omitting the `assetSelection` in a real implementation would only be caught by the gateway unit test. This is an acceptable split of concerns given the mock architecture; no action required.

**NIT-B-2** (advisory): `ruff format` was applied to the entire `apps/api/` codebase as formatting debt clearance. This produces a large diff in unrelated files (mostly line-wrapping of long strings and set literals). This is cosmetically correct but makes the F-042 diff harder to read in isolation. Recommend a dedicated formatting commit in future sprints to keep feature diffs clean. No functional issue.

---

## Summary

All 9 required compliance areas from agreed.md are satisfied. All hard invariants (#1–#6) are either N/A or confirmed compliant. All round-1 and round-2 Mode A findings (H1, H2, M1, M2, L1, L2, L3, NIT-2, NIT-3, NIT-R2-1) are addressed in the implementation. No new HIGH or MEDIUM issues found.

---

VERDICT: APPROVED
