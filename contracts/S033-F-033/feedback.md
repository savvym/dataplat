# S033-F-033 — Reviewer Mode A Feedback

Reviewer: Mode A (contract review)
Date: 2026-05-28
Target contract: `contracts/S033-F-033/proposed.md`
Verdict: **CHANGES_REQUESTED**

---

## Summary

The contract is well-structured and covers the two V-criteria with a solid test
plan. Two HIGH findings require code-level fixes before implementation begins;
two MEDIUM and two NIT findings require documentation/contract corrections in
`agreed.md`. There are no BLOCKER-level violations of the hard invariants.

---

## Findings

### [HIGH-1] PyArrow `(group_by, "count")` is semantically wrong — use `count_all`

**Location**: §4.4 `_aggregate()`, `agg_specs.append((group_by, "count"))`

**Problem**: PyArrow's `TableGroupBy.aggregate` interprets `(col, "count")` as
"count *non-null occurrences* of `col` within each group". When `col` is the
group-by key itself and a row's key value is `NULL`, that row forms a null-key
group whose `(group_by, "count")` value is **0** — not the actual row count for
that group.

Verified against the installed PyArrow 24.0.0:

```python
t = pa.table({'lang': ['zh', 'zh', 'en', None], 'score': [1,2,3,4]})
t.group_by('lang').aggregate([('lang', 'count')])
# → lang: ["zh", "en", null]   lang_count: [2, 1, 0]  ← null group WRONG
```

The null group should have count=1 (one row has `lang=NULL`), but `count`
returns 0 because the target column is the group key and its value is NULL.
This means:

- V2 ("Result is consistent with direct Lance count for the same filter")
  **fails for any real data containing null values in the `group_by` column**,
  because `sum(group counts)` underestimates `table.count_rows(filter=...)`.
- `attr_lang_code`, `attr_lang_confidence`, all `attr_*` columns are nullable
  in `CHUNKS_SCHEMA`; null group-by values are realistic in production.

**Required fix**: use `count_all` for the "count rows per group" semantic:

```python
# In _aggregate() — count branch:
if op == "count":
    agg_specs.append(([], "count_all"))      # ← was: (group_by, "count")
    rename_map["count_all"] = "count"         # ← was: rename_map[f"{group_by}_count"] = "count"
```

`([], "count_all")` counts *all rows* in each group regardless of nullity in
any column. With PyArrow 24.0.0, the correct output column name for this form
is `"count_all"` (verified):

```python
t.group_by('lang').aggregate([([], 'count_all')])
# → lang: ["zh", "en", null]   count_all: [2, 1, 1]  ✓
```

The rename_map entry changes from `f"{group_by}_count"` → `"count"` to
`"count_all"` → `"count"`.

The V-criteria tests use non-null mock data, so they would **pass** with
the current `(group_by, "count")` implementation — but that is a test-coverage
gap, not evidence of correctness. `count_all` is the semantically correct idiom
and the fix is trivial.

**Also note**: the `_VALID_OPS` constant set (§4.1) currently includes `"count"`.
Since `"count"` as an `op:col` form is explicitly rejected in `_parse_metrics`
(the `op == "count"` guard), the constant should be documented or renamed to
`_VALID_BINARY_OPS = frozenset({"sum", "mean", "min", "max"})` to avoid
confusion with the standalone `"count"` metric. This is a NIT but arises from
the same section.

---

### [HIGH-2] R4 is an open question — must be resolved in `agreed.md` before build

**Location**: §6 Risk R4 ("lancedb `.select()` before `.where()` ordering")

The proposed contract correctly flags this as an open question and asks the
reviewer to confirm. **Confirmed**: the ordering is safe.

`table.search()` (no query argument) returns a `LanceEmptyQueryBuilder` which
uses a **fluent builder** pattern: every method (`.select()`, `.where()`,
`.limit()`, `.offset()`) simply assigns to an instance variable (`self._columns`,
`self._where`, etc.) and returns `self`. The accumulated state is read once in
`to_query_object()` when `.to_arrow()` is called. Call order is irrelevant.

Verified by inspecting
`apps/api/.venv/lib/python3.12/site-packages/lancedb/query.py` lines 860–902
and 1662–1669:

```python
# LanceEmptyQueryBuilder.to_query_object():
return Query(
    columns=self._columns,   # set by .select()
    filter=self._where,      # set by .where()
    limit=self._limit,
    ...
)
```

`.search().select(cols).where(filter).to_arrow()` is **fully equivalent** to
`.search().where(filter).select(cols).to_arrow()`.

**Required action**: close R4 in `agreed.md` with the above confirmation;
record the recommended ordering convention (we recommend aligning with F-032's
established `.where().select()` ordering for readability consistency, though
either is correct).

---

### [MEDIUM-1] R2 risk description is factually wrong

**Location**: §6 Risk R2

The proposed text states: _"PyArrow's `group_by` by default **drops rows with
null key values** (they are excluded from all groups)"_.

This is incorrect. Null key values are **not excluded**; they form a separate
group with key `NULL`. The actual problem is that `(group_by, "count")` returns
`count=0` for that null-key group (because it counts non-null values of the
target column). After applying the HIGH-1 fix (`count_all`), null-key groups
will correctly receive `count=1` (or more), and R2 as described will no longer
apply.

