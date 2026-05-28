# S033-F-033 — Chunk Aggregate Endpoint: proposed.md

Sprint ID: S033-F-033
Feature: F-033 `chunk_aggregate_endpoint`
Status: PROPOSED
Dependencies: F-032 (chunk query endpoint) ✓, F-008 (auth) ✓

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
| `apps/api/dataplat_api/schemas/chunks.py` | **MODIFIED** — add `ChunkAggregateRequest` and `ChunkAggregateResponse` |
| `apps/api/dataplat_api/routers/chunks.py` | **MODIFIED** — add `POST /api/chunks/aggregate` handler; `LanceQueryError` already defined there |
| `apps/api/tests/test_chunks_aggregate.py` | **NEW** — 10 unit tests, mock Lance table pattern (mirrors F-032 test style) |
| `packages/api-types/openapi.json` | **MODIFIED** — regenerated via manual script; same command as F-032 agreed.md §D9 |

No new router registration needed (the route is added to the existing `router`
object already mounted in `main.py`). No Postgres migration, no Alembic change,
no new dependency.

---

## §3 Schema design

### 3.1 Request — `ChunkAggregateRequest`

```python
class ChunkAggregateRequest(BaseModel):
    """Request body for POST /api/chunks/aggregate.

    filter   — DataFusion SQL predicate fragment applied to the Lance chunks
               table before grouping (e.g. "source_id = 42").
               None / omitted means group over all rows.  Max 1000 chars.
    group_by — Name of a single Lance column to group by (e.g. "attr_lang_code",
               "producer_asset").  Must be a valid CHUNKS_SCHEMA column name;
               unknown names cause a 400 at PyArrow grouping time.
    metrics  — Non-empty list of metric specifiers.  Two forms are accepted:
                 "count"          — count rows per group (no target column needed)
                 "op:COLNAME"     — apply op ∈ {sum, mean, min, max} to COLNAME
                                    e.g. "sum:attr_quality_score"
               Unknown ops or columns produce HTTP 400.
    """

    filter:   str | None       = Field(default=None, max_length=1000)
    group_by: str              = Field(..., min_length=1, max_length=128)
    metrics:  list[str]        = Field(..., min_length=1)
```

**Metric-string grammar** (enforced in the handler, not Pydantic, so errors →
HTTP 400 rather than 422):

```
metric  ::= "count"
          | op ":" column_name
op      ::= "sum" | "mean" | "min" | "max"
column_name ::= [a-zA-Z_][a-zA-Z0-9_]*   (max 128 chars)
```

