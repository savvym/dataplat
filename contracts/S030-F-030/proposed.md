# S030-F-030 — `attr_minhash` tagger: proposed.md

Sprint ID: S030-F-030  
Feature: F-030 `minhash_dedup`  
Status: PROPOSED

---

## §1 Summary

### What will be built

A MinHash-based near-duplicate detection tagger that operates on the chunks LanceDB table.  
For every source partition, the tagger:

1. Reads all chunks for that source (columns: `chunk_id`, `text`).
2. Computes a 128-shingle MinHash signature for every chunk using the `datasketch` library.
3. Clusters near-duplicates via `MinHashLSH` (Jaccard threshold 0.85, 128 permutations).
4. Assigns a deterministic integer `attr_minhash_cluster_id` to every chunk (clusters formed by
   the LSH query; singletons each get their own unique id).
5. Marks the lexicographically-lowest `chunk_id` within each cluster as the head
   (`attr_minhash_is_head = True`); all others get `False`.
6. Writes `attr_minhash_signature`, `attr_minhash_cluster_id`, and `attr_minhash_is_head` back
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
- `_compute_signature(text: str) -> list[int]` — builds word-level 3-grams (shingles),
  runs `datasketch.MinHash(num_perm=128)` over them, returns `list(minhash.hashvalues)`
  as Python `list[int]` (uint64 elements, matching `pa.list_(pa.uint64())`).
- `_cluster_rows(rows: list[dict]) -> list[dict]` — inserts each signature into a
  `MinHashLSH(threshold=0.85, num_perm=128)`, queries the LSH for each chunk's near-
  neighbours, assigns cluster IDs (0-based incrementing integer), marks the
  lexicographically smallest `chunk_id` in each cluster as head.
  Returns the same list augmented with `attr_minhash_signature`, `attr_minhash_cluster_id`,
  `attr_minhash_is_head`.
- `update_minhash_in_lance(source_id, ...)` — opens the `chunks` Lance table, fetches rows
  with `where_clause = f"source_id = {source_id}"`, calls `_cluster_rows`, then issues
  per-row `table.update(where=f"chunk_id = '{chunk_id}'", values={...})` for each chunk.
  Returns total row count for that source (for Dagster metadata).

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
                   "attr_minhash_cluster_id, attr_minhash_is_head in chunks table.",
   )
   def attr_minhash(context: AssetExecutionContext) -> MaterializeResult:
       partition_key = context.partition_key
       source_id = int(partition_key.removeprefix("src_"))
       row_count = update_minhash_in_lance(source_id)
       if row_count == 0:
           context.log.warning("attr_minhash: no rows found for source_id=%d", source_id)
       return MaterializeResult(
           metadata={"source_id": source_id, "rows_updated": row_count}
       )
   ```
   Add `attr_minhash` to the `assets=[...]` list in the `Definitions(...)` call.

### 2.3 MODIFIED — `apps/api/dataplat_api/dagster/gateway.py`

Two additions:

1. **New GraphQL mutation constant** `_LAUNCH_ATTR_MINHASH_BACKFILL_MUTATION` — copied verbatim
   from `_LAUNCH_ATTR_LANG_BACKFILL_MUTATION` with `assetSelection: [{"path": ["attr_minhash"]}]`
   and `title: "F-030 attr_minhash"`.

2. **New async method** `launch_attr_minhash_backfill(self, partition_keys: list[str]) -> str`
   — copied verbatim from `launch_attr_lang_backfill`, referencing the new mutation constant.
   Full httpx error chain must be preserved: `TimeoutException`, `ConnectError`, `HTTPError`,
   non-2xx status check, JSON parse guard, GraphQL `errors` field check, `__typename` dispatch,
   raises `DagsterGatewayError` on any failure path.

3. Update the module-level docstring header that lists all public methods to include
   `launch_attr_minhash_backfill`.

### 2.4 MODIFIED — `apps/api/dataplat_api/schemas/runs.py`

Add `"attr_minhash"` to the `RunCreate.asset` Literal:

```python
asset: Literal["extract_mineru", "chunks", "attr_quality", "attr_lang", "attr_minhash"]
```

Update the `RunCreate` docstring bullet that enumerates supported values.

### 2.5 MODIFIED — `apps/api/dataplat_api/routers/runs.py`

Insert a new `elif` branch for `"attr_minhash"` immediately before the terminal `else` clause:

```python
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

Add `datasketch` to the `pip install --no-cache-dir` block.  
No version pin is required beyond what `pip` resolves unless the local PyPI mirror restricts
availability (confirm `datasketch>=1.6` is present on the mirror; if not, note the pinned
version in the Dockerfile comment, as done for `fasttext-langdetect`).  
The comment block at the top of the file must be updated to reference F-030.

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
  return a list of 128 uint64 values.
- **`test_compute_signature_length`**: result is always length 128.
- **`test_cluster_rows_all_unique`**: N rows with completely distinct text → N distinct
  cluster IDs, each is_head=True.
- **`test_cluster_rows_near_duplicates`**: two rows with identical text → same cluster_id,
  exactly one is_head=True (the lex-lowest chunk_id).
