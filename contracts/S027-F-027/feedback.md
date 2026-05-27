# S027-F-027 — Trigger quality tagger — Review Feedback

**Status: CHANGES_REQUESTED**

Two blocking issues and three minor issues. The blocking issues share a common root: the
proposed contract invents an "augmentation row" write model that is incompatible with the
feature spec, F-028, and the design doc. Everything else in the contract (gateway shape,
Literal widening, `all)` ordering, invariants 2/4/5/6) is correct.

---

## Issue 1 — [BLOCKING] D3's "augmentation row" approach contradicts three sources of truth

**What the contract proposes (D2–D3):**  
Insert *new* rows into Lance with `producer_asset = "attr_quality"`, copying every
content field from the source chunk rows and overwriting `attr_quality_score`,
`attr_quality_provider`, plus lineage fields (`augmented_from`, `augmenter_id`,
`augmenter_config_hash`).

**Why it is wrong — three independent sources all mandate column-mode:**

1. **F-027 verification criterion V2** (`spec/feature_list.json`):
   ```
   "After run completes, SELECT attr_quality_score FROM lance_chunks
    WHERE source_id={id} returns non-null float values for all rows"
   ```
   No `producer_asset` filter. "All rows" means the *original*
   `producer_asset='chunks'` rows must have non-null `attr_quality_score`.
   Under D3 those rows still have `attr_quality_score = NULL` → V2 **fails**.

2. **F-028 description + verification** (`spec/feature_list.json`):
   ```json
   "description": "... updates only the attr_quality_score and attr_quality_provider
    columns in Lance (column mode, no row inserts)",
   "verification": [
     "After running quality tagger, all chunk rows for the source have
      attr_quality_score between 0.0 and 1.0",
     "No new rows were inserted into Lance (row count is unchanged)"
   ]
   ```
   D3 doubles the row count for every source — F-028 would fail its V2 on the rows
   F-027 left behind.

3. **Design doc §8.2** (authoritative):
   ```python
   elif category == "tagger":
       # Column mode: only update this tagger's columns
       column_name = f"attr_{asset_name.removeprefix('attr_')}"
       ds.merge_insert(on="chunk_id", when_matched_update_all=[column_name]).execute(obj)
   ```
   Taggers update columns on existing rows; augmenters insert new rows.
   `attr_quality` is a *tagger*, not an augmenter. The `augmented_from` /
   `augmenter_id` / `augmenter_config_hash` fields are the augmenter lineage fields —
   they do not apply here.

**Required fix:** Replace D3 entirely. The `attr_quality` asset must update columns on
existing `producer_asset='chunks'` rows for the given `source_id`. Two acceptable
implementations (both verified against lancedb 0.30.2 in the running container):

*Option A — pure SQL update (simplest, no Python read-back):*
```python
table.update(
    where=f"source_id = {source_id} AND producer_asset = 'chunks'",
    values_sql={
        "attr_quality_score": "LEAST(1.0, CAST(token_count AS FLOAT) / 512.0)",
        "attr_quality_provider": "'length_heuristic'",
    }
)
```
`values_sql` is confirmed present in lancedb 0.30.2; DataFusion evaluates the
expression per-row so no Python iteration is needed.

*Option B — read → compute → merge_insert (matches design doc §8.2 exactly, better
F-028 readiness):*
```python
rows = table.to_arrow(
    filter=f"source_id = {source_id} AND producer_asset = 'chunks'"
)
scored = [
    {
        "chunk_id": r["chunk_id"],
        "attr_quality_score": min(1.0, float(r["token_count"]) / 512.0),
        "attr_quality_provider": "length_heuristic",
    }
    for r in rows.to_pylist()
]
(
    table
    .merge_insert("chunk_id")
    .when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"])
    .execute(scored)
)
```

Either option is acceptable. The key invariant: **zero new rows; two columns updated
on existing `producer_asset='chunks'` rows**. The asset returns `MaterializeResult`
(D4 is otherwise unchanged). `quality_tagger.py` should export the scoring helper
(renamed from `write_quality_scores_to_lance()` to `update_quality_scores_in_lance()`
to signal column mode) with the chosen write implementation.

---

## Issue 2 — [BLOCKING] Proposed verification queries silently rewrite the spec

**In proposed.md §4, V2 and V3:**
```sql
-- Proposed V2
SELECT attr_quality_score FROM chunks
WHERE source_id=<id> AND producer_asset='attr_quality'

-- Proposed V3
SELECT DISTINCT attr_quality_provider FROM chunks
WHERE source_id=<id> AND producer_asset='attr_quality'
```

The contract adds `AND producer_asset='attr_quality'` to both queries. This predicate
does not exist in the feature spec. Its effect is to make D3's augmentation rows satisfy
the check while the actual spec criterion — which queries **all** rows for the source —
continues to fail.

**Required fix:** The `checks.sh` `attr_quality` layer and the §4 verification table
must use the spec-faithful queries:
```sql
-- V2 (correct)
SELECT attr_quality_score FROM chunks WHERE source_id=<id>
-- assert zero rows with attr_quality_score IS NULL

-- V3 (correct)
SELECT DISTINCT attr_quality_provider FROM chunks WHERE source_id=<id>
-- assert exactly one distinct value: 'length_heuristic'
```
After fixing Issue 1, the original `producer_asset='chunks'` rows will carry the scores
and these queries will pass without the extra filter.