The set of valid `op` values is a module-level constant:
`_VALID_OPS = frozenset({"count", "sum", "mean", "min", "max"})`.

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

    Example (group_by="attr_lang_code", metrics=["count"]):
      [
        {"attr_lang_code": "zh", "count": 42},
        {"attr_lang_code": "en", "count": 17},
      ]
    """

    groups: list[dict[str, Any]]
```

Rationale for the wrapper model (rather than a bare JSON array): consistent with
the F-032 `ChunkQueryResponse` wrapper pattern; extensible (e.g., a future
`total_groups` field can be added without breaking callers that just read
`groups`).

---

## §4 Implementation approach

### 4.1 Metric validation (before Lance I/O)

Parse each metric string before touching Lance so callers get fast feedback:

```python
def _parse_metrics(metrics: list[str]) -> list[tuple[str, str | None]]:
    """Return list of (op, column_or_None) tuples.

    Raises LanceQueryError for any malformed or unknown op.
    """
    parsed = []
    for m in metrics:
        if m == "count":
            parsed.append(("count", None))
        elif ":" in m:
            op, col = m.split(":", 1)
            if op not in _VALID_OPS or op == "count":
                raise LanceQueryError(f"Unknown metric op: {op!r}")
            if not col:
                raise LanceQueryError(f"Metric {m!r}: column name is empty")
            parsed.append((op, col))
        else:
            raise LanceQueryError(f"Invalid metric specifier: {m!r}")
    return parsed
```

### 4.2 Column selection

Build the minimal set of columns to fetch from Lance:

```python
columns_to_fetch: list[str] = [body.group_by]
for op, col in parsed_metrics:
    if col is not None and col not in columns_to_fetch:
        columns_to_fetch.append(col)
```

Fetching only the required columns avoids pulling 24-column rows (especially the
1024-float `attr_embed_vector`) just to compute a count.

### 4.3 Lance fetch — inside `asyncio.to_thread()`

Everything from table-open through PyArrow aggregation runs in a single
`_execute()` closure dispatched via `asyncio.to_thread()`, matching the F-032
pattern exactly:

```python
def _execute() -> list[dict]:
    try:
        table = get_or_create_chunks_table()
        q = table.search().select(columns_to_fetch)
        if body.filter:
            q = q.where(body.filter)
        # No .limit() — we need ALL matching rows for a correct GROUP BY.
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

### 4.4 PyArrow aggregation — `_aggregate()`

```python
def _aggregate(
    tbl: pa.Table,
    group_by: str,
    parsed_metrics: list[tuple[str, str | None]],
) -> list[dict]:
    """Group tbl by group_by and compute metrics using PyArrow."""
    agg_specs: list[tuple[str, str]] = []
    rename_map: dict[str, str] = {}   # PyArrow output col → desired output key

    for op, col in parsed_metrics:
        if op == "count":
            # Count non-null occurrences of the group_by column itself.
            # Since group_by is the key, every row in a group has a non-null
            # key value, so this equals the row count per group.
            agg_specs.append((group_by, "count"))
            rename_map[f"{group_by}_count"] = "count"
        else:
            agg_specs.append((col, op))
            rename_map[f"{col}_{op}"] = f"{op}_{col}"

    result = tbl.group_by(group_by).aggregate(agg_specs)

    # Rename PyArrow-generated column names to user-friendly output names.
    new_names = [rename_map.get(n, n) for n in result.column_names]
    result = result.rename_columns(new_names)

    return result.to_pylist()
```

**PyArrow column naming convention** (verified against PyArrow 14.x / 24.x):
`table.group_by(["col"]).aggregate([("col", "count")])` produces a column named
`"col_count"`. This is the source of the `rename_map` entries above.

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

    def _execute() -> list[dict]: ...  # see §4.3

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
| **V1** POST with `filter="source_id = <id>"`, `group_by="attr_lang_code"`, `metrics=["count"]` → `[{"attr_lang_code": "zh", "count": 42}, ...]` | `test_aggregate_count_by_lang_code` (mock returns 42 zh + 17 en rows; assert response groups match) |
| **V2** Sum of per-group counts equals `table.count_rows(filter=...)` | `test_aggregate_count_consistent_with_direct_count` (mock: 3 groups with counts 10+20+12=42; also call `count_rows(filter=...)` → 42; assert sum equals) |

### 5.2 Full test list — `apps/api/tests/test_chunks_aggregate.py`

| Test name | What it verifies |
|---|---|
| `test_aggregate_count_by_lang_code` | V1: filter + group_by "attr_lang_code" + metrics=["count"] → correct groups list shape and values |
| `test_aggregate_count_consistent_with_direct_count` | V2: sum of all group counts == `table.count_rows(filter=filter)` using same mock data |
| `test_aggregate_no_filter` | No filter field; mock returns all rows; groups computed correctly |
| `test_aggregate_empty_result` | Filter matches zero rows → `groups=[]` |
| `test_aggregate_numeric_metric` | metrics=["sum:attr_quality_score"] → groups contain `"sum_attr_quality_score"` key with correct value |
| `test_aggregate_multiple_metrics` | metrics=["count", "mean:attr_quality_score"] → groups contain both "count" and "mean_attr_quality_score" keys |
| `test_aggregate_no_token_returns_401` | Missing Authorization header → 401 |
| `test_aggregate_invalid_metric_returns_400` | metrics=["badop:col"] → 400 "Lance query error" |
| `test_aggregate_filter_too_long_returns_422` | filter of 1001 chars → 422 (Pydantic max_length) |
| `test_aggregate_lance_error_returns_400` | `get_or_create_chunks_table` raises Exception → 400 |

### 5.3 Mock pattern

All tests follow the F-032 `_make_mock_table` pattern. The aggregate endpoint
calls `.search().select(cols).where(filter).to_arrow()` (no `.limit()`/`.offset()`),
so the mock's `qb` chain must support `.select()` before `.where()`. The test
helper `_make_mock_aggregate_table(rows, count_rows_return)` will:
- Set `mock_table.count_rows.return_value = count_rows_return`
- Wire the query builder chain: `search() → qb`, `qb.select() → qb`,
  `qb.where() → qb`, `qb.to_arrow() → arrow_result`
- Set `arrow_result.to_pylist.return_value = rows`
- **Crucially**: for V2, `arrow_result` is a real `pa.Table` built from `rows`
  (not a MagicMock), so PyArrow's `.group_by().aggregate()` can execute on
  real data and the test can assert the math.

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

`bash verify/checks.sh backend` must exit 0 with the new tests included.

---

## §6 Risks / open questions

| # | Risk | Severity | Mitigation / Decision needed |
|---|---|---|---|
| R1 | **Full-table scan for large datasets** — no `.limit()` means all matching rows are pulled into process memory for grouping. A filter of `"source_id = 42"` may match millions of rows. | MEDIUM | Acceptable for MVP (document in docstring; recommend callers apply tight filters). Post-MVP: push GROUP BY to DuckDB/DataFusion SQL. |
| R2 | **Null values in `group_by` column** — PyArrow's `group_by` by default drops rows with null key values (they are excluded from all groups). A `count` metric therefore undercounts if any rows have `NULL` in the group-by column. | LOW | Document in response schema docstring. Does not affect V-criteria tests (mock data is non-null). |
| R3 | **PyArrow `_count` column naming** — PyArrow's output column name for `(col, "count")` is `"{col}_count"`. If the group_by column name itself contains `_count` as a suffix (e.g. `"some_count"`), renaming produces `"some_count_count"` → renamed to `"count"`, which is fine. However, if a user requests both `"count"` and `"sum:count"` on a column named `count`, the rename map has a collision. | LOW | `count` is not a valid CHUNKS_SCHEMA column name, so this collision cannot occur with real data. Document as unsupported edge case. |
| R4 | **lancedb `.select()` before `.where()` ordering** — The F-032 query endpoint calls `.where()` before `.select()`. The aggregate endpoint reverses this (`.select()` first to minimize fetched columns, then `.where()`). Need to confirm lancedb 0.30.2's query builder accepts this order. | MEDIUM | **Open question for reviewer**: confirm `.search().select(cols).where(filter).to_arrow()` is valid in lancedb 0.30.2. If not, implementer should apply `.where()` first, then `.select()`. |
| R5 | **PyArrow `mean` on integer columns** — PyArrow silently upcasts to float for `mean`; this is correct behavior. `min`/`max` on string columns return the lexicographic min/max, which may surprise callers. | LOW | Document in request schema docstring. No code change needed. |
| R6 | **`group_by` validation** — unknown column name is not caught by Pydantic (it's just a `str`); it surfaces as a PyArrow error (column not found) inside `_execute()` → wrapped in `LanceQueryError` → HTTP 400. This is the same lazy-validation approach used in F-032 for `columns`. | LOW | Consistent with F-032 pattern; acceptable for MVP. |
| R7 | **OpenAPI codegen** — same manual regeneration script as F-032 (no Makefile). | LOW | Same D9 procedure; implementer runs and commits in same commit. |
