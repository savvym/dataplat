# S034-F-034 — Chunk Distribution Endpoint: agreed.md

Sprint ID: S034-F-034
Feature: F-034 `chunk_distribution_endpoint`
Status: AGREED
Dependencies: F-033 (chunk aggregate endpoint) ✓, F-008 (auth) ✓

Reviewer findings addressed:
- [M1] Added 2 Pydantic validation tests (filter_too_long → 422, bins_out_of_range → 422); test count → 13
- [M2] Changed `type: str` to `type: Literal["numeric", "categorical"]` in response schema
- [N3] Clarified `test_distribution_empty_table` makes 2 API calls (one numeric, one categorical)
- [N4] Added `qb.where.assert_not_called()` to categorical no-filter test
- [N5] Added note that `bins` is a minor extension beyond design doc's `{filter, column}` signature

OQ1 resolved: Silently ignore `bins` for categorical columns.
OQ2 resolved: Deferred `min`/`max`/`null_count` metadata to follow-up feature.

---

## §1 Scope

This sprint adds `POST /api/chunks/distribution` to the existing `chunks` router.
The endpoint accepts an optional DataFusion SQL filter, a target column name, and
an optional `bins` parameter. It returns a histogram of values for the named column.

The column's distribution type is detected **automatically** via PyArrow schema
introspection on the loaded Arrow table — no client-supplied type hint is required.

> **Note [N5]:** The `bins` parameter is a minor addition beyond the design doc's
> `{ filter, column }` signature (§9.1). It is purely additive with a safe default
> (10), backward-compatible, and required for practical usability of numeric histograms.

### 1.1 Numeric distribution

Triggered when the column's PyArrow type is floating-point or integer.
Produces an equal-width histogram with `bins` buckets (default 10, range 1–100).
Null values are excluded from binning entirely.

Response shape:
```json
{
  "column": "attr_quality_score",
  "type": "numeric",
  "buckets": [
    {"range": [0.0, 0.1], "count": 42},
    {"range": [0.1, 0.2], "count": 17},
    {"range": [0.2, 0.3], "count": 5}
  ]
}
```

Bucket semantics: half-open `[lower, upper)` on the right for all bins except
the last, which is closed `[lower, upper]`. This matches `numpy.histogram`
semantics exactly.

### 1.2 Categorical distribution

Triggered when the column's PyArrow type is string (`pa.utf8` / `pa.large_utf8`).
Produces one bucket per distinct value, ordered by count descending. Null values
appear as `{"value": null, "count": N}` if present.

Response shape:
```json
{
  "column": "attr_lang_code",
  "type": "categorical",
  "buckets": [
    {"value": "en", "count": 150},
    {"value": "zh", "count": 42},
    {"value": null,  "count": 3}
  ]
}
```

### 1.3 Edge cases

| Situation | Behavior |
|---|---|
| Filter matches 0 rows | `"buckets": []` — type is still detected from the Lance schema |
| Numeric column: all non-null values are identical | Single bin: `{"range": [v, v], "count": N}` |
| Numeric column: all values are null | `"buckets": []` |
| Categorical column: all values are null | `[{"value": null, "count": N}]` |
| Unknown column name | HTTP 400 — DataFusion rejects the column at scan time; error propagated via `LanceQueryError` |
| Unsupported column type (bool, list, timestamp, …) | HTTP 400 — `LanceQueryError("unsupported type …")` |
| Missing auth token | HTTP 401 |
| `bins` provided with a categorical column | Silently ignored (OQ1 resolved) |

The endpoint requires a valid bearer token and does **not** apply per-user row
scoping on the Lance side (ACL deferred to post-MVP, design doc §11.6).

---

