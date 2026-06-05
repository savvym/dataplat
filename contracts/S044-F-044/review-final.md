# S044-F-044 — Mode B Review (Post-Implementation)

**Verdict: APPROVED**

**Commit reviewed**: `95b390d`  
**Agreed contract**: `contracts/S044-F-044/agreed.md` (Revision 2)  
**Date**: 2026-06-04  
**Reviewer**: Mode B (post-implementation diff review)

---

## Blockers

**None.** No CHANGES_REQUESTED items.

---

## Findings by severity

| Severity | Count | Items |
|---|---|---|
| BLOCKER | 0 | — |
| MAJOR | 0 | — |
| MINOR | 0 | — |
| NIT | 1 | Type annotation mismatch in test helper |

---

## Checklist — agreed.md §3 Implementation Plan

### Step 1 — Parquet bytes serialised before S3 calls
✅ `train_bytes = _rows_to_parquet_bytes(obj.train_rows)` and `val_bytes = _rows_to_parquet_bytes(obj.val_rows)` are computed prior to any `put_object` call. Unchanged from F-043.

### Step 2 — `_build_dataset_infos()` pure helper added
✅ `dagster/dagster_platform/hf_dataset_io_manager.py` lines 96–154: `_build_dataset_infos(train_bytes, val_bytes, train_count, val_count) -> bytes` is a standalone module-level function.  
Shape confirmed: top-level key `"default"`, `features` with `{"dtype":"string","_type":"Value"}` for `instruction`/`output`/`chunk_id`, `splits.train`/`splits.validation` with `name`/`num_bytes`/`num_examples`/`dataset_name`, `download_size` and `dataset_size` both equal `len(train_bytes) + len(val_bytes)`. Exactly the minimal subset specified in OQ-2.

### Step 3 — Five `put_object` calls in declared order, wrapped in `try/except`
✅ Sequence (lines 243–295): train Parquet → val Parquet → recipe.json → README.md → dataset_infos.json. Dataset_infos.json is last among S3 writes, matching §3 ordering rationale.  
✅ `try:` at line 242 wraps all five calls plus `update_dataset_row()`.  
✅ `except (botocore.exceptions.ClientError, psycopg2.Error) as exc:` at line 304 — see Disclosed Deviation 2 below.

### Step 4 — `sample_count` / `size_bytes` computed correctly
✅ `sample_count = len(obj.train_rows) + len(obj.val_rows)` (line 292); `size_bytes = len(train_bytes) + len(val_bytes)` (line 293). Parquet buffers only, measured in-memory before upload, consistent with OQ-3 ruling.

### Step 5 — `update_dataset_row()` added to `sft_synthesis_qa.py`
✅ Signature `update_dataset_row(dataset_id: int, sample_count: int, size_bytes: int) -> None` — **no `context` parameter** (NIT-4 ✓).  
✅ SQL (lines 186–196): `UPDATE dataset SET status = 'done', sample_count = %s, size_bytes = %s, materialized_at = NOW() WHERE id = %s AND status = 'pending'` — **`AND status = 'pending'` present** (M1 ✓).  
✅ `try: ... conn.commit() / finally: conn.close()` pattern mirrors `fetch_dataset_row()`.  
✅ `stats` column NOT touched; docstring notes deferral (NIT-3 ✓).  
✅ `psycopg2` (sync), no `context` parameter, PLATFORM_DB_URL from `os.environ`.

### Step 6 — `context.add_output_metadata()` extended
✅ Now includes `sample_count`, `size_bytes`, `dataset_status="done"` in addition to F-043 fields (line 314–322). Placed outside `try/except` — reachable only if no exception occurred.

### §3.4 — `DatasetOutput.dataset_card_md` field
✅ `dataset_card_md: str | None = None` is the **last field** in the `DatasetOutput` dataclass (`sft_synthesis_qa.py` line 67). Python default-field ordering satisfied (NIT-2 ✓).  
✅ `fetch_dataset_row()` SELECT extended to include `dataset_card_md` (line 137); returned dict includes `"dataset_card_md"` key.  
✅ `definitions.py` and `_run_dataset_asset()` in `sft_synthesis_qa.py` both extract `dataset_card_md` from `db_row` and pass it to `DatasetOutput(...)`.  
✅ README.md content: `obj.dataset_card_md if obj.dataset_card_md is not None else stub` (lines 228–232).

---

## Checklist — Verification criteria (V1/V2/V3)

### V1 — DB row updated to `status='done'`
✅ **V1a** `test_db_row_updated_to_done` — `update_dataset_row` mock asserted called with `(7, 4, expected_size)`.  
✅ **V1b** `test_db_update_not_called_if_minio_fails` — ClientError causes exception; mock called 0 times; `ctx.log.error.assert_called_once()`.  
✅ **V1c** `test_size_bytes_equals_parquet_buffer_sum` — third positional arg to `update_dataset_row` equals `len(rpb(train)) + len(rpb(val))`.

