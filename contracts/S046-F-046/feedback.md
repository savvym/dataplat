# Mode A Review — S046-F-046 (`GET /api/datasets/{id}`)

**Reviewer**: reviewer (Mode A)  
**Date**: 2026-06-04  
**Revision reviewed**: proposed.md rev 1  
**Verdict**: **CHANGES_REQUESTED**

---

## Evidence read

| File | Status |
|---|---|
| `spec/feature_list.json` F-046 entry | Read — confirmed id, depends_on, passes |
| `contracts/S046-F-046/proposed.md` | Read in full |
| `apps/api/dataplat_api/db/models.py` Dataset class (lines 249–280) | Read — 13 columns verified |
| `apps/api/dataplat_api/routers/datasets.py` | Read — current route order confirmed |
| `apps/api/dataplat_api/routers/recipes.py` | Read — F-039 owner-scope + route-order precedent |
| `apps/api/dataplat_api/schemas/datasets.py` | Read — existing schemas unbroken |
| `apps/api/tests/test_datasets_list.py` | Read — F-045 mock patterns |
| `apps/api/tests/test_recipes_get.py` | Read — F-039 test set precedent |
| `CLAUDE.md` | Invariants #1–#6 + sprint rules applied |

---

## Critical checks (pass / fail)

### C1 — ORM column inventory matches proposed schema exactly

Independently verified from `models.py` lines 259–279. The `Dataset` class has **exactly 13** `Mapped[...]` columns:

| Column | ORM type | Nullable | Proposed Pydantic type | Match? |
|---|---|---|---|---|
| `id` | `Mapped[int]` BigInteger PK | NOT NULL | `int` | ✓ |
| `recipe_id` | `Mapped[Optional[int]]` BigInteger FK | nullable | `int \| None` | ✓ |
| `recipe_snapshot` | `Mapped[dict]` JSONB | **NOT NULL** | `dict` (not `dict\|None`) | ✓ |
| `version_tag` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `hf_repo_uri` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `dataset_card_md` | `Mapped[Optional[str]]` Text | nullable | `str \| None` | ✓ |
| `sample_count` | `Mapped[Optional[int]]` BigInteger | nullable | `int \| None` | ✓ |
| `size_bytes` | `Mapped[Optional[int]]` BigInteger | nullable | `int \| None` | ✓ |
| `stats` | `Mapped[Optional[dict]]` JSONB | nullable | `dict \| None` | ✓ |
| `status` | `Mapped[str]` Text | NOT NULL | `str` | ✓ |
| `materialized_by` | `Mapped[Optional[int]]` BigInteger FK | nullable | `int \| None` | ✓ |
| `materialized_at` | `Mapped[Optional[sa.DateTime]]` | nullable | `datetime \| None` | ✓ |
| `dagster_run_id` | `Mapped[Optional[str]]` Text | nullable | `str \| None` | ✓ |

**No `created_at` column on `Dataset`** — confirmed. OQ-4 resolution is correct. The contract does not invent a synthetic field.  
**`recipe_snapshot` is `dict` (not `dict | None`)** — `nullable=False` in ORM, `dict` in schema. Correct.  
**`stats` is `dict | None`** — `nullable=True` in ORM. Correct.  
Result: **PASS**.

### C2 — Owner-scope correctness

The proposed handler uses:
```python
select(Dataset).where(Dataset.id == id).where(Dataset.materialized_by == current_user.id)
```

The ownership column on `Dataset` is `materialized_by` (not `owner_id`), confirmed from:
1. `models.py` line 272: `materialized_by: Mapped[Optional[int]] = mapped_column(sa.BigInteger, sa.ForeignKey("users.id"), nullable=True)`
2. `datasets.py` list endpoint (F-045): `.where(Dataset.materialized_by == current_user.id)` — consistent field name.
3. `routers/datasets.py` materialize handler step 4: `materialized_by=current_user.id` — this is how the ownership FK is set on insert.

Both filters are in a **single** `SELECT` (not a two-step load-then-check), collapsing wrong-owner → 404 identically to `recipes.py` line 143. Test 3 (`test_get_dataset_wrong_owner_returns_404`) correctly simulates this by returning `None` from the mock.  
Result: **PASS**.

### C3 — `from_attributes=True` present

