# S031-F-031 — `LanceChunksIOManager` column mode: proposed.md

Sprint ID: S031-F-031  
Feature: F-031 `lance_io_manager_column_mode`  
Status: PROPOSED  
Dependencies: F-028 (`attr_quality`) ✓, F-029 (`attr_lang`) ✓, F-030 (`attr_minhash`) ✓

---

## §1 Summary

### What will be built

`LanceChunksIOManager` is extended to support a **column mode** write path.
Currently, every asset routed through the IO manager uses **row mode**: delete existing rows
for `(source_id, producer_asset)` then bulk-add the new rows.  The three tagger assets
(`attr_quality`, `attr_lang`, `attr_minhash`) bypass the IO manager entirely and do their own
per-row `table.update()` calls inside the tagger modules, because row mode would destroy the
other taggers' columns.

F-031 corrects this by:

1. Adding a `compute_*_scores()` function to each tagger module (`quality_tagger.py`,
   `lang_tagger.py`, `minhash_tagger.py`).  These functions read chunk data from Lance,
   compute tagger values, and **return a `list[dict]` without writing to Lance**.

2. Updating the three tagger assets in `definitions.py` to add
   `io_manager_key="lance_chunks_io"`, change their return type to `list[dict]`, and call
   the new `compute_*_scores()` functions.

3. Adding a **column mode** path to `LanceChunksIOManager.handle_output()`.  Column mode:
   - Is triggered when `producer_asset != "chunks"` (i.e. all tagger assets).
   - Reads the existing full-schema rows for this `(source_id, producer_asset='chunks')`
     from Lance (read-modify-write pattern — see D3).
   - Merges the incoming tagger columns into the fetched rows in-memory.
   - Writes back via `merge_insert("chunk_id").when_matched_update_all().execute(...)` — 
     keyed on `chunk_id`, never touching rows belonging to other sources or other taggers'
     columns.

4. Adding a new `attr_col_isolation` layer to `verify/checks.sh` that exercises
   V-criterion #1 (cross-tagger column isolation end-to-end).

### Why read-modify-write instead of partial-column merge_insert

Prior sprints (S028-F-028/review-final.md, H1) established that lancedb 0.30.2
`when_matched_update_all()` replaces the **entire row** when matched — it does not support
a partial-column update kwarg.  Whether a **partial-column input table** (fewer columns than
the full schema) causes the same full-row replacement is untested and unverified.  D3 and D10
address this explicitly.  The proposed implementation uses the safe read-modify-write pattern,
which satisfies V-criterion #2 (`merge_insert("chunk_id")`) while guaranteeing that no column
is silently nulled out.

### Scope limits

- No cross-source clustering changes; each partition processed independently.
- No new Postgres migration or API route.
- No `make codegen` — all changes are internal to the Dagster image.
- The existing `update_*_in_lance()` functions are **kept but marked deprecated** — they are no
  longer called from tagger assets, but removing them is a separate cleanup task outside this
  sprint.
- No batched LLM calls (quality tagger remains per-chunk HTTP).

---

## §2 Files

### 2.1 MODIFIED — `dagster/dagster_platform/lance_io_manager.py`

Primary change: add a column-mode branch to `handle_output()`.

New logic (after the empty-list early-return and partition-key guard):

```
if producer_asset == "chunks":
    # existing row mode — delete + add (unchanged)
else:
    # column mode — read-modify-write via merge_insert
    _column_mode_write(table, obj, source_id)
```

New private function `_column_mode_write(table, incoming_rows, source_id)`:
1. If `incoming_rows` is empty → early return (no read, no write).
2. Fetch existing full-schema rows: `table.search().where(where_clause).to_list()`.
3. Build a `{chunk_id: existing_row}` index.
4. For each dict in `incoming_rows`, merge its keys (excluding `chunk_id`) into the
   corresponding existing row.  If a `chunk_id` in `incoming_rows` is not found in the
   existing index, log a warning and skip (defensive guard against stale chunk_ids).
5. Convert the merged rows list to a PyArrow table using the existing `CHUNKS_SCHEMA`
   (imported from `chunker.py`) to avoid schema type drift.
6. Call `table.merge_insert("chunk_id").when_matched_update_all().execute(pa_table)`.