### V2 — `dataset_infos.json` uploaded and valid
✅ **V2a** `test_dataset_infos_json_uploaded` — Key `"7_v1/dataset_infos.json"` present in put_object calls.  
✅ **V2b** `test_dataset_infos_json_valid_json` — `json.loads()` on Body bytes does not raise.  
✅ **V2c** `test_dataset_infos_json_content` — `splits.train.num_examples==3`, `splits.validation.num_examples==1`, `num_bytes` values equal pre-computed buffer sizes.  
✅ **V2d** `test_dataset_infos_json_features_schema` — each of `instruction`/`output`/`chunk_id` equals `{"dtype":"string","_type":"Value"}`.  
✅ **V2e** `test_dataset_infos_download_and_dataset_size` — both fields equal `len(rpb(train))+len(rpb(val))`.  
✅ **V2f** `test_build_dataset_infos_helper` — direct unit test of `_build_dataset_infos()`, isolated from S3 mock.

### V3 — `README.md` content
✅ **V3a** `test_readme_uses_dataset_card_md` — Body contains custom card content.  
✅ **V3b** `test_readme_fallback_when_no_card_md` — Body contains stub string including prefix.

---

## Checklist — Round-1/2 findings (M1/M2/L1/L2/NIT-1 through NIT-5)

### M1 — `AND status = 'pending'` in UPDATE predicate
✅ **Confirmed.** Literal string `AND status = 'pending'` appears in the SQL at `sft_synthesis_qa.py` lines 193–194. Test `test_update_dataset_row_happy_path` asserts `"AND status = 'pending'" in sql`. Test `test_update_dataset_row_sql_contains_and_status_pending` provides a second string-level assertion.

### M2 — Single F-043 test renamed; 13 others untouched
✅ **Confirmed.** `test_handle_output_total_four_objects` → `test_handle_output_total_five_objects`; assertion updated from `call_count == 4` to `call_count == 5`. The two other test function signature reformats (`test_handle_output_uploads_readme_and_recipe`, `test_key_prefix_uses_dataset_id_version_tag`) are whitespace-only splits caused by ruff; the test bodies are unchanged. All 13 other F-043 test names are present and unmodified.

### L1 — `try/except` wrapping all 5 uploads + DB UPDATE
✅ **Confirmed.** Single `try:` block at line 242 encloses all five `put_object` calls and `update_dataset_row()`. `except (botocore.exceptions.ClientError, psycopg2.Error)` at line 304 calls `context.log.error()` before `raise`. See Disclosed Deviation 2 for the combined-handler analysis.

### L2 — V1 earned by DB-layer assertions, not HTTP round-trip
✅ **Confirmed.** V1a/V1b/V1c are all mock-based DB-layer unit tests. No HTTP endpoint invoked. `contracts/S044-F-044/agreed.md` §4 L2 note is consistent.

### NIT-1 — Docstrings updated from "four objects" to "five objects"
✅ **Confirmed.** Module-level docstring (line 4): "uploads five objects". Class docstring (line 164): "uploads five objects to:". Method docstring step 6 (line 196): "Upload five objects via put_object". All five object names enumerated in both class and method docstrings.

### NIT-2 — `dataset_card_md: str | None = None` as last field
✅ **Confirmed.** Field appears at line 67, after `version_tag: str` (line 66), which is the last non-default field. Python dataclass ordering constraint satisfied.

### NIT-3 — `stats` column deferred, documented
✅ **Confirmed.** `update_dataset_row()` docstring explicitly states "NOTE: 'stats' (JSONB heavy-stats column) is NOT updated here; it remains NULL after F-044. Population of stats is deferred to a later sprint." No `stats` reference in the UPDATE SQL.

### NIT-4 — `context.log.error()` in `handle_output`, NOT in `update_dataset_row`
✅ **Confirmed.** `update_dataset_row()` has no `context` parameter (signature: `dataset_id: int, sample_count: int, size_bytes: int`). The `context.log.error()` call is in `handle_output()`'s `except` clause (line 305). Test `test_update_dataset_row_no_context_param` inspects `inspect.signature(update_dataset_row)` and asserts `"context" not in sig.parameters`.

### NIT-5 — §3.6 failure table cosmetic (4-column clean table)
✅ **N/A for code.** NIT-5 was a cosmetic note about the agreed.md contract table, not a code requirement ("no functional change"). The implementation does not reproduce the §3.6 table in code; the failure semantics are correctly implemented (status stays `'pending'` on any error, `context.log.error()` fires, `raise` propagates). Accepted.

---

## Disclosed Deviations (both accepted)

### Deviation 1 — `_run_handle_output()` returns `tuple[MagicMock, MagicMock]`
The agreed.md contract stub showed `_run_handle_output()` returning a single `mock_s3`. The implementer extended it to return `(mock_s3, mock_update_dataset_row)` so F-044 tests can assert on both the S3 mock and the DB update mock. All F-043 callers updated to `mock_s3, _ = _run_handle_output(...)`. **Accepted** — strictly superior to the spec; no F-043 test semantics changed.

