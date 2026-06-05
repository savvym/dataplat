# Sprint S049-F-049 — Mode A Reviewer Feedback

**Reviewer**: Mode A (independent read)  
**Date**: 2026-06-05  
**Document reviewed**: `contracts/S049-F-049/proposed.md` (Revision 1)  
**Feature**: F-049 — `GET /api/runs` list endpoint  

---

## References read

- `spec/feature_list.json` — F-049 entry (P0, depends_on F-048, 3 verification criteria)
- `spec/product-spec.md` and `spec/tech-direction.md`
- `contracts/S045-F-045/agreed.md` (list endpoint pattern + M1 lynchpin)
- `contracts/S048-F-048/agreed.md` (run detail + owner-scope)
- `contracts/S046-F-046/agreed.md` (detail/list divide)
- `apps/api/dataplat_api/routers/runs.py` (current state, post-F-048)
- `apps/api/dataplat_api/schemas/runs.py` (current state, post-F-048)
- `apps/api/dataplat_api/db/models.py` lines 282–327 (Run ORM model)
- `apps/api/tests/test_datasets_list.py` (F-045 precedent)
- `apps/api/tests/test_runs_get.py` (F-048 precedent)

---

## Overall assessment

The proposal is thorough, well-structured, and correct in almost all of its substance.
The owner-scope design, the two-query pattern, the schema boundary rationale,
the route-ordering analysis, and the hard-invariant audit are all solid.
Two MEDIUM findings require resolution before the document can be APPROVED;
one LOW inconsistency should be corrected; one NIT is optional polish.
No BLOCKERs were found.

---

## Scrutiny-point-by-point analysis

### 1. `RunListItem` field set — list/detail divide (F-045/F-046 precedent)

**PASS.** The 10-field list item correctly excludes `asset_keys`, `partition_keys`,
`config`, and `trigger_context`. The justification table in §4 is accurate:
- `asset_keys` / `partition_keys`: Postgres `ARRAY(Text)` columns — bulky, detail-level,
  exactly parallel to `recipe_snapshot` / `stats` excluded from `DatasetListItem` in F-045.
- `config`: JSONB, always `None` in current trigger paths, detail-level.
- `trigger_context`: JSONB, always `None`, internal/opaque, detail-level.

All nullable FK fields (`triggered_by`, `dataset_id`, `recipe_id`, `source_collection_id`)
are correctly typed as `int | None` matching the ORM model (`nullable=True`).
The `dagster_run_id` NOT NULL constraint is correctly reflected as non-nullable `str`.

The divide is consistent with F-045/F-046 precedents and well-justified.

---

### 2. Status query param — 422 before SQL?

**PASS.** `Optional[Literal["pending", "running", "success", "failure"]]` on a
FastAPI query parameter is resolved at the request-binding layer before any handler
code runs. An invalid value (`?status=bogus`) will produce **422 Unprocessable Entity**
before any SQL statement is constructed or executed. The rationale for `Literal` over
`RunStatus` enum in §4 is sound (TEXT column, no existing enum class).

T10 (`test_list_runs_invalid_status_returns_422`) explicitly verifies the 422 response.

---

### 3. Both queries share owner-scope + status filter — M1 lynchpin

**PASS (structure).** §5 explicitly shows both the page query and the COUNT query
carry `Run.triggered_by == current_user.id`, and both conditionally append
`.where(Run.status == status)` when the filter is present. The two-query pattern
with `literal_binds=True` SQL assertions (T6, T7) mirrors the exact M1 pattern
from F-045 `agreed.md` §11 and `test_datasets_list.py` test 6.

**See M1 below for a gap in T7's coverage of V3.**

---

### 4. Route registration order

**PASS.** The required order after this sprint is:

```
POST ""                          — trigger_extract_run  (existing)
GET  ""                          — list_runs            (NEW, must be inserted here)
GET  /{id}                       — get_run_detail       (existing)
GET  /dagster/{dagster_run_id}   — get_run_status       (existing)
```

The proposal correctly identifies the insertion point and explains why `GET ""`
must precede `GET /{id}`. One technical clarification (NIT-1 below): the actual
shadowing risk between `GET ""` and `GET /{id}` is lower than described (they are
structurally distinct paths and FastAPI can distinguish them regardless of order),
but the recommendation to place `GET ""` before `GET /{id}` is still conventional
and correct.

---

### 5. Ordering: `started_at DESC NULLS LAST + id DESC` — nullable check

**PASS.** Confirmed from `db/models.py` line 316:

```python
started_at: Mapped[Optional[sa.DateTime]] = mapped_column(
    sa.DateTime(timezone=True), nullable=True
)
```

`started_at` IS nullable. `NULLS LAST` is the correct choice — it pushes
`status='pending'` rows (which have `started_at=None`) to the bottom, surfacing
active and completed runs first. This mirrors `materialized_at DESC NULLS LAST`
from F-045. SQLAlchemy 2.0's `.nulls_last()` method generates the correct
`ORDER BY run.started_at DESC NULLS LAST` SQL on Postgres.

**See M2 below: ordering has no test coverage.**

---

### 6. Test coverage mapping V1/V2/V3

**V1** → T1 (3 rows → total:3, len:3), T5 (field completeness). ✓  
**V2** → T8 (success filter behavior), T7 (SQL structural). ✓  
**V3** → T9 (running filter behavior), T7 (SQL structural — **see M1**). ⚠

---

### 7. Pagination decision

**PASS.** §6 documents the deferral clearly with identical rationale to F-045.
`total` is included in the response envelope for forward-compatibility.
The spec verification criteria (V1/V2/V3) do not test pagination behavior.
This is the correct MVP boundary call.

---

### 8. Owner isolation test

**PASS.** T4 (`test_list_runs_owner_isolation`) uses two independent user overrides
(user A: 2 runs, user B: 1 run) with separate session mocks, asserting each user's
call returns only their own total and items. This mirrors `test_list_datasets_only_own_datasets`
(test 5 in `test_datasets_list.py`) exactly. The pattern is correct.

---

### 9. Hard invariants #5 (async) and #6 (codegen)

**PASS.** Both are explicitly committed in §11:
- Invariant #5: `✓ Required` with exact specification (`async def`, `AsyncSession`,
  `await session.execute()`, no `session.query()`).
- Invariant #6: `Required — hard requirement` with the specific `make codegen` command
  and explicit "same commit" language. Also called out in §3 and §12 DoD.

---

### 10. Files table — codegen in same-commit requirement

**PASS.** The `packages/api-types/openapi.json` row is present in §3 with status
"**generated**" and the explicit codegen snippet. The §3 "Codegen hard requirement"
paragraph, §11 invariant #6, and §12 DoD checklist all restate the same-commit
requirement. The implementer cannot miss it.

---

### 11. Open questions resolution

| OQ | Status | Resolution |
|---|---|---|
| OQ-1: Multi-value `?status=` | RESOLVED | Single-value only for MVP. No spec criterion tests multi-status. `IN (...)` would add complexity without spec benefit. **CONFIRMED: single value is sufficient.** |
| OQ-2: `Literal` vs `RunStatus` enum | RESOLVED | `Literal` is appropriate for MVP. `Run.status` is TEXT (no Postgres enum); no existing `RunStatus` enum in codebase. **CONFIRMED: `Literal` is correct.** |
| OQ-3: Defer `limit`/`offset` | RESOLVED | Already settled in §6 with full rationale. Not actually open. |
| OQ-4: Include `triggered_by` in `RunListItem` | RESOLVED | Include it. The caller already knows their own id; the field is present in `RunDetailResponse`; it makes the list item self-describing. **CONFIRMED: include `triggered_by`.** |
| OQ-5: Dedicated ordering test (T12) | → See M2 | The claim that T6/T7 implicitly verify ORDER BY is wrong (see M2). Resolve by adding T12. |

---

## Findings

---

### MEDIUM M1 — T7 description and §9 V3 SQL-structural claim are contradictory

**Location**: §8 T7 description, §9 V3 mapping.

**Problem**: §8 T7 specifies:
> "Call `GET /api/runs?status=success`; compile each with `literal_binds=True`; assert the literal string `"success"` appears in BOTH queries."

§9 V3 claims:
> "T7 ... same test covers all status values **by parameterisation**; specifically checks the literal appears in both queries."

The T7 test description shows only a single call with `?status=success`. It is not
described as a parameterized test (`@pytest.mark.parametrize`). If the implementer
writes T7 exactly as described in §8, the SQL-structural assertion for `?status=running`
(V3) is absent — only the behavior-level T9 covers V3. The §9 V3 claim of SQL-structural
coverage "by parameterisation" is then false.

**Required fix (pick one):**

A. **Parameterize T7** over at least `["success", "running"]` (or all four status values)
   using `@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])`.
   Update the T7 row in §8 to show the parametrize decorator and loop the assertion.
   Update the "Maps to spec criterion" column to `"V2 + V3 structural (parameterized)"`.