- **`test_cluster_rows_head_election`**: three rows in same cluster → head is the
  lexicographically smallest chunk_id regardless of insertion order.
- **`test_cluster_rows_idempotent`**: running `_cluster_rows` twice on the same data
  produces identical cluster_id and is_head assignments.
- **`test_cluster_rows_empty_list`**: empty input list returns empty list (no crash).

### 2.9 MODIFIED — `verify/checks.sh`

Add a new `attr_minhash)` layer (mirroring the `attr_lang)` layer) and append
`bash "$0" attr_minhash` to the `all)` block.

The `attr_minhash)` layer performs:

1. **Setup** (shared with other layers): mint JWT, create collection, upload PDF.
2. **Prereq chain**: trigger `extract_mineru`, poll to `COMPLETED_SUCCESS`; trigger `chunks`,
   poll to `COMPLETED_SUCCESS`. (Same prereq chain as `attr_lang`.)
3. **Baseline row count**: capture `row_count_before` from LanceDB via Python one-liner
   (`lancedb.connect(...).open_table("chunks").count_rows(f"source_id = {source_id}")`).
4. **Trigger**: `POST /api/runs { "asset": "attr_minhash", "source_ids": [source_id] }` →
   expect HTTP 202, capture `backfill_id`.
5. **Poll**: poll Dagster backfill status to `COMPLETED_SUCCESS` (same poll loop as other layers).
6. **V1 — Signatures non-null**: Assert that every row for the source has a non-null, non-empty
   `attr_minhash_signature` (list of 128 uint64 values). Fail if any row has null or length ≠ 128.
7. **V2 — Exactly one head per cluster**: Group rows by `attr_minhash_cluster_id`; assert that
   exactly one row per cluster has `attr_minhash_is_head = True`.
8. **V3 — No new rows (idempotency)**: `row_count_after == row_count_before`. Fail if not equal.
9. **V4 — Second run idempotent**: Trigger `attr_minhash` again for the same source, poll to
   `COMPLETED_SUCCESS`, re-run V1–V3 assertions. Row count and cluster assignments must be stable.

---

## §3 Design decisions

### D1 — Pure helper module, no Dagster imports in `minhash_tagger.py`

**Decision**: `minhash_tagger.py` imports only `os`, `lancedb`, `datasketch`, and `pyarrow`.
No Dagster imports (`AssetExecutionContext`, `MaterializeResult`, etc.).

**Rationale**: Identical to `lang_tagger.py` and `quality_tagger.py`. Pure modules are
importable in unit tests without standing up a full Dagster environment. The Dagster asset
definition (in `definitions.py`) is the thin wrapper that calls the pure helper.

### D2 — Two-phase processing: cluster-first, then per-row update

**Decision**: All rows for a source are fetched once, the full clustering is computed in-memory,
and only then are per-row `table.update()` calls issued.

**Rationale**: MinHash LSH clustering is inherently a batch operation — you cannot assign
cluster IDs row-by-row because the cluster boundaries are not known until all signatures have
been inserted into the LSH index. The single-pass fetch-cluster-update avoids re-opening the
table and re-reading rows mid-update.

### D3 — 128 permutations, Jaccard threshold 0.85, word-level 3-grams

**Decision**: `MinHash(num_perm=128)`, `MinHashLSH(threshold=0.85, num_perm=128)`,
shingles are word-level 3-grams (whitespace-split trigrams on lowercased text).

**Rationale**:
- 128 permutations balances accuracy (low false-negative rate) with memory and serialisation
  cost. The Arrow column type `pa.list_(pa.uint64())` with fixed length 128 was already encoded
  in `CHUNKS_SCHEMA` when the schema was designed for F-025.
- Threshold 0.85 matches the dedup aggressiveness stated in `feature_list.json` for F-030.
- Word 3-grams are robust to minor punctuation and casing variation while being meaningful
  for natural-language prose; character shingles are noisier at this granularity.

### D4 — Head election: lexicographically smallest `chunk_id` in cluster

**Decision**: Within each cluster, the row with the lexicographically smallest `chunk_id`
(string comparison) is assigned `attr_minhash_is_head = True`.

**Rationale**: `chunk_id` values are UUIDs assigned sequentially during chunking; the
lexicographically smallest UUID within a cluster is therefore the earliest-created chunk for
that source, providing a stable, deterministic, human-auditable head selection rule. This is
idempotent: re-running on the same data always picks the same head.

### D5 — No cross-source clustering

**Decision**: Each source partition is processed independently; no LSH index is shared across
sources.

**Rationale**: The MVP dedup goal is within-source near-duplicate removal (e.g., repeated
sections in a single PDF). Cross-source dedup requires a persistent global LSH index with
upsert semantics, which is a separate storage-management problem beyond F-030 scope.
Agreed.md §1.3 defers cross-collection deduplication.

### D6 — Sentinel: empty/whitespace text treated as normal rows

**Decision**: Chunks where `text` is `None`, empty, or whitespace-only are not skipped;
they receive the MinHash of the empty shingle set and participate in clustering normally.