Metadata added via `context.add_output_metadata()`:
```python
{"row_count": len(incoming_rows), "mode": "column", "merge_key": "chunk_id"}
```

The TODO comment at line 77 (`TODO F-028: dispatch column mode vs. row mode`) is removed and
replaced by the dispatch logic.

### 2.2 MODIFIED — `dagster/dagster_platform/quality_tagger.py`

Add a new public function:

```python
def compute_quality_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, score via LLM gateway.

    Returns list of dicts: [{"chunk_id": str, "attr_quality_score": float,
    "attr_quality_provider": str}, ...]
    Does NOT write to Lance.
    """
```

Implementation:
- Reuses `_build_lance_storage_options()`, same `where_clause`, same `score_chunks_via_gateway()`.
- Reads `chunk_id` and `text` for `(source_id, producer_asset='chunks')` rows.
- Returns partial dicts (3 keys: `chunk_id`, `attr_quality_score`, `attr_quality_provider`).

The existing `update_quality_scores_in_lance()` and `_llm_update()` are kept unchanged but
gain a deprecation notice in their docstrings.

### 2.3 MODIFIED — `dagster/dagster_platform/lang_tagger.py`

Add a new public function:

```python
def compute_lang_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, detect language via fasttext.

    Returns list of dicts: [{"chunk_id": str, "attr_lang_code": str,
    "attr_lang_confidence": float}, ...]
    Does NOT write to Lance.
    """
```

Implementation mirrors `compute_quality_scores()` but calls `detect_language()` instead.
Returns partial dicts (3 keys).

The existing `update_lang_in_lance()` and `_lang_update()` are kept but deprecated.

### 2.4 MODIFIED — `dagster/dagster_platform/minhash_tagger.py`

Add a new public function:

```python
def compute_minhash_scores(source_id: int) -> list[dict]:
    """Read chunk_id + text from Lance, compute MinHash signatures and clusters.

    Fetches ALL rows for the source (required by batch clustering), sorts by
    chunk_id ascending, runs _cluster_rows(), returns partial dicts.

    Returns list of dicts: [{"chunk_id": str, "attr_minhash_signature": list[int],
    "attr_minhash_cluster_id": int, "attr_minhash_is_head": bool}, ...]
    Does NOT write to Lance.
    """
```

Implementation:
- Same Lance read pattern as `_minhash_update()`.
- Calls existing `_cluster_rows()` (unchanged — it is already a pure function).
- Returns partial dicts (4 keys: `chunk_id` + three attr columns).

The existing `update_minhash_in_lance()` and `_minhash_update()` are kept but deprecated.

### 2.5 MODIFIED — `dagster/dagster_platform/definitions.py`

All three tagger asset decorators gain `io_manager_key="lance_chunks_io"`.

`attr_quality` changes:
- Add `io_manager_key="lance_chunks_io"`.
- Change return annotation from `MaterializeResult` to `list[dict[str, Any]]`.
- Replace `row_count = update_quality_scores_in_lance(source_id)` with
  `rows = compute_quality_scores(source_id)`.
- Replace `return MaterializeResult(metadata={...})` with
  `context.add_output_metadata({...}); return rows`.
- Remove `update_quality_scores_in_lance` from the import; add `compute_quality_scores`.

Same pattern for `attr_lang` (→ `compute_lang_scores`) and `attr_minhash`
(→ `compute_minhash_scores`).

The `rows_updated` metadata value comes from `len(rows)` (produced before returning).  The
IO-level `row_count` / `mode` / `merge_key` metadata is added by `LanceChunksIOManager`.

No change to `extract_mineru`, `chunks`, `source_asset`, `hello_world_job`, or `Definitions`.

### 2.6 NEW — `dagster/tests/test_lance_io_manager_column_mode.py`

Unit tests for the column-mode path of `LanceChunksIOManager`.  No Dagster runtime needed —
all external I/O (lancedb, `build_lance_storage_options`, `CHUNKS_SCHEMA`) is monkeypatched.

