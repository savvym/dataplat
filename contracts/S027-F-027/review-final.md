# S027-F-027 — Mode B Review

**Diff reviewed:** `6094064..2e6ba92`
**Contract:** `contracts/S027-F-027/agreed.md`
**Reviewer:** Mode B
**Date:** 2026-05-27

---

## Verdict

**APPROVED.**

All six critical checks pass. Every blocking and required issue from `feedback.md` is
resolved. All eight files in the agreed.md §2 manifest are present. All six hard
invariants are satisfied. CAL-1 through CAL-10 clear. Two non-blocking observations
noted (OBS-1, OBS-2); neither requires a change before merge.

---

## Critical checks (task brief)

### CC-1 — Column-mode update: zero new rows

**PASS.**

`quality_tagger.py` implements two update paths; neither creates new rows.

**Option A** (`quality_tagger.py:110–116`):
```python
table.update(
    where=where_clause,
    values_sql={
        "attr_quality_score": "LEAST(1.0, CAST(token_count AS FLOAT) / 512.0)",
        "attr_quality_provider": f"'{QUALITY_PROVIDER}'",
    },
)
```
`table.update()` is an in-place SQL mutation — no row is inserted.

**Option B fallback** (`quality_tagger.py:162–165`):
```python
(
    table.merge_insert("chunk_id")
    .when_matched_update_all(updates=["attr_quality_score", "attr_quality_provider"])
    .execute(scored)
)
```
`.when_matched_update_all()` updates existing rows keyed on `chunk_id`. There is no
`.when_not_matched_insert_all()` call anywhere in the file — zero new rows in this
path too. The `scored` list (`quality_tagger.py:153–160`) contains only `chunk_id`,
`attr_quality_score`, and `attr_quality_provider`; lineage columns are absent.

Idempotency is documented at `quality_tagger.py:84`: "re-running overwrites the same
two columns — no row count change."

This directly resolves feedback.md Issue 1 (BLOCKING).

---

### CC-2 — Verification queries: `source_id` only, no `producer_asset` filter

**PASS.**

Both V2 and V3 checks in `checks.sh` use `source_id` as the sole predicate:

**V2** (`checks.sh:2408, 2425`):
```bash
# V2 query: source_id only (NO producer_asset filter per agreed.md Issue 2 fix).
rows = t.search().where(f'source_id = {src_id}').select(['attr_quality_score']).to_list()
```

**V3** (`checks.sh:2434, 2453`):
```bash
# V3 query: source_id only (NO producer_asset filter per agreed.md Issue 2 fix).
rows = t.search().where(f'source_id = {src_id}').select(['attr_quality_provider']).to_list()
```

Neither query has `AND producer_asset = ...`. The comments explicitly acknowledge the
Issue 2 fix. This resolves feedback.md Issue 2 (BLOCKING).

---

### CC-3 — Router dispatch: explicit `elif` + defensive `else`

**PASS.**

`runs.py:140–173` implements a correct three-way dispatch:

```python
if body.asset == "extract_mineru":       # L140 — existing
    ...
    asset_keys: list[str] = ["extract_mineru"]
elif body.asset == "attr_quality":       # L150 — new
    ...
    kind = "attr_quality"
    asset_keys = ["attr_quality"]
elif body.asset == "chunks":             # L160 — converted from bare else
    ...
    kind = "chunk"
    asset_keys = ["chunks"]
else:                                    # L170 — new defensive guard
    # Defensive: should be unreachable because RunCreate.asset Literal
    # validation rejects any other value at parse time (→ FastAPI 422).
    raise ValueError(f"Unhandled asset type: {body.asset!r}")  # L173
```

The old `else:  # body.asset == "chunks" — guaranteed by RunCreate.asset Literal
validation` comment is gone. This resolves feedback.md Issue 4 (MINOR).

---

### CC-4 — `openapi.json` enum updated in same commit as `schemas/runs.py`

**PASS.**

Both changes are in commit `2e6ba92`:

- `schemas/runs.py:61`: `asset: Literal["extract_mineru", "chunks", "attr_quality"]`
- `openapi.json:1276–1279`: the `asset` enum gains `"attr_quality"` as third value
- `openapi.json:1298`: the `RunCreate.description` string updated to mention F-027

Invariant #6 satisfied. Resolves agreed.md D7.

---

### CC-5 — No LLM SDK imports

**PASS.**

`quality_tagger.py:19–24` top-level imports:
```python
from __future__ import annotations
import os
from typing import Any
import lancedb
```

No `anthropic`, `openai`, `litellm`, `langchain`, or any other LLM SDK. A grep for
`import (anthropic|openai|litellm|langchain|boto3)` across all 8 changed files returned
empty. Invariant #4 satisfied (stub is pure arithmetic: `quality_tagger.py:53`).

---

### CC-6 — Stub deviation comment in `checks.sh` V3 block

**PASS.**

`checks.sh:2435–2436` (immediately before the V3 Python assertion):
```bash
# F-027 stub deviation: provider is 'length_heuristic', not an LLM model name.
# F-028 will replace with real model name once LLM gateway exists.
```

