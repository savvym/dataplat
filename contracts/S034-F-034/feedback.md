# S034-F-034 — Reviewer Feedback (Mode A)

Reviewer: `reviewer` agent
Date: 2026-05-28
Verdict: **CHANGES_REQUESTED**

---

## Overall assessment

The proposal is well-scoped, follows the F-032/F-033 patterns faithfully, and
correctly satisfies both V1 and V2 verification criteria. The handler structure,
async thread dispatch, error propagation, and mock pattern are all solid. No hard
invariants are violated. Two medium-severity gaps and three nits must be addressed
before implementation proceeds.

---

## Findings

### [1] MEDIUM — Missing Pydantic field-validation tests (422 coverage)

F-033 explicitly tests `filter` exceeding `max_length=1000` → 422
(`test_aggregate_filter_too_long_returns_422`). This is now an established
pattern in the chunks test suite. The F-034 proposal has **zero** Pydantic
constraint tests despite three constrained fields:

| Field  | Constraint         | Missing test                         |
|--------|--------------------|--------------------------------------|
| filter | max_length=1000    | filter of 1001 chars → 422           |
| column | min_length=1       | empty column string → 422            |
| bins   | ge=1, le=100       | bins=0 or bins=101 → 422             |

At minimum, add `test_distribution_filter_too_long_returns_422` (mirrors F-033
exactly) and `test_distribution_bins_out_of_range_returns_422` (exercises the
`ge`/`le` bounds on `bins`). The `column` empty-string case is covered implicitly
by Pydantic but should also have a named test for consistency.

These tests require no mock setup (Pydantic validates before the handler runs),
so they are low-cost to write. Omitting them leaves the 422 contract untested and
breaks the pattern established in the sprint before this one.

**Required action:** Add at least 2 new tests (filter too long → 422; bins out of
range → 422) to `test_chunks_distribution.py` and update the test count from 11
to 13 in §4.2 and §4.5.

---

### [2] MEDIUM — `ChunkDistributionResponse.type` should be `Literal["numeric", "categorical"]`

The proposal declares:

```python
type: str   # "numeric" | "categorical"
```

`type` is a discriminator that can only ever take two values. Using plain `str`
means:

1. FastAPI's OpenAPI generator emits `"type": "string"` with no enum constraint,
   so the generated TypeScript types in `packages/api-types/` will be `string`
   rather than `"numeric" | "categorical"`. Clients that pattern-match on `type`
   get no compile-time help.
2. There is no runtime guard if a future code path accidentally returns a different
   string — Pydantic will accept it silently.

The fix is a one-line change in `schemas/chunks.py`:

```python
from typing import Any, Literal   # add Literal to existing import

class ChunkDistributionResponse(BaseModel):
    column:  str
    type:    Literal["numeric", "categorical"]
    buckets: list[dict[str, Any]]
```

This is consistent with Pydantic v2 and FastAPI and costs nothing. The generated
TS type becomes `"numeric" | "categorical"` which is exactly what the F-063
frontend will need.

**Required action:** Change `type: str` → `type: Literal["numeric", "categorical"]`
in `ChunkDistributionResponse`. Update imports in `schemas/chunks.py` accordingly
(add `Literal` — it may already be imported via `typing`; check first).

---

### [3] NIT — `test_distribution_empty_table` description is ambiguous

The test description says "Lance returns 0-row table → `{"buckets": []}` for
**both** a float column (numeric) **and** a string column (categorical)". It is
unclear whether this is:

(a) **Two assertions in one test** (the test calls the endpoint twice with
    different `column` values against the same 0-row mock), or
(b) **Two sub-descriptions** meaning the single test only checks one column type.

Variant (a) is the correct interpretation and the implementer should be explicit
about it: the test should call `POST /api/chunks/distribution` twice — once with a
float-typed column, once with a string-typed column — against the same 0-row
`pa.Table`, and assert `buckets == []` in both cases.

If only one column type is checked, the numeric `all-null` path is already covered
by `test_distribution_numeric_all_null`, but the categorical 0-row path (distinct
from `all_null`) is only covered by `test_distribution_empty_table`. Ensure the
categorical 0-row case is explicitly asserted.

**Required action:** Clarify in the proposal that `test_distribution_empty_table`
makes two endpoint calls (one numeric column, one categorical column) and asserts
`buckets == []` for both. No code change required if the intent is already (a).

---

### [4] NIT — `test_distribution_categorical_no_filter` should assert `qb.where` not called

F-033's `test_aggregate_no_filter` includes:

```python
qb.where.assert_not_called()
```

