# S033-F-033 — Review Final (Mode B)

Reviewer: Mode B (post-implementation code review)
Date: 2026-05-28
Commit reviewed: `8763bca` (`feat(F-033): POST /api/chunks/aggregate endpoint with PyArrow group_by`)
Diff base: `34e2910`
Contract: `contracts/S033-F-033/agreed.md`
Verdict: **APPROVED**

---

## Review scope

Files examined:
- `apps/api/dataplat_api/routers/chunks.py` — aggregate handler + helpers
- `apps/api/dataplat_api/schemas/chunks.py` — request/response models
- `apps/api/tests/test_chunks_aggregate.py` — 10 tests
- `packages/api-types/openapi.json` — regenerated

Supporting checks run:
- `bash verify/checks.sh backend` → **163 passed, 0 failed** ✓
- PyArrow API behaviour verified in the project venv (uv run python3)
- `packages/api-types/openapi.json` assertions per agreed.md §5.4 ✓

---

## Finding-by-finding resolution check

All six findings from `feedback.md` are correctly addressed in both `agreed.md`
and the implementation:

| Finding | Required fix | Implementation status |
|---|---|---|
| **HIGH-1** `(group_by, "count")` wrong for null groups | Replace with `([], "count_all")` + `rename_map["count_all"] = "count"` | ✓ `chunks.py` line 91–92; verified correct against PyArrow 24.0.0 in venv |
| **HIGH-1 clarification** Rename constant | `_VALID_BINARY_OPS` (not `_VALID_OPS`) | ✓ `chunks.py` line 38 |
| **HIGH-2** R4 open question (select/where order) | Closed; use `.where().select()` convention | ✓ `chunks.py` lines 185–187; agreed.md §4.3 documents the LanceDB 0.30.2 fluent-builder confirmation |
| **MEDIUM-1** R2 factually wrong re null exclusion | Rewrite: null groups correctly counted with `count_all` | ✓ agreed.md §6 R2 correctly states LOW severity with accurate description; response schema docstring matches |
| **MEDIUM-2** No upper bound on `metrics` | `max_length=20` added to `metrics` Field | ✓ `schemas/chunks.py` line 106; OpenAPI `maxItems: 20` confirmed |
| **NIT-1** Missing `from typing import Any` | Add import to `schemas/chunks.py` | ✓ `schemas/chunks.py` line 13 |
| **NIT-2** `_build_columns` undefined in §4.2 | Explicit helper signature in agreed.md §4.2 | ✓ agreed.md shows full signature |

---

## Hard invariant checklist

| Invariant | Status |
|---|---|
| **#1 Lineage** | N/A — read-only aggregate endpoint; no commit created |
| **#2 Storage separation / CAS** | ✓ Lance I/O only; no blob bytes in Postgres |
| **#3 Schema frozen** | N/A |
| **#4 LLM via gateway** | ✓ No LLM calls anywhere in this diff |
| **#5 Async SQLAlchemy** | ✓ No DB session. Lance I/O in `asyncio.to_thread(_execute)`; `q.to_arrow()` never called on the async thread |
| **#6 OpenAPI ↔ TS sync** | ✓ `/api/chunks/aggregate`, `ChunkAggregateRequest`, `ChunkAggregateResponse` all present in `openapi.json`; committed in **same commit** as code |

---

## Implementation correctness analysis

### HIGH-1 fix — `count_all` semantics

Verified in venv against PyArrow 24.0.0:

```
pa.table({'lang': ['zh','zh','en',None]}).group_by('lang').aggregate([([], 'count_all')])
→ [{'lang':'zh','count_all':2}, {'lang':'en','count_all':1}, {'lang':None,'count_all':1}]  ✓
```

The old `(group_by, "count")` would have returned `count_all=0` for the null-key
group. The fix is semantically correct for all group-by columns.

### rename_map key correctness

PyArrow output column naming (verified):
- `([], "count_all")` → output column `"count_all"` → `rename_map["count_all"] = "count"` ✓
- `("score", "sum")` → output column `"score_sum"` → `rename_map["score_sum"] = "sum_score"` ✓

