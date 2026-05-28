# S032-F-032 — Chunk query endpoint: feedback.md

Reviewer: Mode A (pre-implementation)
Sprint: S032-F-032
Proposal: `contracts/S032-F-032/proposed.md`
Date: 2026-05-28

---

## Verdict: CHANGES_REQUESTED

One blocker must be resolved before implementation begins.  Two lower-priority
items are flagged for the implementer's attention.  All confirmatory checks are
recorded below.

---

## BLOCKER

### B1 — `get_or_create_chunks_table()` called outside the `try` block (§5 handler outline)

**Location:** `_execute()` closure, line `table = get_or_create_chunks_table()` in §5.

**Problem:**

```python
def _execute() -> tuple[list[dict], int]:
    table = get_or_create_chunks_table()   # ← OUTSIDE try/except
    try:
        total = table.count_rows(...)
        ...
    except Exception as exc:
        raise LanceQueryError(str(exc)) from exc
    return arrow_tbl.to_pylist(), total
```

`get_or_create_chunks_table()` is called **before** the `try:` block.  If it raises
(network failure, bucket missing, permission error), the exception propagates as a
raw `Exception` out of `_execute()`, through `asyncio.to_thread()`, to the outer
handler which catches only `LanceQueryError`.  The uncaught exception causes
FastAPI to return **HTTP 500**.

The test `test_query_lance_error_returns_400` (§6.1) mocks
`get_or_create_chunks_table` to raise `Exception("parse error")` and asserts
HTTP 400.  With the current code that test will receive HTTP 500 and **fail**.

**Required fix — choose one:**

**Option A (preferred): move the call inside `try`**

```python
def _execute() -> tuple[list[dict], int]:
    try:
        table = get_or_create_chunks_table()   # ← inside try
        total = table.count_rows(filter=body.filter)
        q = table.search()
        if body.filter:
            q = q.where(body.filter)
        if body.columns:
            q = q.select(body.columns)
        q = q.limit(body.limit).offset(body.offset)
        arrow_tbl = q.to_arrow()
    except Exception as exc:
        raise LanceQueryError(str(exc)) from exc
    return arrow_tbl.to_pylist(), total
```

This also removes the redundant conditional on `count_rows` (see M1 below).

**Option B: keep table open outside try, fix only the test**

Change `test_query_lance_error_returns_400` to mock `table.count_rows` raising
instead of `get_or_create_chunks_table` raising.  This is architecturally
defensible (table-open errors → 500; query errors → 400) but the test description
must be updated accordingly.

Option A is preferred because it makes the error surface consistent: any Lance
I/O failure, including intermittent S3 timeouts during a real query, returns 400
rather than 500.

---

## MEDIUM

### M1 — Redundant `count_rows` conditional

**Location:** §3 D4 description and §5 handler outline.

```python
total: int = (
    table.count_rows(filter=body.filter)
    if body.filter
    else table.count_rows()
)
```

`table.count_rows(filter=None)` is identical to `table.count_rows()` — the
`filter` parameter defaults to `None` (confirmed in lancedb 0.30.2 source:
`def count_rows(self, filter: Optional[str] = None) -> int`).  The conditional
adds code paths and a test assertion (`"called with no arguments when filter=None"`)
without providing any benefit.

**Fix:**
```python
total: int = table.count_rows(filter=body.filter)
```