### Deviation 2 — Combined `except (botocore.exceptions.ClientError, psycopg2.Error)`
The agreed.md §3.5 note reads: "DB errors from `update_dataset_row()` propagate directly without being caught here … Alternatively, a second `except psycopg2.Error` clause may be added at the implementer's discretion for symmetric logging." The implementer used a single combined handler. **Accepted** — functionally equivalent and explicitly offered as an alternative in the contract. Both exception types receive identical structured logging and re-raise behaviour. NIT-4 compliance is satisfied: `context` is still only in `handle_output`, not in the helper.

---

## Hard invariants (CLAUDE.md)

| # | Status | Evidence |
|---|---|---|
| 1 — Lineage | ✅ N/A (OQ-5 ruling) | No `Commit` row involved; `dataset` row is the terminal artifact. |
| 2 — Storage separation + CAS | ✅ | `sample_count` (int) + `size_bytes` (int) stored in Postgres. All bytes (Parquet, dataset_infos.json, recipe.json, README.md) go to MinIO only. |
| 3 — Schema frozen post-publish | ✅ | `AND status = 'pending'` guard makes the UPDATE a no-op after first successful publish. `recipe_snapshot` not touched. |
| 4 — LLM calls through gateway | ✅ N/A | No LLM calls. |
| 5 — Async SQLAlchemy (`apps/api/`) | ✅ N/A | All new code in `dagster/`; sync `psycopg2` consistent with established Dagster pattern. |
| 6 — OpenAPI ↔ TS type sync | ✅ N/A | No `apps/api/` schema changes; `make codegen` not required. |

---

## Format-only files (non-blocking)

The following files in the diff contain **whitespace/alignment changes only** (ruff format pass); no semantic changes:

| File | Change type |
|---|---|
| `dagster/dagster_platform/chunker.py` | Alignment removal on `pa.schema([...])`, dict literals; `("attr_embed_vector", pa.list_(...))` split to multi-line; `rows.append({...})` expanded. Content identical when whitespace-normalised. |
| `dagster/dagster_platform/lance_io_manager.py` | Dict alignment removal. |
| `dagster/dagster_platform/lang_tagger.py` | Dict alignment removal. |
| `dagster/dagster_platform/minhash_tagger.py` | Dict alignment removal. |
| `dagster/dagster_platform/quality_tagger.py` | Dict alignment removal. |
| `dagster/dagster_platform/sft_synthesis_qa.py` (non-F-044 hunks) | Dict alignment removal in `_build_lance_storage_options()`; `call_llm_gateway` f-string quote style change (`f"{{\"instruction\"...}}"` → `f'{{"instruction"...}}'`) — same runtime string. |

---

## Test count verification

| File | F-043 tests carried forward | New F-044 tests | Total |
|---|---|---|---|
| `test_hf_dataset_io_manager.py` | 14 (13 unchanged + 1 renamed/updated) | 17 (V2a–f, V1a–c, V3a–b, A1–A6) | 31 |
| `test_sft_synthesis_qa.py` | 27 (unchanged) | 5 (B1–B3, NIT-4 sig, B1-extra) | 32 |
| **Total** | **41** | **22** | **63** |

63/63 pass inside `dataplat-dagster-worker-cpu-1` container per commit message attestation.

---

## NIT (non-blocking, no re-spin required)

**NIT-R1** — `_make_mock_psycopg2_conn()` return type annotation mismatch  
**File**: `dagster/tests/test_sft_synthesis_qa.py` line 522  
**What**: Return type is annotated `-> tuple[MagicMock, MagicMock, MagicMock]` (3 elements) but the function body returns `return mock_conn, mock_cursor` (2 elements). The docstring also references a third `mock_context_manager_cursor` that is not returned separately.  
**Why non-blocking**: All four callers correctly unpack 2 values (`mock_conn, mock_cursor = ...`). Python's runtime tuple unpacking will work correctly. The annotation is cosmetically wrong but causes no test failures.  
**Fix when convenient**: Change annotation to `-> tuple[MagicMock, MagicMock]` and remove the third item from the docstring.

---

## Conclusion

All 13 agreed.md §3 implementation steps are reflected in the diff. All 3 verification criteria (V1/V2/V3) map to passing tests. All 7 round-1 findings (M1/M2/L1/L2/NIT-1/NIT-2/NIT-3) and 2 round-2 NITs (NIT-4/NIT-5) are addressed. All 6 hard invariants are satisfied or explicitly N/A. The two implementer-disclosed deviations are confirmed equivalent and accepted. The 5 format-only files contain whitespace changes only with no semantic differences.

**APPROVED** — `passes` for F-044 may be flipped to `true` in `spec/feature_list.json`.
