# S031-F-031 — `LanceChunksIOManager` column mode: feedback.md

Reviewer: Mode A (pre-implementation contract review)  
Sprint: S031-F-031  
Proposed: `contracts/S031-F-031/proposed.md`  
Verdict: **CHANGES_REQUESTED**

---

## Summary

The design is substantially sound.  D2 (probe-before-commit), D3b (read-modify-write as safe
primary), D7 (CHUNKS_SCHEMA-typed merge), D10 (WHERE scoped to `producer_asset='chunks'`),
and the verification plan all check out.  The V-criterion mapping is complete and the unit-test
stub in §4.2 correctly exercises both `merge_insert` and the absence of `delete`.

Five issues must be resolved before the contract is approved for implementation.

---

## Findings

### F1 — HIGH: Concurrent tagger execution race condition absent from risk table

**Location:** §3 D3 / §5 risk table  
**Problem:**  
D3b uses a read-modify-write (RMW) pattern inside `_column_mode_write()`:

1. Read full existing rows from Lance.
2. Merge incoming tagger columns in-memory.
3. Write back via `merge_insert("chunk_id")`.

This RMW is **not atomic**.  If `attr_quality` and `attr_lang` are materialised
concurrently for the same `source_id` (e.g., two backfill runs triggered within the same
Dagster daemon cycle, or a manual retrigger overlapping with a scheduled run):

```
T0:  attr_quality IOManager reads:  {chunk_id="c1", attr_quality_score=None, attr_lang_code=None}
T0:  attr_lang    IOManager reads:  {chunk_id="c1", attr_quality_score=None, attr_lang_code=None}
T1:  attr_quality writes (merged):  {chunk_id="c1", attr_quality_score=0.8,  attr_lang_code=None}
T1:  attr_lang    writes (merged):  {chunk_id="c1", attr_quality_score=None, attr_lang_code="en"}
     ↑ SILENT DATA LOSS: quality score destroyed because lang tagger read before quality wrote
```

This is a **regression** relative to the current implementation.  F-028 and F-029 deliberately
used per-row `table.update(where=f"chunk_id = '{chunk_id}'", values={tagger_columns_only})`
precisely to avoid this class of problem.  The prior sprint review (S028-F-028 review-final.md
H1) explicitly rejected `when_matched_update_all()` for a full-row input table; D3b
reintroduces a structurally equivalent hazard at the IOManager level.

The risk table R1–R7 covers cross-source collision (R1), schema type drift (R2), compaction
latency (R3), double-I/O for minhash (R4), orphaned chunk_ids (R5), CI import speed (R6),
and MaterializeResult import (R7).  **The RMW concurrency hazard is entirely absent.**

Note: if the D2 probe exits 0 (D3a viable), D3a is naturally concurrency-safe because a
partial-column input table only overwrites the columns present in it.  This is an additional
argument for choosing D3a as the primary path if the probe confirms safety.

**Required fix:**  
Add **R8** to the risk table:

> | R8 | Concurrent IOManager execution for the same source_id: two taggers both read existing
> rows before either writes, causing the second `merge_insert` to overwrite the first
> tagger's new values with the stale pre-write snapshot | Low (sequential execution is the
> default Dagster scheduler behaviour) | High (silent data loss, no error raised) | Sequential
> execution assumption: document in agreed.md that the three tagger assets MUST NOT be
> materialised for the same partition in overlapping Dagster runs.  If D2 probe exits 0, choose
> D3a (partial-column merge_insert) as primary — D3a is naturally concurrent-safe.

---

### F2 — MEDIUM: D6 claims `"mode": "column_skipped"` but §2.1 does not update the early-return

**Location:** §2.1 / §3 D6  
**Problem:**  
D6 states:

> "LanceChunksIOManager.handle_output() receives obj = [] and takes the existing D11
> early-return path with metadata `{"row_count": 0, "mode": "column_skipped"}`."

The current D11 early-return in `lance_io_manager.py` (line 49) is:

```python
context.add_output_metadata({"row_count": 0, "mode": "row_skipped"})
```

This emits `"row_skipped"` unconditionally.  Section §2.1 describes the new dispatch logic
as being added **after** the empty-list early-return ("New logic (after the empty-list
early-return and partition-key guard)"), meaning the early-return fires before the mode
dispatch.  As written, an empty column-mode asset return will therefore always emit
`"mode": "row_skipped"`, not `"column_skipped"`.

The implementer needs explicit direction.  There are two valid resolutions:

- **Option A (mode-aware early-return):** In §2.1, describe making the early-return
  mode-aware by checking `producer_asset` *before* the guard, so it can emit
  `"column_skipped"` vs `"row_skipped"` correctly.  This requires the `producer_asset`
  derivation to move above the empty-list check.

- **Option B (accept "row_skipped" for empty column-mode):** Correct D6 to state that
  empty returns from column-mode assets also emit `"mode": "row_skipped"` (or omit the
  `mode` key claim entirely).  Less informative but requires no extra code change.

**Required fix:** Choose one option and make the §2.1 and D6 descriptions consistent.

---

### F3 — MEDIUM: `attr_col_isolation` layer not added to `all)` in `checks.sh`

**Location:** §2.7 / `verify/checks.sh` line 1343  
**Problem:**  
Section §2.7 says: "Add a new layer `attr_col_isolation` (see §4).  No changes to existing
layers."  The current `all)` case in `checks.sh` ends at line 1342:

