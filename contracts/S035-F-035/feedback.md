# Sprint S035-F-035 — Reviewer Feedback (Mode A)

**Decision: CHANGES_REQUESTED**

The proposal is well-structured and covers all the right ground — auth guard, 404 path, DataFusion escaping, codegen obligation, and a solid test plan. Three issues must be fixed before implementation begins; two are hard blockers.

---

## Finding 1 — BLOCKER: `_execute()` code sketch omits the exception-wrapping try/except

**Location:** §3.2 (the `_execute()` function body) and §3.3 (the outer `asyncio.to_thread` call-site).

**Problem:**

Every existing handler in `chunks.py` follows a two-layer exception pattern that the proposal's code sketch does **not** show:

```python
# Layer 1 — inside _execute(): wrap ALL Lance I/O
def _execute() -> dict | None:
    try:                                          # ← REQUIRED, not shown in §3.2
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
    except Exception as exc:                      # ← REQUIRED, not shown in §3.2
        raise LanceQueryError(str(exc)) from exc

# Layer 2 — outer handler: convert LanceQueryError → HTTP 400
try:                                              # ← REQUIRED, not shown in §3.3
    row = await asyncio.to_thread(_execute)
except LanceQueryError as exc:                    # ← REQUIRED, not shown in §3.3
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Lance query error: {exc}",
    ) from exc
if row is None:
    raise HTTPException(status_code=404, ...)
return ChunkRead(**row)
```

The prose in §3.2 does correctly say *"any Lance exception inside `_execute` is caught, re-raised as `LanceQueryError`, and converted to HTTP 400"*, but an implementer following the **code** rather than the prose would produce a handler that returns HTTP 500 for all Lance errors.

**Impact:** The test `test_get_chunk_lance_error_returns_400` (listed in the test plan) would **fail** with the code as drawn, and the endpoint would diverge from the established `LanceQueryError` contract.

**Required fix:** Update §3.2 and §3.3 code blocks to include both try/except layers, exactly mirroring the pattern in `query_chunks` and `aggregate_chunks`. The final agreed spec must show the complete, copy-pasteable handler.

---

## Finding 2 — BLOCKER: Escape test covers only the no-special-char case

**Location:** §4 — `test_get_chunk_where_called_with_escaped_id`.

**Problem:**

The proposed test asserts that `.where()` is called with `"chunk_id = 'abc-123'"` — a chunk_id that contains no single quotes. This passes whether or not the escaping code is present, making the test useless as an injection-prevention guard.

**Required fix:** Add a second assertion (or a separate test) that verifies the escape logic actually fires:

```python
def test_get_chunk_where_escapes_single_quote(client: TestClient) -> None:
    """chunk_id containing a single quote is doubled before interpolation."""
    mock_table = _make_mock_table([])
    app.dependency_overrides[get_current_user] = _override_current_user
    try:
        with patch(
            "dataplat_api.routers.chunks.get_or_create_chunks_table",
            return_value=mock_table,
        ):
            # This ID embeds a single quote; after escaping it becomes "it''s".
            client.get(
                "/api/chunks/it's",
                headers={"Authorization": "Bearer faketoken"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    qb = mock_table.search.return_value
    qb.where.assert_called_once_with("chunk_id = 'it''s'")
```

Without this, a future refactor that removes the `.replace()` would silently break injection safety while the test suite stays green.

---

## Finding 3 — MEDIUM: No `max_length` constraint on the `chunk_id` path parameter

**Location:** §3.1 — route signature.

**Problem:**

The route is declared as:

```python
@router.get("/{chunk_id}", response_model=ChunkRead)
async def get_chunk_by_id(
    chunk_id: str,
    current_user: User = Depends(get_current_user),
) -> ChunkRead:
```

`chunk_id` is an unbounded string. A caller could supply a 10 MB path segment, which would be single-quote-escaped and sent to DataFusion as a very long predicate string. The `filter` field on `ChunkQueryRequest` carries `max_length=1000`; the same defensive principle should apply here.

The design doc describes chunk IDs as UUID or `{source_id}_{seq}` identifiers — both comfortably fit in 256 characters.

**Suggested fix:**

```python
from fastapi import Path as FPath   # or reuse `Path` from fastapi

@router.get("/{chunk_id}", response_model=ChunkRead)
async def get_chunk_by_id(
    chunk_id: str = FPath(..., max_length=256),
    current_user: User = Depends(get_current_user),
) -> ChunkRead:
```

---

## Finding 4 — NIT: Mock-helper provenance unspecified for new test file

**Location:** §4 — test implementation notes.

**Observation:**

`_make_mock_table` is defined as a module-local function in `test_chunks_query.py`. The new file `test_chunks_get_by_id.py` will need an equivalent; cross-importing from another test file is bad practice. The established codebase convention (visible in `test_chunks_aggregate.py`) is to define a local `_make_*_mock_table` per test module.

The agreed spec should explicitly state: *define a module-local `_make_mock_table` in `test_chunks_get_by_id.py` (do not import from `test_chunks_query.py`)*, to avoid an implementer making the wrong choice.

---

## Summary table

| # | Severity | Short description | Blocks impl? |
|---|----------|-------------------|--------------|
| 1 | BLOCKER  | `_execute()` + outer handler missing two-layer try/except in code sketch | Yes |
| 2 | BLOCKER  | Escape test uses no-special-char ID; won't catch missing `.replace()` | Yes |
| 3 | MEDIUM   | No `max_length` on `chunk_id` path param | No |
| 4 | NIT      | Mock-helper provenance for new test file not specified | No |

---

## What is already correct (no action needed)

- **Route/method conflict analysis** (§3.1): correct — all existing routes are POST; GET `/{chunk_id}` is unambiguous.
- **404 logic** (§3.3): correct — zero-row Lance result → 404; the "nonexistent-id" case naturally falls through the DataFusion predicate with zero matches.
- **`ChunkRead(**row)` deserialization**: correct — follows `query_chunks` precedent; Pydantic's optional defaults handle absent Lance columns without extra code.
- **`asyncio.to_thread` threading model**: correct — no new threading model introduced.
- **Hard invariants coverage** (§5): accurate and complete; invariant #6 (codegen obligation) is correctly called out and satisfied.
- **No per-user row scoping**: correct for MVP scope per §11.6.
- **Test plan breadth**: the five test cases cover both V-criteria plus 401, 400, and filter-construction, which is appropriate.

Fix findings 1 and 2, incorporate 3, note 4, then re-submit for a second Mode A pass.
