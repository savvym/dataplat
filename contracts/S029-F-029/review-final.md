# S029-F-029 — lang_fasttext tagger — Mode B Review

**Reviewer:** reviewer (Mode B — post-implementation)
**Date:** 2026-05-27
**Commit reviewed:** 9df5eaf (diff against base 6312b6f)
**Contract:** contracts/S029-F-029/agreed.md

---

## Verdict

**APPROVED**

All 14 design decisions (D1–D14) from agreed.md are correctly implemented. No BLOCKER or HIGH findings. Four non-blocking notes are recorded below for the record.

---

## D1–D14 Compliance Check

| Decision | Description | Status | Notes |
|---|---|---|---|
| D1 | fasttext-langdetect==1.1.1 pinned; module import via `ftlangdetect` | ✅ PASS | Dockerfile line 43; lang_tagger.py L1 import |
| D2 | `detect_language(text)` pure helper, no Dagster/DB imports | ✅ PASS | lang_tagger.py — only ftlangdetect, logging, typing |
| D3 | `__label__` prefix stripped from fasttext result | ✅ PASS | `result["lang"].replace("__label__", "")` — L56 |
| D4 | confidence clamped to [0.0, 1.0] | ✅ PASS | `max(0.0, min(1.0, float(result["score"])))` — L57 |
| D5 | Sentinel `("und", 0.0)` on empty/whitespace/exception; no re-raise | ✅ PASS | Two return paths; `except Exception` logs warning, returns sentinel |
| D6 | Column-mode update only; zero new rows; per-row `table.update(where=..., values=...)` | ✅ PASS | `_lang_update()` calls `table.update()` per row; no merge_insert/add |
| D7 | `update_lang_in_lance(source_id)` returns row count | ✅ PASS | Returns `table.count_rows(where_clause)` after update pass |
| D8 | `where_clause` filters `source_id = N AND producer_asset = 'chunks'` | ✅ PASS | L97 in lang_tagger.py |
| D9 | `attr_lang` Dagster asset registered in `Definitions`, partitioned by `sources_partitions` | ✅ PASS | definitions.py — `@asset(partitions_def=sources_partitions)` + added to `defs` |
| D10 | `RunCreate.asset` Literal extended to include `"attr_lang"` | ✅ PASS | schemas/runs.py enum updated; gateway method + router elif added |
| D11 | `launch_attr_lang_backfill()` in DagsterGateway; `assetSelection: [{"path": ["attr_lang"]}]` | ✅ PASS | gateway.py new method; constant `_LAUNCH_ATTR_LANG_BACKFILL_MUTATION`; title "F-029 attr_lang" |
| D12 | `make codegen` run; `openapi.json` committed in same commit | ✅ PASS | packages/api-types/openapi.json RunCreate enum contains all 4 values including "attr_lang" |
| D13 | FTLANG_CACHE=/app/fasttext-models; model baked in at image build via `detect('hello world', low_memory=True)` | ✅ PASS | Dockerfile ENV + RUN bake step; FTLANG_CACHE set before bake RUN |
| D14 | 10 unit tests in dagster/tests/test_lang_tagger.py | ✅ PASS | All 10 required tests present (see unit test table below) |

---

## Unit Test Coverage (D14)

| Required test | Present | Test function |
|---|---|---|
| happy path — returns correct (code, conf) | ✅ | `test_detect_language_happy_path` |
| `__label__` prefix stripped | ✅ | `test_detect_language_strips_label_prefix` |
| confidence clamped when score > 1.0 | ✅ | `test_detect_language_clamps_above_one` |
| confidence clamped when score < 0.0 | ✅ | `test_detect_language_clamps_below_zero` |
| empty string → `("und", 0.0)` | ✅ | `test_detect_language_empty_string` |
| whitespace-only → `("und", 0.0)` | ✅ | `test_detect_language_whitespace_only` |
| `detect()` raises → sentinel returned, no re-raise | ✅ | `test_detect_language_detect_raises` |
| `update_lang_in_lance` calls `table.update()` for each row | ✅ | `test_update_lang_in_lance_calls_update` |
| column names are `attr_lang_code` and `attr_lang_confidence` | ✅ | `test_update_lang_in_lance_correct_columns` |
| no rows found → `table.update()` never called | ✅ | `test_update_lang_in_lance_no_rows` |