Matches the prescribed wording in agreed.md §4. Resolves feedback.md Issue 5 (MINOR).

---

## All five `feedback.md` issues resolved

| # | Severity | Issue | Evidence of resolution |
|---|---|---|---|
| 1 | BLOCKING | D3 augmentation rows: inserting new `producer_asset='attr_quality'` rows | Option A: `quality_tagger.py:110–116` (`table.update`). Option B: `quality_tagger.py:162–165` (`merge_insert.when_matched_update_all`). No `when_not_matched_insert_all` anywhere. ✅ |
| 2 | BLOCKING | `AND producer_asset='attr_quality'` in V2/V3 verification queries | `checks.sh:2425, 2453` — `source_id` only filter. ✅ |
| 3 | REQUIRED | D2 argued for delete+reinsert; must describe column-mode | agreed.md D2 rewritten; `quality_tagger.py:75–84` documents and implements column-mode. ✅ |
| 4 | MINOR | `else` comment wrong after Literal widening | `runs.py:160` explicit `elif body.asset == "chunks":`; `runs.py:170–173` defensive `else: raise ValueError(...)`. ✅ |
| 5 | MINOR | No stub-deviation comment in `checks.sh` | `checks.sh:2435–2436` comment present. ✅ |

---

## Contract §2 file manifest

All 8 required files delivered in commit `2e6ba92`:

| File | Status |
|---|---|
| `dagster/dagster_platform/quality_tagger.py` | ✅ New (166 lines) |
| `dagster/tests/test_quality_tagger.py` | ✅ New (47 lines) |
| `apps/api/dataplat_api/schemas/runs.py` | ✅ Modified (L61) |
| `apps/api/dataplat_api/routers/runs.py` | ✅ Modified (L150–173) |
| `apps/api/dataplat_api/dagster/gateway.py` | ✅ Modified (L217, L902–982) |
| `packages/api-types/openapi.json` | ✅ Modified (L1276–1279, L1298) |
| `dagster/dagster_platform/definitions.py` | ✅ Modified (L52–54, L194–265) |
| `verify/checks.sh` | ✅ Modified (L1340, L2184–2460) |

---

## Design decisions compliance

| Decision | Requirement | Evidence |
|---|---|---|
| D1 — Stub scorer | `min(1.0, float(token_count) / 512.0)`, no LLM | `quality_tagger.py:53` ✅ |
| D2 — Column-mode update | Option A `table.update()` + Option B `merge_insert().when_matched_update_all()` | `quality_tagger.py:109–125, 162–165` ✅ |
| D3 — No lineage fields modified | `augmented_from`, `augmenter_id`, `augmenter_config_hash` untouched | `quality_tagger.py:81–82` explicitly states this; neither update path writes these columns ✅ |
| D4 — No `io_manager_key` | Returns `MaterializeResult` | `definitions.py:194–202` — `@asset` decorator has no `io_manager_key`; `definitions.py:203` function returns `MaterializeResult` ✅ |
| D5 — Mirrors `launch_chunks_backfill()` | Same GraphQL union types; only asset path differs | `gateway.py:217–248` vs `gateway.py:186–211`: identical 7-type union handling; only mutation name and `assetSelection[0].path` differ ✅ |
| D6 — Literal widened; `Run.kind` is plain text | No migration needed | `schemas/runs.py:61`; `runs.py:158` sets `kind = "attr_quality"` ✅ |
| D7 — `openapi.json` in same commit | Invariant #6 | Both in `2e6ba92` ✅ |
| D8 — `checks.sh` attr_quality layer with inline prereqs | E2E: PDF upload → extract_mineru → chunks → attr_quality | `checks.sh:2184–2460`: creates own test PDF, polls extract then chunks to COMPLETED_SUCCESS, then runs attr_quality ✅ |

---

## Invariant compliance

| # | Invariant | Evidence |
|---|---|---|
| 1 | Lineage mandatory | `runs.py:176–190`: `Run(dagster_run_id=backfill_id, kind="attr_quality", ...)` inserted via async Postgres. Lance row lineage fields explicitly excluded from update paths (`quality_tagger.py:81–82`). ✅ |
| 2 | Storage separation + CAS | Quality scores written to Lance (S3-backed via `quality_tagger.py:97–98`). No blob bytes in Postgres. ✅ |
| 3 | Schema frozen post-publish | `attr_quality_score` and `attr_quality_provider` already exist in CHUNKS_SCHEMA (established F-025). NULL → populated. No schema alteration. ✅ |
| 4 | LLM calls through gateway | `quality_tagger.py:19–24`: zero LLM SDK imports. Pure arithmetic at `quality_tagger.py:53`. ✅ |
| 5 | Async SQLAlchemy | `runs.py:103`: `session: AsyncSession`. `runs.py:192`: `await session.commit()`. `quality_tagger.py` is Dagster layer — not in `apps/api/`. ✅ |
| 6 | OpenAPI ↔ TS type sync | `openapi.json:1279` + `schemas/runs.py:61` in same commit `2e6ba92`. ✅ |

