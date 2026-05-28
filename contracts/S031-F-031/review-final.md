# S031-F-031 — `LanceChunksIOManager` column mode: review-final.md

Reviewer: Mode B (post-implementation diff review)
Sprint: S031-F-031
Commit: d1bb538
Verdict: **APPROVED**

---

## Summary

The implementation is correct, well-structured, and all verification criteria pass.
Five pre-implementation findings (F1–F5) are fully resolved.  The implementer made
one authorised deviation from the §2.1 spec (chose D3a over D3b after the D2 probe
confirmed exit 0); this is explicitly permitted by §3 D3 and is well-documented.
Three minor NITs are noted below but none are blockers.

---

## V-criteria status

| Criterion | Status | Evidence |
|---|---|---|
| V #1: Quality scores preserved after lang tagger | ✅ PASS | `checks.sh attr_col_isolation` E2E layer; 7-step orchestration with pre/post snapshot + `math.isclose(rel_tol=1e-5)` assertion |
| V #2: Column merge uses `merge_insert("chunk_id")`, not full row replacement | ✅ PASS | `test_column_mode_calls_merge_insert_not_delete` unit test (assert `merge_insert("chunk_id")` called, `delete` NOT called) + static grep in `checks.sh` |

---

## Findings from feedback.md (F1–F5)

### F1 — HIGH: RMW race condition absent from risk table

**Resolution: ✅ ADDRESSED**

R8 added to the agreed.md risk table with full documentation of the concurrency
hazard, the sequential-execution assumption (Dagster default), and the note that
D3a (chosen by the implementer) is naturally concurrent-safe.  The implementation
is D3a (partial-column merge_insert), which eliminates the race entirely: each
tagger's payload contains only its own columns and never carries other taggers'
values, so concurrent writes for different tagger assets on the same source cannot
produce silent data loss.

The module-level docstring in `lance_io_manager.py` documents this explicitly:
> "Naturally concurrency-safe: each tagger payload contains only its own columns."

### F2 — MEDIUM: D6 metadata inconsistency (empty-list early-return)

**Resolution: ✅ ADDRESSED**

Option A implemented as agreed.  `producer_asset` is derived from
`context.asset_key.path[-1]` **before** the empty-list early-return guard
(lines 139/143–150 of `lance_io_manager.py`).  The early-return correctly
emits `"column_skipped"` for tagger assets and `"row_skipped"` for the `chunks`
asset.  Test 3 (`test_column_mode_empty_list_early_return`) verifies this
explicitly, including the absence of any Lance I/O.

### F3 — MEDIUM: `attr_col_isolation` missing from `all)` in `checks.sh`

**Resolution: ✅ ADDRESSED**

Line 1343 of `verify/checks.sh`:
```bash
bash "$0" attr_col_isolation  # F-031
```
is present in the `all)` case, immediately after `bash "$0" attr_minhash  # F-030`.
Running `bash verify/checks.sh all` will now exercise the cross-tagger isolation
check as part of the standard CI gate.

### F4 — NIT: bare ints in `add_output_metadata()`

**Resolution: ✅ ADDRESSED**

All three tagger assets in `definitions.py` use `MetadataValue.int()` wrappers:
```python
context.add_output_metadata({
    "source_id": MetadataValue.int(source_id),
    "chunk_count": MetadataValue.int(len(rows)),
})
```
`MaterializeResult` import is correctly retained for `extract_mineru`.

### F5 — NIT: fragile float comparison in `isolation_check.py`

**Resolution: ✅ ADDRESSED**

The post-lang assertion in `checks.sh` (lines 3604–3606) uses:
```python
assert math.isclose(r["attr_quality_score"], snapshot[cid], rel_tol=1e-5), ...
```
`import math` is present in the heredoc.  This is immune to the float32 →
JSON round-trip noise described in F5.

---

## New findings (Mode B)

### M1 — MEDIUM: §2.1 spec describes D3b implementation steps; code implements D3a (authorised deviation)

**Severity:** MEDIUM (non-blocking)

The agreed.md §2.1 description of `_column_mode_write()` specifies the five-step
D3b (read-modify-write) procedure:
1. Fetch full-schema existing rows via `table.search().where(...).to_list()`
2. Build `{chunk_id: existing_row}` index
3. Merge incoming columns into existing rows
4. Convert merged rows to `pa.Table` using `CHUNKS_SCHEMA`
5. Call `merge_insert("chunk_id").when_matched_update_all().execute(merged_pa_table)`

The implementation follows the **D3a** (direct partial merge) path:
1. Validate `chunk_id` keys in incoming dicts (defensive guard)
2. Convert partial dicts directly to `pa.Table` (schema inferred from dict keys)
3. Call `merge_insert("chunk_id").when_matched_update_all().execute(partial_pa_table)`

This deviation is **explicitly permitted** by §3 D3:
> "If the D2 probe confirms that partial-column merge_insert preserves missing
> columns (exit 0), the implementer SHOULD switch to **D3a** as primary."

The module docstring documents the D2 probe result ("D2 probe confirmed exit 0")
and the rationale for choosing D3a.  The code is correct and the behaviour
(concurrency-safety, no full-row read, no CHUNKS_SCHEMA type-coercion on write) is
strictly better than D3b for this deployment.

The only issue is that §2.1 was not updated to reflect the D3a implementation.
This creates a spec-code mismatch that could confuse future maintainers reading the
agreed.md contract.  **No action required to ship this sprint**, but the discrepancy
should be noted for any future documentation update.

