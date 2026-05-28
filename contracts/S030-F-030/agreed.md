# S030-F-030 — `attr_minhash` tagger: agreed.md

Sprint ID: S030-F-030  
Feature: F-030 `minhash_dedup`  
Status: AGREED (all 7 feedback findings addressed)

---

## Feedback resolution

| # | Severity | Resolution |
|---|---|---|
| F1 | HIGH | §2.1 now uses `f"source_id = {source_id} AND producer_asset = 'chunks'"` everywhere — matches lang_tagger.py exactly |
| F2 | HIGH | §2.1 `_compute_signature` bullet now says `list(minhash.hashvalues.tolist())` exclusively; contradictory short form removed |
| F3 | MEDIUM | Option A adopted: rows are sorted by `chunk_id` ascending before passing to `_cluster_rows`, guaranteeing stable integer labels |
| F4 | MEDIUM | §2.9 V2 now includes the full Python snippet for head-per-cluster assertion |
| F5 | MEDIUM | `test_cluster_rows_order_invariant` added to test spec (§2.8) |
| F6 | NIT | §2.6 now specifies the Dockerfile comment template with `>=1.6` floor |
| F7 | NIT | §2.5 now instructs adding `# F-030: attr_minhash backfill` comment |

---

## §1 Summary

### What will be built

A MinHash-based near-duplicate detection tagger that operates on the chunks LanceDB table.  
For every source partition, the tagger:

1. Reads all chunks for that source (columns: `chunk_id`, `text`) where `producer_asset = 'chunks'`.
2. **Sorts rows by `chunk_id` ascending** (canonical order — F3 fix).
3. Computes a 128-shingle MinHash signature for every chunk using the `datasketch` library.
4. Clusters near-duplicates via `MinHashLSH` (Jaccard threshold 0.85, 128 permutations).
5. Assigns a deterministic integer `attr_minhash_cluster_id` to every chunk (clusters formed by
   the LSH query; singletons each get their own unique id). Integer labels are stable because
   input order is fixed by the sort in step 2.
6. Marks the lexicographically-lowest `chunk_id` within each cluster as the head
   (`attr_minhash_is_head = True`); all others get `False`.
7. Writes `attr_minhash_signature`, `attr_minhash_cluster_id`, and `attr_minhash_is_head` back
   into the existing Lance rows via per-row `table.update()` — **zero new rows are created**.

The tagger is exposed as a Dagster partitioned asset (`attr_minhash`), wired into the existing
`sources_partitions` partition set. The API gains a new backfill trigger path:
`POST /api/runs { "asset": "attr_minhash", "source_ids": [...] }`.

All three target columns already exist in `CHUNKS_SCHEMA` (added during F-025); this sprint
writes real values into them for the first time.

### Scope limits

- No cross-source clustering (MVP): each source is processed independently.
- No LLM call: MinHash is purely algorithmic. CLAUDE.md invariant #4 does not apply.
- No new Postgres table or migration: only LanceDB column-mode updates.
- No new Dagster resource: same MinIO / LanceDB connection pattern as `lang_tagger.py`.

---

## §2 Files

### 2.1 NEW — `dagster/dagster_platform/minhash_tagger.py`

Pure helper module (no Dagster imports). Mirrors the structure of `lang_tagger.py`.

Exports one public function:

```
update_minhash_in_lance(source_id: int) -> int
```

Internal layout:

- `_build_lance_storage_options()` — reads `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`,
  `MINIO_ENDPOINT` from environment (identical to `lang_tagger._build_lance_storage_options`).

- `_compute_signature(text: str) -> list[int]` — builds word-level 3-grams (shingles) from
  lowercased text (whitespace-split then sliding window of 3), runs
  `datasketch.MinHash(num_perm=128)` over them via `minhash.update(shingle.encode('utf-8'))`,
  returns `list(minhash.hashvalues.tolist())` — `.tolist()` converts NumPy `uint64` scalars to
  plain Python `int` before building the list, which PyArrow coerces safely to
  `pa.list_(pa.uint64())` on write. (F2 fix: single canonical expression, no ambiguity.)
  For empty/whitespace text: create a MinHash with no updates (empty shingle set); return its
  hashvalues normally — this is deterministic and well-defined.