```bash
bash "$0" attr_minhash  # F-030
;;
```

`attr_col_isolation` is not in this list.  Running `bash verify/checks.sh all` (the standard
CI invocation) will therefore **skip the column isolation test entirely** unless the layer is
explicitly added to `all)`.

The wording "No changes to existing layers" is technically satisfied (the existing layer
bodies are unchanged), but the natural reading of §2.7's intent is that CI will exercise the
new check.

**Required fix:**  
In §2.7, add the explicit instruction that the implementer must also add:

```bash
bash "$0" attr_col_isolation  # F-031
```

to the `all)` case in `checks.sh` (after `attr_minhash`).

---

### F4 — NIT: `add_output_metadata()` in §2.5 shows bare Python values without `MetadataValue` wrappers

**Location:** §3 D5  
**Problem:**  
D5 shows:

```python
context.add_output_metadata({"source_id": ..., "chunk_count": len(rows)})
```

The `chunks` asset (definitions.py lines 199–205) established the canonical pattern for
this IO manager:

```python
context.add_output_metadata({
    "source_id": MetadataValue.int(source_id),
    "chunk_count": MetadataValue.int(len(rows)),
    "text_length": MetadataValue.int(len(text)),
})
```

Without the wrappers the values are plain Python ints, which Dagster accepts but renders
less richly in the asset catalogue.  Consistency with the existing pattern is preferred.

**Required fix:** Update D5 to show `MetadataValue.int()` wrappers, and note that
`MetadataValue` is already imported in `definitions.py`.

---

### F5 — NIT: isolation_check.py float comparison uses `1e-6` which is fragile for `pa.float32()` round-trips

**Location:** §4.4, `isolation_check.py` line 585  
**Problem:**

```python
assert abs(r["attr_quality_score"] - snapshot[cid]) < 1e-6
```

`attr_quality_score` is stored as `pa.float32()`.  The value travels through:
`float32` → Python float (Lance) → `json.dump()` → `json.load()` → Python float comparison.  
Float32 has approximately 7 decimal digits of precision.  For quality scores near 1.0,
the absolute error from the float32→float64→JSON→float64 round-trip can exceed 1e-6
(e.g., `float32(0.9999999)` ≈ `1.0000001192` as float64, a delta of ~1.2e-7 which is fine,
but near the boundary for values like `float32(0.123456789)` the JSON-round-trip error can
be larger).

**Required fix:** Replace with `math.isclose` and add the import:

```python
import math
...
assert math.isclose(r["attr_quality_score"], snapshot[cid], rel_tol=1e-5), \
    f"FAIL post_lang: attr_quality_score changed for chunk_id={cid}: ..."
```

`rel_tol=1e-5` (10 ULPs for float32) is tight enough to catch actual column destruction
while immune to round-trip noise.

---

## Items confirmed correct

The following checklist items from the review prompt were evaluated and found satisfactory;
no changes are requested:

- **D1 dispatch** (`producer_asset == "chunks"` binary switch): Sound for MVP scope;
  self-extending for future taggers without IOManager changes. ✓
- **D2 probe strategy**: Correct gating of D3a vs D3b; probe script is self-contained and
  correctly tests the partial-column preservation question. ✓
- **D3b correctness** (full-row read-modify-write): Unconditionally safe against
  `when_matched_update_all()` full-row semantics given the merged rows carry all 24 fields.
  V-criterion #2 satisfied. ✓
- **D4 compute_* functions** (no-write pattern): Clean separation of domain logic from IO
  concern; consistent with no-Dagster-imports guarantee. ✓
- **D7 CHUNKS_SCHEMA usage** in `pa.Table.from_pylist()`: Correct single source of truth
  for type fidelity on write. ✓
- **D8 keep deprecated update_*_in_lance()**: Avoids breaking the existing test suite
  during the sprint; cleanup deferred cleanly. ✓
- **D10 WHERE clause** (`producer_asset = 'chunks'` fixed): Correctly reads the rows to
  update (not the tagger's own hypothetical rows); `merge_insert` matching on `chunk_id`
  is unambiguous. ✓
- **Hard invariants**: No API route, no Postgres migration, no LLM SDK import outside
  gateway, no `make codegen`. ✓
- **V-criteria mapping**: Both V #1 (column isolation) and V #2 (merge_insert, not delete)
  are fully addressed by the §4 plan. ✓
- **R1–R7 (excluding the missing R8)**: Coverage of cross-source collision, schema type
  drift, compaction latency, double-I/O for minhash, orphaned chunk_ids. ✓

---

## Required changes before APPROVED

| # | Severity | Section | Action |
|---|---|---|---|
| F1 | HIGH | §5 | Add R8 documenting the RMW concurrent-write hazard; note D3a as concurrency-safe if D2 probe exits 0 |
| F2 | MEDIUM | §2.1 + §3 D6 | Align early-return implementation description with D6 metadata claim (choose Option A or B and update both sections) |
| F3 | MEDIUM | §2.7 | Explicitly instruct implementer to add `bash "$0" attr_col_isolation` to `all)` case in `checks.sh` |
| F4 | NIT | §3 D5 | Add `MetadataValue.int()` wrappers to `add_output_metadata()` example |
| F5 | NIT | §4.4 | Replace `abs(...) < 1e-6` with `math.isclose(..., rel_tol=1e-5)` in isolation_check.py |

Address F1–F3 in the updated proposed.md and resubmit for a final Mode A pass or proceed
directly to agreed.md if changes are purely additive.