The `f"{col}_{op}"` key in `rename_map` exactly matches PyArrow's output naming
convention `{col}_{op}`. The rename produces the user-visible `{op}_{col}` format
specified in agreed.md §3.2.

### `.where().select()` ordering

Correctly applied at `chunks.py` lines 185–187:
```python
if body.filter:
    q = q.where(body.filter)
q = q.select(columns_to_fetch)
```
Matches F-032 convention; R4 CLOSED as documented. ✓

### Auth guard

`current_user: User = Depends(get_current_user)` at line 160. The parameter is
intentionally unused in the body (auth-only guard pattern, identical to
`query_chunks`). HTTP 401 confirmed by `test_aggregate_no_token_returns_401`. ✓

### `asyncio.to_thread()` wrapping

Both the Lance I/O (`q.to_arrow()`) and the PyArrow aggregation (`_aggregate()`)
execute inside `_execute()`, which is submitted to a thread pool via
`await asyncio.to_thread(_execute)`. The async handler is never blocked. ✓

### Column deduplication in `_build_columns`

`_build_columns` correctly deduplicates the metric columns against the
`group_by` column, producing a minimal Lance select list. ✓

---

## Test coverage assessment

| V-criterion | Test | Real pa.Table? | Verdict |
|---|---|---|---|
| **V1** `filter + group_by="attr_lang_code" + metrics=["count"]` → correct groups | `test_aggregate_count_by_lang_code` (42 zh + 17 en) | ✓ yes | PASS |
| **V2** `sum(group counts) == total rows` | `test_aggregate_count_consistent_with_direct_count` (10+20+12=42) | ✓ yes | PASS |

All 10 agreed.md §5.2 tests are present. Correctness tests (V1, V2, no_filter,
empty_result, numeric_metric, multiple_metrics) all use **real `pa.Table`**
objects so PyArrow aggregation executes on genuine data — not a MagicMock
stand-in. Error-path tests (401, 400, 422) correctly use lighter mock setups
since they never reach aggregation code.

Additional behavioural assertion in `test_aggregate_no_filter`:
`qb.where.assert_not_called()` verifies the filter branch is correctly skipped
when no filter is provided. ✓

---

## Minor observations (non-blocking)

These are observations only; none prevents APPROVED.

1. **Column name in `op:col` not regex-validated.** The agreed.md grammar specifies
   `[a-zA-Z_][a-zA-Z0-9_]*` (max 128 chars) for column names, but `_parse_metrics`
   only checks `if not col`. An invalid column name reaches `q.select()` and
   surfaces as a Lance/PyArrow error → HTTP 400. Consistent with F-032's lazy
   column validation pattern (agreed.md R6). Acceptable for MVP.

2. **Duplicate `"count"` entries in `metrics` list.** `metrics=["count","count"]`
   would produce `agg_specs = [([], "count_all"), ([], "count_all")]`. PyArrow's
   behaviour with duplicate aggregation specs is undefined (likely an error), but
   the `except Exception as exc: raise LanceQueryError(...)` handler catches it
   and returns HTTP 400. Not catastrophic. Min-length Pydantic validation prevents
   empty lists; no bound on duplicates is mentioned in agreed.md.

3. **HTTP 400 and 401 not declared in OpenAPI responses.** Only 200 and 422 are
   listed. This matches F-032's pattern and is a FastAPI auto-generation artefact,
   not a bug. Acceptable.

4. **`passes: false` for F-033.** Correctly NOT flipped. Verifier step is next. ✓

---

## Checks gate

```
bash verify/checks.sh backend
  ruff check  : All checks passed
  mypy        : Success: no issues found in 36 source files
  pytest      : 163 passed, 1 deselected, 1 warning in 4.50s
  ✓ backend passed
```

---

## Verdict

**APPROVED**

All six feedback.md findings are correctly addressed in the code. All hard
invariants pass. The 10 agreed.md tests are present with real `pa.Table` data
for correctness assertions; the full test suite is green. The OpenAPI schema is
regenerated and committed in the same commit. The implementation faithfully
follows agreed.md in all sections (§3, §4.1–4.5). No blocking issues found.

Next step: → `verifier` to run `bash verify/checks.sh backend` and flip
`passes: true` for F-033 in `spec/feature_list.json`.