This guards against a regression where `where(None)` is accidentally passed to
Lance (which could behave differently from no `.where()` call at all, depending on
the Lance version). The F-034 proposal's `test_distribution_categorical_no_filter`
description does not mention this assertion.

**Required action:** Add `qb.where.assert_not_called()` to
`test_distribution_categorical_no_filter`, mirroring the F-033 pattern. Save the
`qb` reference from the mock (same way `test_aggregate_no_filter` does it).

---

### [5] NIT — `bins` parameter extends the design doc signature without acknowledgment

Design doc §9.1 (line 823) specifies:

```
POST   /chunks/distribution   — { filter, column }  返回 histogram
```

The proposal adds `bins` (not in the design doc). This is a sensible and
backward-compatible extension (default=10 makes the behavior predictable for
callers that don't pass `bins`), and is unlikely to require human approval given
it is purely additive with a safe default. However, the proposal should briefly
acknowledge in §1 (Scope) or §5 (Risks) that `bins` is a minor extension beyond
the design doc's `{ filter, column }` signature, not a spec violation.

**Required action:** Add a one-sentence note in §1 (Scope) acknowledging the
`bins` addition. No code change.

---

## OQ1 resolution (reviewer opinion)

**Silently ignore `bins` for categorical.** The proposal's reasoning is correct:
a caller that sends the default body `{"column": "attr_lang_code"}` does not set
`bins` deliberately — it just gets the Pydantic default of 10. Returning 400
would punish correct callers and is surprising. Silent ignore is the right choice.
No further discussion needed; resolve OQ1 as "silently ignore" in `agreed.md`.

## OQ2 resolution (reviewer opinion)

**Defer `min`/`max`/`null_count` metadata.** The verification criteria do not
require it, the response schema is non-breaking to extend, and adding it now
increases scope without a concrete consumer. Resolve OQ2 as "deferred" in
`agreed.md`.

---

## Invariant checklist

| # | Invariant | Status |
|---|-----------|--------|
| 1 | Lineage mandatory | N/A — read-only endpoint, no commits |
| 2 | Storage separation + CAS | ✓ — no Postgres blob writes |
| 3 | Schema frozen post-publish | N/A — no Silver/Gold commits |
| 4 | LLM calls via gateway | N/A — no LLM calls |
| 5 | Async SQLAlchemy | N/A — no DB session in this handler |
| 6 | OpenAPI ↔ TS codegen | ✓ — `make codegen` and OpenAPI assertion in §4.4 |

---

## Correctness spot-checks (no issues found)

- `pa.ChunkedArray.drop_null()` → `ChunkedArray`; `len()` and `to_pylist()` both
  work. `np.array(..., dtype=np.float64)` correct for int32/float32 inputs. ✓
- `col_min == col_max` guard before `np.histogram` is the correct exact-equality
  check for the "all identical values" edge case (numpy raises `ValueError` on
  `range=(v, v)`). ✓
- `arrow_tbl.schema.field(body.column).type` is unreachable when the column name
  is unknown (DataFusion raises at `.to_arrow()` time, caught by the outer except).
  The 0-row case is handled because Lance preserves schema on empty result sets. ✓
- `pa.Table.group_by(col).aggregate([([], "count_all")])` produces a null-key
  group for rows with null in `col`, correctly included in the `{"value": null,
  "count": N}` bucket. Same `count_all` pattern as F-033 HIGH-1 fix. ✓
- `LanceQueryError` re-raise pattern inside `_execute()` correctly prevents
  double-wrapping; outer `except LanceQueryError as exc: raise HTTPException(...)`
  maps to 400. ✓
- Auth dependency override pattern identical to F-032/F-033. ✓
- `from __future__ import annotations` already in `routers/chunks.py` — the new
  `Literal` annotation in schemas will also need it (already present in
  `schemas/chunks.py` via `from __future__ import annotations`). ✓

---

## Required changes summary

| # | Severity | File(s) changed |
|---|----------|-----------------|
| 1 | MEDIUM   | `test_chunks_distribution.py` — add ≥2 Pydantic validation tests; update test count in §4.2/§4.5 |
| 2 | MEDIUM   | `schemas/chunks.py` — `type: Literal["numeric", "categorical"]`; update `proposed.md` §3.2 |
| 3 | NIT      | `proposed.md` §4.2 — clarify `test_distribution_empty_table` makes 2 API calls |
| 4 | NIT      | `proposed.md` §4.2 — add `qb.where.assert_not_called()` to categorical no-filter test |
| 5 | NIT      | `proposed.md` §1 or §5 — note `bins` as minor addition beyond design doc |

None of the above require architecture changes. Items 3–5 are documentation
clarifications only. After addressing 1 and 2, re-submit for Mode A re-review.