Coverage:
- Column mode is triggered when `producer_asset` is not `"chunks"`.
- `merge_insert("chunk_id")` is called; `table.delete()` is NOT called.
- Incoming tagger columns are merged into the existing full-schema row.
- Columns not in the incoming dicts (other taggers' columns) survive unchanged.
- Empty `obj` list → early return, no Lance read, no `merge_insert` call.
- Missing `chunk_id` in existing rows → warning logged, row skipped.
- IOManager metadata: `mode == "column"`, `merge_key == "chunk_id"`.

### 2.7 MODIFIED — `verify/checks.sh`

Add a new layer `attr_col_isolation` (see §4).  No changes to existing layers.

---

## §3 Design decisions

### D1 — Mode dispatch: `producer_asset == "chunks"` vs. everything else

**Decision:** In `LanceChunksIOManager.handle_output()`, dispatch on `producer_asset`:
- `"chunks"` → existing row mode (delete-before-insert).
- anything else → new column mode (read-modify-write, merge_insert).

**Rationale:** The chunks asset is the only row-producing asset.  Every asset that uses the IO
manager for column-mode updates will have a different `producer_asset` value (e.g. `"attr_quality"`).
A binary dispatch on `"chunks"` is simpler than an allowlist and is self-extending: future tagger
assets automatically receive column mode without modifying the IO manager.  If a future asset
needed row mode with a different `producer_asset` value, an explicit override kwarg can be added
then; there is no such asset in the current scope.

---

### D2 — Probe test for partial-column merge_insert behavior in lancedb 0.30.2

**Decision:** Before finalising the implementation strategy (D3), a standalone probe script
(not part of the test suite) must be run inside the `dagster-webserver` container to determine
whether `merge_insert("key").when_matched_update_all().execute(partial_pa_table)` preserves
columns that are absent from the input table.

Probe script (`verify/probe_partial_merge.py`, run once and discarded):

```python
import lancedb, pyarrow as pa, os, sys

storage_options = {
    "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
    "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
    "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
    "aws_region":            "us-east-1",
    "allow_http":            "true",
}
db = lancedb.connect("s3://lance/probe_col_test", storage_options=storage_options)

schema = pa.schema([
    ("id",   pa.string()),
    ("colA", pa.float32()),
    ("colB", pa.string()),
])
initial = pa.table({
    "id":   ["r1", "r2"],
    "colA": pa.array([1.0, 2.0], pa.float32()),
    "colB": ["original_B1", "original_B2"],
})
tbl = db.create_table("probe_col_test", data=initial, schema=schema)

# merge_insert with only id + colA (no colB)
partial = pa.table({
    "id":   ["r1"],
    "colA": pa.array([99.0], pa.float32()),
})
tbl.merge_insert("id").when_matched_update_all().execute(partial)

rows = {r["id"]: r for r in tbl.search().select(["id", "colA", "colB"]).to_list()}
preserved = rows["r1"]["colB"] == "original_B1"
unchanged = rows["r2"]["colA"] == 2.0
print(f"partial merge preserves colB: {preserved}")   # True → D3a viable
print(f"unaffected row r2 unchanged:  {unchanged}")   # True always

db.drop_table("probe_col_test")
sys.exit(0 if preserved else 1)
```

**Rationale:** Prior sprint S028-F-028 (review-final.md H1) established that bare
`when_matched_update_all()` replaces the entire row.  That finding was for a full-column
input table.  Partial-column behaviour is undocumented in lancedb 0.30.2.  Running the probe
before implementation eliminates the ambiguity.  If the probe exits 0 (D3a viable), the
implementer may optionally simplify the IOManager to skip the full-row read (D3a path).
If the probe exits 1 (D3a fails), D3b is the mandatory implementation strategy.

---

### D3 — Column write strategy: read-modify-write (D3b) as safe primary

**Decision:** The primary implementation uses **D3b (read-modify-write)**:

1. `_column_mode_write()` reads all existing full-schema rows for the source partition.
2. Builds a `{chunk_id → existing_row_dict}` index.
3. For each incoming partial dict, merges the new tagger columns into the existing row.
4. Converts the merged dicts to `pa.Table` using `CHUNKS_SCHEMA` from `chunker.py`.
5. Calls `table.merge_insert("chunk_id").when_matched_update_all().execute(merged_pa_table)`.

If the D2 probe confirms that partial-column merge_insert preserves missing columns (exit 0),
the agreed.md may choose **D3a (direct partial merge)**:

1. Convert incoming partial dicts directly to a `pa.Table` using a schema derived from the
   keys of the first row (chunk_id + tagger columns only).
2. Call `table.merge_insert("chunk_id").when_matched_update_all().execute(partial_pa_table)`.
3. Skip the full-row read entirely.

Either path satisfies V-criterion #2 (`merge_insert("chunk_id")` with no delete).

**Rationale:** D3b is unconditionally safe: because we rebuild each row from its existing
values before writing, no column can be nulled out regardless of lancedb version semantics.
The cost is one extra Lance read per tagger run — acceptable for MVP.  D3a is more efficient
but requires D2 probe confirmation.  The proposed.md takes the safe path; reviewer may approve
D3a if the probe result is known before agreed.md is finalised.

---

### D4 — New `compute_*_scores()` public functions: read-only, no Lance write

**Decision:** Each tagger module adds one new public function:

| Module | New function | Return keys |
|---|---|---|
| `quality_tagger.py` | `compute_quality_scores(source_id) -> list[dict]` | `chunk_id`, `attr_quality_score`, `attr_quality_provider` |
| `lang_tagger.py` | `compute_lang_scores(source_id) -> list[dict]` | `chunk_id`, `attr_lang_code`, `attr_lang_confidence` |
| `minhash_tagger.py` | `compute_minhash_scores(source_id) -> list[dict]` | `chunk_id`, `attr_minhash_signature`, `attr_minhash_cluster_id`, `attr_minhash_is_head` |

Each function opens Lance, reads `chunk_id` + `text` for the source, computes scores/tags,
and returns partial dicts.  **No writes to Lance.**

For `compute_minhash_scores()`: all rows must be fetched before clustering (batch operation —
inherited from `_cluster_rows()`).  The function fetches ALL rows, sorts by `chunk_id`, calls
the existing `_cluster_rows()`, and returns the results.  `_cluster_rows()` is unchanged.

**Rationale:** Separating compute from write mirrors the no-Dagster-imports guarantee already
in place.  `compute_*` functions can be unit-tested with a mock Lance table.  The IOManager
handles the write concern; the tagger modules handle the domain logic.  Two Lance connections
are made per tagger run (one in `compute_*`, one in `_column_mode_write`) — acceptable for MVP.

---

### D5 — Asset return type: `list[dict]` + `add_output_metadata()` replaces `MaterializeResult`

**Decision:** After adding `io_manager_key="lance_chunks_io"`, each tagger asset:

1. Calls `rows = compute_*_scores(source_id)`.
2. Calls `context.add_output_metadata({"source_id": ..., "chunk_count": len(rows)})`.
3. Returns `rows` (type: `list[dict[str, Any]]`).

The `MaterializeResult` return and the `rows_updated` / `row_count` metadata move to the IO
manager (`mode`, `row_count`, `merge_key`).

**Rationale:** Dagster's contract when `io_manager_key` is set: `handle_output()` receives
the return value of the asset function.  Returning `MaterializeResult` from an io-manager-
backed asset is incorrect — the IO manager would receive a `MaterializeResult` object, not
the data.  `add_output_metadata()` is the correct API for asset-level metadata alongside an
IO manager (same pattern used by the `chunks` asset since F-026).

---

### D6 — Empty `compute_*` result: no-op in IOManager column mode

**Decision:** If `compute_*_scores()` returns an empty list (no chunks for the source), the
asset returns `[]`.  `LanceChunksIOManager.handle_output()` receives `obj = []` and takes the
existing D11 early-return path with metadata `{"row_count": 0, "mode": "column_skipped"}`.
No Lance read, no `merge_insert` call.

**Rationale:** Idempotent zero-chunk handling is already correct for row mode.  Column mode
must not attempt a Lance read when there is nothing to write; `_column_mode_write()` would
succeed (matching zero rows) but at the cost of a wasted round-trip.  Early-return is cheaper
and consistent with the row-mode empty-list guard.

---

### D7 — D3b read scope: fetch full existing rows using `CHUNKS_SCHEMA`

**Decision:** In `_column_mode_write()`, the existing rows are fetched without a column
`.select()` (i.e. `table.search().where(where_clause).to_list()`).  The merged result is
converted to `pa.Table` using `CHUNKS_SCHEMA` (imported from `chunker.py`) rather than an
ad-hoc schema, to avoid type-drift between the schema used at table creation and the schema
used at merge time.

**Rationale:** `CHUNKS_SCHEMA` is the single source of truth for the chunks table schema
(defined once in `chunker.py`).  Using it for the `pa.Table.from_pylist()` call in D3b
ensures that `pa.string()` vs `pa.large_string()` and `pa.uint64()` alignment issues do not
arise.  `from_pylist(rows, schema=CHUNKS_SCHEMA)` will raise early and clearly on any field
name or type mismatch rather than silently coercing.

---

### D8 — Backward compatibility: keep `update_*_in_lance()` as deprecated

**Decision:** `update_quality_scores_in_lance()`, `update_lang_in_lance()`, and
`update_minhash_in_lance()` are kept in their respective modules.  Their docstrings gain a
`.. deprecated::` notice stating they are no longer called from Dagster assets and may be
removed in a future sprint.  Their implementations are **not changed**.

**Rationale:** These functions are referenced in existing unit tests
(`test_quality_tagger_llm.py`, `test_lang_tagger.py`, `test_minhash_tagger.py`).  Removing
them silently would break the test suite.  The new `compute_*_scores()` functions are
independently tested in `test_lance_io_manager_column_mode.py`.  Cleanup is deferred.

---

### D9 — No new Postgres migration, no API change, no `make codegen`

**Decision:** F-031 is an internal Dagster refactor.  No FastAPI routes, no OpenAPI schema,
no Postgres table, and no TypeScript type generation are touched.  `make codegen` is not run.

**Rationale:** All changes are confined to `dagster/dagster_platform/` and `dagster/tests/`.
The FastAPI trigger path (`POST /api/runs`) is unchanged; it still sends the same asset names
to Dagster.  Dagster's behaviour from FastAPI's perspective is identical: same partition key,
same asset key, same run metadata.

---

### D10 — Lance read in `_column_mode_write()` uses `where_clause` scoped to `producer_asset='chunks'`

**Decision:** The WHERE clause for the full-row fetch in D3b is:
```
source_id = {source_id} AND producer_asset = 'chunks'
```
This is the same predicate already used by all tagger modules.  Even though the current asset
being written is `attr_quality` (for example), the rows that need updating are the original
`producer_asset='chunks'` rows — column-mode taggers add attributes to those rows.

**Rationale:** Rows in the chunks table have `producer_asset` set to `'chunks'` by the
`chunks` asset.  Tagger assets do not add new rows; they update existing rows.  Reading with
`producer_asset='chunks'` ensures the IOManager fetches exactly the rows to be updated and no
phantom rows.  Writing them back via `merge_insert("chunk_id")` matches on the unique
`chunk_id` key — no ambiguity regardless of `producer_asset` value in the existing row.

---

## §4 Verification plan

### V-criterion mapping

| Criterion | How verified |
|---|---|
| V #1: Run quality tagger, note attr_lang_code null; run lang tagger; verify attr_quality_score unchanged and attr_lang_code now set | `attr_col_isolation` layer in `checks.sh` |
| V #2: Column merge uses merge_insert on chunk_id key, not full row replacement | Unit test (assert `merge_insert("chunk_id")` called; `delete` NOT called) + grep in CI |
| General: no regression to existing tagger layers | Existing `attr_quality`, `attr_lang`, `attr_minhash` checks.sh layers must still pass |

---

### 4.1 D2 probe — run before implementation

```bash
# Run inside dagster-webserver container once before writing code:
docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver \
  python verify/probe_partial_merge.py
# Exit 0 → D3a viable (partial merge preserves columns)
# Exit 1 → D3b mandatory (full-row read-modify-write)
# Record result in agreed.md §3 D3 before implementation begins.
```

---

### 4.2 V-criterion #2 — unit test (in `test_lance_io_manager_column_mode.py`)

```python
def test_column_mode_calls_merge_insert_not_delete(monkeypatch):
    """Column mode must call merge_insert('chunk_id'); must NOT call delete."""
    import pyarrow as pa
    from unittest.mock import MagicMock, patch

    # Stub existing Lance rows — one row per tagger column set
    existing_rows = [
        {
            "chunk_id": "src_1_0",
            "source_id": 1,
            "collection_id": 10,
            "text": "hello world test",
            "producer_asset": "chunks",
            "producer_version": "v1",
            "augmented_from": None,
            "augmenter_id": None,
            "augmenter_config_hash": None,
            "attr_quality_score": 0.8,
            "attr_quality_provider": "mock",
            "attr_lang_code": None,
            "attr_lang_confidence": None,
            "attr_minhash_signature": None,
            "attr_minhash_cluster_id": None,
            "attr_minhash_is_head": None,
        }
    ]
    mock_merge = MagicMock()
    mock_merge.when_matched_update_all.return_value = mock_merge
    mock_merge.execute.return_value = None

    mock_table = MagicMock()
    mock_table.search.return_value.where.return_value.to_list.return_value = existing_rows
    mock_table.merge_insert.return_value = mock_merge

    mock_db = MagicMock()
    mock_db.create_table.return_value = mock_table

    incoming = [{"chunk_id": "src_1_0", "attr_lang_code": "en", "attr_lang_confidence": 0.99}]

    # Build a mock OutputContext with asset_key = ["attr_lang"], partition_key = "src_1"
    mock_ctx = MagicMock()
    mock_ctx.has_partition_key = True
    mock_ctx.partition_key = "src_1"
    mock_ctx.asset_key.path = ["attr_lang"]

    with patch("lancedb.connect", return_value=mock_db):
        from dagster_platform.lance_io_manager import LanceChunksIOManager
        mgr = LanceChunksIOManager()
        mgr.handle_output(mock_ctx, incoming)

    # V-criterion #2: merge_insert("chunk_id") was called
    mock_table.merge_insert.assert_called_once_with("chunk_id")
    # Row mode delete must NOT be called in column mode
    mock_table.delete.assert_not_called()
    # Metadata recorded
    mock_ctx.add_output_metadata.assert_called_once()
    meta = mock_ctx.add_output_metadata.call_args[0][0]
    assert meta["mode"] == "column"
    assert meta["merge_key"] == "chunk_id"
```

---

### 4.3 V-criterion #2 — static grep assertion in checks.sh

```bash
# Part of the attr_col_isolation layer setup:
grep -n 'merge_insert("chunk_id")' \
    dagster/dagster_platform/lance_io_manager.py | grep -q "." \
  || { echo "FAIL: merge_insert not found in lance_io_manager.py"; exit 1; }
echo "PASS: merge_insert(\"chunk_id\") present in lance_io_manager.py"
```

---

### 4.4 V-criterion #1 — new `attr_col_isolation` layer in `checks.sh`

The layer follows the standard pattern: mint JWT → upload PDF → create collection →
trigger extract_mineru + chunks → poll → trigger attr_quality → poll → snapshot → trigger
attr_lang → poll → verify isolation.

**Python assertion script** (piped via `docker compose exec -T`):

```python
# isolation_check.py — injected as a heredoc in checks.sh
import lancedb, json, os, sys

src_id  = int(sys.argv[1])
phase   = sys.argv[2]   # "pre_lang" or "post_lang"
snap_f  = "/tmp/quality_snapshot.json"

storage_options = {
    "aws_access_key_id":     os.environ["MINIO_ROOT_USER"],
    "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
    "endpoint":              f"http://{os.environ['MINIO_ENDPOINT']}",
    "aws_region":            "us-east-1",
    "allow_http":            "true",
}
db  = lancedb.connect("s3://lance/chunks", storage_options=storage_options)
tbl = db.open_table("chunks")

where = f"source_id = {src_id} AND producer_asset = 'chunks'"
rows  = (tbl.search()
            .where(where)
            .select(["chunk_id", "attr_quality_score", "attr_quality_provider",
                     "attr_lang_code", "attr_lang_confidence"])
            .to_list())
assert rows, f"No rows found for source_id={src_id}"

if phase == "pre_lang":
    # After quality tagger, before lang tagger
    for r in rows:
        assert r["attr_quality_score"] is not None, \
            f"FAIL pre_lang: attr_quality_score is None for chunk_id={r['chunk_id']}"
        assert r["attr_lang_code"] is None, \
            f"FAIL pre_lang: attr_lang_code should be None but got " \
            f"'{r['attr_lang_code']}' for chunk_id={r['chunk_id']}"
    snapshot = {r["chunk_id"]: r["attr_quality_score"] for r in rows}
    with open(snap_f, "w") as fh:
        json.dump(snapshot, fh)
    print(f"PASS pre_lang: {len(rows)} row(s), snapshot saved")

elif phase == "post_lang":
    # After lang tagger — quality columns must be identical to snapshot
    with open(snap_f) as fh:
        snapshot = json.load(fh)
    for r in rows:
        cid = r["chunk_id"]
        assert r["attr_lang_code"] is not None, \
            f"FAIL post_lang: attr_lang_code is None for chunk_id={cid}"
        assert abs(r["attr_quality_score"] - snapshot[cid]) < 1e-6, \
            f"FAIL post_lang: attr_quality_score changed for chunk_id={cid}: " \
            f"was {snapshot[cid]}, now {r['attr_quality_score']}"
    print(f"PASS post_lang: {len(rows)} row(s), quality scores unchanged")
```

**Shell orchestration** inside the `attr_col_isolation` layer:

```bash
# Step 1: static grep
grep -n 'merge_insert("chunk_id")' \
    dagster/dagster_platform/lance_io_manager.py | grep -q "."

# Step 2: setup (same setup_source helper used by attr_quality / attr_lang layers)
SRC_ID=$(setup_source)     # upload PDF, create collection → returns source_id

# Step 3: prereqs
trigger_and_poll extract_mineru "$SRC_ID"
trigger_and_poll chunks         "$SRC_ID"

# Step 4: run quality tagger
trigger_and_poll attr_quality   "$SRC_ID"

# Step 5: pre-lang snapshot + assert
docker compose exec -T dagster-webserver \
    python - "$SRC_ID" pre_lang <<'PY'
<isolation_check.py content>
PY

# Step 6: run lang tagger
trigger_and_poll attr_lang      "$SRC_ID"

# Step 7: post-lang assertion
docker compose exec -T dagster-webserver \
    python - "$SRC_ID" post_lang <<'PY'
<isolation_check.py content>
PY
```

---

### 4.5 Regression: existing tagger layers

After implementing F-031, the following layers must still pass without modification:

```bash
bash verify/checks.sh attr_quality
bash verify/checks.sh attr_lang
bash verify/checks.sh attr_minhash
```

These test the end-to-end materialization of each tagger.  The only asset-level behavioural
change is that the tagger assets now go through the IOManager; the Lance column values written
must be identical to pre-F-031.

---

## §5 Risks

| # | Risk | Probability | Severity | Mitigation |
|---|---|---|---|---|
| R1 | lancedb 0.30.2 `merge_insert` in D3b overwrites rows from OTHER sources | Low | Critical | `where_clause` scopes read AND `merge_insert` matches only on `chunk_id` — chunk_ids are globally unique (UUID prefix), so cross-source collision is impossible |
| R2 | `pa.Table.from_pylist(rows, schema=CHUNKS_SCHEMA)` fails on type mismatch (e.g. `pa.large_string()` stored by lancedb vs `pa.string()` declared in CHUNKS_SCHEMA) | Medium | High | Validate CHUNKS_SCHEMA against actual table schema in probe_partial_merge.py; cast mismatched columns in `_column_mode_write()` |
| R3 | `merge_insert` in D3b triggers full-table compaction, causing visible latency on large tables | Low | Medium | Acceptable for MVP; deferred compaction is standard lancedb behaviour; add note to ops runbook |
| R4 | minhash `compute_minhash_scores()` reads Lance twice per run (once for text, once in D3b for full rows) — doubles I/O for attr_minhash | Medium | Low | Two sequential reads against the same Lance table are fast for MVP chunk counts (<10 k rows per source); optimise in a future sprint with D3a if D2 probe confirms safety |
| R5 | An attr_quality or attr_lang `compute_*` result dict has a `chunk_id` not present in the existing Lance index (stale reference from a deleted chunk) | Low | Medium | D3b's merge step logs a warning and skips orphaned incoming rows; no crash, no data loss |
| R6 | Test isolation: `test_lance_io_manager_column_mode.py` imports `CHUNKS_SCHEMA` which transitively imports lancedb; slow or unavailable in CI | Low | Low | All tests already import lancedb (existing test suite); no new dependency |
| R7 | After refactor, `MaterializeResult` is no longer imported from `dagster` for tagger assets | Low | Low | `MaterializeResult` import in definitions.py is still needed for `extract_mineru`; import must be kept |
