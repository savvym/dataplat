# S027-F-027 — Trigger quality tagger — Agreed Contract

> Derived from `proposed.md` with all 5 findings from `feedback.md` addressed:
> Issue 1 BLOCKING (D3 augmentation rows → column-mode update),
> Issue 2 BLOCKING (verification queries remove producer_asset filter),
> Issue 3 REQUIRED (D2 rewritten for column mode),
> Issue 4 MINOR (explicit elif + defensive else in router),
> Issue 5 MINOR (stub-deviation comment in checks.sh).

---

## 1. What

Extend `POST /api/runs` so callers can pass `asset: "attr_quality"`.
When the API receives that value it triggers a Dagster partitioned backfill for the
new `attr_quality` asset. The asset reads already-chunked rows from the Lance
`chunks` table, scores them with a **stub length-heuristic scorer** (no LLM call),
and **updates** `attr_quality_score` and `attr_quality_provider` columns on existing
`producer_asset='chunks'` rows in-place. No new rows are created.

The stub scorer formula is:

```
attr_quality_score    = min(1.0, float(token_count) / 512.0)
attr_quality_provider = "length_heuristic"
```

This satisfies all three verification criteria and keeps the code free of any LLM
dependency. A real LLM scorer is deferred to F-028, which will replace the stub by
routing through `apps/api/dataplat_api/llm/` once that gateway exists.

### Scope boundary

F-027 delivers:
- API layer: `asset: "attr_quality"` accepted by `POST /api/runs`.
- Dagster layer: new `attr_quality` asset registered in `definitions.py`.
- Dagster layer: new `quality_tagger.py` helper module with stub scorer.
- Lance layer: `attr_quality_score` and `attr_quality_provider` columns populated on
  existing chunk rows (column-mode update, zero new rows).
- Checks layer: new `attr_quality)` check layer in `verify/checks.sh`.

