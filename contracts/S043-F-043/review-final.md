# S043-F-043 — Reviewer Mode B (Post-Implementation) Review

**Verdict**: APPROVED
**Commit reviewed**: 86e1193
**Reviewed**: 2026-06-04

---

## Summary

The implementer has delivered a complete, correct implementation of the `sft_synthesis_qa` materializer as specified in agreed.md (revision 2). All five new files conform to the contract, all 8 locked decisions are faithfully implemented, all 6 hard invariants are satisfied, and 41 new tests cover every V1–V5 verification criterion. No out-of-scope features were introduced. The diff is clean: only `dagster/` files and contract/progress artifacts changed; `apps/api/` was not touched.

---

## Hard invariant compliance

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | **Lineage mandatory** | ✅ | `fetch_dataset_row()` reads the already-frozen `recipe_snapshot` from `dataset.recipe_snapshot` (committed by F-042). The materializer does not re-freeze. `chunk_id` is included as `pa.string()` in `DATASET_SCHEMA` (`hf_dataset_io_manager.py` line 71) and threaded through `qa_rows` → `DatasetOutput.train_rows/val_rows`. No lineage gap. |
| 2 | **Storage separation + CAS** | ✅ | Parquet bytes written to MinIO only via `s3.put_object()`; `recipe_snapshot` metadata stays in Postgres as a JSONB column (`fetch_dataset_row` is SELECT-only). No blob bytes written to Postgres anywhere in the new code. Path is deterministic (`{dataset_id}_{version_tag}/...`) per design doc §4.3. |
| 3 | **Schema frozen post-publish** | ✅ | Only DB interaction is `SELECT id, recipe_snapshot, hf_repo_uri FROM dataset ...`. No `UPDATE`, `INSERT`, or `DELETE` against the `recipe` table (confirmed by grep). `recipe_snapshot` is read-only. |
| 4 | **LLM calls through gateway** | ✅ | `call_llm_gateway()` uses `requests.post(f"{LLM_GATEWAY_URL}/api/internal/llm/completions", ...)`. AST scan (`test_no_direct_llm_sdk_imports_sft_synthesis_qa`, `test_no_direct_llm_sdk_imports_hf_dataset_io_manager`) confirms zero `import anthropic` / `import openai` / `from anthropic` / `from openai` in both new helper files. Manual grep on live files confirms the same. Comment-only string mentioning "anthropic" in the docstring of `hf_dataset_io_manager.py` (line 15) is not an import statement. |
| 5 | **Async SQLAlchemy (apps/api/ scope)** | ✅ N/A | No SQLAlchemy in `dagster/`. Postgres access uses `psycopg2` (sync), consistent with `quality_tagger.py`. Invariant is explicitly scoped to `apps/api/dataplat_api/`. |
| 6 | **OpenAPI ↔ TS type sync** | ✅ N/A | `apps/api/` untouched (confirmed: `git diff 9612981..86e1193 -- apps/ | wc -l` → 0). `make codegen` not required. |

---

## Locked decision compliance (8 items)

| # | Decision | Status | Evidence |
|---|---|---|---|
| D1 | `requests.post` to internal gateway | ✅ | `sft_synthesis_qa.py` line 253–260: `requests.post(f"{gateway_url}/api/internal/llm/completions", ...)` with `json={"messages": [...], "max_tokens": max_tokens}`. Matches `quality_tagger.py` precedent. |
| D2 | `val_ratio` from `recipe_snapshot["output"]["splits"]["validation"]`, fallback 0.1 | ✅ | `sft_synthesis_qa.py` lines 366–369: `recipe_snapshot.get("output", {}).get("splits", {}).get("validation", 0.1)`. Also present identically in `definitions.py` lines 106–110 and `_run_dataset_asset` lines 366–369. |
| D3 | No operator row insert (deferred F-092) | ✅ | No `INSERT INTO operator` anywhere. `sft_synthesis_qa.py` docstring line 21 explicitly states deferral. No operator table query at runtime. |
| D4 | `MINIO_DATASETS_BUCKET` not in `apps/api/` (deferred F-047) | ✅ | `hf_dataset_io_manager.py` line 139: `os.environ.get("MINIO_DATASETS_BUCKET", "datasets")`. `apps/api/` diff is empty. Deferral noted in both docstring (line 20) and `handle_output` docstring (line 109). |
| D5 | Zero-row materialization allowed; warning logged | ✅ | `definitions.py` lines 126–133: `if not chunks: context.log.warning(...)`. `_run_dataset_asset` lines 789–796: equivalent `logger.warning`. Zero-row Parquet tested by `test_parquet_empty_rows_valid`. |
| D6 | `chunk_id` in Parquet output as `pa.string()` column | ✅ | `hf_dataset_io_manager.py` lines 67–73: `DATASET_SCHEMA = pa.schema([("instruction", pa.string()), ("output", pa.string()), ("chunk_id", pa.string())])`. Asserted in `test_parquet_columns_instruction_output` for both train and val keys. |
| D7 | `max_tokens` from `recipe_snapshot["schema"]["config"]["max_tokens"]`, fallback 512 | ✅ | `sft_synthesis_qa.py` line 372: `template_config.get("max_tokens", 512)`. Forwarded to `call_llm_gateway(prompt, max_tokens=max_tokens, ...)`. Tested by `test_call_llm_gateway_max_tokens_passed`. |
| D8 | `@dataclass DatasetOutput` (not `TypedDict`) | ✅ | `sft_synthesis_qa.py` lines 46–62: `@dataclass class DatasetOutput` with `train_rows`, `val_rows`, `recipe_snapshot`, `dataset_id`, `version_tag` fields. `from dataclasses import dataclass` import on line 32. |