### N1 — NIT: Unit test suite does not exercise "other tagger columns survive unchanged"

**Severity:** NIT (non-blocking)

Agreed.md §2.6 lists "Columns not in the incoming dicts (other taggers' columns)
survive unchanged" as a required unit-test coverage point.  None of the five unit
tests in `test_lance_io_manager_column_mode.py` mock this end-to-end: they verify
that the correct (partial) pa.Table is passed to `execute()`, but they do not
assert that absent columns are preserved after the write.

In D3a, this property is guaranteed by the D2 probe (lancedb behaviour), not by
application code.  The E2E `attr_col_isolation` layer covers it at integration
level.  Acceptable for MVP.

### N2 — NIT: Stale chunk_id (value absent from Lance) is silently no-op'd in D3a; R5 mitig. text is D3b-specific

**Severity:** NIT (non-blocking)

The agreed.md R5 mitigation states: "D3b's merge step logs a warning and skips
orphaned incoming rows."  In D3a, a chunk_id value that does not exist in the
Lance table is silently dropped by `when_matched_update_all()` (pure update
semantics, no insert path).  No warning is logged for this scenario.

The code does correctly warn on rows that are **missing the `chunk_id` key
entirely** (malformed dicts), which is the practical guard.  The case of a
well-formed `{"chunk_id": "stale_id", ...}` that refers to a deleted chunk will
be silently skipped — correct behaviour (no crash, no data loss), but no telemetry.
Low operational impact for MVP.

### N3 — NIT: Agreed.md R2 mitigation references CHUNKS_SCHEMA type casting in `_column_mode_write`; inapplicable for D3a

**Severity:** NIT (non-blocking)

R2 reads: "Validate CHUNKS_SCHEMA against actual table schema in probe; cast
mismatched columns in `_column_mode_write()`."  In D3a, `_column_mode_write()`
does not use `CHUNKS_SCHEMA` at all — the partial pa.Table schema is inferred
purely from the incoming dict keys (chunk_id + 2–4 tagger columns).  CHUNKS_SCHEMA
is only used in `handle_output()` for the `create_table(..., exist_ok=True)` call
(correct for table-creation path).

For the partial write, type correctness is the responsibility of the `compute_*`
functions (which produce Python float/str/bool values that PyArrow infers
accurately).  The R2 mitigation note in agreed.md is now stale for D3a.  No code
defect.

---

## Hard invariants check

| Invariant | Status | Notes |
|---|---|---|
| #4 — LLM calls via gateway only | ✅ | `quality_tagger.py` calls `requests.post(gateway_url + "/api/internal/llm/completions")`.  No direct Anthropic/OpenAI SDK imports anywhere. |
| #5 — Async SQLAlchemy | ✅ | No SQLAlchemy anywhere in this diff; pure Dagster/lancedb stack. |
| #6 — OpenAPI ↔ TS type sync | ✅ | No FastAPI route or Pydantic schema changes.  `make codegen` not required. |
| Lineage (#1) | ✅ | Column-mode writes never touch `producer_asset`, `producer_version`, `augmented_from`, `augmenter_id`, `augmenter_config_hash`. |
| Storage separation (#2) | ✅ | No blob bytes in Postgres.  All writes go to Lance/MinIO. |
| Schema frozen post-publish (#3) | ✅ | No schema changes. |

---

## Unit test coverage (5 tests)

| Test | What it verifies | Adequate? |
|---|---|---|
| `test_column_mode_calls_merge_insert_not_delete` | V-crit #2: `merge_insert("chunk_id")` called, `delete` NOT called; metadata `mode="column"`, `merge_key="chunk_id"`, `row_count=1` | ✅ |
| `test_column_mode_passes_partial_schema_to_execute` | D3a: `execute()` receives pa.Table with only `{chunk_id, attr_quality_score, attr_quality_provider}` — not full CHUNKS_SCHEMA | ✅ |
| `test_column_mode_empty_list_early_return` | F2 fix: empty list → mode `"column_skipped"`, no `create_table`, no `merge_insert`, no `delete` | ✅ |
| `test_column_mode_missing_chunk_id_warning` | Defensive guard: row missing `chunk_id` key → WARNING logged; valid row still processed | ✅ |
| `test_row_mode_still_uses_delete_add` | Regression: `producer_asset="chunks"` → `delete()` + `add()` called, `merge_insert` NOT called | ✅ |

Coverage gap (N1 above): "other tagger columns unchanged" not unit-tested.
Acceptable — covered by E2E `attr_col_isolation` layer.

---

## `spec/feature_list.json` check

`F-031.passes` is `false` ✅  — No premature flip.

---

## Regression check

Per the sprint prompt, all three regression layers passed before this review:
- `bash verify/checks.sh attr_quality` ✅
- `bash verify/checks.sh attr_lang` ✅
- `bash verify/checks.sh attr_minhash` ✅

---

## Decision

**APPROVED**

0 blockers.  0 HIGH findings.  1 MEDIUM finding (M1 — authorised D3a deviation,
§2.1 not updated; no action required for merge).  3 NITs (N1–N3, all non-blocking).

The implementation correctly satisfies both V-criteria, addresses all five feedback
findings from `feedback.md`, and does not violate any hard invariant.  The
`feature_list.json` F-031 flag remains `passes: false` as required pending verifier
sign-off.
