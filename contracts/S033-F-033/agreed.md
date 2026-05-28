# S033-F-033 — Chunk Aggregate Endpoint: agreed.md

Sprint ID: S033-F-033
Feature: F-033 `chunk_aggregate_endpoint`
Status: AGREED
Dependencies: F-032 (chunk query endpoint) ✓, F-008 (auth) ✓

---

## Reviewer feedback addressed

| Finding | Resolution |
|---|---|
| HIGH-1: `(group_by, "count")` wrong for null groups | Fixed: use `([], "count_all")` + `rename_map["count_all"] = "count"`. See §4.4. |
| HIGH-2: R4 open question (select/where ordering) | Closed: lancedb 0.30.2 fluent builder — ordering irrelevant. Convention: use `.where().select()` to match F-032. See §4.3. |
| MEDIUM-1: R2 factually wrong re null exclusion | Rewritten: null-key groups are correctly counted with `count_all`. See §6. |
| MEDIUM-2: No upper bound on metrics list | Fixed: `max_length=20` added to `metrics` field. See §3.1. |
| NIT-1: Missing `from typing import Any` import | Added to §2 files-changed for `schemas/chunks.py`. |
| NIT-2: `_build_columns` inconsistency | Resolved: §4.2 now shows the explicit helper function signature. |

---

## §1 Objective