- `_cluster_rows(rows: list[dict]) -> list[dict]` — expects rows already **sorted by `chunk_id`
  ascending** (F3 fix: canonical order guarantees stable cluster labels). For each row, computes
  its MinHash signature via `_compute_signature`, inserts it into a `MinHashLSH(threshold=0.85,
  num_perm=128)` keyed by `chunk_id`. After all insertions, queries the LSH for each chunk's
  near-neighbours, uses union-find to assign connected components as clusters. Each cluster gets
  a 0-based incrementing integer `attr_minhash_cluster_id` (assigned in order of first-seen
  `chunk_id` within the sorted list — deterministic). The lex-smallest `chunk_id` in each cluster
  is `attr_minhash_is_head = True`; all others `False`. Returns the list augmented with the three
  new column values.

- `update_minhash_in_lance(source_id: int) -> int` — opens the `chunks` Lance table, fetches
  rows with:
  ```
  where_clause = f"source_id = {source_id} AND producer_asset = 'chunks'"
  ```
  (F1 fix: matches lang_tagger.py line 139 exactly). **Sorts fetched rows by `chunk_id`
  ascending** (F3 fix). Calls `_cluster_rows(sorted_rows)`, then issues per-row
  `table.update(where=f"chunk_id = '{chunk_id}'", values={...})` for each chunk. Returns
  `table.count_rows(where_clause)` (same predicate, same pattern as lang_tagger.py line 142).

Sentinel behaviour: chunks where `text` is `None`, empty, or whitespace-only receive the MinHash
of an empty shingle set. They are treated as full first-class rows: they cluster with each other
if there are multiple empty-text chunks, and the head-election rule (lex-lowest `chunk_id`) applies
normally. This avoids a special-case branch and is idempotent.

### 2.2 MODIFIED — `dagster/dagster_platform/definitions.py`

Two changes:

1. Add import:
   ```python
   from dagster_platform.minhash_tagger import update_minhash_in_lance
   ```

2. Define and register `attr_minhash` asset — identical structure to `attr_lang`:
   ```python
   @asset(
       partitions_def=sources_partitions,
       description="MinHash dedup tagger (F-030): updates attr_minhash_signature, "
                   "attr_minhash_cluster_id, attr_minhash_is_head in chunks table. "
                   "Zero new rows created. Column-mode update only.",
   )
   def attr_minhash(context: AssetExecutionContext) -> MaterializeResult:
       partition_key = context.partition_key
       source_id = int(partition_key.removeprefix("src_"))
       context.log.info(
           "attr_minhash: starting for partition_key=%s source_id=%d",
           partition_key, source_id,
       )
       row_count = update_minhash_in_lance(source_id)
       context.log.info(
           "attr_minhash: updated %d row(s) for source_id=%d", row_count, source_id,
       )
       if row_count == 0:
           context.log.warning(
               "attr_minhash: zero rows updated for source_id=%d — "
               "chunks may not yet exist",
               source_id,
           )
       return MaterializeResult(
           metadata={
               "source_id": MetadataValue.int(source_id),
               "rows_updated": MetadataValue.int(row_count),
           }
       )
   ```
   Add `attr_minhash` to the `assets=[...]` list in the `Definitions(...)` call.

### 2.3 MODIFIED — `apps/api/dataplat_api/dagster/gateway.py`

Three additions:

1. **New GraphQL mutation constant** `_LAUNCH_ATTR_MINHASH_BACKFILL_MUTATION` — copied verbatim
   from `_LAUNCH_ATTR_LANG_BACKFILL_MUTATION` with `assetSelection: [{"path": ["attr_minhash"]}]`
   and `title: "F-030 attr_minhash"`.

2. **New async method** `launch_attr_minhash_backfill(self, partition_keys: list[str]) -> str`
   — copied verbatim from `launch_attr_lang_backfill`, referencing the new mutation constant.
   Full httpx error chain must be preserved: `TimeoutException`, `ConnectError`, `HTTPError`,
   non-2xx status check, JSON parse guard, GraphQL `errors` field check, `__typename` dispatch,
   raises `DagsterGatewayError` on any failure path.

3. Update the module-level docstring header that lists all public methods to include
   `launch_attr_minhash_backfill(partition_keys) -> str  # F-030`.

