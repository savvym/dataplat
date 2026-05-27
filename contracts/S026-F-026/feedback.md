# S026-F-026 — LanceChunksIOManager Row Mode — Mode A Review

**Status:** CHANGES_REQUESTED  
**Date:** 2026-05-27  
**Reviewer:** Mode A

---

## Verdict

**CHANGES_REQUESTED** — one HIGH finding must be resolved before implementation. One MEDIUM issue requires a concrete fix in the agreed.md verification plan. One NIT is advisory.

---

## Confirmed Sound

The following aspects were verified and require no changes:

- **Dagster 1.11.16 API availability** (live container exec): `OutputContext.partition_key`, `OutputContext.has_partition_key`, `OutputContext.asset_key`, `OutputContext.add_output_metadata()` all confirmed present.
- **`io_manager_key` routing** (D9): when `io_manager_key` is set on `@asset(...)`, Dagster passes the return value to `handle_output(obj, context)`. Switching `chunks` from returning `MaterializeResult` to returning `list[dict]` is correct. `MaterializeResult` and `MetadataValue` imports remain needed by `extract_mineru`.
- **`context.asset_key.path[-1]`** for `producer_asset` (D6): confirmed valid `OutputContext` attribute.
- **`context.add_output_metadata()` inside `handle_output()`** (R2): known-working pattern in Dagster 1.11.16; risk assessed correctly as low.
- **`lancedb` API** (D2): `lancedb.connect()` / `create_table(exist_ok=True)` / `table.delete()` / `table.add()` is the correct established pattern matching F-025 usage. `lance.dataset()` approach from design doc §8.2 pseudocode was rightly superseded.
- **D11 empty-list early return**: correct safety guard; skip both delete and add when `obj` is empty.
- **D10 dead code retention**: acceptable scope discipline; the comment approach avoids widening scope to update `test_chunker.py`.
- **D7 column-mode deferral**: row-mode-only implementation with a TODO comment deferring Postgres category lookup to F-028 is correct.
- **Invariant compliance §5**: all 6 invariants verified as non-violated. No lineage skipped (Lance is not a lineage-tracked commit store). No blobs in Postgres. No schema change to `CHUNKS_SCHEMA`. No direct LLM calls. IO manager lives in `dagster/`, not `apps/api/` — sync psycopg2 prohibition does not apply (and no psycopg2 is used here anyway). No OpenAPI change.
- **V5 second backfill triggering pattern**: `POST /api/runs {"asset": "chunks", "source_ids": [CH_SRC_ID]}` after `CH_BF_STATUS = COMPLETED_SUCCESS` — correct; no race risk.
- **V6 duplicate chunk_id snippet**: logic is correct.
- **`Definitions(resources={"lance_chunks_io": ...})` wiring**: correct Dagster pattern for making an IO manager available to assets.

---

## Findings

### C1 — HIGH — R1 guard uses wrong access pattern; raises before guard executes

**Location:** proposed.md §6 "R1 — Mitigation" paragraph:

> In `handle_output()`, raise `ValueError("LanceChunksIOManager requires a partitioned asset; context.partition_key is not set")` if `context.partition_key` **is falsy**.

**Problem:** `OutputContext.partition_key` in Dagster 1.11.16 does **not** return `None` or `""` when the asset has no partition. It raises `DagsterInvariantViolationError` unconditionally. The guard `if not context.partition_key` therefore never executes — the exception fires on the attribute access itself, producing a confusing Dagster-internal traceback instead of the intended descriptive `ValueError`.

Confirmed in live container:
```
>>> oc = OutputContext.__new__(OutputContext)
>>> oc._partition_key = None
>>> oc.partition_key   # raises DagsterInvariantViolationError
```

**Required fix:** Use `context.has_partition_key` (confirmed available in Dagster 1.11.16 `OutputContext`) as the guard, then access `context.partition_key` only after:

