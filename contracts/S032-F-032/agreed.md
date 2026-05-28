# S032-F-032 — Chunk query endpoint: agreed.md

Sprint ID: S032-F-032
Feature: F-032 `chunk_query_endpoint`
Status: AGREED
Dependencies: F-025 (chunks table) ✓, F-008 (auth) ✓

---

## §1 Summary

This sprint ships `POST /api/chunks/query`, a new FastAPI endpoint that executes a
caller-supplied DataFusion SQL filter against the Lance chunks table and returns a
paginated list of matching chunks plus a total count. The request body accepts four
fields: `filter` (a DataFusion SQL predicate fragment), `columns` (optional column
projection), `limit`, and `offset`. Lance I/O is synchronous and is wrapped in
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
| `apps/api/tests/test_chunks_query.py` | **NEW** — unit tests (12 cases, no live Lance/S3) |
| `packages/api-types/openapi.json` | **MODIFIED** — regenerated via manual script (no Makefile; invariant #6) |

No Postgres migration, no Alembic change, no Dagster change.

---

## §3 Design decisions

### D1 — Separate router file with prefix `/api/chunks`

Create `routers/chunks.py` with `APIRouter(prefix="/api/chunks", tags=["chunks"])`.

### D2 — Lance operations wrapped in `asyncio.to_thread()`

All Lance calls are executed inside a single synchronous `_execute()` helper,
dispatched via `await asyncio.to_thread(_execute)` from the async handler.
Both count and paginated fetch are bundled in one `to_thread()` call.

### D3 — Filter validated for length; Lance parse errors → 400

- `filter` is `str | None` with `max_length=1000` (Pydantic rejects over-length → 422).
- Inside `_execute()`, **all** Lance/DataFusion exceptions (including table-open
  failures) are caught and re-raised as `LanceQueryError` → HTTP 400.
- No further SQL-injection sanitization needed (DataFusion is read-only predicate
  parsing only).

### D4 — Single unconditional `count_rows` call + search chain

```python
total = table.count_rows(filter=body.filter)  # None is valid, same as no filter
q = table.search()
if body.filter:
    q = q.where(body.filter)
if body.columns:
    q = q.select(body.columns)
q = q.limit(body.limit).offset(body.offset)
arrow_tbl = q.to_arrow()
```

**Reviewer feedback M1 applied:** No conditional on `count_rows` — `filter=None`
is semantically identical to calling with no filter.

### D5 — No per-user row scoping on Lance

Auth guard prevents unauthenticated access; no owner-scoping injected into filter.
Documented prominently in handler docstring (design doc §11.6 deferral).

### D6 — `columns` defaults to `None` (all columns returned)

When `columns=None`, Lance returns all 24 schema columns.

### D7 — `ChunkRead` mirrors all 24 CHUNKS_SCHEMA fields; all nullable except `chunk_id`

All 24 fields present. `chunk_id: str` is the only required field.

### D8 — Arrow → Python via `to_pylist()`

PyArrow handles timestamp/list conversions automatically.

### D9 — `openapi.json` regenerated manually

```bash
cd apps/api && uv run python -c "
import json
from dataplat_api.main import app
print(json.dumps(app.openapi(), indent=2))
" > ../../packages/api-types/openapi.json
```

Committed in the same commit as router/schema files.

### D10 — `limit` defaults to 100, capped at 1000; `offset` defaults to 0

Pydantic enforces: `limit: int = Field(default=100, ge=1, le=1000)`,
`offset: int = Field(default=0, ge=0)`.

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

## §5 Handler outline (with all feedback fixes applied)

### `apps/api/dataplat_api/routers/chunks.py`

```python
"""Chunks router — S032-F-032.

POST /api/chunks/query — execute a DataFusion SQL filter on the Lance chunks
table and return matching chunks with a total count.
"""
from __future__ import annotations  # ← N2 fix

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from dataplat_api.auth.dependencies import get_current_user
from dataplat_api.db.models import User
from dataplat_api.schemas.chunks import ChunkQueryRequest, ChunkQueryResponse, ChunkRead
from dataplat_api.storage.lance import get_or_create_chunks_table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class LanceQueryError(Exception):  # ← N1 fix: Exception, not ValueError
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
        try:  # ← B1 fix: get_or_create_chunks_table INSIDE try
            table = get_or_create_chunks_table()
            # Total count (M1 fix: unconditional filter= argument).
            total: int = table.count_rows(filter=body.filter)
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

### 6.1 Unit test structure — `apps/api/tests/test_chunks_query.py`

12 test cases with mock Lance table:

| Test name | What it verifies |
|---|---|
| `test_query_by_source_id` | V1: filter "source_id = 5", limit=10; 2 rows returned, total=2 |
| `test_query_limit_applied` | V1 pagination: 10 rows returned but total=50 |
| `test_query_by_quality_score` | V2: filter "attr_quality_score > 0.8"; all scores > 0.8 |
| `test_query_no_matches` | V3: filter "1=0"; `{"items": [], "total": 0}` |
| `test_query_no_filter_returns_all` | No filter; mock returns 5 rows, count=5 |
| `test_query_with_columns_projection` | `columns=["chunk_id", "text"]`; `.select()` called correctly |
| `test_query_no_token_returns_401` | Missing auth → 401 |
| `test_query_invalid_filter_too_long` | 1001-char filter → 422 |
| `test_query_invalid_limit_zero_returns_422` | limit=0 → 422 |
| `test_query_invalid_offset_negative_returns_422` | offset=-1 → 422 |
| `test_query_lance_error_returns_400` | Mock raises Exception → HTTP 400 |
| `test_query_response_shape` | `"items"` and `"total"` keys present; items have `chunk_id` |

### 6.2 Ruff / Mypy gates

- `ruff check .` must pass.
- `mypy dataplat_api` must pass; no `Any` escapes.

### 6.3 OpenAPI assertion (run by implementer, not a checks.sh layer)

```bash
python3 -c "
import json
data = json.load(open('packages/api-types/openapi.json'))
assert '/api/chunks/query' in data['paths'], 'Missing /api/chunks/query'
assert 'ChunkQueryRequest' in data['components']['schemas']
assert 'ChunkRead' in data['components']['schemas']
assert 'ChunkQueryResponse' in data['components']['schemas']
print('openapi.json sync: OK')
"
```

---

## §7 Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | `.offset()` on lancedb 0.30.2 LanceEmptyQueryBuilder | Confirmed supported (reviewer validated) |
| R2 | `attr_embed_vector` payload size (1024 floats per row) | Callers should use `columns` to exclude; document in handler |
| R3 | `to_pylist()` type mismatch → Pydantic 500 | Permissive nullable types; real type bugs surface as 500 (acceptable) |
| R4 | `"1=0"` not valid DataFusion | Fallback: use `"chunk_id = '__no_match__'"` in V3 if needed |
| R5 | Mock closure capture hazard | Confirmed no hazard (module-level name lookup) |
| R6 | No Makefile for codegen | D9 manual regeneration; `checks.sh contract` guards `[[ -f Makefile ]]` |
| R7 | DDL injection via filter | DataFusion read-only predicate parsing; not a threat |

---

## Feedback resolution summary

| Finding | Resolution |
|---|---|
| B1 (BLOCKER): `get_or_create_chunks_table()` outside try | **Fixed**: moved inside `try` block (Option A) — §5 |
| M1 (MEDIUM): Redundant count_rows conditional | **Fixed**: unconditional `table.count_rows(filter=body.filter)` — §5, D4 |
| N1 (NIT): LanceQueryError inherits ValueError | **Fixed**: inherits `Exception` — §5 |
| N2 (NIT): Missing `from __future__ import annotations` | **Fixed**: added to router — §5 |
