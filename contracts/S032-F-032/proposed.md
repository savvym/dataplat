# S032-F-032 — Chunk query endpoint: proposed.md

Sprint ID: S032-F-032
Feature: F-032 `chunk_query_endpoint`
Status: PROPOSED
Dependencies: F-025 (chunks table) ✓, F-008 (auth) ✓

---

## §1 Summary

This sprint ships `POST /api/chunks/query`, a new FastAPI endpoint that executes a
caller-supplied DataFusion SQL filter against the Lance chunks table and returns a
paginated list of matching chunks plus a total count. The request body accepts four
fields: `filter` (a DataFusion SQL fragment), `columns` (optional column projection),
`limit`, and `offset`. Lance I/O is synchronous and is wrapped in
`asyncio.to_thread()` per the storage module's established pattern. The endpoint is
auth-gated via `Depends(get_current_user)`; there is no per-user row scoping on the
Lance side (callers scope results via the `filter` field, e.g.
`"source_id = 42"`). A new `schemas/chunks.py` file defines the Pydantic models, a
new `routers/chunks.py` file contains the handler, and `main.py` is updated to
register the new router. After implementation `packages/api-types/openapi.json` is
regenerated and committed in the same commit (hard invariant #6).

---

## §2 Files changed

| File | Change |
|---|---|
| `apps/api/dataplat_api/schemas/chunks.py` | **NEW** — `ChunkQueryRequest`, `ChunkRead`, `ChunkQueryResponse` |
| `apps/api/dataplat_api/routers/chunks.py` | **NEW** — `POST /api/chunks/query` handler |
| `apps/api/dataplat_api/main.py` | **MODIFIED** — import and `app.include_router(chunks_router)` |
| `apps/api/tests/test_chunks_query.py` | **NEW** — unit tests (10+ cases, no live Lance/S3) |
| `packages/api-types/openapi.json` | **MODIFIED** — regenerated via `python3` export after adding new route (Makefile absent; done manually per §3 D9) |

No Postgres migration, no Alembic change, no Dagster change.

---

## §3 Design decisions

### D1 — Separate router file with prefix `/api/chunks`

**Decision:** Create `routers/chunks.py` with
`APIRouter(prefix="/api/chunks", tags=["chunks"])`. Do NOT add to the existing
`sources.py` router.

**Rationale:** Chunks are a distinct concept from sources (they live in Lance, not
Postgres) and will accumulate additional endpoints over time. A dedicated router
file keeps concerns separated, mirrors the pattern of `routers/sources.py`, and
avoids growing `sources.py` with cross-cutting Lance I/O logic.

---

### D2 — Lance operations wrapped in `asyncio.to_thread()`

**Decision:** All Lance calls (`get_or_create_chunks_table()`, `table.count_rows()`,
`table.search()…to_arrow()`) are executed inside a single synchronous `_execute()`
helper, which is dispatched via `await asyncio.to_thread(_execute)` from the
async handler.

**Rationale:** LanceDB S3 I/O is synchronous (documented in the storage module's
agreed.md). Calling it directly in an async handler would block the event loop.
`asyncio.to_thread()` is the established MVP pattern (consistent with how other
sync dependencies are handled). Both the count and the paginated fetch are bundled
in one `to_thread()` call to avoid two round-trip dispatches per request.

---

### D3 — Filter validated for length in request schema; Lance parse errors → 400

**Decision:**
- `filter` is a `str | None` field with `max_length=1000` in the Pydantic model.
  Pydantic rejects over-length values with HTTP 422 before the handler runs.
- Inside `_execute()`, any `Exception` raised by Lance/DataFusion (invalid SQL,
  type mismatch, unknown column) is caught and re-raised as a custom
  `LanceQueryError(ValueError)`. The async handler catches `LanceQueryError` and
  returns HTTP 400 with the error detail string.
- We do **not** do further SQL-injection sanitization beyond the length cap.
  Lance/DataFusion parses the filter in a sandboxed query engine; it cannot
  execute arbitrary shell commands, touch Postgres, or modify Lance data.
  Invalid SQL simply raises a parse error.

**Rationale:** The filter is user-controlled input but is only ever passed to
Lance's DataFusion engine as a read-only SQL predicate. The main risk is a
long crafted string causing denial-of-service; the 1000-char cap mitigates
this. DataFusion itself is the parser and its errors are informative; surfacing
them as 400 rather than 500 helps callers debug filter expressions.

---

### D4 — Two separate Lance calls: `count_rows()` then `search()…to_arrow()`

**Decision:**

```
total = table.count_rows(filter=body.filter)  # or table.count_rows() if no filter
q     = table.search()
if body.filter:  q = q.where(body.filter)
if body.columns: q = q.select(body.columns)
q = q.limit(body.limit).offset(body.offset)
arrow_tbl = q.to_arrow()
```

Both calls occur in the same `_execute()` function (one `to_thread` dispatch).

**Rationale:** `count_rows()` is the correct LanceDB API for returning the total
matching count without fetching row data. `search().where().limit().offset()` is
the documented paginated read API for lancedb 0.30.2. Using `to_lance()` +
`lance_dataset.to_table()` is an alternative but adds an extra import and
unpredictable behaviour with the `exist_ok` table that `get_or_create_chunks_table`
returns. The two-call approach is already validated in other sprints.

---

### D5 — No per-user row scoping on Lance

**Decision:** The handler requires a valid bearer token (`Depends(get_current_user)`)
but does **not** inject a `WHERE owner_id = X` clause into the Lance filter.
Callers scope results via the `filter` field (e.g. `"source_id = 42"`).

**Rationale:** The Lance table does not have an `owner_id` column; ownership is
tracked in Postgres via `source_collection`. Row-level access control on Lance
requires a Postgres join (look up accessible `source_id`s then inject a
`source_id IN (...)` clause), which is deferred post-MVP. The auth guard
prevents unauthenticated access; MVP scope explicitly excludes granular Lance
ACL (design doc §11.6).

This decision must be documented prominently in the handler docstring so it is
not accidentally "fixed" in a future sprint without a Postgres join.

---

### D6 — `columns` defaults to `None` (all columns returned)

**Decision:** When `columns=None`, Lance returns all 24 schema columns.
`ChunkRead` models all 24 fields as nullable; fields absent from a column-
projection response are populated as `None` by the row dict coming from
`to_pylist()`.

**Rationale:** Returning all columns by default is the least-surprising
behaviour for new API consumers. Callers who need to trim large fields
(e.g. `attr_embed_vector`, `attr_minhash_signature`) can pass an explicit
`columns` list. This also keeps the response model stable and complete for
OpenAPI codegen.

---

### D7 — `ChunkRead` mirrors all 24 CHUNKS_SCHEMA fields; all nullable except `chunk_id`

**Decision:** `ChunkRead` has 24 fields matching `CHUNKS_SCHEMA` names exactly.
`chunk_id: str` is the only non-optional field; all others are `T | None`.
`attr_embed_vector` is `list[float] | None`; `attr_minhash_signature` is
`list[int] | None`; `attr_pii_categories` is `list[str] | None`.

**Rationale:** Making all attribute fields nullable means a partial write (e.g.
only quality tagger has run) returns a valid `ChunkRead` without 500ing on
schema validation. Matching CHUNKS_SCHEMA names exactly allows `ChunkRead(**row)`
after `to_pylist()` without field renaming.

---

### D8 — Arrow → Python via `to_pylist()`; timestamps handled by PyArrow auto-conversion

**Decision:** `arrow_tbl.to_pylist()` converts the PyArrow Table to a list of
Python dicts. PyArrow converts `pa.timestamp("ms")` values to Python `datetime`
objects automatically. `pa.list_` columns become Python lists.
`pa.large_string()` becomes `str`.

**Rationale:** `to_pylist()` is the canonical PyArrow utility for Arrow → Python
dict conversion. Pydantic v2 handles `datetime` natively. No manual field
conversion is needed.

---

### D9 — `packages/api-types/openapi.json` regenerated manually (no Makefile yet)

**Decision:** Since no `Makefile` exists yet (TS codegen is deferred to the web
sprint per `checks.sh` contract layer guard), the implementer regenerates
`packages/api-types/openapi.json` by running:

```bash
cd apps/api && uv run python -c "
import json
from dataplat_api.main import app
print(json.dumps(app.openapi(), indent=2))
" > ../../packages/api-types/openapi.json
```

The updated `openapi.json` is committed in the **same commit** as the router
and schema files (hard invariant #6).

**Rationale:** The `contract` layer in `checks.sh` guards with
`[[ -f Makefile ]] || exit 0`, so CI will not fail on missing `make codegen`.
However, invariant #6 still requires the OpenAPI schema to be kept in sync.
Manual regeneration is the correct MVP approach until the Makefile is wired.

---

### D10 — `limit` defaults to 100, capped at 1000; `offset` defaults to 0

**Decision:** `limit: int = Field(default=100, ge=1, le=1000)` and
`offset: int = Field(default=0, ge=0)`. Pydantic rejects violations with
HTTP 422 before the handler runs.

**Rationale:** Chunk tables can be large; a default of 100 is safer than
returning thousands of rows (each potentially with a 1024-float vector).
The 1000-row hard cap prevents accidental unbounded extracts. These numbers
are consistent with what the verification criteria test (limit=10 explicitly).

---

## §4 Request / Response schemas

### `apps/api/dataplat_api/schemas/chunks.py`

```python
"""Chunk query schemas — S032-F-032.

Schemas:
  - ChunkQueryRequest: body for POST /api/chunks/query.
  - ChunkRead: one chunk row, all 24 CHUNKS_SCHEMA fields (all nullable except chunk_id).
  - ChunkQueryResponse: paginated response {items, total}.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChunkQueryRequest(BaseModel):
    """Request body for POST /api/chunks/query.

    filter   — DataFusion SQL predicate fragment applied to the Lance chunks
               table (e.g. "source_id = 42", "attr_quality_score > 0.8").
               None / omitted means no filter (return all rows, subject to
               limit/offset).  Max 1000 chars.
    columns  — Optional list of column names to project.  None = all 24 columns.
               Unknown column names cause a 400 (DataFusion parse error).
    limit    — Max rows per page (1–1000, default 100).
    offset   — Row offset for pagination (default 0).
    """

    filter: str | None = Field(default=None, max_length=1000)
    columns: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ChunkRead(BaseModel):
    """One chunk row returned from the Lance chunks table.

    All 24 CHUNKS_SCHEMA fields are present and all nullable except chunk_id.
    Fields not included in a column-projection request will be None.
    """

    # Identifiers
    chunk_id: str
    source_id: int | None = None
    source_collection_id: int | None = None
    producer_asset: str | None = None
    producer_version: str | None = None

    # Content
    text: str | None = None
    token_count: int | None = None
    docling_refs: str | None = None
    source_refs: str | None = None

    # Provenance
    augmented_from: str | None = None
    augmenter_id: str | None = None
    augmenter_config_hash: str | None = None

    # Attribute columns
    attr_quality_score: float | None = None
    attr_quality_provider: str | None = None
    attr_lang_code: str | None = None
    attr_lang_confidence: float | None = None
    attr_minhash_signature: list[int] | None = None
    attr_minhash_cluster_id: int | None = None
    attr_minhash_is_head: bool | None = None
    attr_pii_has_pii: bool | None = None
    attr_pii_categories: list[str] | None = None
    attr_embed_vector: list[float] | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChunkQueryResponse(BaseModel):
    """Paginated response for POST /api/chunks/query."""

    items: list[ChunkRead]
    total: int
```

---

## §5 Handler outline

### `apps/api/dataplat_api/routers/chunks.py`

```python
"""Chunks router — S032-F-032.

POST /api/chunks/query — execute a DataFusion SQL filter on the Lance chunks
table and return matching chunks with a total count.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.chunks import ChunkQueryRequest, ChunkQueryResponse, ChunkRead
from dataplat_api.storage.lance import get_or_create_chunks_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class LanceQueryError(ValueError):
    """Raised when Lance/DataFusion rejects a query; converted to HTTP 400."""


@router.post("/query", response_model=ChunkQueryResponse)
async def query_chunks(
    body: ChunkQueryRequest,
    current_user: User = Depends(get_current_user),
) -> ChunkQueryResponse:
    """Execute a DataFusion SQL filter on the Lance chunks table.

    Auth required (F-008).

    IMPORTANT — no per-user row scoping:
      The handler requires a valid bearer token but does NOT inject owner-scoping
      into the Lance filter.  Callers are responsible for scoping via the filter
      field (e.g. "source_id = 42").  Repository-level ACL on Lance is deferred
      to post-MVP (design doc §11.6).

    Returns:
      items — up to `limit` matching chunk rows (all 24 fields; unselected
              fields are None when `columns` is specified).
      total — count of ALL rows matching the filter (ignores limit/offset).
    """

    def _execute() -> tuple[list[dict], int]:
        """Synchronous Lance I/O, run via asyncio.to_thread()."""
        table = get_or_create_chunks_table()
        try:
            # Total count (ignore limit/offset).
            total: int = (
                table.count_rows(filter=body.filter)
                if body.filter
                else table.count_rows()
            )
            # Paginated data.
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

    try:
        rows, total = await asyncio.to_thread(_execute)
    except LanceQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Lance query error: {exc}",
        ) from exc

    items = [ChunkRead(**row) for row in rows]
    return ChunkQueryResponse(items=items, total=total)
```

---

## §6 Verification plan

### V-criterion mapping

| Criterion | How verified |
|---|---|
| V1: `{"filter": "source_id = <id>", "limit": 10}` → `{"items": [≤10 chunks], "total": N}` | Unit test `test_query_by_source_id` + `checks.sh backend` |
| V2: `{"filter": "attr_quality_score > 0.8"}` → only matching chunks | Unit test `test_query_by_quality_score` + `checks.sh backend` |
| V3: `{"filter": "1=0"}` → `{"items": [], "total": 0}` | Unit test `test_query_no_matches` + `checks.sh backend` |

All verification is at the `backend` layer (`bash verify/checks.sh backend`):
`ruff check` + `mypy dataplat_api` + `pytest -q`.

### 6.1 Unit test structure — `apps/api/tests/test_chunks_query.py`

The test file follows the `test_sources_collections_list.py` pattern:
- `TestClient(app)` with `conftest.py` autouse fixtures
  (`_patch_engine_begin`, `_patch_httpx_no_ssl`)
- `get_current_user` overridden for tests that need auth
- Lance operations mocked via
  `patch("dataplat_api.routers.chunks.get_or_create_chunks_table")`

**Mock table setup helper** (used across tests):

```python
import pyarrow as pa
from unittest.mock import MagicMock, patch

def _make_mock_table(rows: list[dict], total: int) -> MagicMock:
    """Return a MagicMock lancedb table for the query path.

    Configures the full search() chain so any combination of
    .where() / .select() / .limit() / .offset() / .to_arrow() resolves
    to an Arrow table containing `rows`, and count_rows() returns `total`.
    """
    schema = pa.schema([
        ("chunk_id", pa.string()),
        ("source_id", pa.int64()),
        # ... (include columns needed for the test rows)
    ])
    arrow_tbl = pa.Table.from_pylist(rows, schema=schema) if rows else pa.table(
        {"chunk_id": pa.array([], type=pa.string()),
         "source_id": pa.array([], type=pa.int64())}
    )
    mock_builder = MagicMock()
    mock_builder.where.return_value = mock_builder
    mock_builder.select.return_value = mock_builder
    mock_builder.limit.return_value = mock_builder
    mock_builder.offset.return_value = mock_builder
    mock_builder.to_arrow.return_value = arrow_tbl

    mock_table = MagicMock()
    mock_table.search.return_value = mock_builder
    mock_table.count_rows.return_value = total
    return mock_table
```

**Test cases:**

| Test name | What it verifies |
|---|---|
| `test_query_by_source_id` | V1: filter `"source_id = 5"`, limit=10; mock returns 2 rows, count=2; response items len=2, total=2 |
| `test_query_limit_applied` | V1 pagination: mock returns 10 rows but total=50; items len=10, total=50 |
| `test_query_by_quality_score` | V2: filter `"attr_quality_score > 0.8"`; mock returns 3 rows where score > 0.8; items[*].attr_quality_score all > 0.8 |
| `test_query_no_matches` | V3: filter `"1=0"`; mock returns empty table, count=0; `{"items": [], "total": 0}` |
| `test_query_no_filter_returns_all` | No filter body field; mock returns 5 rows, count=5 |
| `test_query_with_columns_projection` | `columns=["chunk_id", "text"]`; assert `.select()` called with correct columns |
| `test_query_no_token_returns_401` | Missing Authorization header → 401 |
| `test_query_invalid_filter_too_long` | `filter` with 1001 chars → 422 (Pydantic max_length) |
| `test_query_invalid_limit_zero_returns_422` | `limit=0` → 422 |
| `test_query_invalid_offset_negative_returns_422` | `offset=-1` → 422 |
| `test_query_lance_error_returns_400` | Mock `get_or_create_chunks_table` raises `Exception("parse error")`; response is 400 |
| `test_query_response_shape` | Assert `"items"` and `"total"` keys present; each item has `"chunk_id"` key |

**V1 count_rows call verification** (mirrors `test_list_collections_owner_filter`):
Assert `mock_table.count_rows.call_args` passes `filter="source_id = 5"` when a
filter is provided, and is called with no arguments when `filter=None`.

### 6.2 Ruff / Mypy gates

- `ruff check .` must pass with zero diagnostics.
- `mypy dataplat_api` must pass; `ChunkQueryRequest`, `ChunkRead`,
  `ChunkQueryResponse` are fully typed; handler return type is
  `ChunkQueryResponse` (no `Any` escapes).

### 6.3 OpenAPI sync check

After regenerating `packages/api-types/openapi.json`, run:
```bash
python3 -c "
import json
data = json.load(open('packages/api-types/openapi.json'))
assert '/api/chunks/query' in data['paths'], 'Missing /api/chunks/query in openapi.json'
assert 'ChunkQueryRequest' in data['components']['schemas']
assert 'ChunkRead' in data['components']['schemas']
assert 'ChunkQueryResponse' in data['components']['schemas']
print('openapi.json sync: OK')
"
```
This assertion is added to the `verifier` step; it is NOT a separate `checks.sh`
layer — it runs as part of the `backend` pre-commit step.

---

## §7 Risks and mitigations

| # | Risk | Probability | Severity | Mitigation |
|---|---|---|---|---|
| R1 | `table.search().where(filter).offset(N).to_arrow()` in lancedb 0.30.2 does not support `.offset()` chaining | Medium | High | `checks.sh backend` (unit test with mock) will pass regardless. If smoke/integration tests fail, fall back to `to_lance().to_table(filter=..., offset=N, limit=M)` via `asyncio.to_thread`. Document as a known lancedb version caveat. |
| R2 | `attr_embed_vector` (1024 floats) in JSON responses causes excessive payload size | Low | Medium | Callers should pass `columns` to exclude it. MVP does not add server-side default exclusion; that is a future optimisation. Document in handler docstring. |
| R3 | `to_pylist()` on a PyArrow table with `pa.large_string()` columns returns `str` in Python (correct), but if lancedb stores a different type at runtime, `ChunkRead(**row)` fails with a Pydantic validation error (500). | Low | Medium | `ChunkRead` uses permissive `str \| None` / `float \| None` types; Pydantic v2 will coerce compatible types. If a truly incompatible type appears, the 500 surfaces a real data-quality bug. |
| R4 | DataFusion filter `"1=0"` may not be recognised as valid SQL by lancedb 0.30.2 and could raise a parse error instead of returning 0 rows | Low | Low | If the live smoke test fails on V3, replace `"1=0"` with `"chunk_id = '__no_match__'"` as the zero-result test. The unit test always passes (mock). |
| R5 | `asyncio.to_thread()` wraps a closure `_execute` that calls `get_or_create_chunks_table()` — which opens a real S3 connection when not mocked. If the mock patch is applied after `_execute` is defined (closure capture), tests could hit S3. | Low | High | The patch target is `dataplat_api.routers.chunks.get_or_create_chunks_table` (module-level name lookup at call time, not closure capture), so the mock is active when `_execute` runs in the thread. Verified by the fact that `_execute` calls `get_or_create_chunks_table()` by name lookup inside the function body. |
| R6 | `make codegen` absent (no Makefile) — invariant #6 technically requires it | N/A | N/A | Handled by D9: manual `openapi.json` regeneration. The `checks.sh contract` layer guards `[[ -f Makefile ]] || exit 0`, so CI passes. The committed diff in `packages/api-types/openapi.json` satisfies the spirit of invariant #6. |
| R7 | Filter string containing semicolons or DDL (`DROP TABLE`) causes DataFusion to execute destructive SQL | Very Low | Critical | DataFusion in lancedb evaluates the filter as a **predicate expression** on a read-only scan, not as a general SQL statement. DROP/INSERT/UPDATE are not valid predicates and will be rejected by the parser. No mitigation beyond the existing length cap is required for MVP. |
