# Sprint S042-F-042 — Proposed Contract

**Feature**: F-042 — Materialize dataset  
**Depends on**: F-037 (recipe create, `passes: true`), F-004 (Dagster gateway, `passes: true`)  
**Sprint directory**: `contracts/S042-F-042/`  
**Author**: leader (inline)  
**Date**: 2026-06-03  
**Revision**: 2 (addresses H1, H2, M1, M2, L1, L2, L3, NIT-2, NIT-3 from Mode A feedback)

---

## 1. Goal

Implement `POST /api/datasets/{recipe_id}/materialize`.

When called by an authenticated user who owns the recipe:

1. Create a `dataset` row in Postgres with `status='pending'`, a frozen `recipe_snapshot` copied from `recipe.definition`, and a monotonically versioned `version_tag` (e.g. `v1`, `v2`, …).
2. Register the new partition key (`ds_<recipe_id>_v<n>`) in the Dagster `dataset_versions` dynamic partition definition.
3. Launch a Dagster asset backfill targeting the `dataset` asset with that partition key.
4. Write the Dagster `backfillId` back into `dataset.dagster_run_id` and commit.
5. Return `HTTP 202 Accepted` with `{"dataset_id": <int>, "dagster_run_id": "<backfill-id>"}`.

Additionally — **H1 fix (in-scope this sprint)**:

6. Fix the F-040 freeze guard in `PUT /api/recipes/{id}` (`recipes.py` lines 197–206): add `Dataset.status != 'failed'` to the existence-check predicate so that recipes whose only dataset rows have `status='failed'` remain editable. A recipe is only locked once a non-failed (i.e. `pending`, `running`, or `done`) dataset row exists referencing it.

The `dataset` asset does not yet exist in the Dagster code location. A minimal stub asset is landed in this sprint (Decision A1) so that V3 is verifiable when this sprint closes. F-043 replaces the stub body; the stub's partition definition and asset key are forward-compatible with F-043.

---

## 2. Verification Matrix

| ID | Feature list criterion | How verified |
|---|---|---|
| **V1** | POST returns 202 with `{"dataset_id": <int>, "dagster_run_id": "..."}` | `test_materialize_202_response` — assert `status_code == 202`, `resp["dataset_id"]` is `int`, `resp["dagster_run_id"]` is non-empty `str` |
| **V2** | Dataset row with `status='pending'`, `recipe_snapshot` == frozen recipe definition | `test_materialize_db_row` — query `dataset` table; assert `status == 'pending'`, `recipe_snapshot == recipe.definition`, `version_tag == 'v1'`, `hf_repo_uri.startswith('s3://datasets/')` |
| **V3** | Dagster shows a backfill launched for the `dataset` asset | `test_materialize_dagster_called` — assert `gateway.add_dataset_partition` called with `'ds_{rid}_v1'`; assert `gateway.launch_dataset_backfill` called with `assetSelection=[{"path": ["dataset"]}]` and `partitionNames=['ds_{rid}_v1']` |
| **V4** | Freeze guard excludes `status='failed'` rows — recipe with only a failed dataset accepts `PUT` | `test_freeze_guard_excludes_failed_row` (A8) — create recipe; seed a `Dataset` row with `status='failed'` referencing it; assert `PUT /api/recipes/{id}` returns 200, not 409 |

All three primary criteria (V1–V3) are verified by the **unit-test suite only** (no live Dagster required for V3 — the stub asset in `definitions.py` satisfies the live integration check; V3 uses mocks). V4 is a unit test against the updated `recipes.py` handler.

---

## 3. File-by-File Change List

### 3.1 New files

| File | Purpose |
|---|---|
| `apps/api/dataplat_api/routers/datasets.py` | New router: `POST /api/datasets/{recipe_id}/materialize` |
| `apps/api/dataplat_api/schemas/datasets.py` | Pydantic schema: `MaterializeResponse` |
| `apps/api/tests/test_datasets_materialize.py` | ≥12 unit tests covering V1/V2/V3/V4 + error paths A1–A9 |

### 3.2 Modified files