**Required action**: rewrite R2 in `agreed.md` to accurately state either
(a) the fixed behaviour (null groups correctly counted with `count_all`), or
(b) if the contract author decides not to fix HIGH-1, the accurate description
of the null-key/count=0 edge case.

---

### [MEDIUM-2] No upper bound on `metrics` list length

**Location**: §3.1 `ChunkAggregateRequest.metrics`

The field is declared `metrics: list[str] = Field(..., min_length=1)` with no
`max_length`. A caller could submit hundreds of metrics, each triggering a
column fetch and PyArrow aggregation pass over all matching rows. Given that
the endpoint already has no `.limit()` guard (R1), adding a reasonable cap
(e.g., `max_length=20`) limits one additional surface for runaway requests.

**Required action**: add `max_length=20` (or a documented alternative bound)
to the `metrics` Field in `agreed.md`. The change is one-line.

---

### [NIT-1] `from typing import Any` not listed in files-changed

**Location**: §2 Files changed — `apps/api/dataplat_api/schemas/chunks.py`

`ChunkAggregateResponse` uses `list[dict[str, Any]]`, requiring
`from typing import Any` to be added to `schemas/chunks.py`. The current
`schemas/chunks.py` does not import `Any`. This is a one-line change but it
must not be omitted or the file will raise `NameError` at import time.

**Required action**: add `from typing import Any` to the `schemas/chunks.py`
import list in `agreed.md`'s files-changed section.

---

### [NIT-2] `_build_columns` referenced in §4.5 but not defined in §4.2

**Location**: §4.5 handler skeleton calls `_build_columns(body.group_by, parsed_metrics)`
but §4.2 only shows the equivalent logic inline.

This is a minor contract inconsistency. `agreed.md` should either (a) show
the `_build_columns` helper signature explicitly, or (b) change §4.5 to show
the inline version to match §4.2. Either is acceptable; the concern is that
the implementer has a clear, non-ambiguous contract.

---

## Hard invariant checklist

| Invariant | Status |
|---|---|
| #1 Lineage mandatory | N/A — no commits in this endpoint |
| #2 Storage separation / CAS | N/A — read-only Lance query |
| #3 Schema frozen post-publish | N/A |
| #4 LLM calls through gateway | ✓ No LLM calls |
| #5 Async SQLAlchemy | ✓ All sync Lance I/O via `asyncio.to_thread()`; no DB session used |
| #6 OpenAPI ↔ TS type sync | ✓ §5.4 asserts new schema components; codegen committed same commit |

---

## V-criteria coverage assessment

| Criterion | Proposed test | Coverage verdict |
|---|---|---|
| **V1** `POST /aggregate {filter, group_by="attr_lang_code", metrics=["count"]}` → `[{attr_lang_code, count}, ...]` | `test_aggregate_count_by_lang_code` with real `pa.Table` mock | ✓ Sufficient **after HIGH-1 fix** |
| **V2** "Consistent with direct Lance count" | `test_aggregate_count_consistent_with_direct_count`: sum of group counts == `len(mock_rows)` | ✓ Sufficient for non-null mock data; semantically correct only **after HIGH-1 fix** |

The mock pattern (§5.3) correctly specifies that V2 uses a real `pa.Table`
(not a MagicMock) so PyArrow aggregation executes on real data. This is the
right design.

---

## What is explicitly NOT blocked

- The `.search().select(cols).where(filter).to_arrow()` call chain (R4) is
  confirmed safe — no code change needed.
- The `ChunkAggregateResponse.groups: list[dict[str, Any]]` loose typing is
  acceptable for an inherently dynamic output shape. The OpenAPI spec will
  generate `array of object` with no property constraints, and the TypeScript
  codegen will produce `Array<Record<string, unknown>>` — this is expected
  and acceptable for MVP.
- R1 (full-table scan) is documented and accepted for MVP. ✓
- R6 (lazy group_by column validation) is consistent with F-032 pattern. ✓

---

## Required changes to unblock APPROVED

1. **Fix `_aggregate()` count aggregation**: replace
   `agg_specs.append((group_by, "count"))` + `rename_map[f"{group_by}_count"] = "count"`
   with `agg_specs.append(([], "count_all"))` + `rename_map["count_all"] = "count"`.

2. **Close R4 in `agreed.md`**: document that `.select().where()` ordering is
   valid in lancedb 0.30.2 (fluent builder; order irrelevant). Pick a convention
   (recommend `.where().select()` to match F-032 style).

3. **Correct R2 in `agreed.md`**: accurately describe null-key group behaviour
   (or remove R2 entirely if HIGH-1 fix is applied, since `count_all` handles
   null groups correctly).

4. **Add `max_length=20` to `metrics` Field** (or document the chosen bound).

5. **Add `from typing import Any`** to the §2 files-changed entry for
   `schemas/chunks.py`.

Items 2–5 are `agreed.md` documentation fixes only. Item 1 is a one-function
code change. After these five items are addressed, the contract may be
resubmitted for APPROVED.
