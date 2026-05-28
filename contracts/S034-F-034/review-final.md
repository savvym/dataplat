# S034-F-034 — Mode B Code Review

Reviewer: `reviewer` agent  
Commit reviewed: `cd0975a` (vs parent `07981f0`)  
Date: 2026-05-28  
Verdict: **APPROVED**

---

## Summary

The implementation faithfully follows every item in `agreed.md`. All five
Mode-A feedback findings (M1, M2, N3, N4, N5) are resolved. Hard invariants
#5 and #6 are satisfied. Code quality, error handling, edge-case coverage, and
test organisation are all solid. No blockers; two non-blocking observations are
noted for the record.

---

## Finding verification — Mode-A feedback round-trip

| Finding | Severity | Status | Evidence |
|---------|----------|--------|----------|
| **M1** — Add ≥2 Pydantic 422 tests | MEDIUM | ✅ RESOLVED | `test_distribution_filter_too_long_returns_422` + `test_distribution_bins_out_of_range_returns_422` present (both `bins=0` and `bins=101` covered); test count updated to 13 in `agreed.md §4.2` and `§4.5` |
| **M2** — `type: Literal["numeric","categorical"]` | MEDIUM | ✅ RESOLVED | `schemas/chunks.py` line 173: `type: Literal["numeric", "categorical"]`; `Literal` added to the `from typing import Any, Literal` import; OpenAPI emits `"enum": ["numeric","categorical"]` (verified by assertion) |
| **N3** — `test_distribution_empty_table` makes 2 calls | NIT | ✅ RESOLVED | Test explicitly constructs two separate `pa.Table` (float + string), patches `get_or_create_chunks_table` twice, and asserts `type=="numeric"` + `type=="categorical"` + `buckets==[]` for both paths |
| **N4** — `qb.where.assert_not_called()` in categorical no-filter test | NIT | ✅ RESOLVED | Present at `test_chunks_distribution.py` line 182 after saving `qb = mock_table.search.return_value` |
| **N5** — Acknowledge `bins` extension from design doc | NIT | ✅ RESOLVED | `agreed.md §1` contains explicit `> **Note [N5]:**` paragraph; handler docstring also documents in-process memory trade-off |

---

## Contract compliance — `agreed.md` §1–§4

### §1 Scope & edge cases

| Edge case | Contract | Implementation |
|-----------|----------|----------------|
| Filter matches 0 rows → `buckets: []`, type from schema | ✓ | `_compute_numeric_distribution` returns `[]` after `drop_null()` on empty; `_compute_categorical_distribution` returns `[]` on `len(tbl)==0`; schema still populated by Lance |
| All-same-value numeric → single bucket `[v,v]` | ✓ | `if col_min == col_max:` guard prevents `np.histogram` `ValueError` |
| All-null numeric → `buckets: []` | ✓ | `drop_null()` → `len(valid)==0` → `return []` |
| All-null categorical → `[{"value": null, "count": N}]` | ✓ | `count_all` on null-key group; returned as `{"value": None, …}` |
| Unknown column → HTTP 400 | ✓ | DataFusion raises at `.to_arrow()`; outer `except Exception as exc: raise LanceQueryError` catches it |
| Unsupported type → HTTP 400 | ✓ | `LanceQueryError(f"… unsupported type …")`; inner `except LanceQueryError: raise` avoids double-wrap |
| Missing auth → HTTP 401 | ✓ | `Depends(get_current_user)` |
| `bins` with categorical → silently ignored (OQ1) | ✓ | Only passed to `_compute_numeric_distribution`; never reaches categorical path |

### §2 Files changed

All four prescribed files are present in the diff and correct:

| File | ∆ |
|------|---|
| `apps/api/dataplat_api/schemas/chunks.py` | `ChunkDistributionRequest` + `ChunkDistributionResponse`; `Literal` added to import |
| `apps/api/dataplat_api/routers/chunks.py` | handler + two helpers; `import numpy as np`; schema imports updated |
| `apps/api/tests/test_chunks_distribution.py` | NEW — 13 tests (confirmed by `grep -c "^def test_"` → 13) |
| `packages/api-types/openapi.json` | Regenerated; assertion passed (`/api/chunks/distribution` path + both schema components + `type` enum) |

