# Sprint S035-F-035 — Reviewer Final (Mode B)

**Decision: APPROVED**

All ten checklist items pass. Every Mode A blocker and medium finding from `feedback.md` was addressed in commit `34072bf`. No new blockers introduced.

---

## Full Checklist

### 1. ✅ Route signature uses `FPath(..., max_length=256)` (F3 fix)

`chunks.py` imports `Path as FPath` from `fastapi` and the handler declares:
```python
chunk_id: str = FPath(..., max_length=256),
```
The `maxLength: 256` constraint is faithfully reflected in the regenerated `openapi.json` path-parameter schema.

---

### 2. ✅ Two-layer exception handling: inner try/except → LanceQueryError, outer catches → HTTP 400 (F1 fix)

Both layers are present and structurally identical to the agreed contract in §3.2 and §3.3:

- **Inner `_execute()`** wraps the entire Lance I/O block in `try/except Exception as exc: raise LanceQueryError(str(exc)) from exc`.
- **Outer handler** wraps `await asyncio.to_thread(_execute)` in `try/except LanceQueryError as exc: raise HTTPException(status_code=HTTP_400_BAD_REQUEST, ...)`.

The pattern matches `query_chunks` / `aggregate_chunks` / `distribution_chunks` exactly.

---

### 3. ✅ Single-quote escaping with `.replace("'", "''")`

Present inside `_execute()`:
```python
safe_id = chunk_id.replace("'", "''")
```

---

### 4. ✅ `.search().where().limit(1).to_arrow().to_pylist()` pattern

```python
arrow_tbl = (
    table.search()
    .where(f"chunk_id = '{safe_id}'")
    .limit(1)
    .to_arrow()
)
rows = arrow_tbl.to_pylist()
```
Matches agreed contract §3.2 precisely.

---

### 5. ✅ Zero rows → None → 404

- `_execute()`: `return rows[0] if rows else None`
- Handler: `if row is None: raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=f"Chunk {chunk_id!r} not found")`

---

### 6. ✅ `ChunkRead(**row)` serialization

`return ChunkRead(**row)` — correct. The `ChunkRead` schema (`schemas/chunks.py`) declares all 23 non-key fields as `... | None = None`, so both a full-row dict and a minimal `{"chunk_id": ...}` dict are valid inputs. This is consistent with the column-projection contract already established in `query_chunks`.

---

### 7. ✅ Auth guard `Depends(get_current_user)`

```python
current_user: User = Depends(get_current_user),
```
Same pattern as every other route in `chunks.py`.

---

### 8. ✅ Test `test_get_chunk_where_escapes_single_quote` uses a chunk_id containing a single quote (F2 fix)

The test:
- Sends a GET to `/api/chunks/it's` (path segment contains a literal `'`).
- Asserts `qb.where.assert_called_once_with("chunk_id = 'it''s'")`.

This directly proves the `.replace("'", "''")` fires. Removing the escape in the implementation would make this assertion fail with `"chunk_id = 'it's'"` (one quote, not two), fulfilling the regression-guard purpose that Mode A required.

**Deviation from feedback.md example (non-blocking):** Mode A's suggested code used `_make_mock_table([])` (→ 404 path). The implementation uses `_make_mock_table([{"chunk_id": "it's"}])` with an added `assert resp.status_code == 200`. Since `ChunkRead` requires only `chunk_id` (all other fields `= None`), the partial dict is a valid `ChunkRead`, and the 200 assertion is correct. This is a harmless improvement — the escape assertion still fires and the test is strictly more informative.

---

### 9. ✅ Test file defines its own module-local `_make_mock_table` (F4 fix)

`test_chunks_get_by_id.py` defines `_make_mock_table(rows)` at module level (lines 53–76). There are no cross-imports from `test_chunks_query.py` or any other test file. The module docstring explicitly notes this convention.

---

### 10. ✅ `packages/api-types/openapi.json` regenerated in same commit (invariant #6)

The `/api/chunks/{chunk_id}` GET path block appears in the same commit (`34072bf`) as the router change. The schema includes `"maxLength": 256`, `"$ref": "#/components/schemas/ChunkRead"` for the 200 response, security requirements, and a 422 error response — all consistent with what FastAPI generates.

**Minor cosmetic note (non-blocking):** The file ends without a trailing newline (`\ No newline at end of file` in the diff). This is an artifact of `make codegen` and does not affect correctness or CI validity.

---

## Summary

| # | Item | Result |
|---|------|--------|
| 1 | `FPath(..., max_length=256)` | ✅ |
| 2 | Two-layer exception handling (F1 fix) | ✅ |
| 3 | `.replace("'", "''")` escaping | ✅ |
| 4 | `.search().where().limit(1).to_arrow().to_pylist()` | ✅ |
| 5 | Zero rows → None → 404 | ✅ |
| 6 | `ChunkRead(**row)` serialization | ✅ |
| 7 | Auth guard `Depends(get_current_user)` | ✅ |
| 8 | Escape test uses single-quote chunk_id (F2 fix) | ✅ |
| 9 | Module-local `_make_mock_table` (F4 fix) | ✅ |
| 10 | `openapi.json` regenerated in same commit (invariant #6) | ✅ |

---

**APPROVED** — implementation is complete and correct per agreed contract. Proceed to verifier.
