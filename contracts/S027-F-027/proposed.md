# S027-F-027 — Trigger quality tagger — Proposed Contract

## 1. What

Extend `POST /api/runs` so callers can pass `asset: "attr_quality"`.  
When the API receives that value it triggers a Dagster partitioned backfill for the
new `attr_quality` asset.  The asset reads already-chunked rows from the Lance
`chunks` table, scores them with a **stub length-heuristic scorer** (no LLM call),
and writes `attr_quality_score` and `attr_quality_provider` back to those rows.

The stub scorer formula is:

```
attr_quality_score    = min(1.0, float(row["token_count"]) / 512.0)
attr_quality_provider = "length_heuristic"
```

This satisfies the verification criterion "provider is set to a non-null string" and
keeps the code free of any LLM dependency.  A real LLM scorer is deferred to F-028,
which will replace the stub by routing through `apps/api/dataplat_api/llm/` once
that gateway exists.

### Scope boundary

F-027 delivers:
- API layer: `asset: "attr_quality"` accepted by `POST /api/runs`.
- Dagster layer: new `attr_quality` asset registered in `definitions.py`.
- Dagster layer: new `quality_tagger.py` helper module with stub scorer.
- Lance layer: `attr_quality_score` and `attr_quality_provider` columns populated for
  every chunk row belonging to the scored source.
- Checks layer: new `attr_quality` check layer in `verify/checks.sh`.