`apps/api/uv.lock` is also updated, reflecting the new explicit `numpy>=1.24`
entry resolved to `numpy==2.4.6`. Correct.

No other files touched; existing tests unmodified.

### §3 Schema & implementation correctness

- **Request schema** (`ChunkDistributionRequest`): `filter: str | None = Field(default=None, max_length=1000)`, `column: str = Field(..., min_length=1, max_length=128)`, `bins: int = Field(default=10, ge=1, le=100)` — exact match with `agreed.md §3.1`. ✓
- **Response schema** (`ChunkDistributionResponse`): `column: str`, `type: Literal["numeric","categorical"]`, `buckets: list[dict[str, Any]]` — exact match with `agreed.md §3.2`. ✓
- **Type detection** (`col_type = arrow_tbl.schema.field(body.column).type`): four-way branch (floating → numeric, integer → numeric, string/large_string → categorical, else → LanceQueryError) — exact match with `agreed.md §3.3`. ✓
- **`_compute_numeric_distribution`**: matches `agreed.md §3.4` verbatim. ✓
- **`_compute_categorical_distribution`**: matches `agreed.md §3.5` verbatim; reuses proven F-033 `count_all` null-safe pattern. ✓
- **Handler structure**: `asyncio.to_thread(_execute)`; `if body.filter: q = q.where(body.filter)` (no `where(None)`); `q = q.select([body.column])`; no `.limit()`; returns `ChunkDistributionResponse(column=body.column, …)` — matches `agreed.md §3.6`. ✓

Minor improvement (non-blocking): the inner `_execute()` return type annotation
is declared as `tuple[Literal["numeric", "categorical"], list[dict]]` rather
than `tuple[str, list[dict]]` as in the agreed.md pseudo-code. This is strictly
more precise and correct; no action required.

### §4 Test coverage

All 13 tests from `agreed.md §4.2` are present with correct names.
V1 (`test_distribution_numeric_with_filter`) and V2
(`test_distribution_categorical_no_filter`) fully satisfy the feature
verification criteria.

Mock pattern mirrors F-033 exactly: real `pa.Table` for correctness tests;
`MagicMock` Lance chain at transport layer only; `patch` target correct;
`app.dependency_overrides` with try/finally cleanup. ✓

---

## Hard invariants

| # | Invariant | Status |
|---|-----------|--------|
| 1 | Lineage mandatory | N/A — read-only endpoint, no commits |
| 2 | Storage separation + CAS | ✓ — no Postgres writes |
| 3 | Schema frozen post-publish | N/A |
| 4 | LLM calls via gateway | N/A — no LLM usage |
| 5 | Async SQLAlchemy from day one | ✓ — no DB session; Lance I/O in `asyncio.to_thread` |
| 6 | OpenAPI ↔ TS type sync | ✓ — `openapi.json` in same commit; assertion passed; `type` field rendered as `"enum": ["numeric","categorical"]` |

---

## Non-blocking observations (no action required)

### NB1 — `column` empty-string 422 test absent

Mode-A finding M1 noted a `column` empty-string case "should also have a named
test for consistency" but the **required action** said "at least 2" (filter
too long + bins out of range). Both required tests are present. The empty-column
test is a nice-to-have; it is covered implicitly by Pydantic's `min_length=1`
and Pydantic is well-tested upstream. No action needed.

### NB2 — `filter=""` treated as "no filter" (silent empty-string)

`if body.filter:` means an empty string `""` passes Pydantic validation (no
`min_length` on `filter`) but is silently treated as `None`. This is an
existing pattern from F-032 and F-033 and is not a new risk introduced by this
sprint. If a stricter contract is desired post-MVP, add `min_length=1` to the
`filter` field across all three endpoints. Not worth holding this sprint.

---

## Conclusion

All M1/M2/N3/N4/N5 findings addressed. All `agreed.md` items implemented.
Invariants #5 and #6 satisfied. 13/13 tests present; pattern adherence to
F-032/F-033 exemplary throughout.

**APPROVED** — verifier may proceed.
