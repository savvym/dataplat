# Sprint S035-F-035 — Proposed

## 1. What will be built

Add `GET /api/chunks/{chunk_id}` to the existing chunks router; it returns the full `ChunkRead` record (all 24 CHUNKS_SCHEMA fields) for the given `chunk_id`, or 404 if no matching row exists in the Lance table.

---

## 2. Files changed

| File | Change |
|------|--------|
| `apps/api/dataplat_api/routers/chunks.py` | Add `GET /{chunk_id}` handler at the end of the file |
| `apps/api/tests/test_chunks_get_by_id.py` | **New file** — unit tests covering both verification criteria |
| `packages/api-types/` (generated) | Updated TypeScript types via `make codegen` after the new endpoint is registered; committed in the same commit |

No changes to `apps/api/dataplat_api/schemas/chunks.py` — `ChunkRead` already contains all 24 required fields (`text`, `token_count`, `source_refs`, `docling_refs`, all `attr_*` columns, `augmented_from`, etc.).

---

## 3. Implementation details

### 3.1 Route signature

```python
@router.get("/{chunk_id}", response_model=ChunkRead)
async def get_chunk_by_id(
    chunk_id: str,
    current_user: User = Depends(get_current_user),
) -> ChunkRead:
```

- **Method/path conflict**: the new route is `GET /{chunk_id}`; all existing routes are `POST /query`, `POST /aggregate`, `POST /distribution`. FastAPI dispatches on both HTTP method and path, so there is no shadowing risk.
- **Auth guard**: same `Depends(get_current_user)` pattern as every other chunk endpoint. Unauthenticated requests get 401 automatically.
- **No per-user row scoping**: consistent with the other chunk endpoints; repository-level ACL on Lance is deferred to post-MVP (design doc §11.6).

### 3.2 Lance lookup strategy

```python
def _execute() -> dict | None:
    table = get_or_create_chunks_table()
    # SQL-escape single quotes to prevent DataFusion injection.
    safe_id = chunk_id.replace("'", "''")
    arrow_tbl = (
        table.search()
             .where(f"chunk_id = '{safe_id}'")
             .limit(1)
             .to_arrow()
    )
    rows = arrow_tbl.to_pylist()
    return rows[0] if rows else None
```

Key points:

- **Single-quote escaping**: `chunk_id.replace("'", "''")` applies standard SQL quoting before the value is interpolated into the DataFusion predicate. This is minimal but sufficient for the ASCII printable range (chunk IDs are `uuid` or `source+offset` identifiers per `CHUNKS_SCHEMA` comments — neither form embeds quotes in practice). A more robust option (parameterized queries) is not yet exposed by the lancedb 0.30.2 public API.
- **`.limit(1)`**: avoids scanning beyond the first match; `chunk_id` is unique by design.
- **`asyncio.to_thread(_execute)`**: wraps sync Lance S3 I/O exactly as the existing `/query`, `/aggregate`, and `/distribution` handlers do. No new threading model introduced.
- **Exception wrapping**: any Lance exception inside `_execute` is caught, re-raised as `LanceQueryError`, and converted to HTTP 400 — the same error pathway already present in the file, re-used here.

### 3.3 404 path

```python
row = await asyncio.to_thread(_execute)
if row is None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Chunk {chunk_id!r} not found",
    )
return ChunkRead(**row)
```

A zero-row result from Lance (filter matched nothing) sets `row = None`, which raises 404. This naturally handles:
- A well-formed ID that does not exist in the table.
- A deliberately malformed path segment such as `"nonexistent-id"` — the DataFusion predicate evaluates to zero matches, not an error, so 404 is returned rather than 400.

### 3.4 Response serialisation

`ChunkRead(**row)` is the same construction used by `query_chunks`. Pydantic handles `None` for all optional fields; it handles list fields (`attr_minhash_signature`, `attr_pii_categories`, `attr_embed_vector`) and the `datetime` fields (`created_at`, `updated_at`) via the existing `ChunkRead` validators.

---

## 4. Verification plan

### V-criterion 1 — `GET /api/chunks/{valid_chunk_id}` returns 200 with all expected fields

**Test**: `test_get_chunk_200_all_fields` in `test_chunks_get_by_id.py`

```
mock get_or_create_chunks_table → returns a mock table whose
  .search().where(...).limit(1).to_arrow().to_pylist()
  chain returns a list with one row dict containing all 24 fields.

GET /api/chunks/abc-123  (with Bearer token)
  → assert status 200
  → assert body["chunk_id"] == "abc-123"
  → assert body["text"] is present and matches mock value
  → assert body["token_count"] is present
  → assert body["source_refs"] is present
  → assert body["docling_refs"] is present
  → assert body["augmented_from"] is present
  → assert all attr_* keys are present in the response body
    (attr_quality_score, attr_quality_provider, attr_lang_code,
     attr_lang_confidence, attr_minhash_signature, attr_minhash_cluster_id,
     attr_minhash_is_head, attr_pii_has_pii, attr_pii_categories,
     attr_embed_vector)
```

### V-criterion 2 — `GET /api/chunks/nonexistent-id` returns 404

**Test**: `test_get_chunk_404_not_found` in `test_chunks_get_by_id.py`

```
mock get_or_create_chunks_table → returns a mock table whose
  .search().where(...).limit(1).to_arrow().to_pylist()
  chain returns an empty list [].

GET /api/chunks/nonexistent-id  (with Bearer token)
  → assert status 404
  → assert "not found" in body["detail"] (case-insensitive)
```

### Additional tests (same file, defensive coverage)

| Test | Scenario |
|------|----------|
| `test_get_chunk_401_no_token` | No Authorization header → 401 (does not override `get_current_user`) |
| `test_get_chunk_lance_error_returns_400` | `get_or_create_chunks_table` side-effects `Exception` → 400 |
| `test_get_chunk_where_called_with_escaped_id` | Asserts `.where()` is called with `"chunk_id = 'abc-123'"` (verifies correct filter construction) |

---

## 5. Hard invariants — relevance and satisfaction

| Invariant | Relevance | How satisfied |
|-----------|-----------|---------------|
| **#1 Lineage** | Not applicable — read-only GET endpoint; does not create or modify commits. | N/A |
| **#2 Storage separation + CAS** | Chunk content is read from Lance (MinIO/S3), not Postgres. | Satisfied — `get_or_create_chunks_table()` hits MinIO; no Postgres bytes-storage. |
| **#3 Schema frozen post-publish** | Not applicable — no schema modifications. | N/A |
| **#4 LLM calls via gateway** | Not applicable — no LLM calls. | N/A |
| **#5 Async SQLAlchemy** | The handler uses `Depends(get_current_user)` which performs an async Postgres user lookup. No additional DB sessions are opened; no sync sessions or `session.query()` introduced. | Satisfied — handler body only touches Lance via `asyncio.to_thread`. |
| **#6 OpenAPI ↔ TS type sync** | Adding a new endpoint changes the generated OpenAPI spec. | Satisfied — `make codegen` is run after implementing the route; the updated `packages/api-types/` diff is committed in the same commit. |