F-027 does NOT deliver:
- Any LLM call (gateway does not exist yet; invariant #4 compliance).
- Column-mode IO manager dispatch (deferred to F-031; TODO in `lance_io_manager.py`).

---

## 2. Files changed / created

### New files

| File | Purpose |
|---|---|
| `dagster/dagster_platform/quality_tagger.py` | Pure helper module: stub scorer + Lance column-update helper. No Dagster imports (same no-Dagster guarantee as `chunker.py`). |
| `dagster/tests/test_quality_tagger.py` | Unit tests for `compute_quality_score()`: score range, provider string, edge cases. |

### Modified files

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/runs.py` | Widen `asset` Literal: `Literal["extract_mineru", "chunks"]` → `Literal["extract_mineru", "chunks", "attr_quality"]`. |
| `apps/api/dataplat_api/routers/runs.py` | Add `elif body.asset == "attr_quality"` branch; convert existing `else` to explicit `elif body.asset == "chunks":`; add defensive `else: raise ValueError(...)` (Issue 4 fix). |
| `apps/api/dataplat_api/dagster/gateway.py` | Add `_LAUNCH_ATTR_QUALITY_BACKFILL_MUTATION` constant + `launch_attr_quality_backfill()` method. |
| `packages/api-types/openapi.json` | Add `"attr_quality"` to the `asset` enum in `RunCreate` schema (invariant #6). |
| `dagster/dagster_platform/definitions.py` | Import helpers from `quality_tagger.py`; define `attr_quality` asset; register in `defs`. |
| `verify/checks.sh` | Add `attr_quality)` layer; add `bash "$0" attr_quality` to the `all)` case after `chunks`. |

---

## 3. Design decisions

### D1 — Stub scorer; no LLM call

The LLM gateway (`apps/api/dataplat_api/llm/`) does not yet exist, and hard invariant
#4 forbids calling any LLM SDK directly. F-027 uses a deterministic stub:

```python
score = min(1.0, float(token_count) / 512.0)
provider = "length_heuristic"
```

F-028 will replace this with real LLM scoring once the gateway is built.

### D2 — Column-mode update: modify existing rows, zero new rows

The `attr_quality` asset performs a **column-mode update** on existing
`producer_asset='chunks'` rows. It does NOT create new rows, does NOT use
`LanceChunksIOManager` (which only supports row-mode insert, per F-026), and does
NOT change `producer_asset`, `augmented_from`, or any lineage fields.

The asset calls `update_quality_scores_in_lance()` in `quality_tagger.py` which
updates the `attr_quality_score` and `attr_quality_provider` columns on rows matching
`source_id = {id} AND producer_asset = 'chunks'`.

**Implementation:** Use lancedb's `table.update()` with per-row scoring. Two options
are acceptable:

*Option A — table.update() with values_sql (simplest, no Python read-back):*
```python
table.update(
    where=f"source_id = {source_id} AND producer_asset = 'chunks'",
    values_sql={
        "attr_quality_score": "LEAST(1.0, CAST(token_count AS FLOAT) / 512.0)",
        "attr_quality_provider": "'length_heuristic'",
    }
)
```

*Option B — read → compute → merge_insert (matches design doc §8.2):*
```python
rows = table.to_arrow(filter=f"source_id = {source_id} AND producer_asset = 'chunks'")
scored = [{"chunk_id": r["chunk_id"], "attr_quality_score": score, "attr_quality_provider": "length_heuristic"} for r in rows.to_pylist()]
table.merge_insert("chunk_id").when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"]).execute(scored)
```

Either option satisfies the invariant: **zero new rows; two columns updated on
existing rows**. Idempotency is automatic (re-running overwrites the same columns).

The implementer should test both options in the running container and pick whichever
works reliably with lancedb==0.30.2.

### D3 — No lineage fields modified (taggers are NOT augmenters)

Per design doc §8.2, taggers update attribute columns only. The `augmented_from`,
`augmenter_id`, and `augmenter_config_hash` fields are augmenter lineage fields and
are NOT used by taggers. The quality tagger leaves these NULL on the existing rows.

### D4 — `attr_quality` asset does not use `io_manager_key`

Since the asset performs a column-mode update (not a row-mode insert), it cannot use
`LanceChunksIOManager` (which only supports row mode). It returns `MaterializeResult`
with metadata (same pattern as `extract_mineru`).

### D5 — `launch_attr_quality_backfill()` mirrors `launch_chunks_backfill()` exactly

The GraphQL mutation shape is identical. Only `assetSelection[0].path` differs
(`["attr_quality"]` instead of `["chunks"]`).

### D6 — `RunCreate.asset` Literal widened; `Run.kind` is plain text

`Run.kind` in models.py is `sa.Text`, not a PG enum. No migration needed. The router
dispatch uses explicit `elif` branches with a defensive `else: raise ValueError(...)`.

### D7 — `packages/api-types/openapi.json` updated in same commit

The `asset` enum gains `"attr_quality"` in both `schemas/runs.py` and `openapi.json`
simultaneously (invariant #6).

### D8 — `verify/checks.sh` `attr_quality` layer

Pattern follows `chunks)` layer. The layer:
1. Requires chunks to have already run for the test source (either rely on `chunks)`
   having run earlier in `all)`, or trigger extract+chunks inline).
2. POST `attr_quality` → poll to COMPLETED_SUCCESS.
3. Query Lance for attr_quality_score and attr_quality_provider.

---

## 4. Verification plan

| Criterion | Check | Pass condition |
|---|---|---|
| V1 — POST returns 202 | `curl` POST `/api/runs` with `{"asset": "attr_quality", "source_ids": [<id>]}` | HTTP 202, response body has `dagster_run_id` |
| V2 — attr_quality_score populated | Lance query: `WHERE source_id=<id>` — count rows where `attr_quality_score IS NULL` | Zero null rows |
| V3 — attr_quality_provider set | Lance query: `WHERE source_id=<id>` — check `attr_quality_provider` value | All rows have `"length_heuristic"` |

**Important:** V2 and V3 query by `source_id` only (NO `producer_asset` filter).
After the column-mode update, the existing `producer_asset='chunks'` rows carry the
scores, so querying all rows for the source returns non-null values.

### checks.sh `attr_quality)` layer structure

```bash
attr_quality)
  # Unit tests
  docker compose -f "$COMPOSE" exec -T dagster-webserver \
    python -m pytest /app/dagster/tests/test_quality_tagger.py -q || exit 1

  # Prerequisite: ensure chunks exist for test source
  # (rely on chunks) having run earlier in all), or trigger inline)

  # V1: POST /api/runs
  HTTP_CODE=$(curl -s -o body.json -w "%{http_code}" ...)
  [ "$HTTP_CODE" = "202" ] || exit 1

  # Poll backfill to COMPLETED_SUCCESS (120s/3s)
  ...

  # V2: attr_quality_score non-null for all rows
  # V3: attr_quality_provider = "length_heuristic"
  # F-027 stub deviation: provider is "length_heuristic", not an LLM model name.
  # F-028 will replace with real model name once LLM gateway exists.
  docker compose -f "$COMPOSE" exec -T fastapi python -c "..."