F-027 does NOT deliver:
- Any LLM call (gateway does not exist yet; invariant #4 compliance).
- `merge_insert()` or full upsert sophistication beyond delete+reinsert.
- Column-mode IO manager dispatch (deferred to F-028, TODO already marked in
  `lance_io_manager.py`).

---

## 2. Files changed / created

### New files

| File | Purpose |
|---|---|
| `dagster/dagster_platform/quality_tagger.py` | Pure helper module: stub scorer + Lance write helpers for the `attr_quality` asset.  No Dagster imports (same no-Dagster guarantee as `chunker.py`). |
| `dagster/tests/test_quality_tagger.py` | Unit tests for `compute_quality_scores()`: score range, provider string, edge cases. |

### Modified files

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/runs.py` | Widen `asset` Literal: `Literal["extract_mineru", "chunks"]` → `Literal["extract_mineru", "chunks", "attr_quality"]`. |
| `apps/api/dataplat_api/routers/runs.py` | Add `elif body.asset == "attr_quality"` branch before the existing `else` in `trigger_extract_run()`; call `gateway.launch_attr_quality_backfill(partition_keys)` and use `kind = "attr_quality"`. |
| `apps/api/dataplat_api/dagster/gateway.py` | Add `_LAUNCH_ATTR_QUALITY_BACKFILL_MUTATION` constant + `launch_attr_quality_backfill(partition_keys: list[str]) -> str` method, structurally identical to `launch_chunks_backfill()` but with `assetSelection: [{path: ["attr_quality"]}]`. |
| `packages/api-types/openapi.json` | Add `"attr_quality"` to the `asset` enum in the `RunCreate` schema component (manual edit — no Makefile; implementer must regenerate from FastAPI's `/openapi.json` export or edit in place and verify round-trip consistency). |
| `dagster/dagster_platform/definitions.py` | Import helpers from `quality_tagger.py`; define `attr_quality` asset; register it in `defs`. |
| `verify/checks.sh` | Add `attr_quality)` layer; add `bash "$0" attr_quality` to the `all)` case. |

---

## 3. Design decisions

### D1 — Stub scorer; no LLM call

The feature spec states `attr_quality_provider` should be set to an LLM model name
such as `"gpt-4o-mini"`.  However, the LLM gateway (`apps/api/dataplat_api/llm/`)
does not yet exist, and hard invariant #4 forbids calling any LLM SDK directly from
a processor.  Therefore F-027 uses a deterministic stub:

```python
attr_quality_score    = min(1.0, float(row["token_count"]) / 512.0)
attr_quality_provider = "length_heuristic"
```

The stub produces a non-null, bounded [0, 1] float and a non-null provider string,
satisfying V2 and V3 of the verification plan.  F-028 will route through
`LLMGateway` once it is built, replacing `"length_heuristic"` with the real model
name and the heuristic with an LLM prompt.  The stub formula is intentional
enough to be semantically meaningful (longer ≈ fuller, up to budget).

### D2 — `attr_quality` asset writes to Lance directly; does NOT use `LanceChunksIOManager`

`LanceChunksIOManager` (F-026) is designed for **row-mode inserts**: it deletes all
rows for `(source_id, producer_asset)` then calls `table.add(new_rows)`.  The
quality tagger does not insert new rows — it updates two columns on **existing**
`producer_asset = 'chunks'` rows.  Re-using `LanceChunksIOManager` would therefore
require either:
(a) reading all existing chunk fields back out of Lance and re-inserting the full
    row set (fragile, breaks lineage of the original `chunks` insert), or
(b) extending the IO manager with column-mode support (that is exactly F-028,
    marked as a TODO in `lance_io_manager.py`).

Decision: the `attr_quality` asset manages its own Lance write by calling the helper
`write_quality_scores_to_lance()` in `quality_tagger.py`, which applies the
established **delete-then-add** pattern scoped to `producer_asset = 'attr_quality'`
rows.

Specifically, the approach is:
1. Read all chunk rows for the source from Lance where `producer_asset = 'chunks'`.
2. For each row, compute the quality score using the stub.
3. Build a new list of rows with `producer_asset = "attr_quality"`, copying all
   fields from the original chunk rows but overwriting `attr_quality_score`,
   `attr_quality_provider`, `producer_asset`, `producer_version`, `updated_at`, and
   setting `augmented_from = original chunk_id`.
4. Delete any existing `producer_asset = 'attr_quality'` rows for this source.
5. Insert the new rows.

This keeps each producer's rows self-contained, preserves lineage via
`augmented_from`, and avoids mutating rows owned by a different producer asset.

### D3 — `producer_asset = "attr_quality"` rows are augmentation rows, not mutations

Rather than in-place column patching of `producer_asset = 'chunks'` rows (which
would violate the principle that each row's `producer_asset` describes its origin),
the quality tagger inserts a parallel set of rows where:

```
producer_asset   = "attr_quality"
augmented_from   = <original chunk_id>
augmenter_id     = "quality_tagger"
augmenter_config_hash = <sha256 of scorer config, or "stub_v0.1" for the stub>
```

All other content fields (`text`, `token_count`, `docling_refs`, `source_refs`) are
copied verbatim from the source chunk.  Downstream consumers wishing to retrieve
quality-scored text join on `augmented_from` or filter on `producer_asset =
'attr_quality'`.

This interpretation is consistent with the CHUNKS_SCHEMA `augmented_from` /
`augmenter_id` / `augmenter_config_hash` fields (agreed.md §4.2) and with the design
doc's lineage invariant (#1).

### D4 — `attr_quality` asset does not use `io_manager_key`

Since the asset manages its own Lance write (D2), it should NOT declare
`io_manager_key="lance_chunks_io"`.  It returns a `MaterializeResult` (same as
`extract_mineru`) rather than a bare `list[dict]`.

### D5 — Lance read uses a simple `to_arrow()` scan filtered by `source_id` + `producer_asset`

`quality_tagger.py` reads source chunk rows as:
```python
tbl.to_arrow(filter=f"source_id = {source_id} AND producer_asset = 'chunks'")
```
This is the safest read pattern in lancedb 0.30.2 (confirmed in `lance_io_manager.py`
tests).  No full-table scan; the predicate push-down limits I/O.

### D6 — `launch_attr_quality_backfill()` mirrors `launch_chunks_backfill()` exactly

The GraphQL mutation shape for `launchPartitionBackfill` is identical for all three
assets (`extract_mineru`, `chunks`, `attr_quality`).  Only `assetSelection[0].path`
differs.  The new method in `gateway.py` will share the same structure and use the
same `_REPOSITORY_LOCATION_NAME` / `_REPOSITORY_NAME` constants.

### D7 — `RunCreate.asset` Literal widened; `Run.kind` column is plain text

`Run.kind` in `apps/api/dataplat_api/db/models.py` is `sa.Text`, not a PG enum, so
no migration is needed.  The only schema change is widening the Pydantic Literal in
`schemas/runs.py`.

### D8 — No Makefile; `packages/api-types/openapi.json` updated manually

The project has no `Makefile`.  The implementer must update the `asset` enum in
`openapi.json` by one of two methods (in order of preference):
1. Start the FastAPI server in a scratch container and `GET /openapi.json`, then
   replace the file.
2. Directly edit the `asset` enum array at the `RunCreate` component to add
   `"attr_quality"` and verify the JSON is still valid.

Method 2 is acceptable because the change is a single enum value addition to a known
location, and the CI `openapi` check will catch any drift.

### D9 — `verify/checks.sh` `attr_quality` layer follows the `chunks` layer pattern

The new layer will:
- V1: POST `{"source_ids": [<id>], "asset": "attr_quality"}` to `/api/runs`,
  assert HTTP 202, capture run_id.
- V2: Poll Dagster until the backfill completes (same timeout/polling as `chunks`
  layer).
- V3: Query Lance and assert `attr_quality_score IS NOT NULL` for all rows belonging
  to the source.
- V4: Query Lance and assert `attr_quality_provider = 'length_heuristic'` for all
  rows.

---

## 4. Verification plan

The three verification criteria from the feature spec map to checks as follows:

| Criterion | Check layer step | Pass condition |
|---|---|---|
| V1 — `POST /api/runs` with `asset: "attr_quality"` returns 202 | `checks.sh attr_quality` → `curl -s -o /dev/null -w "%{http_code}"` on `POST /api/runs` | HTTP status code is `202` |
| V2 — `attr_quality_score` is populated (not null) for every chunk row after job completes | Lance scan: `SELECT attr_quality_score FROM chunks WHERE source_id=<id> AND producer_asset='attr_quality'`; assert no NULL values | Zero rows with `attr_quality_score IS NULL` |
| V3 — `attr_quality_provider` is set to a recognisable scorer string | Lance scan: `SELECT DISTINCT attr_quality_provider FROM chunks WHERE source_id=<id> AND producer_asset='attr_quality'` | Exactly one distinct value: `"length_heuristic"` |

Additional unit-test verification (not in `checks.sh`, but required for APPROVED):

| Test | File | Pass condition |
|---|---|---|
| Score is in `[0.0, 1.0]` for any input | `test_quality_tagger.py` | `pytest` green |
| `token_count=0` → `score=0.0` | `test_quality_tagger.py` | Edge case asserted |
| `token_count=512` → `score=1.0` | `test_quality_tagger.py` | Exact boundary asserted |
| `token_count=1024` → `score=1.0` (cap) | `test_quality_tagger.py` | Cap asserted |
| `attr_quality_provider = "length_heuristic"` for all rows | `test_quality_tagger.py` | Field value asserted |

---

## 5. Invariant compliance

| # | Invariant | Compliance |
|---|---|---|
| 1 | Lineage mandatory | Each scored row carries `augmented_from = <chunk_id>`, `augmenter_id = "quality_tagger"`, `augmenter_config_hash = "stub_v0.1"`. Parent chunk_id is traceable. |
| 2 | Storage separation + CAS | Quality scores are written to Lance (content store); no blob bytes go to Postgres. `Run` row in Postgres stores only the run_id and kind string. |
| 3 | Schema frozen post-publish | No schema change to Lance or Postgres.  `attr_quality_score` and `attr_quality_provider` already exist in `CHUNKS_SCHEMA` (they were `NULL` before).  No new columns added. |
| 4 | LLM calls through gateway only | Stub uses a pure arithmetic formula — zero LLM calls.  No Anthropic/OpenAI SDK imported.  When F-028 replaces the stub, it must route through `apps/api/dataplat_api/llm/`. |
| 5 | Async SQLAlchemy | All new code in `apps/api/` (schemas, router, gateway) uses the existing async session patterns.  `quality_tagger.py` lives in the Dagster layer and uses raw psycopg2 (same pattern as `extractor.py` and `chunker.py`; invariant #5 is scoped to `apps/api/dataplat_api/`). |
| 6 | OpenAPI ↔ TS type sync | `packages/api-types/openapi.json` updated in the same commit as `schemas/runs.py`. The `RunCreate.asset` enum gains `"attr_quality"` in both files simultaneously. |

---

## 6. Risks and open questions

### R1 — Lance `to_arrow()` performance for large sources (MEDIUM)
Reading all `producer_asset = 'chunks'` rows for a source into memory in one call
may be slow for sources with many chunks.  For MVP this is acceptable; F-028 can
introduce streaming or batched reads if profiling shows a problem.

### R2 — lancedb 0.30.2 predicate push-down correctness (LOW)
The `filter=` argument to `to_arrow()` has been confirmed working for simple
`AND`-predicates in the existing `lance_io_manager.py` tests.  No new risk beyond
what is already present in `chunks`.

### R3 — `merge_insert()` vs. delete+reinsert (DEFERRED TO IMPLEMENTER)
lancedb 0.30.2 exposes a `merge_insert()` API.  If the implementer finds it reliable
in the running container they may use it to avoid the read-back step (D2 / D5).
If `merge_insert()` proves unreliable, delete+reinsert is the safe fallback.
The contract does not mandate which mechanism is used, but the implemented behaviour
must be idempotent (re-running the asset for the same `source_id` must not duplicate
rows).

### R4 — `CHUNKS_SCHEMA` duplication (LOW, existing risk)
`CHUNKS_SCHEMA` is duplicated between `chunker.py` and
`apps/api/dataplat_api/storage/lance.py` (agreed.md R6).  F-027 adds no new
duplication; both copies already contain `attr_quality_score` and
`attr_quality_provider`.  If a future sprint changes either schema, both files must
be updated in the same commit.

### R5 — Backfill prerequisite: source must have `chunks` rows (MEDIUM)
The `attr_quality` asset depends on the `chunks` asset having run for the same
`source_id`.  If no `chunks` rows exist, `to_arrow()` returns an empty table and the
asset will write zero scored rows.  The `checks.sh` layer must ensure the `chunks`
layer has run for the test source_id before triggering `attr_quality`.  The
dependency is a process dependency, not a Dagster asset dependency, and is handled
by check ordering in `checks.sh`.

### R6 — No Dagster asset dependency declared between `chunks` and `attr_quality` (ACCEPTED)
Adding a formal `deps=["chunks"]` to the `attr_quality` asset spec would require
Dagster to schedule them together.  For MVP, the API caller is expected to trigger
them in order (chunks first, then attr_quality).  If ordering enforcement is needed,
it can be added in a later sprint via a Dagster sensor or asset check.

### R7 — Manual `openapi.json` update (LOW)
Without `make codegen` the implementer must edit `openapi.json` manually.  The CI
`openapi` check (if present) will catch drift.  If no CI check exists, the reviewer
must diff the `RunCreate.asset` enum before approving.