```python
# WRONG — partition_key raises before this guard can fire:
if not context.partition_key:
    raise ValueError("...")

# CORRECT:
if not context.has_partition_key:
    raise ValueError(
        "LanceChunksIOManager requires a partitioned asset; "
        "context.has_partition_key is False"
    )
source_id = int(context.partition_key.removeprefix("src_"))
```

The agreed.md must explicitly specify this two-line guard pattern in the `handle_output()` implementation description.

---

### C2 — MEDIUM — V5 CH_COUNT1 capture cannot use "the same snippet used in V1"

**Location:** proposed.md §4 "V5" description:

> The `CH_COUNT1` value is extracted by running **the same lancedb Python snippet used in V1** and capturing its integer output.

**Problem:** V1's snippet (checks.sh line 1969) prints:
```
  V1 OK: 7 chunk rows written for source_id=42
```
This is a human-readable sentence, not a bare integer. Shell command substitution `CH_COUNT1=$(docker ... python -c "...")` captures the full string `  V1 OK: 7 chunk rows written for source_id=42`. Any subsequent integer comparison (`[ "$CH_COUNT2" -eq "$CH_COUNT1" ]` or arithmetic expansion) will fail with a "bad variable" / "integer expression expected" error in bash.

**Required fix:** V5 must use a **dedicated** Python snippet that prints only the integer count on stdout. The agreed.md verification plan and the `checks.sh` diff must show this snippet explicitly — it must **not** reference "the V1 snippet". Example:

```python
import lancedb, os, sys
src_id = int(os.environ['SRC_ID'])
db = lancedb.connect('s3://lance/chunks', storage_options={
    'aws_access_key_id': os.environ['S3_USER'],
    'aws_secret_access_key': os.environ['S3_PASS'],
    'endpoint': 'http://minio:9000',
    'aws_region': 'us-east-1',
    'allow_http': 'true',
})
t = db.open_table('chunks')
n = t.count_rows(f"source_id = {src_id} AND producer_asset = 'chunks'")
print(n)
sys.exit(0)
```

Then in shell:
```bash
CH_COUNT1=$(docker compose ... python -c "...snippet above...")
# ... trigger second backfill, poll ...
CH_COUNT2=$(docker compose ... python -c "...snippet above...")
[ "$CH_COUNT2" -eq "$CH_COUNT1" ] || { echo "FAIL V5: ..."; exit 1; }
echo "  V5 OK: idempotent row count ${CH_COUNT1} == ${CH_COUNT2}"
```

---

### C3 — NIT — §2 table omits explicit chunker.py import list for lance_io_manager.py

**Location:** proposed.md §2, row for `dagster/dagster_platform/lance_io_manager.py`:

> **Purpose:** `LanceChunksIOManager` class with row-mode `handle_output()` and a `NotImplementedError` `load_input()`

**Issue:** D1 states "The IO manager imports from `chunker` (for `CHUNKS_SCHEMA` and `build_lance_storage_options()`)". This is not reflected in the §2 table. An implementer reading only §2 might re-define these inline or import incompletely, creating drift from the source of truth.

**Suggested fix:** Extend the Purpose cell to:
> `LanceChunksIOManager` class with row-mode `handle_output()` and a `NotImplementedError` `load_input()`; imports `CHUNKS_SCHEMA` and `build_lance_storage_options` from `chunker.py`

This is advisory — no re-review required; the implementer may resolve this inline while addressing C1 and C2.

---

## What is needed before agreed.md

1. **C1** — Revise the `handle_output()` implementation description (D5 / R1) to specify the `has_partition_key` guard pattern explicitly.
2. **C2** — Replace "the same snippet used in V1" with the full dedicated count-only snippet in §4 V5, and show the shell variable capture and integer comparison idiom.
3. **C3** — (Optional) Update §2 Purpose for `lance_io_manager.py` to name `CHUNKS_SCHEMA` and `build_lance_storage_options` as explicit imports.

No architectural changes are required. The design is sound; these are precision gaps in the implementation specification.