| File | Change |
|---|---|
| `apps/api/dataplat_api/dagster/gateway.py` | Append `_ADD_DATASET_PARTITION_MUTATION`, `_LAUNCH_DATASET_BACKFILL_MUTATION`, `add_dataset_partition()`, `launch_dataset_backfill()`. Update module docstring (lines 13–24) to list new methods. |
| `apps/api/dataplat_api/main.py` | Import `datasets_router` and call `app.include_router(datasets_router)`. |
| `apps/api/dataplat_api/routers/recipes.py` | Update freeze guard in `update_recipe` handler (lines 197–206): add `.where(Dataset.status != 'failed')` to the existence-check predicate. Before: `select(exists().where(Dataset.recipe_id == recipe.id))`. After: `select(exists().where(Dataset.recipe_id == recipe.id).where(Dataset.status != 'failed'))`. |
| `dagster/dagster_platform/definitions.py` | Append `dataset_versions = DynamicPartitionsDefinition(name="dataset_versions")` and stub `@asset def dataset(...)`. Add `dataset` to `Definitions(assets=[...])`. |
| `packages/api-types/openapi.json` | Regenerated via `make codegen` — same commit (invariant #6). |

### 3.3 Test files (gateway)

The existing `apps/api/tests/test_gateway_chunks_backfill.py` is the model. Gateway tests for the two new methods are added as a **new file**:

| File | Purpose |
|---|---|
| `apps/api/tests/test_gateway_dataset_backfill.py` | Unit tests for `add_dataset_partition` and `launch_dataset_backfill` methods |

---

## 4. Route Flow (Ordered — the Rollback Boundary)

**Endpoint**: `POST /api/datasets/{recipe_id}/materialize`  
**Status on success**: `202 Accepted`  
**No request body** (recipe_id is the sole input; no override config in MVP).

```
Step 1.  Auth
         Depends(get_current_user) — 401 if token absent/invalid.

Step 2.  Load recipe (owner-scoped)
         SELECT recipe WHERE id = recipe_id AND owner_id = current_user.id
         → 404 if absent or wrong owner (no enumeration leak; mirrors
           recipes.py lines 141-152).

Step 3.  Compute version_tag and partition_key
         SELECT COUNT(*) FROM dataset WHERE recipe_id = recipe_id
         n = count + 1
         version_tag    = f"v{n}"          ← plain Python str local
         partition_key  = f"ds_{recipe_id}_v{n}"  ← plain Python str local
         (COUNT(*) deliberately includes rows with status='failed' so that
          a failed v1 attempt causes the next attempt to produce v2, not v1.
          This prevents reuse of a version_tag that already exists in Dagster's
          partition registry. The (recipe_id, version_tag) UNIQUE constraint
          uq_dataset_recipe_version is the hard race guard; a concurrent INSERT
          with the same version_tag → IntegrityError → 409.)

Step 4.  INSERT dataset row
         status          = 'pending'
         recipe_id       = recipe_id
         recipe_snapshot = copy of recipe.definition (JSONB — frozen at call
                           time, satisfying invariant #3)
         version_tag     = version_tag
         hf_repo_uri     = '__pending__'   ← placeholder; real value computed
                           after flush (Step 5)
         materialized_by = current_user.id
         dagster_run_id  = None

Step 5.  await session.flush() to obtain dataset.id (Identity() — DB-assigned).
         dataset.hf_repo_uri = f"s3://datasets/{dataset.id}_{version_tag}"
         (Flush-then-update pattern in one open transaction; the '__pending__'
          placeholder is never committed. Rationale: hf_repo_uri is NOT NULL
          so we cannot defer it, but dataset.id is not available before flush.)

         ── Capture primitives before commit (M2: avoids post-expire ORM access).
         ── After session.commit() SQLAlchemy expires non-PK attributes on the
         ── dataset ORM object. Capture all values we need in post-commit steps
         ── as plain Python locals NOW, while the transaction is still open:
         dataset_id: int    = dataset.id        # PK — DB-assigned on flush
         version_tag: str   = version_tag       # already a local; annotated for clarity
         partition_key: str = partition_key     # already a local; annotated for clarity

Step 6.  await session.commit()
         The dataset row is now durable. Dagster calls happen AFTER commit so
         a Dagster outage never prevents the row from being written.

         ── Rollback boundary: Steps 7-9 are Dagster side-effects.
         ── If any of them fail, we UPDATE dataset.status = 'failed' and
         ── return 503. We do NOT delete the row (tombstone semantics: the row
         ── records the failed attempt and can be inspected/cleaned by ops).
         ──
         ── Per-status freeze-guard behavior (F-040 recipes.py, post H1 fix):
         ──   status='pending'  → recipe is LOCKED (materialization in flight)
         ──   status='running'  → recipe is LOCKED (materialization in flight)
         ──   status='failed'   → recipe is NOT LOCKED (tombstone is a
         ──                        transient state; user may edit and retry)
         ──   status='done'     → recipe is LOCKED (invariant #3: published)
         ──
         ── Note: user retry after a failed row produces the next version_tag
         ── (v2 if v1 is failed) per the COUNT(*)-includes-failed approach in
         ── Step 3. The failed v1 row is preserved for audit and stays in Dagster
         ── as a stale partition (harmless — no materialization event, never
         ── targeted by FastAPI again since each retry creates a higher version).
         ──
         ── Residual risk (L2): if the tombstone UPDATE in Step 7 or Step 8
         ── itself fails (e.g. DB connection drops between the Step 6 commit
         ── and the error-path UPDATE), a status='pending' row with
         ── dagster_run_id=NULL persists indefinitely. F-050's webhook
         ── correlates by dagster_run_id — a NULL value means it will never
         ── be auto-transitioned. Recovery: ops runs direct SQL:
         ──   UPDATE dataset SET status='failed'
         ──   WHERE dagster_run_id IS NULL AND status='pending';
         ── This requires a double DB failure (commit succeeds, next execute
         ── fails) and the row is inert (no data written to MinIO); risk is
         ── accepted as operationally recoverable.

Step 7.  await gateway.add_dataset_partition(partition_key)
         Registers ds_{recipe_id}_v{n} in the "dataset_versions" partition def.
         On DagsterGatewayError:
           → await session.execute(
                 update(Dataset)
                 .where(Dataset.id == dataset_id)   # use captured local int
                 .values(status="failed")
             )
           → await session.commit()
           → return JSONResponse(status_code=503, content={"detail": str(exc)})

Step 8.  await gateway.launch_dataset_backfill([partition_key])
         Launches launchPartitionBackfill for assetSelection=[{"path": ["dataset"]}].
         On DagsterGatewayError:
           → await session.execute(
                 update(Dataset)
                 .where(Dataset.id == dataset_id)   # use captured local int
                 .values(status="failed")
             )
           → await session.commit()
           → return JSONResponse(status_code=503, content={"detail": str(exc)})

Step 9.  Write backfill_id back to the dataset row.
         await session.execute(
             update(Dataset)
             .where(Dataset.id == dataset_id)       # use captured local int
             .values(dagster_run_id=backfill_id)
         )
         await session.commit()

Step 10. Return HTTP 202:
         {"dataset_id": dataset_id, "dagster_run_id": backfill_id}
         (Use captured locals — do NOT access dataset.id or dataset.dagster_run_id
          after the Step 6 commit, as those attributes are expired.)
```

**Why commit before Dagster calls (Step 6)?**  
Mirrors the pattern in `sources.py` lines 308-315 (F-012): durability of the DB row is independent of Dagster availability. If Dagster is down, the `pending` row provides the audit record and a recovery hook for future polling (F-044/F-050).

**Why not rollback (DELETE) on Dagster failure?**  
The tombstone (`status='failed'`) approach is safer than a DELETE:
- It preserves the audit trail.
- It avoids a TOCTOU race if Step 7 succeeded but Step 8 failed (the partition was already registered in Dagster; re-running with the same partition_key would collide unless we can confirm the partition was not registered).
- The failed row is a transient record only — thanks to the H1 freeze-guard fix, `status='failed'` rows do **not** lock the recipe. The user can edit the recipe and retry; the next call produces `version_tag='v{n+1}'`.

---

## 5. Schema Changes and OpenAPI Regeneration

### 5.1 No new migrations

No database schema changes are required. The `dataset` table already has all necessary columns (`recipe_snapshot JSONB NOT NULL`, `status TEXT NOT NULL`, `hf_repo_uri TEXT NOT NULL`, `dagster_run_id TEXT`, `version_tag TEXT NOT NULL`, `materialized_by BIGINT`, `recipe_id BIGINT FK`). Confirmed from `apps/api/dataplat_api/db/models.py` lines 238-271.

The `uq_dataset_recipe_version` UNIQUE constraint on `(recipe_id, version_tag)` (models.py line 244-246) is the race guard for concurrent materialize calls. No additional index is needed.

### 5.2 New Pydantic schema — `apps/api/dataplat_api/schemas/datasets.py`

```python
class MaterializeResponse(BaseModel):
    dataset_id: int
    dagster_run_id: str
```

No request body model is needed (path param only). Intentionally omitting an optional request-body override for now: no "materialize with config override" feature in MVP (design doc §1.3 deferred list).

### 5.3 OpenAPI regeneration

Invariant #6: after the router is registered and FastAPI generates the new path (`POST /api/datasets/{recipe_id}/materialize`), run:

```
make codegen
```

The resulting diff in `packages/api-types/openapi.json` must be committed in the **same commit** as all other code changes. CI will reject a mismatch.

---

## 6. Dagster Stub Asset — Rationale and Forward-Compatibility (Decision A1)

**Decision**: Land a minimal stub `dataset` asset in `dagster/dagster_platform/definitions.py` as part of this sprint.

**Rationale**: Without the `dataset` asset present in the Dagster code location, `gateway.launch_dataset_backfill()` will return a `PartitionSetNotFoundError` or `InvalidSubsetError` from Dagster GraphQL (the partition def `dataset_versions` would exist but no asset would be associated with it). This makes V3 unverifiable in any live integration check. A stub of ~10 lines unblocks V3 with zero risk of breaking F-043.

**Stub specification** (to be placed in `definitions.py` after the existing `attr_minhash` asset, before `hello_op`):

```python
# F-042: DynamicPartitionsDefinition for dataset versions.
# Partition key format: "ds_{recipe_id}_v{n}" (design doc §5.3, line 532).
# F-043 will replace the body below and extend the decorator; see forward-
# compatibility guarantees below.
dataset_versions = DynamicPartitionsDefinition(name="dataset_versions")

@asset(
    partitions_def=dataset_versions,
    description=(
        "Dataset materializer stub (F-042). Partition key: ds_{recipe_id}_v{n}. "
        "Real body implemented in F-043 (sft_synthesis_qa materializer). "
        "This stub returns a no-op MaterializeResult so that launchPartitionBackfill "
        "can resolve the asset without error during F-042 integration testing."
    ),
)
def dataset(context: AssetExecutionContext) -> MaterializeResult:
    """Stub dataset asset (F-042). Body replaced by F-043."""
    partition_key = context.partition_key
    context.log.info("dataset stub: partition_key=%s — no-op (F-042 stub)", partition_key)
    return MaterializeResult(metadata={})
```

**Forward-compatibility guarantees** (what F-042 freezes):

| Element | Status | Notes |
|---|---|---|
| Asset key `dataset` | **FROZEN** — must not change | Matches `assetSelection=[{"path": ["dataset"]}]` in `launch_dataset_backfill` |
| `partitions_def=dataset_versions` | **FROZEN** — must not change | `DynamicPartitionsDefinition(name="dataset_versions")` is the shared partition registry |
| Function body | **REPLACEABLE** by F-043 | Stub no-op body will be replaced with the real sft_synthesis_qa materializer |
| `return` type / value | **REPLACEABLE** by F-043 | F-043 returns actual dataset output |
| `io_manager_key` kwarg | **ADDABLE** by F-043 | F-043 must add `io_manager_key="hf_dataset_io"` to the `@asset` decorator per design doc §8.1 (`HFDatasetIOManager` writes Parquet + README + recipe.json to MinIO) |
| `Definitions(resources={...})` | **EXTENDABLE** by F-043 | F-043 must add `"hf_dataset_io": HFDatasetIOManager()` to the resources dict |

**What F-043 changes**: the function body, the `return` type/value, `io_manager_key` (to be added as `io_manager_key="hf_dataset_io"`), and the `Definitions(resources={...})` call site. The `partitions_def=dataset_versions` and asset key `dataset` are frozen and MUST NOT change between sprints.

---

## 7. Gateway Additions

### 7.1 New mutation constants

#### `_ADD_DATASET_PARTITION_MUTATION`

Mirrors `_ADD_SOURCE_PARTITION_MUTATION` exactly; only `partitionsDefName` differs (passed as a variable, so the mutation text is identical — only the Python call site changes the value to `"dataset_versions"`).

> **Implementation note**: The mutation text of `_ADD_DATASET_PARTITION_MUTATION` is structurally identical to `_ADD_SOURCE_PARTITION_MUTATION`. Per the project convention of one mutation constant per asset (gateway.py comments at lines 97-99, 155-157, 186-188), they are kept as **separate named constants** for self-documentation and so that future changes (e.g. adding metadata fields per-asset) do not require splitting a shared constant.

```graphql
mutation AddDatasetPartition(
  $partitionKey: String!
  $partitionsDefName: String!
  $repositorySelector: RepositorySelector!
) {
  addDynamicPartition(
    partitionKey: $partitionKey
    partitionsDefName: $partitionsDefName
    repositorySelector: $repositorySelector
  ) {
    __typename
    ... on AddDynamicPartitionSuccess { partitionKey partitionsDefName }
    ... on DuplicateDynamicPartitionError { partitionsDefName partitionName message }
    ... on UnauthorizedError { message }
    ... on PythonError { message }
  }
}
```

#### `_LAUNCH_DATASET_BACKFILL_MUTATION`

Mirrors `_LAUNCH_CHUNKS_BACKFILL_MUTATION` (gateway.py lines 188-215); only the mutation name in the GraphQL operation name differs. The `assetSelection` path is set at the call site (`["dataset"]`), not hard-coded in the mutation text.

```graphql
mutation LaunchDatasetBackfill($backfillParams: LaunchBackfillParams!) {
  launchPartitionBackfill(backfillParams: $backfillParams) {
    __typename
    ... on LaunchBackfillSuccess { backfillId }
    ... on PartitionSetNotFoundError { message }
    ... on PartitionKeysNotFoundError { message }
    ... on PythonError { message }
    ... on UnauthorizedError { message }
    ... on InvalidSubsetError { message }
    ... on RunConflict { message }
  }
}
```

### 7.2 New gateway methods

#### `add_dataset_partition(partition_key: str) -> None`

- Mirrors `add_source_partition` (gateway.py lines 600-692).
- Variables: `partitionKey=partition_key`, `partitionsDefName="dataset_versions"`, `repositorySelector={"repositoryLocationName": _REPOSITORY_LOCATION_NAME, "repositoryName": _REPOSITORY_NAME}`.
- Idempotent: `DuplicateDynamicPartitionError` → logged at DEBUG, not raised (same as `add_source_partition`).
- Raises `DagsterGatewayError` for all other failure types.

#### `launch_dataset_backfill(partition_keys: list[str]) -> str`

- Mirrors `launch_chunks_backfill` (gateway.py lines 858-936).
- Variables: `backfillParams={"assetSelection": [{"path": ["dataset"]}], "partitionNames": partition_keys, "title": "F-042 dataset"}`.
- Returns `backfillId` string.
- Raises `DagsterGatewayError` for all non-`LaunchBackfillSuccess` typenames and network failures.

### 7.3 Module docstring update

Add to the module-level docstring (gateway.py lines 13–24):

```
    add_dataset_partition(partition_key) -> None     # F-042
    launch_dataset_backfill(partition_keys) -> str   # F-042
```

---

## 8. Test Plan

### 8.1 `apps/api/tests/test_datasets_materialize.py` (route tests)

All tests use `pytest_asyncio` / `httpx.AsyncClient` with the mock-session pattern established in `test_recipes_create.py`. `DagsterGateway` is injected via `app.dependency_overrides`.

| Test ID | Description | Expected |
|---|---|---|
| **V1** `test_materialize_202_response` | Authenticated user, valid recipe owner, all mocks succeed | 202; body has `dataset_id: int` and `dagster_run_id: str` (non-empty) |
| **V2** `test_materialize_db_row` | Same setup; inspect the dataset row written to DB | `status='pending'`; `recipe_snapshot == recipe.definition`; `version_tag == 'v1'`; `hf_repo_uri` starts with `'s3://datasets/'`; `dagster_run_id` is set to mock backfill id |
| **V3** `test_materialize_dagster_called` | Assert on mock call signatures | `add_dataset_partition` called with `'ds_{rid}_v1'`; `launch_dataset_backfill` called with `['ds_{rid}_v1']` and payload contains `assetSelection=[{"path": ["dataset"]}]` |
| **A1** `test_materialize_401_no_auth` | Request without Authorization header | 401 |
| **A2** `test_materialize_404_recipe_not_found` | recipe_id does not exist | 404 |
| **A3** `test_materialize_404_wrong_owner` | recipe_id exists but belongs to a different user | 404 (no enumeration leak) |
| **A4** `test_materialize_v2_second_call_increments_version` | Call materialize twice on the same recipe. Session mock is stateful: first call's INSERT is committed (visible in the mock DB state) before second call's `COUNT(*)` executes — a naive in-memory mock must reflect the committed row on the second call to return `count=1`. | First call: `version_tag='v1'`, `partition_key='ds_{rid}_v1'`. Second call: `version_tag='v2'`, `partition_key='ds_{rid}_v2'`. Both return 202. |
| **A5** `test_materialize_409_concurrent_race` | Simulate a race by forcing `uq_dataset_recipe_version` IntegrityError on second insert | 409 Conflict |
| **A6** `test_materialize_503_add_partition_fails` | `gateway.add_dataset_partition` raises `DagsterGatewayError` | 503; dataset row exists in DB with `status='failed'`; `dagster_run_id` is None |
| **A7** `test_materialize_503_launch_backfill_fails` | `gateway.launch_dataset_backfill` raises `DagsterGatewayError` | 503; dataset row exists in DB with `status='failed'`; `dagster_run_id` is None |
| **A8** `test_freeze_guard_excludes_failed_row` | Create recipe; seed a `Dataset` row with `status='failed'`, `recipe_id=rid` referencing that recipe; call `PUT /api/recipes/{rid}` with a valid update payload | 200 (not 409); freeze guard does NOT block a recipe that has only failed dataset rows. Validates the H1 fix in `recipes.py`. |
| **A9** `test_materialize_after_failed_retry_increments_version` | Setup: create recipe; insert a `Dataset` row with `status='failed'`, `version_tag='v1'`, `recipe_id=rid` for that recipe. Call `POST /api/datasets/{rid}/materialize` with mocks succeeding. | 202; new dataset row exists with `version_tag='v2'`, `status='pending'`; old failed row still present with `status='failed'`, `version_tag='v1'`. Both gateway methods called with `'ds_{rid}_v2'`. Validates that `COUNT(*)` includes failed rows so v1 is never reused. |

**Note on A5**: The `(recipe_id, version_tag)` UNIQUE constraint (`uq_dataset_recipe_version`, models.py lines 244-246) is the correct guard. The count-based version_tag computation is not atomic by itself; the constraint is the hard guarantee. Test A5 mocks the IntegrityError path directly.

### 8.2 `apps/api/tests/test_gateway_dataset_backfill.py` (gateway unit tests)

Mirrors `test_gateway_chunks_backfill.py` exactly in structure. Each method gets 5 tests.

**For `add_dataset_partition`**:

| Test ID | Description | Expected |
|---|---|---|
| `test_add_dataset_partition_success` | `AddDynamicPartitionSuccess` response | returns `None`; payload sent with `partitionsDefName='dataset_versions'` |
| `test_add_dataset_partition_duplicate` | `DuplicateDynamicPartitionError` response | returns `None` (idempotent no-op, logged at DEBUG) |
| `test_add_dataset_partition_unauthorized` | `UnauthorizedError` response | raises `DagsterGatewayError` |
| `test_add_dataset_partition_python_error` | `PythonError` response | raises `DagsterGatewayError` |
| `test_add_dataset_partition_network_error` | `httpx.ConnectError` | raises `DagsterGatewayError("Cannot connect to Dagster")` |

**For `launch_dataset_backfill`**:

| Test ID | Description | Expected |
|---|---|---|
| `test_launch_dataset_backfill_success` | `LaunchBackfillSuccess` response | returns `backfillId` string; payload has `assetSelection=[{"path": ["dataset"]}]`, `title="F-042 dataset"` |
| `test_launch_dataset_backfill_python_error` | `PythonError` response | raises `DagsterGatewayError("launchPartitionBackfill failed")` |
| `test_launch_dataset_backfill_unauthorized` | `UnauthorizedError` response | raises `DagsterGatewayError` |
| `test_launch_dataset_backfill_network_error` | `httpx.ConnectError` | raises `DagsterGatewayError("Cannot connect to Dagster")` |
| `test_launch_dataset_backfill_invalid_subset` | `InvalidSubsetError` response | raises `DagsterGatewayError` |

---

## 9. Risks and Open Questions for Reviewer

### R1 — Rollback strategy on Dagster failure (tombstone vs. delete)

**Decision taken**: tombstone (`status='failed'`), not DELETE.

**Rationale**: described in §4 above. The key concern is that `add_dataset_partition` (Step 7) is **not idempotent in terms of the partition being registered** — if it succeeds but Step 8 fails, a DELETE would leave a stale partition in Dagster's partition definition. Tombstone approach keeps the DB and Dagster in a consistent state for manual recovery.

**Residual risk**: if the tombstone UPDATE itself fails (DB dropout between Steps 6 and 7 or 8), a `status='pending'` row with `dagster_run_id=NULL` persists. This is recoverable by ops via direct SQL (`UPDATE dataset SET status='failed' WHERE dagster_run_id IS NULL AND status='pending'`). This risk is accepted as operationally manageable: it requires a double DB failure (commit succeeds, next execute fails) and the row is inert (no data written to MinIO).

### R2 — F-040 freeze guard interaction

**RESOLVED**: Fix is mandatory, scoped to this sprint. See §1 Goal item 6 and §3.2 Modified files. The `PUT /api/recipes/{id}` handler in `recipes.py` (lines 197–206) must add `Dataset.status != 'failed'` to the existence-check predicate. Test A8 verifies this. The rationale: a `status='failed'` row represents a failed attempt with no data committed to MinIO — it is not a "published Silver/Gold equivalent" per invariant #3. Locking the recipe on a failed attempt would leave users unable to fix their recipe and retry without direct DB surgery.

### R3 — `add_dataset_partition` idempotency

`add_dataset_partition` treats `DuplicateDynamicPartitionError` as a no-op (mirrors `add_source_partition`). On the second call to `POST /api/datasets/{recipe_id}/materialize`, a new `version_tag` is computed so a duplicate partition key should never occur. However, if the API is called with a race or during recovery from a `failed` row, the partition might already exist. The idempotency here is correct and safe.

### R4 — `hf_repo_uri` format

The design doc §5.2 (line 522) names the MinIO bucket `datasets`. The URI is computed as `f"s3://datasets/{dataset.id}_{version_tag}"` (e.g. `s3://datasets/7_v1`). F-043/F-044 will write Parquet files under this prefix. If the format should differ (e.g. include the recipe_id), the reviewer should flag it now before F-043 hard-codes the S3 prefix.

**Note**: The `spec/feature_list.json` F-044 entry says `s3://datasets/{dataset_id}_v{version}/data/`. This implies the prefix root is `s3://datasets/{dataset_id}_{version_tag}` — consistent with the proposed format. Confirmed acceptable.

### R5 — Dagster stub asset in `definitions.py`

The stub `dataset` asset (§6) adds `dataset_versions` to `definitions.py`. This is a `DynamicPartitionsDefinition(name="dataset_versions")` object that must be distinct from the `sources_partitions` object (different name string). No namespace collision.

**Reviewer question**: Is it acceptable for this stub to be imported into the live Dagster code location from day one of this sprint, before F-043 ships the real body? Risk: if a user accidentally triggers the `dataset` asset manually in the Dagster UI, the stub returns `MaterializeResult(metadata={})` and marks the partition as materialized — no data is written to MinIO, no Postgres update. This is a no-op, not a data-loss event.

### R6 — No `Run` table row

F-042 does **not** create a `run` table row. The `run` table (models.py lines 274-320) is for tracking the Dagster run status via webhook (F-050) and polling (F-048/F-049). Those features are not yet implemented. F-042 returns the `dagster_run_id` (backfill ID) and stores it in `dataset.dagster_run_id`. F-048/F-050 will pick this up when implemented.

**Reviewer question**: Is this acceptable, or should F-042 create a `run` row with `kind='materialize'` now (even with F-048/F-049 unimplemented)? Recommendation: defer — creating a `run` row requires wiring the `dataset_id` FK, `asset_keys`, etc., and those semantics belong with F-048. Adding them now without the corresponding GET endpoint creates dead data.

---

## 10. Out of Scope

The following are explicitly **NOT** implemented in this sprint:

| Item | Deferred to |
|---|---|
| Real materialization body (LLM calls, Parquet writing) | F-043 |
| `HFDatasetIOManager` (status=done, sample_count, size_bytes update) | F-044 |
| `GET /api/datasets` — list datasets endpoint | F-045 |
| `GET /api/datasets/{id}` — dataset detail endpoint | F-046 |
| `GET /api/datasets/{id}/download` — download endpoint | F-047 |
| Run status webhook / run table row for materialize | F-048, F-050 |
| WebSocket run subscription for dataset progress | F-051 |
| Recipe versioning display in UI | F-083 |
| Dagster job/sensor for status callbacks | F-050 |
| Visibility / ACL beyond private (owner-only) | MVP boundary §11.6 |
| Any request body for config overrides | MVP boundary |
| `io_manager_key` on the stub `dataset` asset | F-043 (adds `io_manager_key="hf_dataset_io"` per design doc §8.1) |

---

## 11. Hard Invariant Checklist

| Invariant | Status | Notes |
|---|---|---|
| #1 Lineage (parents[], processor, config hash) | ✅ N/A | F-042 creates a dataset *row* (not a Commit). Lineage is relevant at the Commit level (F-043/F-044 write Parquet; F-042 only stages the intent). |
| #2 Storage separation (no blob bytes in Postgres) | ✅ | `recipe_snapshot` is a copy of the recipe `definition` JSONB — metadata, not content. No file bytes are stored in Postgres. |
| #3 Schema frozen post-publish | ✅ | `recipe_snapshot = copy(recipe.definition)` at INSERT time. Immutable from this point. F-040 freeze guard (post H1 fix) locks the recipe once a `status != 'failed'` dataset row exists — failed rows represent uncommitted attempts and do not trigger the freeze. |
| #4 LLM through gateway | ✅ N/A | No LLM calls in this sprint. |
| #5 Async SQLAlchemy | ✅ | All session operations use `AsyncSession`; no `session.query()` patterns; `await session.flush()`, `await session.commit()` throughout. Post-commit updates use direct `update(Dataset).where(...).values(...)` statements with captured `dataset_id` local (M2 pattern) — no ORM attribute access after commit. |
| #6 OpenAPI ↔ TS type sync | ✅ | `make codegen` runs after route registration; `packages/api-types/openapi.json` diff committed in same commit. |

---

STATUS: REVISION 2 — awaiting reviewer Mode A round 2