## §2 Files changed

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/chunks.py` | **MODIFIED** — add `ChunkDistributionRequest` and `ChunkDistributionResponse` models; add `Literal` import |
| `apps/api/dataplat_api/routers/chunks.py` | **MODIFIED** — add `POST /api/chunks/distribution` handler + `_compute_numeric_distribution()` + `_compute_categorical_distribution()` helpers; add `import numpy as np` at module level |
| `apps/api/tests/test_chunks_distribution.py` | **NEW** — 13 unit tests; real `pa.Table` mock pattern (mirrors F-033 style) |
| `packages/api-types/openapi.json` | **MODIFIED** — regenerated via `make codegen` (hard invariant #6) |

No new router registration needed (the route is added to the existing `router`
object already mounted in `main.py`). No Postgres migration, no Alembic change.

`numpy` is used for equal-width binning (`numpy.histogram`). It is a transitive
dependency of PyArrow; however, the implementer must verify `import numpy` succeeds
in the uv environment and add `numpy` to `apps/api/pyproject.toml`
`[project.dependencies]` if it is not already listed explicitly.

---

## §3 Implementation details

### 3.1 Request schema — `ChunkDistributionRequest`

```python
class ChunkDistributionRequest(BaseModel):
    """Request body for POST /api/chunks/distribution.

    filter  — DataFusion SQL predicate fragment applied before computing the
              distribution (e.g. "source_id = 42").
              None / omitted means all rows.  Max 1000 chars.
    column  — Name of the Lance column to compute the distribution for.
              Must be a valid CHUNKS_SCHEMA column name; unknown names cause a
              400 (DataFusion parse error at scan time).
              Supported types: floating-point, integer, string (utf8/large_utf8).
              Unsupported types (bool, list, timestamp, …) cause a 400.
              NOTE: all integer columns (token_count, source_id,
              attr_minhash_cluster_id) are treated as numeric and binned as a
              histogram. Callers wanting categorical treatment of an integer
              column should use POST /api/chunks/aggregate with group_by instead.
    bins    — Number of equal-width histogram buckets for numeric columns
              (default 10, range 1–100).  Silently ignored for categorical
              columns.
    """

    filter: str | None = Field(default=None, max_length=1000)
    column: str        = Field(..., min_length=1, max_length=128)
    bins:   int        = Field(default=10, ge=1, le=100)
```

### 3.2 Response schema — `ChunkDistributionResponse`

```python
from typing import Any, Literal   # add Literal to existing import

class ChunkDistributionResponse(BaseModel):
    """Response for POST /api/chunks/distribution.

    column  — Echo of the requested column name.
    type    — "numeric" or "categorical", auto-detected from the PyArrow schema.
    buckets — List of bucket dicts; shape depends on type:
                Numeric:     {"range": [lower: float, upper: float], "count": int}
                Categorical: {"value": str | None, "count": int}
              Empty list when no non-null values exist (numeric) or 0 rows match
              the filter (both types).
    """

    column:  str
    type:    Literal["numeric", "categorical"]   # [M2] — not plain str
    buckets: list[dict[str, Any]]
```

### 3.3 Column type detection

After fetching the Arrow table, introspect the PyArrow field type:

```python
col_type = arrow_tbl.schema.field(body.column).type

if pa.types.is_floating(col_type) or pa.types.is_integer(col_type):
    dist_type = "numeric"
elif pa.types.is_string(col_type) or pa.types.is_large_string(col_type):
    dist_type = "categorical"
else:
    raise LanceQueryError(
        f"Column {body.column!r} has unsupported type {col_type!r} for "
        f"distribution; supported: floating-point, integer, string"
    )
```

**When the column does not exist:** DataFusion rejects the column name at
`.to_arrow()` time and raises an exception. This is caught by the outer
`except Exception as exc: raise LanceQueryError(str(exc)) from exc` block,
producing HTTP 400. The `schema.field()` call is never reached.

**When the result set is empty (0 rows):** `arrow_tbl.schema` still reflects the
full Lance schema, so type detection succeeds and the handler correctly returns
`{"buckets": []}` rather than erroring.

### 3.4 Numeric helper — `_compute_numeric_distribution()`

```python
def _compute_numeric_distribution(
    col_array: pa.ChunkedArray,
    bins: int,
) -> list[dict]:
    """Compute equal-width histogram buckets for a numeric column.

    Returns [] if the column contains no non-null values.
    Returns a single bucket [val, val] if all values are identical.
    Null values are excluded before binning.
    """
    valid = col_array.drop_null()
    if len(valid) == 0:
        return []

    values = np.array(valid.to_pylist(), dtype=np.float64)
    col_min = float(values.min())
    col_max = float(values.max())

    # numpy.histogram raises ValueError when range=(v, v); handle separately.
    if col_min == col_max:
        return [{"range": [col_min, col_max], "count": int(len(values))}]

    counts, edges = np.histogram(values, bins=bins)
    return [
        {"range": [float(edges[i]), float(edges[i + 1])], "count": int(counts[i])}
        for i in range(len(counts))
    ]
```

### 3.5 Categorical helper — `_compute_categorical_distribution()`

```python
def _compute_categorical_distribution(
    tbl: pa.Table,
    column: str,
) -> list[dict]:
    """Compute value counts for a categorical (string) column.

    Returns [] if tbl has 0 rows.
    Null values form their own bucket: {"value": null, "count": N}.
    Results are sorted by count descending.
    """
    if len(tbl) == 0:
        return []

    result = tbl.group_by(column).aggregate([([], "count_all")])
    # Rename PyArrow output column "count_all" → "count".
    new_names = ["count" if n == "count_all" else n for n in result.column_names]
    result = result.rename_columns(new_names)
    result = result.sort_by([("count", "descending")])

    rows = result.to_pylist()
    return [{"value": row[column], "count": row["count"]} for row in rows]