---

## Verification criteria (V1–V5)

| Criterion | Test(s) | Status |
|---|---|---|
| **V1** — `data/train-00000.parquet` and `data/validation-00000.parquet` exist at `{dataset_id}_{version_tag}/data/` | `test_hf_dataset_io_manager.py::test_handle_output_uploads_parquet` — asserts `"7_v1/data/train-00000.parquet"` and `"7_v1/data/validation-00000.parquet"` in `put_object` Keys | ✅ |
| **V2** — Parquet columns include `instruction`, `output`, `chunk_id` | `test_hf_dataset_io_manager.py::test_parquet_columns_instruction_output` — reads back Parquet bytes via `pq.read_table`, asserts all three column names for both splits | ✅ |
| **V3** — `README.md` and `recipe.json` exist alongside Parquet files | `test_hf_dataset_io_manager.py::test_handle_output_uploads_readme_and_recipe` — asserts `"7_v1/README.md"` and `"7_v1/recipe.json"` in Keys; `test_handle_output_total_four_objects` asserts exactly 4 `put_object` calls | ✅ |
| **V4** — `recipe.json` equals serialised `recipe_snapshot` | `test_hf_dataset_io_manager.py::test_recipe_json_matches_snapshot` — decodes Body bytes and asserts `json.loads(body) == recipe_snapshot` with a non-trivial snapshot; `test_recipe_json_is_valid_json` exercises unicode and nested structures | ✅ |
| **V5** — No direct SDK imports (AST walk + behavioural) | `test_sft_synthesis_qa.py::test_no_direct_llm_sdk_imports_sft_synthesis_qa`, `test_no_direct_llm_sdk_imports_hf_dataset_io_manager` (AST walk); `test_call_llm_gateway_uses_requests_post` (behavioural: exactly one `requests.post` call, endpoint URL verified) | ✅ |

---

## Additional test coverage (beyond V1–V5)

The 41 tests exceed the minimum specified in agreed.md:

- **`test_sft_synthesis_qa.py`** (27 tests): partition key parsing (7 cases including multi-digit version `v10`), Lance read with/without filter, LLM gateway happy path, `max_tokens` forwarding, parse failures (fallback True/False), missing key fallback, `RequestException` fallback, deterministic split (reproducibility, ratio ±3%, zero val, full val, empty input, no overlap), AST SDK checks (×2), end-to-end integration (×2: happy path + fallback).
- **`test_hf_dataset_io_manager.py`** (14 tests): V1–V4 criteria, zero-row Parquet (D5), custom bucket env override, exact 4-object count, key prefix pattern (`42_v3/`), `_rows_to_parquet_bytes` unit tests (×2), `load_input` raises `NotImplementedError`.

All 41 tests pass per leader's verification (`41/41` in the established `docker compose run --rm --no-deps -T dagster-worker-cpu python -m pytest` pattern).

---

## Out-of-scope confirmation

The diff touches **only** these paths:
- `dagster/dagster_platform/sft_synthesis_qa.py` (new)
- `dagster/dagster_platform/hf_dataset_io_manager.py` (new)
- `dagster/dagster_platform/definitions.py` (modified)
- `dagster/tests/test_sft_synthesis_qa.py` (new)
- `dagster/tests/test_hf_dataset_io_manager.py` (new)
- `claude-progress.txt`, `contracts/S043-F-043/` (process artifacts)

Confirmed **absent** from diff:
- ✅ `apps/api/` — not touched (0 lines)
- ✅ `dataset` row status/`sample_count`/`size_bytes` update — deferred to F-044; no `UPDATE dataset` SQL
- ✅ `dataset_infos.json` write — deferred to F-044; not present
- ✅ Operator table seed — deferred to F-092; no `INSERT INTO operator`
- ✅ `apps/api/dataplat_api/config.py` `MINIO_DATASETS_BUCKET` — deferred to F-047; not present

---

## Findings

### Blocker (HIGH)

*None.*

### MEDIUM

*None.*

### LOW

*None.*

### NIT

**NIT-1** — `sft_synthesis_qa.py` defines `_build_lance_storage_options()` (a private helper) but does not expose it publicly. `read_chunks_from_lance()` calls it inline. This is consistent with the `quality_tagger.py` pattern and carries no correctness concern. If a future sprint adds Lance-reading helpers, a shared utility module may reduce duplication — not a F-043 concern.

**NIT-2** — `_get_put_object_bodies()` in `test_hf_dataset_io_manager.py` (lines 929–940) contains a positional-args fallback path (`c.args[1]`, `c.args[2]`) that will never be exercised since `handle_output` always calls `put_object` with keyword arguments. The dead code is harmless but could be simplified. Not a correctness issue.

**NIT-3** — The `_run_dataset_asset()` wrapper in `sft_synthesis_qa.py` (lines 330–409) duplicates the asset body logic from `definitions.py`. This is the established project pattern (mirroring `quality_tagger.py`'s test-wrapper convention) and was explicitly mandated by agreed.md. The duplication is intentional; no refactoring is needed.

---

## Verdict line

APPROVED
