# S032-F-032 Review — Mode B (Post-Implementation)

Commit reviewed: `0959288`
Reviewer date: 2026-05-28

---

## Verdict: APPROVED

---

## Checklist

- [x] `schemas/chunks.py` matches §4
- [x] `routers/chunks.py` matches §5 (with B1/M1/N1/N2 fixes)
- [x] `main.py` updated (import + `app.include_router`)
- [x] 12 tests present and cover the right cases
- [x] `openapi.json` in same commit (invariant #6)
- [x] Auth (`Depends(get_current_user)`) on the handler
- [x] `asyncio.to_thread(_execute)` pattern correct
- [x] No sync DB sessions

---

## Findings

None. All items below verify clean.

### schemas/chunks.py

Verbatim match to §4. All 24 `ChunkRead` fields present; all nullable except
`chunk_id: str`; `ChunkQueryRequest` constraints (`max_length=1000`,
`ge=1, le=1000` on `limit`, `ge=0` on `offset`) match exactly.
`from __future__ import annotations` present.

### routers/chunks.py — feedback fixes

| Fix | Contract requirement | Implemented |
|-----|----------------------|-------------|
| B1  | `get_or_create_chunks_table()` inside the `try` block | ✅ First statement inside `try` |
| M1  | Unconditional `table.count_rows(filter=body.filter)` | ✅ No conditional branch; `None` passed when no filter supplied |
| N1  | `LanceQueryError` inherits `Exception`, not `ValueError` | ✅ `class LanceQueryError(Exception)` |
| N2  | `from __future__ import annotations` at top of router | ✅ Line 6 |

### Handler structure

Matches §5 outline exactly: `_execute()` sync helper dispatched via
`await asyncio.to_thread(_execute)`; `LanceQueryError` caught in the outer
async scope and converted to HTTP 400; `ChunkRead(**row)` construction and
`ChunkQueryResponse(items=items, total=total)` return are correct.

The closure over `body` inside `_execute` is safe: `body` is a function
parameter bound before `_execute` is defined and is never reassigned.

### main.py

Import added alphabetically (`chunks` between `auth` and `documents`) and
`app.include_router(chunks_router)` inserted in the correct position. No
unrelated lines disturbed.

### Tests (12 / 12)

Every test from the §6.1 table is present and exercises the right behaviour:

| Test | Verdict |
|------|---------|
| `test_query_by_source_id` | V1: 2-row filter result, total=2 ✅ |
| `test_query_limit_applied` | V1 pagination: items=10, total=50 ✅ |
| `test_query_by_quality_score` | V2: quality-score filter, scores validated ✅ |
| `test_query_no_matches` | V3: empty result, total=0 ✅ |
| `test_query_no_filter_returns_all` | No filter; also asserts `count_rows(filter=None)` (M1 regression) ✅ |
| `test_query_with_columns_projection` | `qb.select.assert_called_once_with(["chunk_id","text"])` ✅ |
| `test_query_no_token_returns_401` | No dep override; real oauth2_scheme rejects ✅ |
| `test_query_invalid_filter_too_long` | 1001-char string → 422 ✅ |
| `test_query_invalid_limit_zero_returns_422` | limit=0 → 422 ✅ |
| `test_query_invalid_offset_negative_returns_422` | offset=-1 → 422 ✅ |
| `test_query_lance_error_returns_400` | `side_effect=Exception(...)` on `get_or_create_chunks_table` → 400 (exercises B1 path) ✅ |
| `test_query_response_shape` | `"items"` + `"total"` keys; `chunk_id` in items ✅ |

The `test_query_lance_error_returns_400` test correctly targets B1's fix:
the mock raises from `get_or_create_chunks_table` itself, confirming that a
table-open failure (now inside `try`) is caught and surfaced as HTTP 400.

### openapi.json (invariant #6)

File modified in the same commit. All four assertions from §6.3 pass:
- `/api/chunks/query` path present with `post` operation ✅
- `ChunkQueryRequest` in `components/schemas` ✅
- `ChunkRead` in `components/schemas` ✅
- `ChunkQueryResponse` in `components/schemas` ✅

### Hard invariants

- **#5 (async SQLAlchemy):** New code has zero SQLAlchemy session usage.
  The only DB import is `dataplat_api.db.models.User` for the type annotation
  on the `Depends(get_current_user)` parameter — no session created or used. ✅
- **#6 (OpenAPI sync):** Confirmed above. ✅

---

## Non-blocking observations

1. **`arrow_tbl` / `total` referenced outside the `try` block.** Both variables
   are assigned inside `try` and used in the `return` statement that follows the
   `except` clause. This is correct at runtime (the `except` branch always
   re-raises, so the `return` is only reached when the try succeeded) and is the
   exact pattern specified in §5 of the agreed contract. Modern mypy follows
   exhaustive control-flow through unconditional-raise `except` blocks and should
   accept this without warning. If a future mypy upgrade flags it, a trivial fix
   is to initialise both before the `try` (e.g. `arrow_tbl = None; total = 0`),
   but there is no production-correctness risk as written.

2. **`test_query_no_token_returns_401` uses the shared `client` fixture but
   calls `client.post` without the `_query` helper.** This is intentional and
   correct — the `_query` helper injects a Bearer token, which would defeat the
   purpose of the 401 test. The inline call is fine.

---

APPROVED