B. **Add T7b** `test_list_runs_status_filter_in_both_queries_running` that repeats the
   same SQL-structural assertion for `?status=running`. Update §9 V3 to reference T7b
   instead of claiming T7 covers V3 "by parameterisation".

Either fix resolves the inconsistency. Option A is preferred (less code duplication,
consistent with how F-038 parameterizes similar filter tests).

---

### MEDIUM M2 — Ordering requirement has no test coverage; T6/T7 claim is incorrect

**Location**: §8 ordering note, OQ-5.

**Problem**: The feature description in `feature_list.json` explicitly states
"runs are ordered by `started_at` descending." The proposed implementation uses
`ORDER BY run.started_at DESC NULLS LAST, run.id DESC`. However:

1. **T6/T7 do NOT verify ordering.** §8 claims "the compiled SQL must contain the ORDER BY
   clause to be a valid page query" — but this is wrong. If the implementer accidentally
   omits the `.order_by()` call entirely, both T6 and T7 will still pass (they only assert
   for `triggered_by` and status literal presence). A handler that returns rows in arbitrary
   order would pass all 11 proposed tests.

2. **OQ-5 is left open** ("If reviewer requires an explicit `ORDER BY … NULLS LAST` string
   assertion, add as T12. Reviewer should decide.").

**Required fix**: Add **T12** `test_list_runs_page_query_has_correct_order_by`. This test
should:
- Capture the page query via `session.execute.call_args_list[0].args[0]` (same mechanism as T6).
- Compile with `literal_binds=True`.
- Assert the compiled SQL contains `"started_at"` (or `"ORDER BY"`) and `"NULLS LAST"`.
- Assert `"id"` also appears in the ORDER BY (tiebreaker).

This is a straightforward extension of the T6 SQL-structural pattern.
The test count becomes 12, within the "9–11 target" range noted in §8 — update the
"Test count: 11" note to "Test count: 12" and update the §12 DoD checklist accordingly.

---

### LOW L1 — §3 files table says "9 fields" for `RunListItem`, but it has 10

**Location**: §3 files table, `schemas/runs.py` row.

**Problem**: The `Reason` column reads:
> "Add `RunListItem` **(9 fields)** and `RunListResponse`…"

Counting `_LIST_ITEM_KEYS` in §8 and the field table in §4, `RunListItem` has exactly
**10 fields**: `id`, `dagster_run_id`, `kind`, `status`, `started_at`, `ended_at`,
`triggered_by`, `dataset_id`, `recipe_id`, `source_collection_id`.

This discrepancy could confuse the implementer (they might stop at 9 and leave a field
out). Fix: change "(9 fields)" to "(10 fields)" in the §3 files table.

---

### NIT-1 — Shadowing risk explanation slightly overstated in §7

**Location**: §7, last paragraph of "Why order matters."

The proposal says `GET /api/runs` "might fail to match or be caught by the parametric handler"
if `GET /{id}` is declared first. In fact, `GET /api/runs` (no trailing segment) and
`GET /api/runs/{id}` (one trailing segment required) are structurally different paths —
FastAPI does not confuse them regardless of declaration order, because `{id}` requires a
non-empty path segment. The real shadowing concern in this router is between
`GET /{id}` and `GET /dagster/{dagster_run_id}` (both have the same segment count but
one has a fixed prefix `dagster/`), which is addressed correctly in F-048.

The recommendation to declare `GET ""` before `GET /{id}` is still correct and
conventional (matching the `POST ""` + `GET ""` pattern in datasets and recipes routers),
so this is **not a behavioral issue** — only a documentation accuracy point. Update the
explanation to note that declaring `GET ""` first is convention/cleanliness, not a
FastAPI path-collision safeguard.

---

## Resolutions required before APPROVED

| Finding | Severity | What to change in the proposal |
|---|---|---|
| M1 | MEDIUM | §8 T7: add `@pytest.mark.parametrize` over status values (or add T7b for `?status=running`); §9 V3: remove "by parameterisation" claim if not parameterizing, or update T7 description to reflect parameterization |
| M2 | MEDIUM | §8: add T12 `test_list_runs_page_query_has_correct_order_by` with `ORDER BY`+`NULLS LAST` SQL assertion; §8 ordering note: remove incorrect claim that T6/T7 verify ordering; §12 DoD: update test count to 12; OQ-5: resolve by specifying T12 is required |
| L1 | LOW | §3 files table: change "(9 fields)" to "(10 fields)" |
| NIT-1 | NIT | §7: correct the shadowing-risk explanation |

---

## What does NOT need to change

- All schema fields, nullable annotations, and exclusions in `RunListItem` — correct.
- The two-query implementation pattern in §5 — correct.
- The `Optional[Literal[...]]` type for the status query param — correct.
- The `func.count().select_from(Run)` pattern for the COUNT query — correct.
- The `started_at DESC NULLS LAST, id DESC` ordering — correct and well-justified.
- All 11 currently-specified tests (T1–T11) — logic and purpose are all sound.
- The hard-invariants audit — complete and accurate.
- Pagination deferral and `total` forward-compatibility rationale — correct.
- OQ-1/OQ-2/OQ-4 recommendations — all confirmed above.
- Route registration ordering recommendation — correct (though explanation can be sharpened per NIT-1).
- Codegen hard requirement language — complete.

---

VERDICT: CHANGES_REQUESTED

---

## Round-2 Review (2026-06-05 — Mode A)

**Document reviewed**: `contracts/S049-F-049/proposed.md` (Revision 2)
**Scope**: Confirm each Round-1 finding is resolved; flag genuine new BLOCKERs only.

---

### Confirmation checklist

**1. T7 parameterized over all four status values (including `?status=running`)**

§8 T7 is now decorated with
`@pytest.mark.parametrize("status_value", ["pending", "running", "success", "failure"])`.
Each variant independently compiles both the page and COUNT queries with `literal_binds=True`
and asserts the literal status value appears in both. The "Maps to spec criterion" column
reads `"V2 + V3 structural (all four status values parameterized)"`.

✅ **M1 RESOLVED.**

---

**2. T12 `test_list_runs_page_query_has_correct_order_by` with correct assertions**

§8 T12 is present. It captures the page query via
`session.execute.call_args_list[0].args[0]`, compiles with `literal_binds=True`,
and asserts the compiled SQL contains `"started_at"`, `"NULLS LAST"`, and `"id"`.
The ordering note in §8 correctly states that T6 and T7 do NOT verify the ORDER BY
clause and that T12 fills that gap.

One cosmetic note (not a blocker): `"id"` as a bare substring will also match
`run.id` in the SELECT list, so it is not load-bearing on its own. In practice the
`"started_at"` + `"NULLS LAST"` pair is sufficient to catch a missing `.order_by()`.
Implementers wanting a tighter assertion can use the substring `"id DESC"` or
`"run.id DESC"`. This is purely cosmetic — the test as specified does prevent the
failure mode M2 was concerned about.

✅ **M2 RESOLVED.**

---

**3. Test count is 12 everywhere**

- §3 files table: "12 unit tests" ✅
- §8 footer: "Test count: 12" ✅
- §12 DoD: "all 12 tests (T1–T12, where T7 is parameterized over 4 status values)" ✅

No residual "11" in a test-count context.

✅ **CONFIRMED.**

---

**4. §9 V-mapping reflects T7 covering V2 + V3**

- V2: cites T8 (behavior) + T7 (SQL-structural `"success"` literal in both queries). ✅
- V3: cites T9 (behavior) + T7 parameterized `"running"` variant (SQL-structural,
  described explicitly as "not implicit"). ✅

✅ **CONFIRMED.**

---

**5. §3 files table says "10 fields" not "9 fields"**

`schemas/runs.py` row reads: "Add `RunListItem` **(10 fields)** and `RunListResponse`…"

✅ **L1 RESOLVED.**

---

**6. §7 / §1 wording softened; declaration order framed as convention/readability**

§7 "Why order matters" now explicitly states: "It is not a FastAPI path-collision
safeguard per se … FastAPI can dispatch them correctly regardless of declaration order …
Declaring `GET ""` first is good convention and aids readability; it is not required for
correctness."

§1 preamble uses "conventional … aids readability" without the former false claim that
`GET /api/runs` would fail to match if declared after `GET /{id}`. The real shadowing
concern (`GET /{id}` vs `GET /dagster/{dagster_run_id}`) is correctly noted as addressed
in F-048.

✅ **NIT-1 RESOLVED.**

---

**7. §13 Round-1 Addenda section exists**

§13 is present with four sub-sections (M1, M2, L1, NIT-1), each summarising the
Round-1 finding and the exact fix applied in Revision 2.

✅ **CONFIRMED.**

---

### New findings

No new BLOCKERs or MEDIUMs are visible in Revision 2.

The cosmetic `"id"` substring note under confirmation point 2 is recorded for
the implementer's awareness but does not block approval.

---

VERDICT: APPROVED