This sprint adds `POST /api/chunks/aggregate` — a new route on the existing
`chunks_router` that accepts a DataFusion SQL filter, a single `group_by` column
name, and a list of metric specifiers, and returns grouped statistics over the
matching Lance rows. The primary use-case is distribution analysis (e.g., "how
many chunks per language code for source 42?"). Aggregation is performed entirely
in Python-process memory using PyArrow's `Table.group_by().aggregate()` API after
fetching only the required columns from Lance; no DataFusion GROUP BY SQL is
emitted (lancedb 0.30.2 does not expose a GROUP BY path through its query-builder
API). The endpoint reuses all established patterns from F-032: sync Lance I/O
wrapped in `asyncio.to_thread()`, `LanceQueryError` → HTTP 400, auth via
`Depends(get_current_user)`, and no per-user row scoping on the Lance side.
After implementation, `packages/api-types/openapi.json` is regenerated and
committed in the same commit (hard invariant #6).

---

## §2 Files changed

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/chunks.py` | **MODIFIED** — add `from typing import Any` import; add `ChunkAggregateRequest` and `ChunkAggregateResponse` models |
| `apps/api/dataplat_api/routers/chunks.py` | **MODIFIED** — add `POST /api/chunks/aggregate` handler + `_parse_metrics()` + `_build_columns()` + `_aggregate()` helpers; imports `import pyarrow as pa` at module level |
| `apps/api/tests/test_chunks_aggregate.py` | **NEW** — 10 unit tests, mock Lance table pattern (mirrors F-032 test style) |
| `packages/api-types/openapi.json` | **MODIFIED** — regenerated via manual script; same command as F-032 §D9 |

No new router registration needed (the route is added to the existing `router`
object already mounted in `main.py`). No Postgres migration, no Alembic change,
no new dependency (PyArrow is already installed).

---

## §3 Schema design

### 3.1 Request — `ChunkAggregateRequest`

```python
from typing import Any
# ... (added to existing imports in schemas/chunks.py)

class ChunkAggregateRequest(BaseModel):
    """Request body for POST /api/chunks/aggregate.

    filter   — DataFusion SQL predicate fragment applied to the Lance chunks
               table before grouping (e.g. "source_id = 42").
               None / omitted means group over all rows.  Max 1000 chars.
    group_by — Name of a single Lance column to group by (e.g. "attr_lang_code",
               "producer_asset").  Must be a valid CHUNKS_SCHEMA column name;
               unknown names cause a 400 at PyArrow grouping time.
    metrics  — Non-empty list of metric specifiers (max 20).  Two forms:
                 "count"          — count rows per group (no target column needed)
                 "op:COLNAME"     — apply op ∈ {sum, mean, min, max} to COLNAME
                                    e.g. "sum:attr_quality_score"
               Unknown ops or columns produce HTTP 400.
    """

    filter:   str | None       = Field(default=None, max_length=1000)
    group_by: str              = Field(..., min_length=1, max_length=128)
    metrics:  list[str]        = Field(..., min_length=1, max_length=20)
```

**Metric-string grammar** (enforced in the handler via `_parse_metrics()`;
errors → HTTP 400 rather than 422):

```
metric  ::= "count"
          | op ":" column_name
op      ::= "sum" | "mean" | "min" | "max"
column_name ::= [a-zA-Z_][a-zA-Z0-9_]*   (max 128 chars)
```

Module-level constant: `_VALID_BINARY_OPS = frozenset({"sum", "mean", "min", "max"})`.

### 3.2 Response — `ChunkAggregateResponse`

```python
class ChunkAggregateResponse(BaseModel):
    """Response for POST /api/chunks/aggregate.

    groups — one dict per distinct value of group_by.  Each dict contains:
               - the group_by column key/value pair
               - one key per requested metric, named as follows:
                   "count"          metric → key "count"
                   "op:COLNAME"     metric → key "{op}_{colname}"
                                    e.g. "sum:attr_quality_score"
                                         → key "sum_attr_quality_score"
    Null-key groups: if rows have NULL in the group_by column, they form a
    separate group with key value null. The "count" metric correctly counts
    all rows in that group (using PyArrow's count_all).

    Example (group_by="attr_lang_code", metrics=["count"]):
      {"groups": [
        {"attr_lang_code": "zh", "count": 42},
        {"attr_lang_code": "en", "count": 17},
      ]}
    """

    groups: list[dict[str, Any]]
```

---

## §4 Implementation approach

### 4.1 Metric validation (before Lance I/O)

```python
_VALID_BINARY_OPS = frozenset({"sum", "mean", "min", "max"})

def _parse_metrics(metrics: list[str]) -> list[tuple[str, str | None]]:
    """Return list of (op, column_or_None) tuples.

    Raises LanceQueryError for any malformed or unknown op.
    """
    parsed = []
    for m in metrics:
        if m == "count":
            parsed.append(("count_all", None))
        elif ":" in m:
            op, col = m.split(":", 1)
            if op not in _VALID_BINARY_OPS:
                raise LanceQueryError(f"Unknown metric op: {op!r}")
            if not col:
                raise LanceQueryError(f"Metric {m!r}: column name is empty")
            parsed.append((op, col))
        else:
            raise LanceQueryError(f"Invalid metric specifier: {m!r}")
    return parsed
```

### 4.2 Column selection — `_build_columns()`

```python
def _build_columns(group_by: str, parsed_metrics: list[tuple[str, str | None]]) -> list[str]:
    """Build minimal column set to fetch from Lance."""
    columns: list[str] = [group_by]
    for op, col in parsed_metrics:
        if col is not None and col not in columns:
            columns.append(col)
    return columns
```

### 4.3 Lance fetch — inside `asyncio.to_thread()`

Uses `.where().select()` ordering (matching F-032 convention). R4 CLOSED:
lancedb 0.30.2 uses a fluent builder (`.select()` and `.where()` just assign
to instance variables; `.to_arrow()` reads them together). Either order works,
but we use `.where().select()` for consistency with F-032.

```python
def _execute() -> list[dict]:
    try:
        table = get_or_create_chunks_table()
        q = table.search()
        if body.filter:
            q = q.where(body.filter)
        q = q.select(columns_to_fetch)
        # No .limit() — we need ALL matching rows for correct GROUP BY.
        arrow_tbl = q.to_arrow()
    except Exception as exc:
        raise LanceQueryError(str(exc)) from exc

    # PyArrow aggregation (see §4.4)
    try:
        groups = _aggregate(arrow_tbl, body.group_by, parsed_metrics)
    except Exception as exc:
        raise LanceQueryError(f"Aggregation error: {exc}") from exc

    return groups
```

### 4.4 PyArrow aggregation — `_aggregate()` [HIGH-1 FIX APPLIED]

```python
def _aggregate(
    tbl: pa.Table,
    group_by: str,
    parsed_metrics: list[tuple[str, str | None]],
) -> list[dict]:
    """Group tbl by group_by and compute metrics using PyArrow.

    Uses count_all (not count) for row-count-per-group to correctly handle
    null-key groups.
    """
    agg_specs: list[tuple] = []
    rename_map: dict[str, str] = {}   # PyArrow output col → desired output key

    for op, col in parsed_metrics:
        if op == "count_all":
            # count_all counts ALL rows in each group regardless of nullity.
            # PyArrow syntax: ([], "count_all") → output column "count_all"
            agg_specs.append(([], "count_all"))
            rename_map["count_all"] = "count"
        else:
            agg_specs.append((col, op))
            rename_map[f"{col}_{op}"] = f"{op}_{col}"

    result = tbl.group_by(group_by).aggregate(agg_specs)

    # Rename PyArrow-generated column names to user-friendly output names.
    new_names = [rename_map.get(n, n) for n in result.column_names]
    result = result.rename_columns(new_names)

    return result.to_pylist()
```

### 4.5 Handler skeleton

```python
@router.post("/aggregate", response_model=ChunkAggregateResponse)
async def aggregate_chunks(
    body: ChunkAggregateRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkAggregateResponse:
    """Compute grouped statistics over the Lance chunks table.

    Auth required (F-008).  No per-user row scoping (§11.6 deferred).
    All matching rows (subject to filter) are loaded into process memory for
    grouping — callers should apply a filter to avoid full-table scans on
    large datasets.
    """
    try:
        parsed_metrics = _parse_metrics(body.metrics)
    except LanceQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Lance query error: {exc}") from exc

    columns_to_fetch = _build_columns(body.group_by, parsed_metrics)

    def _execute() -> list[dict]:
        ... # as shown in §4.3

    try:
        groups = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Lance query error: {exc}") from exc

    return ChunkAggregateResponse(groups=groups)
```

---

## §5 Verification plan

### 5.1 Mapping verification criteria to tests

| V-criterion | Covered by |
|---|---|
| **V1** POST with `filter="source_id = <id>"`, `group_by="attr_lang_code"`, `metrics=["count"]` → `[{"attr_lang_code": "zh", "count": 42}, ...]` | `test_aggregate_count_by_lang_code` (real pa.Table mock with 42 zh + 17 en rows; assert response groups match) |
| **V2** Sum of per-group counts equals `table.count_rows(filter=...)` | `test_aggregate_count_consistent_with_direct_count` (real pa.Table: 3 groups with counts 10+20+12=42; mock `count_rows(filter=...)` → 42; assert sum equals) |

### 5.2 Full test list — `apps/api/tests/test_chunks_aggregate.py`

| Test name | What it verifies |
|---|---|
| `test_aggregate_count_by_lang_code` | V1: filter + group_by "attr_lang_code" + metrics=["count"] → correct groups list shape and values |
| `test_aggregate_count_consistent_with_direct_count` | V2: sum of all group counts == `table.count_rows(filter=filter)` using same real pa.Table data |
| `test_aggregate_no_filter` | No filter field; real pa.Table returns all rows; groups computed correctly |
| `test_aggregate_empty_result` | Filter matches zero rows → `groups=[]` |
| `test_aggregate_numeric_metric` | metrics=["sum:attr_quality_score"] → groups contain `"sum_attr_quality_score"` key with correct value |
| `test_aggregate_multiple_metrics` | metrics=["count", "mean:attr_quality_score"] → groups contain both "count" and "mean_attr_quality_score" keys |
| `test_aggregate_no_token_returns_401` | Missing Authorization header → 401 |
| `test_aggregate_invalid_metric_returns_400` | metrics=["badop:col"] → 400 "Lance query error" |
| `test_aggregate_filter_too_long_returns_422` | filter of 1001 chars → 422 (Pydantic max_length) |
| `test_aggregate_lance_error_returns_400` | `get_or_create_chunks_table` raises Exception → 400 |

### 5.3 Mock pattern

Tests that verify aggregation correctness (V1, V2, no_filter, empty_result,
numeric_metric, multiple_metrics) use a **real `pa.Table`** (not a MagicMock)
as the Arrow result, so PyArrow's `.group_by().aggregate()` executes on real
data. The mock wraps the Lance query-builder layer:
- `mock_table.count_rows.return_value = N`
- `qb.where() → qb`, `qb.select() → qb`, `qb.to_arrow() → real_pa_table`

Tests for error paths (401, 400, 422) can use MagicMock since they don't reach
the aggregation code.

### 5.4 OpenAPI assertion (implementer runs post-codegen)

```bash
python3 -c "
import json
data = json.load(open('packages/api-types/openapi.json'))
assert '/api/chunks/aggregate' in data['paths'], 'Missing /api/chunks/aggregate'
assert 'ChunkAggregateRequest' in data['components']['schemas']
assert 'ChunkAggregateResponse' in data['components']['schemas']
print('openapi.json sync: OK')
"
```

### 5.5 Checks gate

`bash verify/checks.sh backend` must exit 0 with all new tests included.

---

## §6 Risks (updated per reviewer feedback)

| # | Risk | Severity | Resolution |
|---|---|---|---|
| R1 | **Full-table scan for large datasets** — no `.limit()` means all matching rows are pulled into process memory for grouping. | MEDIUM | Acceptable for MVP (document in docstring; recommend callers apply tight filters). Post-MVP: push GROUP BY to DuckDB/DataFusion SQL. |
| R2 | **Null-key groups** — rows with NULL in `group_by` column form a separate group. With `count_all`, the count is correct. `min`/`max` on string columns return lexicographic values. | LOW | Documented in response schema docstring. `count_all` handles null groups correctly. |
| R3 | **PyArrow column naming collision** — if `count_all` appears as a column name in Lance schema, rename_map key collides. | LOW | `count_all` is not a valid CHUNKS_SCHEMA column name. No action needed. |
| R4 | ~~`.select()` before `.where()` ordering~~ | **CLOSED** | lancedb 0.30.2 fluent builder: ordering irrelevant. Convention: use `.where().select()` to match F-032. |
| R5 | **PyArrow `mean` on integer columns** — silently upcasts to float. `min`/`max` on string columns returns lexicographic. | LOW | Documented in request schema docstring. |
| R6 | **`group_by` validation** — unknown column surfaces as PyArrow error → HTTP 400. Same lazy-validation as F-032. | LOW | Consistent with F-032; acceptable for MVP. |
