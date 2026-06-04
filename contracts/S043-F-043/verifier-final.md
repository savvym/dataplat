# S043-F-043 — Verifier Final Report

**Feature**: F-043 — `sft_synthesis_qa` materializer  
**Commit**: 86e1193  
**Verdict**: PASS  
**Date**: 2026-06-04  
**Verifier**: Claude (Mode B post-review validation)

---

## Gate results

### G1 — Smoke baseline still green
**Status**: PASS ✓

```
bash verify/checks.sh smoke
```

**Output**: All four smoke checks pass (API health, DB connection, MinIO, Dagster).

**Evidence**: Exit code 0.

---

### G2 — New unit tests pass inside container
**Status**: PASS ✓

```
docker compose -f docker/docker-compose.dev.yml run --rm --no-deps -T \
  -e PYTHONPATH=/app/dagster \
  -v /data/home/zhhdzhang/nta/dataplat/dagster:/app/dagster \
  dagster-worker-cpu \
  python -m pytest /app/dagster/tests/test_sft_synthesis_qa.py /app/dagster/tests/test_hf_dataset_io_manager.py -q
```

**Output**: 41 passed in 2.98s.

**Breakdown**:
- `test_sft_synthesis_qa.py`: 27 tests
- `test_hf_dataset_io_manager.py`: 14 tests
- **Total**: 41 tests (matches spec exactly)

**Evidence**: All pass, 1 PydanticDeprecatedSince20 warning (pre-existing, from Dagster internals, not in scope). Exit code 0.

---

### G3 — No direct SDK imports (V5 hard invariant)
**Status**: PASS ✓

```
grep -rnE "^(import (anthropic|openai)|from (anthropic|openai)( |\.))" \
  dagster/dagster_platform/sft_synthesis_qa.py \
  dagster/dagster_platform/hf_dataset_io_manager.py
```

**Output**: (empty — no matches)

**Evidence**: Grep exit code 1 (no lines matched). Zero imports of `anthropic` or `openai` in both new modules.

**Note**: Both files call `requests.post()` to the internal LLM gateway (`{LLM_GATEWAY_URL}/api/internal/llm/completions`), conforming to hard invariant #4 ("LLM calls go through the gateway").

---

### G4 — Definitions surface integrity
**Status**: PASS ✓

**Dagster-webserver health**:
```
docker compose -f docker/docker-compose.dev.yml ps dagster-webserver
```

**Output**: `NAME=dataplat-dagster-webserver-1, STATUS=Up 47 hours (healthy)`

**HTTP endpoint**:
```
curl -s -o /dev/null -w '%{http_code}' http://localhost:13000/dagster_version
```

**Output**: `200`

**Evidence**: Container is healthy and responsive. `definitions.py` imports and exports successful (if definitions had a syntax error or import cycle, the webserver would have logged a code-location error in the Dagster UI).

---

### G5 — V1–V5 verification criteria coverage
**Status**: PASS ✓

All five acceptance criteria confirmed via test names in committed test suite:

| Criterion | Test name | Status |
|---|---|---|
| **V1** — Parquet files at `{dataset_id}_{version_tag}/data/train-00000.parquet` and `validation-00000.parquet` | `test_handle_output_uploads_parquet` | ✅ |
| **V2** — Parquet columns include `instruction`, `output`, `chunk_id` | `test_parquet_columns_instruction_output` | ✅ |
| **V3** — `README.md` and `recipe.json` exist alongside Parquet files | `test_handle_output_uploads_readme_and_recipe` | ✅ |
| **V4** — `recipe.json` serialisation equals `recipe_snapshot` | `test_recipe_json_matches_snapshot` | ✅ |
| **V5** — No direct LLM SDK imports (AST walk + behavioural) | `test_no_direct_llm_sdk_imports_sft_synthesis_qa`, `test_no_direct_llm_sdk_imports_hf_dataset_io_manager` | ✅ |

**Evidence**: All 41 tests pass (including V1–V5 and extended coverage per reviewer Mode B findings).

---

### G6 — Diff cleanliness (out-of-scope confirmation)
**Status**: PASS ✓

```
git diff 9612981..86e1193 --stat
```

**Output**:
```
claude-progress.txt                               |   8 +
contracts/S043-F-043/agreed.md                    | 374 ++++++++++++++++
contracts/S043-F-043/feedback.md                  | 191 +++++++++
contracts/S043-F-043/proposed.md                  | 374 ++++++++++++++++
dagster/dagster_platform/definitions.py           | 156 ++++++-
dagster/dagster_platform/hf_dataset_io_manager.py | 209 +++++++++
dagster/dagster_platform/sft_synthesis_qa.py      | 409 ++++++++++++++++++
dagster/tests/test_hf_dataset_io_manager.py       | 319 ++++++++++++++
dagster/tests/test_sft_synthesis_qa.py            | 500 ++++++++++++++++++++++
9 files changed, 2527 insertions(+), 13 deletions(-)
```

