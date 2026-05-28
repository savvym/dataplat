# S030-F-030 — `attr_minhash` tagger: feedback.md

Reviewer: independent (Mode A — contract review)  
Date: 2026-05-28  
Reviewed against: `proposed.md`, `lang_tagger.py`, `quality_tagger.py`, `chunker.py`,
`schemas/runs.py`, `routers/runs.py`, `gateway.py`, `Dockerfile`, `checks.sh`,
`feature_list.json`, `CLAUDE.md`

---

## Summary verdict

**CHANGES_REQUESTED** — two HIGH findings (F1, F2) must be resolved before implementation begins.
Three MEDIUM findings should be addressed in the same revision. Two NITs are optional.

---

## Findings

### F1 — HIGH: `where_clause` missing `AND producer_asset = 'chunks'`

**Location**: §2.1, `update_minhash_in_lance` description, and the prose
`where_clause = f"source_id = {source_id}"`.

**Problem**: Both existing taggers filter exclusively on `producer_asset = 'chunks'`:

- `lang_tagger.py` line 139:
  `where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"`
- `quality_tagger.py` line 169:
  `where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"`

The proposed contract for `minhash_tagger.py` omits this predicate. If future sprints
introduce rows with a different `producer_asset` value for the same `source_id`, the
tagger would erroneously process those rows and overwrite columns that belong to a
different asset type. Even today the omission is a latent correctness bug and deviates
from the established tagger contract without justification.

**Required fix**: Change every occurrence of `f"source_id = {source_id}"` in §2.1 to
`f"source_id = {source_id} AND producer_asset = 'chunks'"`. Apply the same predicate
to the `count_rows` call that computes the return value (match the `lang_tagger.py`
pattern exactly: `row_count = table.count_rows(where_clause)`).

---

### F2 — HIGH: `_compute_signature` return value spec is self-contradictory

**Location**: §2.1 (`_compute_signature` bullet) vs. D8.

**§2.1 says**:
> "returns `list(minhash.hashvalues)` as Python `list[int]` (uint64 elements,
> matching `pa.list_(pa.uint64())`)"

**D8 says**:
> "`_compute_signature` returns `list(minhash.hashvalues.tolist())` — a plain Python
> list of integers."

These are not equivalent:

- `list(minhash.hashvalues)` → `list[numpy.uint64]` (NumPy scalar elements, NOT plain
  Python `int`).
- `list(minhash.hashvalues.tolist())` → `list[Python int]` (plain Python integers).

PyArrow's `table.update(values={...: list[numpy.uint64]})` may fail or silently
misbehave depending on the LanceDB/PyArrow version, because the update path expects
either a plain Python list or an explicit Arrow array, not a list of NumPy scalars.
D8 is correct; §2.1 is wrong.

An implementer reading §2.1 (the spec section, not the rationale) will write the shorter
`list(minhash.hashvalues)` form and introduce a subtle type bug that may only surface
under certain PyArrow builds.

**Required fix**: Update §2.1's `_compute_signature` bullet to state explicitly:
> "returns `list(minhash.hashvalues.tolist())` — `.tolist()` converts NumPy `uint64`
> scalars to plain Python `int` before building the list, which PyArrow coerces safely
> to `pa.list_(pa.uint64())` on write."

Remove the parenthetical `(uint64 elements, …)` from the shorter form so there is
exactly one canonical expression in the spec.

---

### F3 — MEDIUM: `attr_minhash_cluster_id` integer labels are not stable across re-runs

**Location**: §2.1 (`_cluster_rows` bullet), D4, test `test_cluster_rows_idempotent`
(§2.8), V4 (§2.9).

**Problem**: The "0-based incrementing integer" cluster IDs are assigned in the order
rows are encountered during the LSH insertion loop. LanceDB / Lance does not guarantee
a deterministic row-fetch order for `table.search().where(...).to_list()` — the order
may vary between runs depending on compaction state, fragment layout, and concurrent
writes. This means:

- `attr_minhash_is_head` is deterministic (lex-min `chunk_id` is stable).
- `attr_minhash_cluster_id` integer labels may be reassigned on a second run: cluster
  that was `cluster_id=0` on run 1 might become `cluster_id=3` on run 2.

V4 (second-run idempotency) asserts "cluster assignments must be stable" but the
proposed implementation does not guarantee this.

**Required fix** (choose one):

Option A (preferred): Specify that `update_minhash_in_lance` sorts rows by `chunk_id`
ascending before passing them to `_cluster_rows`. Because `chunk_id` is a UUID and the
row set is fixed for a given source, this guarantees a canonical insertion order and
thus stable integer labels. Add this sort step to the §2.1 spec and update D4 to
mention it.

Option B: Explicitly document that `attr_minhash_cluster_id` is a run-local opaque
integer label (not stable across re-runs), rename V4 to check only `is_head` stability
and `cluster_id` *cardinality* (same number of distinct clusters), and clarify in D4
that only `is_head` is the stable, user-facing dedup signal.

Option A is strongly preferred because the verifier's V4 step as written implies label
stability, and downstream consumers querying `WHERE attr_minhash_cluster_id = N` across
runs would get inconsistent results under Option B.