---

## Issue 3 — [REQUIRED] D2 must be reworded to match the corrected write approach

D2 currently argues at length why `LanceChunksIOManager` is not used and why D3
(delete+reinsert of new rows) is needed. Once D3 is replaced by column-mode update,
this argument no longer holds. The revised D2 should state:

- The `attr_quality` asset writes to Lance directly (bypasses `LanceChunksIOManager`)
  because it performs a **column-mode update** on existing rows, which `LanceChunksIOManager`
  does not support (column mode is a F-028 TODO in `lance_io_manager.py`, D7).
- It calls `update_quality_scores_in_lance()` in `quality_tagger.py` which issues
  `table.update()` (Option A) or `merge_insert` (Option B) scoped to
  `source_id = {id} AND producer_asset = 'chunks'`.
- Idempotency is guaranteed because re-running overwrites the same two columns on the
  same rows — no rows are created or deleted.

The D5 section on `to_arrow()` reads is still correct for Option B but can be removed
if Option A is chosen.

---

## Issue 4 — [MINOR] `routers/runs.py`: `else` branch comment becomes wrong after Literal widening

Current code (line 150):
```python
else:  # body.asset == "chunks" — guaranteed by RunCreate.asset Literal validation
```

After widening the Literal to three values and adding the `elif body.asset ==
"attr_quality":` branch, the `else` still fires for `"chunks"` — but the comment's
"guaranteed" claim is now false (a future bug could reach `else` with an unexpected
value).

**Required fix:** The contract must specify that the implementer changes `else` to an
explicit `elif` and adds a defensive `else`:

```python
elif body.asset == "attr_quality":
    ...
    kind = "attr_quality"
    asset_keys = ["attr_quality"]
elif body.asset == "chunks":
    ...
    kind = "chunk"
    asset_keys = ["chunks"]
else:
    # Should be unreachable: Pydantic Literal guards all entry points.
    raise ValueError(f"Unhandled asset type: {body.asset!r}")
```

---

## Issue 5 — [MINOR] `checks.sh` V4 must document the stub deviation from the spec

F-027 V3 in the feature spec reads: *"attr_quality_provider is set to the LLM model
name used (e.g., 'gpt-4o-mini')"*. The stub uses `"length_heuristic"`, which is not
an LLM model name — this deviation is correct and justified by invariant #4 (no LLM
gateway yet). However, the `checks.sh` assertion comment must make this explicit so
the CI signal remains trustworthy for future reviewers:

```bash
# V4: provider = 'length_heuristic' (stub, F-027 only).
# F-028 will replace with the real LLM model name once the gateway exists.
assert_provider_eq "length_heuristic"
```

Without this comment, a future reviewer may flag the `"length_heuristic"` value as
a regression when F-028 changes it.

---

## Accepted (no changes needed)

| Item | Decision | Reason |
|---|---|---|
| D1 — stub scorer formula | Accepted | Correct arithmetic; invariant #4 compliant; bounded [0, 1] |
| D6 — `launch_attr_quality_backfill()` mirrors `launch_chunks_backfill()` | Accepted | Structure is correct; only `assetSelection[0].path` and `title` differ |
| D7 — Literal widening in `schemas/runs.py` | Accepted | Correct Pydantic pattern |
| D4 — returns `MaterializeResult`, no `io_manager_key` | Accepted | Correct given direct Lance write |
| D8 — no Makefile, manual `openapi.json` edit | Accepted | Consistent with project state; `checks.sh contract` layer guards for this |
| `all)` chain: `bash "$0" attr_quality` after `bash "$0" chunks` | Accepted | Ordering is correct (chunks is prerequisite) |
| Invariant #2 (storage separation) | Compliant | No blob bytes in Postgres |
| Invariant #4 (no LLM calls) | Compliant | Pure arithmetic stub |
| Invariant #5 (async SQLAlchemy) | Compliant | New API code uses existing async patterns |
| Invariant #6 (OpenAPI ↔ TS sync) | Compliant | Manual edit + CI `contract` check guarded by `[[ -f Makefile ]]` exit-0 |
| R3 — `merge_insert()` vs. delete+reinsert | Deferred | Still deferred; either Option A (`table.update`) or Option B (`merge_insert`) is acceptable as long as no new rows are created |

---

## Summary of required changes

To reach APPROVED the implementer must:

1. **Replace D3** with column-mode update (Option A or Option B above). Delete all
   content referencing `producer_asset="attr_quality"` rows, `augmented_from`,
   `augmenter_id`, `augmenter_config_hash` from `quality_tagger.py` design.
2. **Fix §4 verification queries** — remove `AND producer_asset='attr_quality'` from V2
   and V3 in both the proposed contract and the `checks.sh` implementation.
3. **Reword D2** to explain column-mode write; remove the delete+reinsert argument.
4. **Fix `routers/runs.py` dispatch** — `elif body.asset == "chunks":` (explicit) +
   defensive `else: raise ValueError(...)`.
5. **Add stub-deviation comment** to `checks.sh` V4 assertion.

Issues 1–3 are interdependent (they all stem from the same D3 error). Fix 1 and the
rest follow mechanically.