```

### 3.6 Handler structure

```python
@router.post("/distribution", response_model=ChunkDistributionResponse)
async def distribution_chunks(
    body: ChunkDistributionRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkDistributionResponse:
    """Compute a value distribution histogram for one column.

    Auth required (F-008).  No per-user row scoping (§11.6 deferred).

    Column type is auto-detected from the PyArrow schema:
      - Floating-point / integer → numeric equal-width histogram (bins buckets).
      - String (utf8/large_utf8) → categorical value counts, count descending.
      - Other types (bool, list, timestamp, …) → HTTP 400.

    All matching rows for the target column are loaded into process memory —
    callers should apply a filter to avoid full-table scans on large datasets.
    Post-MVP: push aggregation to DuckDB/DataFusion SQL.
    """

    def _execute() -> tuple[str, list[dict]]:
        """Synchronous Lance I/O + distribution computation, via asyncio.to_thread()."""
        try:
            table = get_or_create_chunks_table()
            q = table.search()
            if body.filter:
                q = q.where(body.filter)
            q = q.select([body.column])
            # No .limit() — all matching rows are needed for the histogram.
            arrow_tbl = q.to_arrow()
        except Exception as exc:
            raise LanceQueryError(str(exc)) from exc

        try:
            col_type = arrow_tbl.schema.field(body.column).type
            if pa.types.is_floating(col_type) or pa.types.is_integer(col_type):
                dist_type = "numeric"
                buckets = _compute_numeric_distribution(
                    arrow_tbl.column(body.column), body.bins
                )
            elif pa.types.is_string(col_type) or pa.types.is_large_string(col_type):
                dist_type = "categorical"
                buckets = _compute_categorical_distribution(arrow_tbl, body.column)
            else:
                raise LanceQueryError(
                    f"Column {body.column!r} has unsupported type {col_type!r}; "
                    f"supported: floating-point, integer, string"
                )
        except LanceQueryError:
            raise  # re-raise without wrapping
        except Exception as exc:
            raise LanceQueryError(f"Distribution error: {exc}") from exc

        return dist_type, buckets

    try:
        dist_type, buckets = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    return ChunkDistributionResponse(
        column=body.column,
        type=dist_type,
        buckets=buckets,
    )
```

New import at module level in `routers/chunks.py` (after existing `import pyarrow as pa`):

```python
import numpy as np
```

New schema symbols to add to the import block in `routers/chunks.py`:

```python
from dataplat_api.schemas.chunks import (
    ChunkAggregateRequest,
    ChunkAggregateResponse,
    ChunkDistributionRequest,   # NEW
    ChunkDistributionResponse,  # NEW
    ChunkQueryRequest,
    ChunkQueryResponse,
    ChunkRead,
)
```

---

## §4 Verification plan

### 4.1 Mapping feature verification criteria to tests

| V-criterion (from feature_list.json) | Covered by |
|---|---|
| **V1** `POST /api/chunks/distribution {"filter": "source_id=<id>", "column": "attr_quality_score"}` → `{"buckets": [{"range": [0,0.1], "count": ...}, ...], "column": "attr_quality_score"}` | `test_distribution_numeric_with_filter` — real `pa.Table` of 100 quality score floats in `[0.0, 1.0)`; filter applied; asserts `type=="numeric"`, `column=="attr_quality_score"`, 10 buckets, each bucket has `range` (list of 2 floats) and `count` (int), counts sum to total rows |
| **V2** `POST /api/chunks/distribution {"column": "attr_lang_code"}` → categorical counts | `test_distribution_categorical_no_filter` — real `pa.Table` with "en" × 150, "zh" × 42, null × 3; asserts `type=="categorical"`, `column=="attr_lang_code"`, buckets have `value`/`count` keys, "en" bucket has `count==150`; also asserts `qb.where.assert_not_called()` [N4] |

### 4.2 Full test list — `apps/api/tests/test_chunks_distribution.py` (13 tests)

| Test name | What it verifies |
|---|---|
| `test_distribution_numeric_with_filter` | V1: float column + filter → 10 buckets; `type="numeric"`; `column` echoed; each bucket has `range` (2-float list) and `count` (int); counts sum to row total |
| `test_distribution_categorical_no_filter` | V2: string column, no filter → `type="categorical"`; buckets have `value`/`count` keys; known value counts present; ordered by count descending; `qb.where.assert_not_called()` [N4] |
| `test_distribution_numeric_default_bins` | No `bins` in request → exactly 10 buckets; all bucket counts sum to non-null row count |
| `test_distribution_numeric_custom_bins` | `bins=5` → exactly 5 buckets returned |
| `test_distribution_numeric_all_null` | Column of all-null floats → `{"type": "numeric", "buckets": []}` |
| `test_distribution_numeric_all_same_value` | All rows have identical float value → single bucket `{"range": [v, v], "count": N}` |
| `test_distribution_categorical_with_null_value` | String column containing null rows → bucket `{"value": null, "count": N}` is present |
| `test_distribution_empty_table` | Lance returns 0-row table → makes 2 API calls (one with a float-typed column, one with a string-typed column against the same 0-row `pa.Table`) and asserts `buckets == []` for both [N3] |
| `test_distribution_invalid_column_returns_400` | `get_or_create_chunks_table` raises on unknown column → HTTP 400, detail contains "Lance query error" |
| `test_distribution_unsupported_type_returns_400` | Real Arrow table with a bool-typed column → HTTP 400, detail contains "unsupported type" |
| `test_distribution_no_token_returns_401` | No Authorization header → HTTP 401 |
| `test_distribution_filter_too_long_returns_422` | [M1] filter of 1001 chars → HTTP 422 (Pydantic validation) |
| `test_distribution_bins_out_of_range_returns_422` | [M1] bins=0 and bins=101 → HTTP 422 (Pydantic validation) |

### 4.3 Mock pattern (mirrors F-033 `test_chunks_aggregate.py`)

Correctness tests use a **real `pa.Table`** — not a `MagicMock` — so that
PyArrow type introspection, `drop_null()`, `group_by().aggregate()`, and
`sort_by()` execute on real data, and `numpy.histogram` runs on real values.

The Lance query-builder chain is mocked at the transport layer only:

```python
def _make_dist_mock_table(real_pa_table: pa.Table) -> MagicMock:
    """Mock Lance table whose query builder returns a real pa.Table at .to_arrow()."""
    mock_table = MagicMock()
    qb = MagicMock()
    qb.where.return_value  = qb
    qb.select.return_value = qb
    qb.to_arrow.return_value = real_pa_table
    mock_table.search.return_value = qb
    return mock_table
```

Patch target: `"dataplat_api.routers.chunks.get_or_create_chunks_table"`.

Auth override (same as F-032, F-033 tests):
```python
app.dependency_overrides[get_current_user] = _override_current_user
```

### 4.4 OpenAPI assertion (implementer runs post-`make codegen`)

```bash
python3 -c "
import json
data = json.load(open('packages/api-types/openapi.json'))
assert '/api/chunks/distribution' in data['paths'], 'Missing /api/chunks/distribution'
assert 'ChunkDistributionRequest'  in data['components']['schemas']
assert 'ChunkDistributionResponse' in data['components']['schemas']
print('openapi.json sync: OK')
"
```

### 4.5 Checks gate

`bash verify/checks.sh backend` must exit 0 with all 13 new tests included in the suite total.

---

## §5 Risks / Open questions

| # | Risk / Question | Severity | Resolution |
|---|---|---|---|
| R1 | **Full-table scan** — no `.limit()` on the Lance query means all matching rows for the target column are pulled into process memory before histogram computation. | MEDIUM | Acceptable for MVP (same trade-off as F-033 R1). Documented in handler docstring. Callers should apply a tight `filter`. Post-MVP: push histogram to DuckDB/DataFusion SQL. |
| R2 | **Integer columns as numeric vs. categorical** — `source_id` and `attr_minhash_cluster_id` are integers but conceptually behave as IDs/categories. All integers are treated as numeric (histogram). | LOW | Documented in `ChunkDistributionRequest.column` docstring. Callers needing categorical treatment should use `POST /api/chunks/aggregate`. |
| R3 | **numpy explicit dependency** — `numpy.histogram` is used for binning. numpy is a transitive PyArrow dependency but may not be listed explicitly. | LOW | Implementer must run `python3 -c "import numpy"` in the uv venv; if missing, add `"numpy>=1.24"` to `apps/api/pyproject.toml`. |
| R4 | **Floating-point edge precision** — `numpy.histogram` uses `float64` edges; large or very small float values may exhibit rounding in JSON output. | LOW | Acceptable for MVP visual histogram. |
| R5 | **Categorical sort stability** — ties break in arbitrary order. | LOW | Acceptable for MVP. |
| OQ1 | **`bins` with categorical column** | RESOLVED | Silently ignored. |
| OQ2 | **`min`/`max`/`null_count` metadata** | RESOLVED | Deferred to follow-up feature. |