---

### F4 — MEDIUM: V2 check in `checks.sh` spec has no concrete implementation

**Location**: §2.9, step 7 (V2).

**Problem**: V2 is described as:
> "Group rows by `attr_minhash_cluster_id`; assert that exactly one row per cluster has
> `attr_minhash_is_head = True`."

V1 and V3 both include concrete Python one-liners (e.g., `lancedb.connect(…).open_table("chunks").count_rows(…)`). V2 does not. Implementing the grouping check against LanceDB requires fetching the columns, iterating over clusters, and asserting head uniqueness — non-trivial to write correctly inline in a Bash here-doc. The implementer will have to design this snippet from scratch, and the contract should not leave it underspecified.

**Required fix**: Add a concrete Python snippet for V2, such as:

```python
import lancedb, collections
tbl = lancedb.connect(db_uri, storage_options=storage_options).open_table("chunks")
rows = tbl.search().where(f"source_id = {source_id} AND producer_asset = 'chunks'") \
           .select(["attr_minhash_cluster_id", "attr_minhash_is_head"]).to_list()
cluster_heads = collections.Counter(
    r["attr_minhash_cluster_id"] for r in rows if r["attr_minhash_is_head"]
)
all_clusters = set(r["attr_minhash_cluster_id"] for r in rows)
assert all_clusters == set(cluster_heads.keys()), "some cluster has no head"
assert all(v == 1 for v in cluster_heads.values()), "some cluster has multiple heads"
```

The exact form may vary but the contract must specify what the script asserts,
not merely what the intent is.

---

### F5 — MEDIUM: `test_cluster_rows_idempotent` does not cover the F3 ordering concern

**Location**: §2.8, `test_cluster_rows_idempotent`.

**Problem**: The test is described as:
> "running `_cluster_rows` twice on the same data produces identical cluster_id and
> is_head assignments."

This tests only that calling `_cluster_rows(rows)` twice with the same Python list
(same reference, same order) yields the same output — a trivial property of any pure
function. It does NOT test that calling `_cluster_rows` on the same rows presented in
a *different order* yields the same cluster_id labels. An implementer who reads this
test spec will write a test that passes even with a non-deterministic implementation.

**Required fix** (tied to the fix for F3): If Option A is adopted for F3 (sort by
chunk_id before clustering), add a test `test_cluster_rows_order_invariant` that
creates two lists with the same rows in different order, calls `_cluster_rows` on
each, and asserts that the resulting `cluster_id` and `is_head` assignments are
identical by `chunk_id` key. This test would have caught the F3 bug at unit-test time.

---

### F6 — NIT: Dockerfile comment should state minimum `datasketch` version

**Location**: §2.6.

**Problem**: §2.6 says "confirm `datasketch>=1.6` is present on the mirror" but
the instruction to the implementer about what to write in the Dockerfile comment
says only "note the pinned version … as done for `fasttext-langdetect`". The body
of the Dockerfile comment block for F-030 should explicitly mention the `>=1.6`
floor even when no pin is used, so future readers know what minimum was tested.

**Suggested fix**: Change the §2.6 Dockerfile comment guidance to:
> "Add a comment line: `# F-030: datasketch>=1.6 (MinHash + MinHashLSH); no version pin`
> unless the mirror requires a pin, in which case pin and note it as done for
> `fasttext-langdetect==1.1.1`."

This is a NIT — acceptable to fold into the agreed.md without blocking implementation.

---

### F7 — NIT: `routers/runs.py` feature-ID comment not mentioned in §2.5

**Location**: §2.5.

**Problem**: The existing router file has comments that tag branches with sprint/feature
IDs (e.g., `# F-028`, `# F-029`). §2.5 does not instruct the implementer to add a
`# F-030` comment to the new `elif` branch, which is inconsistent with the existing
style. Minor, but causes confusion when bisecting commits by feature.

**Suggested fix**: Add one line to the §2.5 change description:
> "Precede the new `elif` block with a comment `# F-030: attr_minhash backfill`."

---

## Required changes before `agreed.md`

| # | Severity | Section | Action |
|---|---|---|---|
| F1 | **HIGH** | §2.1 | Add `AND producer_asset = 'chunks'` to `where_clause` everywhere in the spec |
| F2 | **HIGH** | §2.1, D8 | Resolve contradiction — §2.1 must say `list(minhash.hashvalues.tolist())` |
| F3 | **MEDIUM** | §2.1, D4, §2.9 V4 | Specify sort-by-chunk_id before clustering, OR downgrade V4 to check cardinality only |
| F4 | **MEDIUM** | §2.9 V2 | Provide concrete Python snippet for the "one head per cluster" assertion |
| F5 | **MEDIUM** | §2.8 | Add `test_cluster_rows_order_invariant` (tied to F3 fix) |
| F6 | NIT | §2.6 | Dockerfile comment template should state `datasketch>=1.6` floor |
| F7 | NIT | §2.5 | Mention `# F-030` comment on new `elif` branch |

---

CHANGES_REQUESTED
