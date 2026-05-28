# Sprint S035-F-035 — Agreed Contract

## 1. What will be built

Add `GET /api/chunks/{chunk_id}` to the existing chunks router; it returns the full `ChunkRead` record (all 24 CHUNKS_SCHEMA fields) for the given `chunk_id`, or 404 if no matching row exists in the Lance table.

---

## 2. Files changed

| File | Change |
|------|--------|
| `apps/api/dataplat_api/routers/chunks.py` | Add `GET /{chunk_id}` handler at the end of the file |
| `apps/api/tests/test_chunks_get_by_id.py` | **New file** — 6 unit tests covering both verification criteria + edge cases |
| `packages/api-types/` (generated) | Updated TypeScript types via `make codegen` after the new endpoint is registered; committed in the same commit |

No changes to `apps/api/dataplat_api/schemas/chunks.py` — `ChunkRead` already contains all 24 required fields.

---

## 3. Implementation details

### 3.1 Route signature (F3 fix: max_length on chunk_id)

```python
from fastapi import Path as FPath

@router.get("/{chunk_id}", response_model=ChunkRead)
async def get_chunk_by_id(
    chunk_id: str = FPath(..., max_length=256),
    current_user: User = Depends(get_current_user),
) -> ChunkRead:
```

- `max_length=256` prevents oversized path segments from reaching DataFusion (F3 fix).
- No method/path conflict — all existing routes are POST.
- Auth guard: same `Depends(get_current_user)` pattern as every other chunk endpoint.

### 3.2 Lance lookup with two-layer exception handling (F1 fix)

```python
def _execute() -> dict | None:
    try:
        table = get_or_create_chunks_table()
        safe_id = chunk_id.replace("'", "''")
        arrow_tbl = (
            table.search()
                 .where(f"chunk_id = '{safe_id}'")
                 .limit(1)
                 .to_arrow()
        )
        rows = arrow_tbl.to_pylist()
        return rows[0] if rows else None
    except Exception as exc:
        raise LanceQueryError(str(exc)) from exc
```

### 3.3 Outer handler with error conversion (F1 fix)

```python
try:
    row = await asyncio.to_thread(_execute)
except LanceQueryError as exc:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Lance query error: {exc}",
    ) from exc

if row is None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Chunk {chunk_id!r} not found",
    )
return ChunkRead(**row)
```

Both layers are mandatory — matching the exact pattern in `query_chunks`, `aggregate_chunks`, and `distribution_chunks`.

### 3.4 Response serialisation

`ChunkRead(**row)` — Pydantic handles `None` for all optional fields; handles list fields and datetime fields via existing validators.

---

## 4. Verification plan

### V-criterion 1 — `GET /api/chunks/{valid_chunk_id}` returns 200 with all expected fields

**Test**: `test_get_chunk_200_all_fields`

Mock `get_or_create_chunks_table` → returns mock table whose `.search().where(...).limit(1).to_arrow().to_pylist()` returns a list with one row dict containing all 24 fields. Assert status 200, body contains all expected fields.

### V-criterion 2 — `GET /api/chunks/nonexistent-id` returns 404

**Test**: `test_get_chunk_404_not_found`

Mock returns empty list `[]`. Assert status 404, "not found" in detail.

### Additional tests

| Test | Scenario |
|------|----------|
| `test_get_chunk_401_no_token` | No Authorization header → 401 |
| `test_get_chunk_lance_error_returns_400` | `get_or_create_chunks_table` raises `Exception` → LanceQueryError → HTTP 400 |
| `test_get_chunk_where_called_with_escaped_id` | chunk_id = `"abc-123"` — asserts `.where()` called with `"chunk_id = 'abc-123'"` (filter construction correctness) |
| `test_get_chunk_where_escapes_single_quote` | **(F2 fix)** chunk_id = `"it's"` — asserts `.where()` called with `"chunk_id = 'it''s'"` (proves the `.replace()` actually fires) |

### F4 fix: Mock-helper provenance

The new test file `test_chunks_get_by_id.py` MUST define its own module-local `_make_mock_table()` helper. Do NOT import from `test_chunks_query.py` or any other test file.

---

## 5. Hard invariants — relevance and satisfaction

| Invariant | Relevance | How satisfied |
|-----------|-----------|---------------|
| **#1 Lineage** | N/A — read-only GET endpoint | N/A |
| **#2 Storage separation + CAS** | Chunk content read from Lance (MinIO/S3), not Postgres | Satisfied |
| **#3 Schema frozen post-publish** | N/A — no schema modifications | N/A |
| **#4 LLM calls via gateway** | N/A — no LLM calls | N/A |
| **#5 Async SQLAlchemy** | Handler uses `Depends(get_current_user)` for async Postgres user lookup; Lance I/O via `asyncio.to_thread` | Satisfied |
| **#6 OpenAPI ↔ TS type sync** | New endpoint changes OpenAPI spec | Satisfied — `make codegen` + commit in same commit |

---

## 6. Reviewer findings addressed

| # | Severity | Finding | Resolution |
|---|----------|---------|-----------|
| F1 | BLOCKER | Missing two-layer try/except in code sketch | §3.2 and §3.3 now show complete exception handling |
| F2 | BLOCKER | Escape test uses no-special-char ID | Added `test_get_chunk_where_escapes_single_quote` with `"it's"` → `"it''s"` |
| F3 | MEDIUM | No max_length on chunk_id path param | Added `FPath(..., max_length=256)` in §3.1 |
| F4 | NIT | Mock-helper provenance unspecified | Explicitly stated: define module-local `_make_mock_table()` in §4 |