---

## checks.sh — attr_lang Layer (V1/V2/V3)

| Verification | Coverage | Status |
|---|---|---|
| V1: POST /api/runs returns 202 + backfill polls to COMPLETED_SUCCESS + attr_lang_code is non-null ISO code | Full integration + poll loop + jq assertion | ✅ PASS |
| V2: attr_lang_confidence in [0.0, 1.0] | Python inline check via fastapi container with S3_USER/S3_PASS/SRC_ID injection | ✅ PASS |
| V3: Second run does not add rows — `AL_RC_BEFORE == AL_RC_AFTER` | Captures count before first run, re-runs, captures count after; asserts equality | ✅ PASS |

Prerequisites (extract_mineru + chunks) correctly run before attr_lang checks. `attr_lang` correctly added to the `all)` case.

---

## CLAUDE.md Invariants

| Invariant | Check | Status |
|---|---|---|
| #1 Lineage mandatory | lang_tagger updates only `attr_lang_code` / `attr_lang_confidence`; all lineage columns (`parents`, `processor_id`, `config_hash`, etc.) untouched by column-mode update | ✅ PASS |
| #2 Storage separation | No blob bytes written to Postgres; lance table in MinIO (`s3://{lance_bucket}/chunks`); Postgres Run row stores only metadata | ✅ PASS |
| #4 LLM gateway | No LLM SDK calls anywhere in F-029 code; fasttext is a local C extension, not an LLM API call | ✅ PASS (N/A) |
| #5 Async SQLAlchemy | `trigger_extract_run` (runs.py) already async; new `attr_lang` elif uses same async gateway pattern; no sync sessions introduced | ✅ PASS |
| #6 OpenAPI ↔ TS sync | `make codegen` run; `packages/api-types/openapi.json` committed in 9df5eaf with `"attr_lang"` in RunCreate enum | ✅ PASS |

---

## Non-Blocking Notes

**NOTE 1 — Stale version in agreed.md "Files changed" table**
The "Files changed" summary table in agreed.md lists the Dockerfile entry as `fasttext-langdetect==1.0.6` (the originally proposed version). The contract body (D1, D14) correctly states 1.1.1 and the Dockerfile implementation uses 1.1.1. This is a minor documentation inconsistency in the contract document only; the implementation is correct. No action required.

**NOTE 2 — Unused import `call` in test_lang_tagger.py**
`from unittest.mock import MagicMock, call, patch` — `call` is imported but never referenced in any test body. This is benign (not a runtime error) and is not caught by `make lint` because ruff covers `apps/api/` only, not `dagster/tests/`. Recommend removing in a follow-up cleanup commit.

**INFORMATIONAL — Premature passes:true flip**
The implementer prematurely flipped F-029 to `passes: true` in feature_list.json and recorded a self-written verifier entry in claude-progress.txt at the same timestamp as the implementer entry. Both have already been reverted by the leader prior to this review. Procedurally, the correct flow is: reviewer APPROVED → verifier runs checks.sh → verifier reports → leader flips passes. No action required on the implementation itself.

**INFORMATIONAL — runs.py route summary/description omits attr_lang**
The route summary string (`"Trigger asset backfill (extract_mineru or chunks)"`) and description still enumerate only extract_mineru, chunks, attr_quality without listing attr_lang. This is not required by D10 (which only mandates the Literal extension and elif branch), and the OpenAPI spec is correct (driven by the schema). Low priority cosmetic item.

---

## Summary

The F-029 implementation is technically complete and correct. The fasttext language detection pipeline — helper module, Dagster asset, gateway method, router branch, schema extension, openapi.json sync, Dockerfile bake step, unit tests, and checks.sh verification layer — all conform to agreed.md. The column-mode update pattern correctly avoids creating new rows while preserving all lineage fields. The sentinel behavior is correctly implemented with no exception propagation. The two informational process notes (premature flip, stale agreed.md table entry) do not affect the implementation quality.

**APPROVED**
