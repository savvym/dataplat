# S042-F-042 Mode A Review — Feedback

**Reviewer**: Mode A (pre-implementation)  
**Date**: 2026-06-03  
**Reviewed**: `contracts/S042-F-042/proposed.md`  
**Verdict at top (required by CLAUDE.md):**

---

## CHANGES_REQUESTED

Two HIGH findings are blocking. Four MEDIUM/LOW findings must be resolved in the agreed.md before implementation starts. NITs are non-blocking but should be corrected in the agreed.md for accuracy.

---

## Findings

---

### H1 — HIGH | F-040 freeze guard: `status='failed'` rows MUST NOT block recipe edits — fix is REQUIRED in this sprint, not optional

**Location**: proposed.md §4 Step 6 comment + §9 R2; recipes.py lines 197–206

**Issue**: The proposed.md is internally contradictory on this point. Step 6's comment says:

> "This is intentional: the user triggered materialization and should not silently edit the recipe mid-flight."

R2 says:

> "Recommendation: Scope F-040 freeze guard fix into this sprint (one-line WHERE change in recipes.py)."

These two statements are in direct conflict. The reviewer resolves the conflict: **R2's recommendation is correct; Step 6's "intentional" framing is wrong.**

Tracing through the current `recipes.py` (lines 197–206):
```python
exists_result = await session.execute(
    select(exists().where(Dataset.recipe_id == recipe.id))
)
dataset_exists = exists_result.scalar_one()
if dataset_exists:
    raise HTTPException(status_code=409, detail="Recipe is locked: ...")
```
This check is blind to `status`. After F-042 lands with the tombstone approach, a user who calls `POST /api/datasets/{recipe_id}/materialize`, gets a 503 (Dagster down), and then wants to fix their recipe and retry, will receive a 409 from `PUT /api/recipes/{id}` forever. The only escape is direct DB surgery.

Invariant #3 says: "Once a Silver/Gold equivalent (a **materialized Dataset**) is committed." A `status='failed'` row is not a materialized dataset — no Parquet has been written, no data published. Locking the recipe based on a failed attempt contradicts invariant #3's intent. The design doc §11.7 invariant #3 explicitly says "once a Silver/Gold equivalent is **committed**" — a failed attempt is not committed output.

The "mid-flight" argument (Step 6 comment) does apply to `status='pending'` and `status='running'` (the recipe is actively being used and must not be edited). It does not apply to `status='failed'`.

**Required change**: The fix must be included in this sprint's scope. It is one line in `recipes.py`:

```python
# Before (current):
select(exists().where(Dataset.recipe_id == recipe.id))

# After (required):
select(exists().where(Dataset.recipe_id == recipe.id)
               .where(Dataset.status != 'failed'))
```

This change must appear in the agreed.md file list under §3.2 Modified files, with the test `test_freeze_guard_excludes_failed_row` added to `test_datasets_materialize.py`.

---

### H2 — HIGH | "The `@asset` decorator is immutable post-F-042" claim is incorrect — it will mislead the F-043 implementer

**Location**: proposed.md §6, paragraph starting "**What F-043 changes**"

> "**What F-043 changes**: only the function body and the `return` statement. The `@asset` decorator, `partitions_def`, and asset key are immutable post-F-042."

This is wrong for `io_manager_key`. The design doc §8.1 explicitly lists:

> "`HFDatasetIOManager` | dataset | MinIO | writes parquet + README + recipe.json"

F-043 implements the `sft_synthesis_qa` materializer. Its output (Parquet + metadata) must be written through `HFDatasetIOManager`. Looking at the established codebase pattern:
- `chunks` (line 158): `@asset(partitions_def=..., io_manager_key="lance_chunks_io", ...)`
- `attr_quality` (line 213): `@asset(partitions_def=..., io_manager_key="lance_chunks_io", ...)`

F-043's `dataset` asset will return data that `HFDatasetIOManager` must process. Therefore:
1. F-043 MUST add `io_manager_key="hf_dataset_io"` to the `@asset` decorator.
2. F-043 MUST add `HFDatasetIOManager()` to `defs = Definitions(..., resources={"lance_chunks_io": ..., "hf_dataset_io": HFDatasetIOManager()})`.

Neither of these is "only the function body." The claim of decorator immutability will either cause the F-043 implementer to skip `HFDatasetIOManager` entirely (violating design doc §8.1) or create confusion when they discover the constraint must be violated.

**Required change**: Replace the "immutable decorator" language with the accurate statement:

> "**What F-043 changes**: the function body, the `return` type/value, `io_manager_key` (to be added as `io_manager_key='hf_dataset_io'`), and the `Definitions(resources={...})` call site. The `partitions_def=dataset_versions` and asset key `dataset` are frozen and MUST NOT change."

---

### M1 — MEDIUM | Missing test: failed-then-retry versioning is not covered

**Location**: proposed.md §8.1 test plan; tests A4, A6, A7

Tests A4 covers success-success (two 202s → v1 then v2). Tests A6/A7 cover tombstone-on-failure (503 + status='failed'). **No test covers the failure-then-retry path:**

1. `POST /api/datasets/{recipe_id}/materialize` → Dagster fails → 503 → dataset row `status='failed'`, `version_tag='v1'`
2. `POST /api/datasets/{recipe_id}/materialize` again → `COUNT(*) = 1` (includes failed row) → `version_tag='v2'` → 202

This is the primary real-world recovery scenario. Without this test, the count-based versioning is only half-verified. Specifically:
- Does the count correctly include `status='failed'` rows? (It must, to prevent v1 being reused.)
- Does the second call produce `partition_key='ds_{rid}_v2'`, not `ds_{rid}_v1`?
- Does the `uq_dataset_recipe_version` constraint allow v2 alongside the existing v1 (failed) row?

**Required change**: Add test `test_materialize_retry_after_failure` (or rename A6/A7 and add this scenario):
- Setup: seed a `dataset` row with `recipe_id=rid, version_tag='v1', status='failed'`
- Call: `POST /api/datasets/{rid}/materialize` with mocks succeeding
- Assert: 202, `version_tag='v2'`, `partition_key='ds_{rid}_v2'`, both gateway methods called with `'ds_{rid}_v2'`

---

### M2 — MEDIUM | Steps 7/8/9 ORM state after commit: pseudo-SQL notation leaves room for a MissingGreenlet bug

**Location**: proposed.md §4 Steps 7, 8, 9

After Step 6's `await session.commit()`, SQLAlchemy 2.x with `expire_on_commit=True` expires all non-PK attributes on the `dataset` ORM object. The route flow uses pseudo-SQL:

> `→ await session.execute(UPDATE dataset SET status='failed' WHERE id=dataset.id)`

The `dataset.id` access is safe (primary keys are not expired). However, Step 9 uses ORM attribute assignment:

> `dataset.dagster_run_id = backfill_id`  
> `await session.commit()`

Assigning to an expired ORM attribute before calling `commit()` works in SQLAlchemy 2.x (writing to a dirty attribute does not trigger a lazy SELECT), but it is subtle behavior. The agreed.md should specify the exact SQLAlchemy pattern to use for all three post-commit writes to remove ambiguity:

**Steps 7 and 8 error paths** — direct `update()` statement (not ORM assignment):
```python
await session.execute(
    update(Dataset)
    .where(Dataset.id == dataset_id)  # use a captured local int, not dataset.id
    .values(status="failed")
)
await session.commit()
```

**Step 9** — direct `update()` statement for consistency:
```python
await session.execute(
    update(Dataset)
    .where(Dataset.id == dataset_id)
    .values(dagster_run_id=backfill_id)
)
await session.commit()
```

Capturing `dataset_id = dataset.id` immediately after Step 5's flush (before the Step 6 commit) makes all post-commit writes safe without relying on the ORM object state.

---

### L1 — LOW | Step 6 comment must be updated once H1 is accepted

**Location**: proposed.md §4 Step 6 comment block

The comment currently says:
> "Rationale: a DELETE after commit risks losing the audit trail; 'failed' status is visible to F-044/F-045 and **prevents F-040's freeze guard from triggering spuriously** (the freeze guard checks for ANY dataset row referencing the recipe — F-040 line 197-206 in recipes.py). Note: once the dataset row exists, F-040 will block recipe edits. **This is intentional: the user triggered materialization and should not silently edit the recipe mid-flight.**"