### 2.4 MODIFIED — `apps/api/dataplat_api/schemas/runs.py`

Add `"attr_minhash"` to the `RunCreate.asset` Literal:

```python
asset: Literal["extract_mineru", "chunks", "attr_quality", "attr_lang", "attr_minhash"]
```

Update the `RunCreate` docstring bullet that enumerates supported values to add:
```
- "attr_minhash" (F-030): run minhash dedup tagger.
```

### 2.5 MODIFIED — `apps/api/dataplat_api/routers/runs.py`

Insert a new `elif` branch for `"attr_minhash"` immediately before the terminal `else` clause.
Precede the new `elif` block with a comment `# F-030: attr_minhash backfill` (F7 fix):

```python
    # F-030: attr_minhash backfill
    elif body.asset == "attr_minhash":
        try:
            backfill_id = await gateway.launch_attr_minhash_backfill(partition_keys)
        except DagsterGatewayError as exc:
            return JSONResponse(status_code=503, content={"detail": str(exc)})
        kind = "attr_minhash"
        asset_keys = ["attr_minhash"]
```

No other logic changes: Steps 1–3 (source validation, partition key derivation, partition
registration) and Step 5 (Run row insert) are already asset-agnostic.

### 2.6 MODIFIED — `docker/dagster/Dockerfile`

Add `datasketch` to the `pip install --no-cache-dir` block. Add a comment line in the
comment block at the top:

```
# F-030: datasketch>=1.6 (MinHash + MinHashLSH for near-duplicate clustering);
# no version pin unless the local PyPI mirror restricts availability.
```

(F6 fix: states the `>=1.6` floor explicitly even when no pin is used.)

If the mirror only has a specific version, pin exactly and note it, as done for
`fasttext-langdetect==1.1.1`.

### 2.7 MODIFIED — `packages/api-types/openapi.json`