**Rationale**: The empty-shingle MinHash is deterministic and well-defined; `datasketch`
handles it without raising. All empty-text chunks for a source will cluster together (Jaccard
similarity = 1.0 on empty sets), with the lex-lowest `chunk_id` as head. This avoids a
sentinel-value special case in the update path and keeps the row count invariant trivial
(all rows written, no skips).

### D7 — `datasketch` version: unpin unless mirror forces a pin

**Decision**: No version pin in the Dockerfile unless the local PyPI mirror lacks the latest
release.

**Rationale**: `datasketch` has a stable public API since 1.5; any 1.x release is compatible.
Pinning to a specific patch version without a concrete reason adds maintenance burden. If the
mirror only has a specific version, add a comment and pin exactly as done for
`fasttext-langdetect==1.1.1`.

### D8 — Signature stored as `list[int]` matching `pa.list_(pa.uint64())`

**Decision**: `_compute_signature` returns `list(minhash.hashvalues.tolist())` — a plain
Python list of integers. LanceDB / PyArrow coerces this to `pa.list_(pa.uint64())` on write
via `table.update(values={"attr_minhash_signature": ...})`.

**Rationale**: `minhash.hashvalues` is a NumPy `uint64` array; `.tolist()` converts it to a
Python list of `int`, which PyArrow can upcast to `uint64`. This is the same coercion that
the chunker uses when writing the initial `None` values for these columns, so the round-trip
is safe. Passing the raw NumPy array would also work but introduces a NumPy dependency in the
tagger module unnecessarily.

### D9 — OpenAPI regeneration in the same commit as schema change

**Decision**: `make codegen` is run immediately after modifying `RunCreate.asset` Literal, and
the `packages/api-types/` diff is included in the same git commit.

**Rationale**: CLAUDE.md invariant #6 is non-negotiable. CI will reject mismatches. This is
the established pattern for every previous asset addition (`attr_quality`, `attr_lang`).

---

## §4 Verification plan

### Unit tests (run inside Dagster container or local `uv run pytest`)

All tests live in `dagster/tests/test_minhash_tagger.py` and import directly from
`dagster_platform.minhash_tagger`. No mocking of I/O is needed for the pure internal helpers.

| Test | What it proves |
|---|---|
| `test_compute_signature_deterministic` | Signature is stable across repeated calls |
| `test_compute_signature_empty_text` | Sentinel path returns valid 128-element list |
| `test_compute_signature_length` | Output always length 128 (schema contract) |
| `test_cluster_rows_all_unique` | N distinct texts → N clusters, all heads |
| `test_cluster_rows_near_duplicates` | Two identical texts → 1 cluster, 1 head |
| `test_cluster_rows_head_election` | Lex-lowest chunk_id is always head |
| `test_cluster_rows_idempotent` | Re-clustering same input → identical output |
| `test_cluster_rows_empty_list` | Empty input is handled gracefully |

The `checks.sh extract` block already invokes `python -m pytest dagster/tests/` inside the
Dagster webserver container. These new tests will be picked up automatically by that invocation.

### E2E checks (`bash verify/checks.sh attr_minhash`)

The new `attr_minhash)` layer in `checks.sh` exercises the full pipeline end-to-end:

| Verification step | Maps to feature criterion |
|---|---|
| V1: all rows have 128-element non-null signature | "signature is computed for every chunk" |
| V2: exactly one is_head per cluster | "head election is correct" |
| V3: row count before == row count after | "column-mode update, no new rows (idempotency)" |
| V4: second run stable (V1–V3 pass again) | "re-running is idempotent" |

The `all)` block in `checks.sh` will also execute `bash "$0" attr_minhash` so the full
suite covers this layer.

### Manual smoke check (optional, pre-submit)

```bash
docker compose -f docker/docker-compose.dev.yml exec dagster-webserver \
  python -c "
from dagster_platform.minhash_tagger import update_minhash_in_lance
n = update_minhash_in_lance(1)
print('rows updated:', n)
"
```

Expected: prints a non-negative integer without raising.

---

## §5 Invariants

| CLAUDE.md invariant | Applicability | How satisfied |
|---|---|---|
| #1 Lineage mandatory | **Not applicable** | `attr_minhash` updates a tagger attribute column in LanceDB; it does not create a new Commit in the lineage graph. Tagging operations are not lineage events. |
| #2 Storage separation + CAS | **Not applicable** | No new blob content is stored; the three minhash columns are derived scalar/list attributes in the existing chunks table. |
| #3 Schema frozen post-publish | **Satisfied by design** | No schema change: all three target columns already exist in `CHUNKS_SCHEMA`. No migration required. |
| #4 LLM calls through gateway | **Not applicable** | MinHash is purely algorithmic; no LLM call is made anywhere in this feature. |
| #5 Async SQLAlchemy | **Satisfied** | `gateway.py` method uses `async def` and `await`. Router uses `AsyncSession` via `Depends(get_session)`. No sync session introduced. |
| #6 OpenAPI ↔ TS type sync | **Satisfied** | `make codegen` run immediately after `RunCreate.asset` Literal change; `packages/api-types/` diff committed in the same commit. |

No CLAUDE.md invariants are violated or waived by this feature.