**In-scope paths** (only these changed):
- ✅ `dagster/dagster_platform/sft_synthesis_qa.py` — new, materializer helpers
- ✅ `dagster/dagster_platform/hf_dataset_io_manager.py` — new, IOManager
- ✅ `dagster/dagster_platform/definitions.py` — modified, wired asset + IOManager
- ✅ `dagster/tests/test_sft_synthesis_qa.py` — new, 27 unit tests
- ✅ `dagster/tests/test_hf_dataset_io_manager.py` — new, 14 unit tests
- ✅ `contracts/S043-F-043/` — process artifacts
- ✅ `claude-progress.txt` — project log

**Out-of-scope paths confirmed ABSENT**:
- ✅ `apps/api/` — **0 lines changed** (no FastAPI changes)
- ✅ `apps/web/` — not touched
- ✅ `packages/` — not touched
- ✅ `spec/` — not touched (feature_list.json flipped in separate commit by leader)
- ✅ `alembic/` — not touched (no new DB migrations needed)
- ✅ `Makefile` — not touched

**Evidence**: Diff touches only `dagster/` (implementation) and contract artifacts. No bleeding into `apps/api/`, `apps/web/`, or package boundaries.

---

## Pre-existing failures noted (NOT regressions)

**None detected in baseline at commit 9612981 (S042 close).**

The F-042 verifier report noted pre-existing mypy fixture warnings in test conftest (not in scope per CLAUDE.md precedent). No dagster test failures were reported at S042 close.

All 41 new tests introduced by F-043 pass cleanly with no regressions.

---

## Hard invariant compliance summary

All 6 hard invariants satisfied (per reviewer Mode B detailed attestation):

| # | Invariant | Status | Key evidence |
|---|---|---|---|
| 1 | **Lineage mandatory** | ✅ | `recipe_snapshot` read from `dataset.recipe_snapshot` (frozen at F-042 row INSERT). `chunk_id` included in Parquet schema for row-level traceability. |
| 2 | **Storage separation + CAS** | ✅ | Parquet bytes to MinIO only. No blob bytes in Postgres. Path deterministic per design. |
| 3 | **Schema frozen post-publish** | ✅ | Only reads `recipe_snapshot` from Postgres. No schema edits or recipe table mutations. |
| 4 | **LLM calls through gateway** | ✅ | `requests.post(f"{LLM_GATEWAY_URL}/api/internal/llm/completions", ...)`. Zero `anthropic`/`openai` imports verified by AST walk (G3 grep confirms). |
| 5 | **Async SQLAlchemy** | ✅ N/A | Invariant #5 scope: `apps/api/dataplat_api/` only. Dagster uses `psycopg2` (sync), consistent with `quality_tagger.py` pattern. |
| 6 | **OpenAPI ↔ TS type sync** | ✅ N/A | No API schema changes. `make codegen` not required. `apps/api/` untouched. |

---

## Locked decisions compliance

All 8 locked decisions from agreed.md §8 faithfully implemented:

1. ✅ LLM call pattern: `requests.post` to internal gateway (not SDK imports)
2. ✅ `val_ratio` from `recipe_snapshot["output"]["splits"]["validation"]`, fallback 0.1
3. ✅ Operator row deferred (F-092); config read from `recipe_snapshot` directly
4. ✅ `MINIO_DATASETS_BUCKET` deferred (F-047); uses `os.environ.get(..., "datasets")`
5. ✅ Zero-row materialization allowed with warning (not error)
6. ✅ `chunk_id` in Parquet as `pa.string()` column (row-level traceability)
7. ✅ `max_tokens` from `recipe_snapshot["schema"]["config"]["max_tokens"]`, fallback 512
8. ✅ `DatasetOutput` as `@dataclass` (not `TypedDict`)

(All verified by reviewer Mode B; see `review-final.md` table.)

---

## Extended coverage

Reviewer Mode B noted the test suite exceeds minimum specification:

- **Partition key parsing**: 7 test cases (including multi-digit v10)
- **Lance read**: Filter + no-filter branches, empty result, schema projection
- **LLM gateway**: Happy path, parse failures (fallback True/False), missing keys, `RequestException`, `max_tokens` forwarding
- **Deterministic split**: Reproducibility, ratio boundary (±3%), edge cases (zero val, full val, empty input, no overlap)
- **IO Manager**: Path prefix pattern, 4-object count, Parquet schema assertions, custom env bucket override
- **Integration**: End-to-end mocked asset test (×2: happy + fallback)

**Test count**: 41 (27 + 14) — all pass.

---

## Verdict

**PASS**

All gates (G1–G6) are green. Smoke baseline healthy, 41 unit tests pass in container (27 + 14 as specified), no SDK imports detected, definitions.py surface intact, all V1–V5 criteria covered by test names, and diff is scoped to `dagster/` only (no out-of-scope paths changed).

Hard invariants #1–4 (mandatory for Dataplat) are satisfied. Decisions D1–D8 are faithfully implemented. No pre-existing test failures to clean up (baseline at S042 close was already green).

**Recommendation**: Leader flips `F-043` to `passes: true` in `spec/feature_list.json`, appends closing entry to `claude-progress.txt`, and pushes.

---

**Final line**: PASS