Regenerated automatically via `make codegen` after the `RunCreate.asset` Literal change in
§2.4. The implementer must run `make codegen` and commit the resulting diff in the **same
commit** as the schema change (CLAUDE.md invariant #6). No manual edits to this file.

### 2.8 NEW — `dagster/tests/test_minhash_tagger.py`

Unit tests for the pure helper module. No Docker, no LanceDB, no network required.
All tests patch or avoid I/O by testing the internal helpers directly.

Required test cases:

- **`test_compute_signature_deterministic`**: same text produces identical signature on two
  calls (MinHash with fixed seed / same permutations).
- **`test_compute_signature_empty_text`**: empty string and whitespace-only string do not raise;
  return a list of 128 elements (all int values).
- **`test_compute_signature_length`**: result is always length 128.
- **`test_cluster_rows_all_unique`**: N rows with completely distinct text → N distinct
  cluster IDs, each is_head=True.
- **`test_cluster_rows_near_duplicates`**: two rows with identical text → same cluster_id,
  exactly one is_head=True (the lex-lowest chunk_id).
- **`test_cluster_rows_head_election`**: three rows in same cluster → head is the
  lexicographically smallest chunk_id regardless of insertion order.
- **`test_cluster_rows_idempotent`**: running `_cluster_rows` twice on the same data
  produces identical cluster_id and is_head assignments.
- **`test_cluster_rows_order_invariant`** (F5 fix): create two lists with the same rows in
  different order, call `_cluster_rows` on each (after sorting by `chunk_id` within the test,
  simulating what `update_minhash_in_lance` does), assert that the resulting `cluster_id` and
  `is_head` assignments are identical by `chunk_id` key. This proves that the sort-then-cluster
  pipeline is order-independent.
- **`test_cluster_rows_empty_list`**: empty input list returns empty list (no crash).

### 2.9 MODIFIED — `verify/checks.sh`

Add a new `attr_minhash)` layer (mirroring the `attr_lang)` layer) and append
`bash "$0" attr_minhash` to the `all)` block.

The `attr_minhash)` layer performs:

1. **Unit tests**: `python -m pytest dagster/tests/test_minhash_tagger.py -v`
2. **Setup** (shared with other layers): mint JWT, create collection, upload PDF.
3. **Prereq chain**: trigger `extract_mineru`, poll to `COMPLETED_SUCCESS`; trigger `chunks`,
   poll to `COMPLETED_SUCCESS`. (Same prereq chain as `attr_lang`.)
4. **Baseline row count**: capture `row_count_before` via Python one-liner:
   ```python
   import lancedb, os
   storage_options = {
       "aws_access_key_id": os.environ["MINIO_ROOT_USER"],
       "aws_secret_access_key": os.environ["MINIO_ROOT_PASSWORD"],
       "endpoint": f"http://{os.environ['MINIO_ENDPOINT']}",
       "aws_region": "us-east-1",
       "allow_http": "true",
   }
   db = lancedb.connect(f"s3://{os.environ.get('MINIO_LANCE_BUCKET','lance')}/chunks", storage_options=storage_options)
   tbl = db.open_table("chunks")
   print(tbl.count_rows(f"source_id = {source_id} AND producer_asset = 'chunks'"))
   ```
5. **Trigger**: `POST /api/runs { "asset": "attr_minhash", "source_ids": [source_id] }` →
   expect HTTP 202, capture `backfill_id`.
6. **Poll**: poll Dagster backfill status to `COMPLETED_SUCCESS` (same poll loop as other layers).
7. **V1 — Signatures non-null and length 128**: Assert that every row for the source has a
   non-null `attr_minhash_signature` that is a list of exactly 128 elements:
   ```python
   rows = tbl.search().where(f"source_id = {source_id} AND producer_asset = 'chunks'") \
              .select(["attr_minhash_signature"]).to_list()
   assert all(r["attr_minhash_signature"] is not None and len(r["attr_minhash_signature"]) == 128 for r in rows)
   ```
8. **V2 — Exactly one head per cluster** (F4 fix — concrete snippet):
   ```python
   import collections
   rows = tbl.search().where(f"source_id = {source_id} AND producer_asset = 'chunks'") \
              .select(["attr_minhash_cluster_id", "attr_minhash_is_head"]).to_list()
   cluster_heads = collections.Counter(
       r["attr_minhash_cluster_id"] for r in rows if r["attr_minhash_is_head"]
   )
   all_clusters = set(r["attr_minhash_cluster_id"] for r in rows)
   assert all_clusters == set(cluster_heads.keys()), "some cluster has no head"
   assert all(v == 1 for v in cluster_heads.values()), "some cluster has multiple heads"
   ```
9. **V3 — No new rows**: `row_count_after == row_count_before`. Fail if not equal.
10. **V4 — Second run idempotent**: Trigger `attr_minhash` again for the same source, poll to
    `COMPLETED_SUCCESS`, re-run V1–V3 assertions. Additionally verify cluster_id labels are
    identical to first run (stable due to sort-by-chunk_id in step 2).

---

## §3 Design decisions

### D1 — Pure helper module, no Dagster imports in `minhash_tagger.py`

**Decision**: `minhash_tagger.py` imports only `os`, `logging`, `lancedb`, `datasketch`.
No Dagster imports.

**Rationale**: Identical to `lang_tagger.py` and `quality_tagger.py`. Pure modules are
importable in unit tests without standing up a full Dagster environment.

### D2 — Two-phase processing: fetch-sort-cluster-update

**Decision**: All rows for a source are fetched once, **sorted by `chunk_id` ascending**,
the full clustering is computed in-memory, and only then are per-row `table.update()` calls issued.

**Rationale**: MinHash LSH clustering is inherently a batch operation — you cannot assign
cluster IDs row-by-row. The canonical sort order guarantees deterministic, stable cluster labels
across re-runs (F3 fix).

### D3 — 128 permutations, Jaccard threshold 0.85, word-level 3-grams

**Decision**: `MinHash(num_perm=128)`, `MinHashLSH(threshold=0.85, num_perm=128)`,
shingles are word-level 3-grams (whitespace-split trigrams on lowercased text).

**Rationale**:
- 128 permutations matches `pa.list_(pa.uint64())` column in CHUNKS_SCHEMA.
- Threshold 0.85 — aggressive enough to catch near-duplicates, conservative enough to avoid
  false positives on merely similar content.
- Word 3-grams are robust to minor punctuation and casing variation.

### D4 — Head election: lexicographically smallest `chunk_id` in cluster

**Decision**: Within each cluster, the row with the lexicographically smallest `chunk_id`
(string comparison) is assigned `attr_minhash_is_head = True`.

**Rationale**: Deterministic, stable, human-auditable. Combined with the sort-by-chunk_id
in D2, ensures both cluster IDs and head assignments are idempotent.

### D5 — No cross-source clustering

**Decision**: Each source partition is processed independently.

**Rationale**: MVP within-source dedup only. Cross-source requires a persistent global LSH index.

### D6 — Sentinel: empty/whitespace text treated as normal rows

**Decision**: Empty-text chunks receive the MinHash of the empty shingle set and cluster normally.

**Rationale**: Deterministic, well-defined, avoids special-case branches. Empty chunks cluster
together (Jaccard = 1.0 on empty sets), lex-lowest `chunk_id` is head.

### D7 — `datasketch` version: `>=1.6` floor, pin only if mirror requires

**Decision**: No version pin unless the local PyPI mirror restricts availability.
Dockerfile comment states `>=1.6` floor.

**Rationale**: `datasketch` has a stable public API since 1.5.

### D8 — Signature stored via `.tolist()` conversion

**Decision**: `_compute_signature` returns `list(minhash.hashvalues.tolist())` — plain Python
list of `int`. PyArrow coerces to `pa.list_(pa.uint64())` on write.

**Rationale**: `.tolist()` converts NumPy `uint64` scalars to plain Python `int`, avoiding
potential issues with NumPy scalar types in the lancedb update path.

### D9 — OpenAPI regeneration in the same commit

**Decision**: `make codegen` run immediately after modifying `RunCreate.asset` Literal.

**Rationale**: CLAUDE.md invariant #6 is non-negotiable.

### D10 — `where_clause` always includes `producer_asset = 'chunks'`

**Decision**: Every query and update in `minhash_tagger.py` uses
`f"source_id = {source_id} AND producer_asset = 'chunks'"`.

**Rationale**: Matches `lang_tagger.py` and `quality_tagger.py` exactly. Prevents erroneously
processing rows from future `producer_asset` types (e.g., augmented translations).

---

## §4 Verification plan

### Unit tests (9 test cases in `dagster/tests/test_minhash_tagger.py`)

| Test | What it proves |
|---|---|
| `test_compute_signature_deterministic` | Signature is stable across repeated calls |
| `test_compute_signature_empty_text` | Sentinel path returns valid 128-element list |
| `test_compute_signature_length` | Output always length 128 (schema contract) |
| `test_cluster_rows_all_unique` | N distinct texts → N clusters, all heads |
| `test_cluster_rows_near_duplicates` | Two identical texts → 1 cluster, 1 head |
| `test_cluster_rows_head_election` | Lex-lowest chunk_id is always head |
| `test_cluster_rows_idempotent` | Re-clustering same input → identical output |
| `test_cluster_rows_order_invariant` | Sort-then-cluster is input-order-independent (F5) |
| `test_cluster_rows_empty_list` | Empty input handled gracefully |

### E2E checks (`bash verify/checks.sh attr_minhash`)

| Step | Maps to feature criterion |
|---|---|
| V1: all rows have 128-element non-null signature | "signature is computed for every chunk" |
| V2: exactly one is_head per cluster (concrete snippet) | "head election is correct" |
| V3: row count before == row count after | "column-mode update, no new rows" |
| V4: second run stable (V1–V3 + same cluster labels) | "idempotent" |

---

## §5 Invariants

| CLAUDE.md invariant | Applicability | How satisfied |
|---|---|---|
| #1 Lineage mandatory | Not applicable | Column-mode tagger attribute update, not a lineage event |
| #2 Storage separation + CAS | Not applicable | No new blob content stored |
| #3 Schema frozen post-publish | Satisfied | No schema change; columns already exist in CHUNKS_SCHEMA |
| #4 LLM calls through gateway | Not applicable | MinHash is purely algorithmic; no LLM call |
| #5 Async SQLAlchemy | Satisfied | gateway.py method is `async def`; router uses `AsyncSession` |
| #6 OpenAPI ↔ TS type sync | Satisfied | `make codegen` in same commit as Literal change |

No CLAUDE.md invariants are violated or waived by this feature.