```

### Unit tests

| Test | Pass condition |
|---|---|
| `test_score_zero_tokens` | token_count=0 → score=0.0 |
| `test_score_at_budget` | token_count=512 → score=1.0 |
| `test_score_above_budget` | token_count=1024 → score=1.0 (capped) |
| `test_score_range` | Any token_count → 0.0 ≤ score ≤ 1.0 |
| `test_provider_string` | provider == "length_heuristic" |

---

## 5. Invariant compliance

| # | Invariant | Compliance |
|---|-----------|------------|
| 1 | Lineage mandatory | The `run` table in Postgres records Dagster backfill_id. Lance row lineage fields (producer_asset, producer_version) are unchanged by the tagger — it only updates attr_* columns. |
| 2 | Storage separation + CAS | Quality scores written to Lance (S3-backed). No blob bytes in Postgres. |
| 3 | Schema frozen post-publish | No schema change. attr_quality_score and attr_quality_provider already exist in CHUNKS_SCHEMA (NULL → populated). |
| 4 | LLM calls through gateway | Zero LLM calls. Pure arithmetic stub. No Anthropic/OpenAI SDK imported. |
| 5 | Async SQLAlchemy | New API code (schemas, router, gateway) uses existing async patterns. quality_tagger.py is in Dagster layer, not apps/api/. |
| 6 | OpenAPI ↔ TS type sync | openapi.json updated in same commit as schemas/runs.py. |

---

## 6. Risks

### R1 — Lance `table.update()` with `values_sql` availability (MEDIUM)
Reviewer confirmed `values_sql` is present in lancedb 0.30.2. If it doesn't work at
runtime, fall back to Option B (read → merge_insert). Implementer should test in
container before committing.

### R2 — Backfill prerequisite (MEDIUM)
The `attr_quality` asset requires chunks to exist for the source_id. If no chunks
exist, `table.update()` is a no-op (zero rows matched). The `checks.sh` layer must
ensure chunks exist before triggering attr_quality. This is handled by ordering in
`all)` (chunks runs before attr_quality).

### R3 — `checks.sh` attr_quality layer must create its own test data OR reuse chunks layer data
If the `attr_quality)` layer runs standalone (not via `all)`), it must ensure chunks
exist. The layer should either: (a) trigger extract+chunks inline (same as chunks
layer does), or (b) reuse the source_id from a prior chunks run. Option (a) is safer
for standalone execution.

### R4 — No Dagster asset dependency declared (ACCEPTED)
No `deps=["chunks"]` on the `attr_quality` asset. The API caller is responsible for
ordering. Consistent with D9 from F-025 (no deps between assets).