This also removes the need for the call-args assertions in §6.1 ("Assert
`mock_table.count_rows.call_args` passes `filter=...` when provided, and is called
with no arguments when `filter=None`") — replace with a single unconditional
assertion: `mock_table.count_rows.assert_called_once_with(filter=body.filter)`.

---

## NITS

### N1 — `LanceQueryError` should inherit from `Exception`, not `ValueError`

**Location:** §5 handler outline.

```python
class LanceQueryError(ValueError):  # ← NIT
```

`ValueError` in Python semantics means "the function received an argument of the
correct type but an inappropriate value".  A query engine parse error is not a
value error in that sense.  Using `ValueError` as the base risks silent swallowing
if any outer code does a broad `except ValueError:`.  Use `Exception` directly:

```python
class LanceQueryError(Exception):
    """Raised when Lance/DataFusion rejects a query; converted to HTTP 400."""
```

---

### N2 — Missing `from __future__ import annotations` in router outline (§5)

**Location:** `routers/chunks.py` outline (§5 top-of-file imports).

The schemas file (§4) correctly includes `from __future__ import annotations`.
The router outline does not.  All other router files in `apps/api/dataplat_api/`
include it.  The implementer should add it as the first import line.

---

## Confirmatory checks (not issues)

The following items were explicitly validated and are **correct as proposed**.

### ✓ `table.search()` without a query vector is valid in lancedb 0.30.2

`LanceTable.search(query=None)` returns a `LanceEmptyQueryBuilder` instance.
The lancedb 0.30.2 docstring reads: *"If None then the select/where/limit clauses
are applied to filter the table."*  The proposed pattern is correct.

### ✓ `.offset()` is supported on `LanceEmptyQueryBuilder` in lancedb 0.30.2

`LanceEmptyQueryBuilder` has an `.offset()` method that sets `self._offset`.
`to_query_object()` includes `offset=self._offset`.  The pagination pattern works.

### ✓ `count_rows(filter=...)` accepts a DataFusion filter string

Signature confirmed: `count_rows(self, filter: Optional[str] = None) -> int`.
The filter is passed directly to the underlying async method.  Valid.

### ✓ Empty table does not error

`count_rows()` on a zero-row table returns `0`.
`table.search()...to_arrow()` on a zero-row table returns a zero-row `pa.Table`.
`pa.Table.to_pylist()` on zero rows returns `[]`.  Result: `{"items": [], "total": 0}`.
V3 criterion is satisfied without special-casing.

### ✓ All 24 `CHUNKS_SCHEMA` fields mapped correctly in `ChunkRead`

Field names match `CHUNKS_SCHEMA` exactly.  Python type mappings are correct:
`pa.string()` / `pa.large_string()` → `str | None`, `pa.float32()` → `float | None`,
`pa.int64()` → `int | None`, `pa.int32()` → `int | None`, `pa.bool_()` → `bool | None`,
`pa.list_(pa.uint64())` → `list[int] | None`, `pa.list_(pa.float32(), 1024)` → `list[float] | None`,
`pa.list_(pa.string())` → `list[str] | None`, `pa.timestamp("ms")` → `datetime | None`.

### ✓ `asyncio.to_thread()` pattern correctly applied

Lance S3 I/O is synchronous.  Bundling both `count_rows()` and `search()...to_arrow()`
inside a single `_execute()` closure dispatched via `asyncio.to_thread(_execute)` is
correct: it avoids blocking the event loop and avoids two separate thread dispatches.

### ✓ Mock strategy is sound (patch target at module-level name)

`patch("dataplat_api.routers.chunks.get_or_create_chunks_table")` patches the
name as looked up at call time inside `_execute()`, not at closure-definition time.
The mock is active when the thread executes.  No closure-capture hazard.

### ✓ `openapi.json` manual regeneration with `checks.sh` contract guard

`checks.sh` `contract` layer begins with `[[ -f Makefile ]] || exit 0`.  Manual
regeneration via `uv run python -c "...app.openapi()..."` committed in the same
commit satisfies hard invariant #6.  The approach in D9 is acceptable.

### ✓ Hard invariants #1–#6

| Invariant | Status |
|---|---|
| #1 Lineage mandatory | N/A — read-only endpoint, no commits written |
| #2 Storage separation / CAS | ✓ — no blob bytes in Postgres; reads from Lance only |
| #3 Schema frozen post-publish | N/A — no schema changes |
| #4 LLM calls via gateway | ✓ — no LLM calls |
| #5 Async SQLAlchemy | ✓ — no Postgres queries in this endpoint |
| #6 OpenAPI ↔ TS sync | ✓ — D9 commits `openapi.json` in same commit |

---

## Required changes before implementation

1. **B1 (BLOCKER):** Move `table = get_or_create_chunks_table()` inside the `try`
   block (Option A), OR change `test_query_lance_error_returns_400` to mock
   `table.count_rows` raising (Option B).  Option A is preferred.
2. **M1 (MEDIUM):** Simplify `count_rows` call to `table.count_rows(filter=body.filter)`.
3. **N1 (NIT):** Change `LanceQueryError(ValueError)` to `LanceQueryError(Exception)`.
4. **N2 (NIT):** Add `from __future__ import annotations` to `routers/chunks.py`.

Once B1 is resolved (and M1/N1/N2 incorporated), submit for re-review or proceed
directly to implementation at the implementer's discretion — the remaining items
are mechanical.