---

## Calibration checks (reviewer-calibration.md)

**CAL-1 — Async session enforcement:** PASS. `runs.py:103` declares `session:
AsyncSession`. `runs.py:192` calls `await session.commit()`. No `session.query()`,
no un-awaited `.commit()` in the diff. `quality_tagger.py` is Dagster layer; no
SQLAlchemy calls.

**CAL-2 — LLM gateway enforcement:** PASS. `quality_tagger.py:19–24` imports
confirmed: `os`, `typing.Any`, `lancedb`. Grep for `import (anthropic|openai|litellm)`
across all 8 changed files: empty. Invariant #4 satisfied.

**CAL-3 — OpenAPI sync:** PASS. `schemas/runs.py` (L61) and
`packages/api-types/openapi.json` (L1279) modified in the same commit `2e6ba92`. The
`RunCreate.description` field in `openapi.json:1298` was also updated to match the
Pydantic docstring. No `make codegen` available in this repo state; manual sync
confirmed correct by diff inspection.

**CAL-4 — Lineage completeness:** PASS. No new `Commit` objects. The `Run` row at
`runs.py:176–190` records `dagster_run_id` (backfill ID) via the existing async
pattern. Lance row lineage fields (`producer_asset`, `producer_version`,
`augmented_from`, `augmenter_id`, `augmenter_config_hash`) are explicitly excluded
from both update paths — `quality_tagger.py:81–82` calls this out as a design
constraint ("taggers are NOT augmenters").

**CAL-5 — CAS path discipline:** N/A. No blob storage writes in this sprint.

**CAL-6 — Schema freeze post-publish:** PASS. `attr_quality_score` and
`attr_quality_provider` already exist in `CHUNKS_SCHEMA` as nullable columns
(established in F-025). This sprint populates them from NULL. No schema alteration.

**CAL-7 — Bronze faithfulness:** N/A. No Bronze adapter code.

**CAL-8 — MVP scope discipline:** PASS. No MVP-deferred features introduced (no
self-registration, no Celery, no Docker-in-Docker, no Kafka, no OAuth, no
repository-level granular ACL).

**CAL-9 — Plugin isolation:** N/A. No plugin code.

**CAL-10 — Test coverage (happy path + failure):** PASS (contract-scoped). The 5
unit tests specified in agreed.md §4 are all present in `test_quality_tagger.py:22–46`:

| Test | Location | Covers |
|---|---|---|
| `test_score_zero_tokens` | L22 | `compute_quality_score(0) == 0.0` |
| `test_score_at_budget` | L27 | `compute_quality_score(512) == 1.0` (boundary) |
| `test_score_above_budget` | L32 | `compute_quality_score(1024) == 1.0` (cap) |
| `test_score_range` | L37 | All values in [0.0, 1.0] across 9 inputs |
| `test_provider_string` | L44 | `QUALITY_PROVIDER == "length_heuristic"` |

End-to-end V1/V2/V3 coverage is provided by `checks.sh:2357–2459`. The contract did
not require failure-mode unit tests for `update_quality_scores_in_lance()` — integration
error paths (e.g. Lance connection failure) are left to checks.sh (which gates on
COMPLETED_SUCCESS before running V2/V3).

---

## Non-blocking observations

**OBS-1** (`quality_tagger.py:120`) — `option_a_exc` bound but never logged.

```python
except Exception as option_a_exc:  # noqa: BLE001
    # Option A failed (e.g. values_sql not supported in this lancedb build).
    # Fall back to Option B: read rows → compute scores → merge_insert.
    _option_b_update(table, source_id, where_clause)
```

`option_a_exc` is assigned but never passed to a logger or re-raised. If Option A fails
at runtime, the job completes successfully via Option B with no signal that Option A is
broken. A one-line `context.log.warning("attr_quality: values_sql unavailable, using
merge_insert fallback: %s", option_a_exc)` in the `definitions.py` caller (or
`import logging; logging.getLogger(__name__).warning(...)` in `quality_tagger.py`
itself) would make this diagnosable. **Not required before merge** — the agreed.md
specifies no logging requirement for the fallback; correctness is unaffected.

**OBS-2** (`quality_tagger.py:144–148`) — Option B reads via `.search().to_list()`
rather than `to_arrow(filter=...)`.

The agreed.md D2 Option B example used `table.to_arrow(filter=...)`. The implementation
uses `table.search().where(...).select([...]).to_list()`. Both return the same rows;
the difference is only API style. The contract says "either option is acceptable" and
constrains only the write path (zero new rows). **Acceptable divergence.**

---

## Verdict

**APPROVED.**

All six critical checks pass. All five `feedback.md` issues are resolved with
file:line evidence. All eight files from agreed.md §2 are present. All six hard
invariants are satisfied. CAL-1 through CAL-10 clear. Two non-blocking observations
noted (OBS-1: silent Option A fallback; OBS-2: `.to_list()` vs `.to_arrow()` read
variant). Neither blocks merge.