`DatasetDetailResponse` includes `model_config = ConfigDict(from_attributes=True)` — required for `model_validate(row)` on an ORM instance.  
Result: **PASS**.

### C4 — Route ordering

Current `datasets.py` route order: `GET "" → POST /{recipe_id}/materialize`.  
Proposed insertion: `GET "" → GET /{id} → POST /{recipe_id}/materialize`.  

`GET ""` vs `GET /{id}`: distinct paths (`/api/datasets` vs `/api/datasets/42`) — no conflict.  
`GET /{id}` vs `POST /{recipe_id}/materialize`: different HTTP methods AND different path depth (1 segment vs 2 segments with a literal `/materialize` suffix) — no conflict.  
Ordering `GET "" → GET /{id} → POST /{recipe_id}/materialize` is consistent with `recipes.py` (read-before-write, parameterized GET before parameterized POST on same prefix).  
Result: **PASS**.

### C5 — Invariant #6 (OpenAPI ↔ TS type sync)

The proposed contract states unambiguously in §5 step 6 and §11 row 6: regenerating `packages/api-types/openapi.json` and committing the diff in the **same commit** as Python changes is a **hard requirement, not advisory**. The manual regeneration command is provided. No `Makefile` caveat softens this — the contract correctly frames it as required. The S045 precedent for manual regeneration is referenced.  
Result: **PASS**.

### C6 — Scope discipline (F-047, UI work)

§3 explicitly excludes F-047 download (separate sprint) and UI work. No download endpoint, no presigned URL logic, no Parquet streaming is mentioned anywhere in the contract.  
Result: **PASS**.

### C7 — Async SQLAlchemy (Invariant #5)

Handler uses `AsyncSession = Depends(get_session)`, `await session.execute(select(...).where(...))`, and synchronous `scalar_one_or_none()` on the result proxy. No `session.query()`. Pattern is identical to `recipes.py` line 142–145.  
Result: **PASS**.

### C8 — No LLM calls, no storage writes, no lineage events

Read-only endpoint. Invariants #1, #2, #3, #4 are N/A — correctly noted in §11.  
Result: **PASS**.

---

## Findings

### MEDIUM-1 — Missing `test_get_dataset_invalid_id_returns_422` (test-set incompleteness vs F-039 precedent)

**Severity**: MEDIUM  
**Location**: §8 test list (8 tests defined; 1 missing from F-039 precedent)

The proposed contract explicitly states in §8: *"All tests follow the `test_recipes_get.py` structure"*. Reading `test_recipes_get.py`, the F-039 test set contains **6 tests** including `test_get_recipe_invalid_id_returns_422`:

> "Non-integer path segment → 422 (FastAPI path param validation fires before handler). The `id` path parameter is typed as `int`; FastAPI rejects any non-integer value with a 422 Unprocessable Entity before the handler body is entered."

The proposed F-046 test list has 8 tests but omits this case. The `GET /{id}` path parameter is `id: int`, so `/api/datasets/not-an-int` will produce 422 automatically from FastAPI — but since the contract explicitly cites `test_recipes_get.py` as the structural template and that template includes the 422 case, omitting it is an inconsistency. It also serves as documentation and regression protection should the path parameter type annotation ever be inadvertently changed.

**Required change**: Add a 9th test to §8:

| # | Test name | What it checks |
|---|---|---|
| 9 | `test_get_dataset_invalid_id_returns_422` | Non-integer path segment (`/api/datasets/not-a-number`) → 422 before handler body executes. Auth override set (so 401 doesn't interfere). Asserts `response.status_code == 422`. |

This test does not require any session mock call because FastAPI path-param validation fires before dependency injection.

---

### NIT-1 — Incorrect feature ID in §3 Out of Scope

**Severity**: NIT  
**Location**: proposed.md §3, second bullet

The contract reads: *"F-067 — Dataset detail page (web UI): frontend component is a separate sprint."*

From `spec/feature_list.json`:
- F-067: "Recipe editor auto-generated config form"
- **F-070**: "Dataset detail page: clicking a dataset shows its recipe_snapshot, stats (split sizes, attribute distributions), and a list of Parquet file paths" — this is the correct feature to call out.

**Required change**: Replace `F-067` with `F-070` in §3.

---

### NIT-2 — `depends_on` mismatch with `feature_list.json`

**Severity**: NIT  
**Location**: proposed.md §1, "Depends on" line

The contract states *"Depends on: F-044 (passes: true), F-045 (passes: true)"* but `feature_list.json` lists F-046's `depends_on` as `["F-044"]` only — F-045 is not a formal spec dependency. Both are `passes: true` so this does not block implementation, but the proposed contract misrepresents the formal dependency graph.

**Required change**: Change the Depends-on line to *"Depends on: F-044 (passes: true) — formal dependency per feature_list.json. F-045 (passes: true) — practical predecessor (same router file); not a formal spec dependency."* or simply drop F-045 from the dependency line.

---

### NIT-3 — Handler docstring should name `materialized_by` explicitly

**Severity**: NIT  
**Location**: proposed.md §5 step 3, handler snippet docstring

The proposed handler snippet omits the ownership field name from the docstring comment. `recipes.py` line 131 has the comment: *"Owner-scoping: combines `id == ?` AND `owner_id == ?` in one query so that a non-existent id and an id owned by another user both return 404 (no-enumeration-leak, mirrors get_source / list_sources_by_collection)."*

The F-046 handler docstring should be similarly explicit — stating `materialized_by` rather than just referring to §9 — to avoid any future maintainer reading `recipes.py` expecting `owner_id` and incorrectly "correcting" the column name. Suggest:

```
Owner-scoping: combines ``id == ?`` AND ``materialized_by == ?`` in one query
so that a non-existent id and an id owned by another user both return 404
(no-enumeration-leak, mirrors get_recipe). ``materialized_by`` is the owner FK
on Dataset (analogous to ``owner_id`` on Recipe).
```

---

## Required changes before APPROVED

1. **[MEDIUM-1]** Add `test_get_dataset_invalid_id_returns_422` as test #9 in §8. Update the test count from 8 → 9 in all references in the contract.
2. **[NIT-1]** Fix `F-067 → F-070` in §3 Out of Scope.
3. **[NIT-2]** Clarify F-045 as a practical predecessor, not a formal `depends_on` dependency.
4. **[NIT-3]** Make the handler docstring explicit about `materialized_by` as the owner FK.

None of these changes affect the core implementation logic, schema, owner-scope policy, or OpenAPI requirements. They are localised edits to the contract document and the test list.

---

CHANGES_REQUESTED

---

## Round 2

**Reviewer**: reviewer (Mode A)
**Date**: 2026-06-04
**Revision reviewed**: proposed.md rev 2

---

### Checklist — each round-1 finding

**M1 — `test_get_dataset_invalid_id_returns_422` added as test #9**: ✓  
§8 row 9 is present with the correct description (non-integer path segment, auth override set, asserts 422, no session mock required). Count "8→9" propagated to all three reference sites: §2 ("9 unit tests"), §4 file table ("9 unit tests"), §8 heading. No stale "8" remains.

**NIT-1 — F-067→F-070**: ✓  
§3 second bullet now reads "**F-070** — Dataset detail page (web UI)". F-067 is gone.

**NIT-2 — F-045 not a formal dependency**: ✓  
§1 "Depends on" line now correctly distinguishes F-044 (formal, per `feature_list.json`) from F-045 (practical predecessor, same router file, not a spec dependency).

**NIT-3 — `materialized_by` named in docstring**: ✓  
§5 step 3 handler snippet includes the full docstring. It names `materialized_by` as the owner FK explicitly and states "analogous to `owner_id` on Recipe" — the maintenance trap is flagged.

**§12 addenda section**: ✓  
Added per S045 precedent. Table accurately maps each finding to its resolution. No implementation logic changed.

---

### Sanity pass — no new issues introduced

- **Schema field list (§6)**: 13 fields, types, and nullability unchanged from rev 1 (which passed C1). Still matches ORM exactly.
- **Route ordering (§5 step 4 / OQ-3)**: `GET "" → GET /{id} → POST /{recipe_id}/materialize` unchanged. Still correct.
- **Invariant #6**: §5 step 6 and §11 row 6 still hard-require same-commit OpenAPI regeneration. No softening introduced.
- **Test #9 correctness**: Auth override precedes the 422 assertion (prevents 401 masking), and the note that FastAPI path-param validation fires before dependency injection is present — test can be written without a session mock. No logical error.
- **§12 narrative**: No new claims; purely a change log. Does not contradict any other section.

No new issues found.

---

APPROVED