After H1 is accepted, the last sentence is incorrect (the freeze guard will be updated to exclude `status='failed'`). The comment must be rewritten to accurately describe the behavior:
- `status='pending'` and `status='running'` rows WILL block edits (recipe is actively in use)
- `status='failed'` rows will NOT block edits (freeze guard excludes them per H1 fix)
- `status='done'` rows WILL block edits (invariant #3)

---

### L2 — LOW | Tombstone UPDATE failure leaves a stuck `status='pending'` row — unacknowledged residual risk

**Location**: proposed.md §4, Steps 7–8 error handling; §9 R1

If the `UPDATE dataset SET status='failed'` at Step 7 or Step 8 itself fails (e.g., the DB connection drops between the Step 6 commit and the error-path UPDATE), the row remains `status='pending'` with `dagster_run_id=NULL` indefinitely. No future webhook (F-050) or poll (F-048) will transition it, because F-050 correlates by `dagster_run_id` — which is NULL.

The proposed.md does not acknowledge this residual risk. **Required addition**: a comment in §4 (or §9 R1) noting:
> "Residual risk: if the tombstone UPDATE itself fails (DB dropout between Steps 6 and 7), a `status='pending'` row with `dagster_run_id=NULL` persists. This is recoverable by ops via direct SQL (`UPDATE dataset SET status='failed' WHERE dagster_run_id IS NULL AND status='pending'`). This risk is accepted as acceptable since it requires a double DB failure (commit succeeds, next execute fails) and the row is inert (no data written to MinIO)."

---

### L3 — LOW | R2 is answered by H1; the proposed.md should close the open question explicitly

**Location**: proposed.md §9 R2

R2 ends with "Reviewer question: Should the freeze guard in F-040 be updated to filter on `status != 'failed'`?"

H1 above answers this definitively: **Yes, it must be updated.** The agreed.md should close R2 with "RESOLVED: fix is mandatory, scoped to this sprint" so there is no ambiguity about whether the implementer should do it.

---

### NIT-1 — NIT | Design doc §3 / §9.1 path notation is ambiguous; proposed.md is correctly following feature_list.json

**Location**: proposed.md §3.1; docs/data_platform_design.md §3 line 212, §9.1 line 842

- Design doc §3 line 212: `POST /api/datasets/materialize (recipe_id)` — recipe_id appears to be a body param
- Design doc §9.1 line 842: `POST /datasets/{id}/materialize` — `{id}` is ambiguous (recipe ID or dataset ID?)
- Feature list F-042: `POST /api/datasets/{recipe_id}/materialize` — explicit path param

Proposed.md correctly follows the feature list. No change needed. Flagged for completeness only.

---

### NIT-2 — NIT | Gateway module docstring line reference is slightly off

**Location**: proposed.md §7.3

> "Add to the module-level docstring (gateway.py lines 11-23)"

The methods table in `gateway.py` actually spans lines 13–24. Minor but should be accurate in the agreed.md.

---

### NIT-3 — NIT | Test A4 must clarify that the mock session commits before the second COUNT(*)

**Location**: proposed.md §8.1 test A4

Test A4: "Call materialize twice on the same recipe." For this to produce v1 then v2, the mock session must have committed (and made visible) the first `dataset` row before the second call's `COUNT(*)` runs. The test description should specify: "session mock is stateful; first call's INSERT is committed before second call executes" — otherwise a naive in-memory mock may return `count=0` for both calls, causing the second INSERT to attempt `version_tag='v1'` → IntegrityError instead of the expected `version_tag='v2'`.

---

## Hard Invariant Checklist (Reviewer Assessment)

| # | Invariant | Assessment |
|---|---|---|
| **#1** Lineage (parents[], processor, config hash) | **✅ N/A** — Correctly identified as N/A. F-042 creates a dataset row; no asset materialization output is committed. Lineage belongs to F-043/F-044. |
| **#2** Storage separation (no blob bytes in Postgres) | **✅ Compliant** — `recipe_snapshot` is a JSONB copy of `recipe.definition` (metadata), not file content. `hf_repo_uri` is a URI string, not bytes. |
| **#3** Schema frozen post-publish | **⚠️ Partially compliant** — `recipe_snapshot = copy(recipe.definition)` at INSERT time is correct. But the F-040 freeze guard currently blocks on failed rows (violates the spirit of §3 "once a Silver/Gold equivalent is committed"). Resolved by H1. |
| **#4** LLM through gateway | **✅ N/A** — No LLM calls in this sprint. |
| **#5** Async SQLAlchemy | **✅ Compliant by design** — All session operations use `AsyncSession`; `await session.flush()`, `await session.commit()`, `select()` throughout. See M2 for a nuance on the post-commit UPDATE pattern. |
| **#6** OpenAPI ↔ TS type sync | **✅ Compliant** — `make codegen` called after router registration, diff committed in same commit. Explicitly listed in §5.3 and §3.2. |

---

## Scope Discipline Assessment

The `dataset` stub in `definitions.py` (§6) is **justified** and is **not** scope creep into F-043. Without the stub:
- `gateway.launch_dataset_backfill()` would receive `InvalidSubsetError` or `PartitionSetNotFoundError` from Dagster (the asset does not exist)
- V3 integration check ("Dagster shows a backfill launched for the `dataset` asset") cannot be verified live

The stub is ~10 lines, forward-compatible (`partitions_def=dataset_versions`, asset key `dataset`), and explicitly designed to be replaced by F-043. The risk (a user accidentally triggering the stub from the Dagster UI) is a no-op (no MinIO write, no Postgres update) — R5 correctly assesses this as acceptable.

**However**: the stub design has a flaw captured in H2 — the "immutable decorator" language must be corrected to allow F-043 to add `io_manager_key="hf_dataset_io"`.

---

## Concurrency/Race Safety Assessment

The count-then-INSERT pattern is **correctly guarded** by `uq_dataset_recipe_version`. The proposed.md acknowledges:
> "The count-based version_tag computation is not atomic by itself; the constraint is the hard guarantee."

Race trace:
- Request A: `SELECT COUNT(*) = 0` → `version_tag='v1'` → INSERT → commit (success)
- Request B: `SELECT COUNT(*) = 0` → `version_tag='v1'` → INSERT → commit → **IntegrityError** → 409 ✅

The UNIQUE constraint is the hard guarantee. Test A5 correctly mocks this path. No further hardening needed.

---

## Gateway Methods Assessment

The two new gateway methods (`add_dataset_partition`, `launch_dataset_backfill`) correctly mirror existing patterns:
- Separate named constants (`_ADD_DATASET_PARTITION_MUTATION`, `_LAUNCH_DATASET_BACKFILL_MUTATION`) per the project convention (gateway.py comments at lines 97-99, 155-157, 186-188)
- `DuplicateDynamicPartitionError` treated as idempotent no-op (same as `add_source_partition`)
- All failure typename branches raise `DagsterGatewayError`
- `backfillId` absent/empty raises `DagsterGatewayError`

The mutation for `_ADD_DATASET_PARTITION_MUTATION` is structurally identical to `_ADD_SOURCE_PARTITION_MUTATION` with only the GraphQL operation name differing — correct, since `partitionsDefName` is passed as a variable (`"dataset_versions"`). ✅

---

## `hf_repo_uri` Flush+Update Pattern Safety

The `'__pending__'` placeholder is inserted, then `session.flush()` assigns the identity, then `dataset.hf_repo_uri = f"s3://datasets/{dataset.id}_{version_tag}"` overwrites it, then `session.commit()`. Under Postgres MVCC, no concurrent reader sees `'__pending__'` because the transaction has not committed. This is identical to the `storage_uri` pattern in `sources.py` (lines 279-291). ✅

---

## Owner-scoped 404 Collapse

Confirmed: proposed.md §4 Step 2 uses the same pattern as `recipes.py` (lines 184-195) and `sources.py` (lines 179-186):
```
SELECT recipe WHERE id = recipe_id AND owner_id = current_user.id
```
Both "recipe not found" and "recipe belongs to another user" return 404. No enumeration leak. ✅

---

## Dagster Orphan Partition Analysis

If `add_dataset_partition` (Step 7) succeeds but `launch_dataset_backfill` (Step 8) fails:
- A partition `ds_{rid}_v{n}` exists in Dagster's partition definition
- The dataset row has `status='failed'` in Postgres
- On next retry: `count = 1` (includes failed row) → `version_tag = f'v{n+1}'` → new partition key → `add_dataset_partition('ds_{rid}_v{n+1}')` → new partition, no collision

The stale partition from the failed attempt (`ds_{rid}_v{n}`) remains in Dagster but is harmless: it has no materialization event and will never be targeted by FastAPI again (each retry creates a higher version). `add_dataset_partition` is idempotent, so a future recovery operation that re-registers `ds_{rid}_v{n}` would also be safe. ✅

---

## Summary of Required Changes

| Finding | Impact | Required action |
|---|---|---|
| **H1** | F-040 freeze guard: failed rows must not lock recipe | Add `Dataset.status != 'failed'` to freeze check in `recipes.py`; add test `test_freeze_guard_excludes_failed_row` |
| **H2** | Decorator "immutable" claim misleads F-043 | Correct §6 to say only `partitions_def` and asset key are frozen; `io_manager_key` will be added by F-043 |
| **M1** | Missing test: retry after failed materialization | Add `test_materialize_retry_after_failure` to test suite |
| **M2** | Post-commit ORM safety in error paths | Specify direct `update()` statements for Steps 7/8/9; capture `dataset_id = dataset.id` before Step 6 commit |
| **L1** | Step 6 comment contradicts H1 resolution | Update comment to accurately describe which statuses trigger the freeze |
| **L2** | Stuck `pending` row risk unacknowledged | Add residual-risk note to §4 or §9 R1 |
| **L3** | R2 open question not closed | Resolve R2 as "RESOLVED: mandatory, in-sprint fix" in agreed.md |

---

## CHANGES_REQUESTED

Resolve H1 and H2 (blocking), M1 and M2 (required before agreed.md), L1–L3 (required in spec text), and NITs before creating `agreed.md`. Once the above changes are made and the proposed.md re-submitted, this review expects to issue APPROVED.

---

## Round 2

**Reviewer**: Mode A (pre-implementation, round 2)
**Date**: 2026-06-03
**Based on**: proposed.md Revision 2

---

### Resolution checks for all round-1 findings

**H1 ✓** — All four required sub-items are present:
1. §3.2 Modified files explicitly lists `recipes.py` with the exact one-line change (`select(exists().where(Dataset.recipe_id == recipe.id).where(Dataset.status != 'failed'))`).
2. §4 Step 6 comment block no longer calls the lock "intentional" for failed rows; it now describes the correct per-status behaviour (`status='failed' → recipe is NOT LOCKED`).
3. §8.1 test A8 is `test_freeze_guard_excludes_failed_row` with the prescribed setup and 200-not-409 assertion.
4. §2 Verification Matrix has V4 mapped explicitly to A8 with the freeze-guard scenario.

**H2 ✓** — §6 forward-compatibility table now correctly partitions the decorator into three categories: FROZEN (`partitions_def`, asset key), REPLACEABLE (body, return), and ADDABLE (`io_manager_key`). The prose "What F-043 changes" sentence names `io_manager_key` and the `Definitions(resources={...})` call-site. §10 Out-of-scope table confirms `io_manager_key` belongs to F-043. The old "immutable decorator" language is gone.

**M1 ✓** — Test A9 (`test_materialize_after_failed_retry_increments_version`) is present. Setup seeds a `Dataset` row with `status='failed'`, `version_tag='v1'`; call succeeds; assertions confirm `version_tag='v2'`, `status='pending'` on the new row, failed row intact, and **both gateway methods called with `'ds_{rid}_v2'`** (the specific partition_key assertion required by round 1).

**M2 ✓** — §4 Step 5 captures `dataset_id: int = dataset.id`, `version_tag` and `partition_key` as plain Python locals explicitly before Step 6 `commit()`. Steps 7, 8, and 9 all use direct `update(Dataset).where(Dataset.id == dataset_id).values(...)` statements — no ORM attribute access post-commit. Step 10 return uses captured locals with an explicit "do NOT access dataset.id after Step 6 commit" note.

**L1 ✓** — §4 Step 6 comment block is fully rewritten with the four-state freeze-guard table (`pending` → LOCKED, `running` → LOCKED, `failed` → NOT LOCKED, `done` → LOCKED). No trace of the old "intentional mid-flight" framing.

**L2 ✓** — The residual-risk note appears verbatim in §4 Step 6 (lines explaining the double-DB-failure scenario, the NULL `dagster_run_id` consequence, the ops recovery SQL, and explicit acceptance of the risk). Also repeated in §9 R1 with identical language.

**L3 ✓** — §9 R2 is closed: "RESOLVED: Fix is mandatory, scoped to this sprint." No ambiguity remains.

**NIT-2 ✓** (was N2) — §7.3 docstring line reference corrected to `lines 13–24`.

**NIT-3 ✓** (was N3) — Test A4 now states: "Session mock is stateful: first call's INSERT is committed (visible in the mock DB state) before second call's `COUNT(*)` executes — a naive in-memory mock must reflect the committed row on the second call to return `count=1`."

---

### New-issue scan (regressions / contradictions / scope creep)

No new HIGH or MEDIUM issues were introduced. One NIT observed:

- **NIT-R2-1** (non-blocking): §7.3 says "Update module docstring (gateway.py lines 13–24)" but §3.2 Modified files also says the same thing for gateway.py. Consistent — no conflict. No change needed; recorded for completeness.

The out-of-scope table in §10 is internally consistent: `io_manager_key` deferred to F-043, consistent with §6 ADDABLE row and H2 resolution. No scope creep detected. No hard-invariant regression.

---

VERDICT: APPROVED
