# S036-F-036 — Mode B Review (post-implementation)

**Reviewer:** Mode B  
**Commit under review:** `ab458ea` (`feat(F-036): GET /api/chunks/{id}/lineage endpoint with augmented_from chain traversal`)  
**Follow-up:** `a07a613` (progress log entry only — no code changes)  
**Date:** 2026-06-02

---

## Checklist trace

### 1. Mode A findings closed in code

#### F1 — Depth-cap ordering (HIGH → CLOSED ✓)

The root check fires **before** the loop can exhaust.  Exact code path at `routers/chunks.py` lines 466–468:

```python
parent_id: str | None = current_row.get("augmented_from")
if parent_id is None:
    break  # root reached — always succeeds even at _depth == _MAX_LINEAGE_DEPTH - 1
```

The `for … else` depth-cap path at lines 497–507 is only reachable after all 32 iterations complete without a `break`.

**Boundary trace — 32-entry chain, root at index 31 (test 9a):**  
`range(32)` yields `_depth = 0 … 31`.  
At `_depth=31`: append entry 32, evaluate `parent_id` → `None` → `break` fires.  
`else` clause does not execute. Returns HTTP 200 with `lineage_chain` length 32. ✓

**Boundary trace — 33-entry chain, no root within 32 iterations (test 9b):**  
At `_depth=31` (the 32nd iteration): append entry 32, `parent_id` is non-null → no break; parent fetch call 33 returns a real row and advances `current_row`.  
`range(32)` exhausted → `else` fires → HTTP 500 with "depth cap … exceeded". ✓  
Python simulation confirmed: `ELSE_FIRED`, 33 Lance calls, 32 entries in chain.

#### F2 — Test 13, broken augmented_from reference (MEDIUM → CLOSED ✓)

`test_lineage_500_broken_augmented_from_chain` present at lines 629–653 of `test_chunks_lineage.py`.  
Mock sequence: call 0 returns `row_a` (`augmented_from='chunk-missing-B'`); call 1 returns `[]` (empty → `None`).  
Handler fires at `routers/chunks.py` lines 484–494 (parent is None → 500 "Broken augmented_from chain").  
Test asserts `status_code == 500` and `"Broken augmented_from chain" in detail`. ✓

#### F3 — Test 14, null source_id on root (MEDIUM → CLOSED ✓)

`test_lineage_500_null_source_id_on_root_chunk` present at lines 659–685.  
Mock: non-augmented chunk with `source_id` overridden to `None` (line 665: `row["source_id"] = None`).  
Handler fires at `routers/chunks.py` lines 512–519 (root_source_id is None → 500 "… has null source_id").  
Test asserts `status_code == 500` and `"null source_id" in detail`. ✓

#### F4 — Route ordering non-issue (NIT → CLOSED ✓)

`get_chunk_lineage` (`/{chunk_id}/lineage`) is registered at line 352, before `get_chunk_by_id` (`/{chunk_id}`) at line 553.  The docstring at lines 10–12 correctly documents the two-segment vs one-segment distinction.  No special ordering hack or cargo-culted comment; style preference only, per agreed.md OQ-4. ✓

#### NIT OQ-2 comment — CLOSED ✓

Comment present immediately before `_fetch_chunk` definition at `routers/chunks.py` lines 383–384 and also in `schemas/chunks.py` lines 192–196 (docstring on `ChunkLineageEntry`).  Both match the agreed deferred-optimisation framing. ✓

#### NIT 9b detail-string assertion — CLOSED ✓

Test 9b (lines 531–537) asserts:
```python
assert "depth cap" in detail.lower() or "exceeded" in detail.lower(), (
    f"Expected depth-cap-exceeded detail, got: {detail!r}"
)
```
Handler depth-cap detail string (`routers/chunks.py` line 503–504):
```
"Lineage chain depth cap (32) exceeded starting from chunk_id=…; possible runaway augmentation chain."
```
Both `"depth cap"` (case-insensitive) and `"exceeded"` are present → assertion passes.  
The 33rd mock call (line 514) supplies a real row dict, ensuring the handler does not fall into the broken-parent branch (which would emit "Broken augmented_from chain" instead). Confirmed by simulation. ✓

---

### 2. Hard invariants

#### #5 — Async SQLAlchemy (PASS ✓)

Both Postgres reads at `routers/chunks.py` lines 521–524 and 535–541 use:
```python
await session.execute(select(...))
```
with `.scalar_one_or_none()` on the sync result proxy.  No `session.query()`, no `Session` (sync) import anywhere in the diff.

#### #6 — OpenAPI codegen committed in same commit (PASS ✓)

`packages/api-types/openapi.json` is listed in `ab458ea`'s stat (+173 lines).  Confirmed present:
- Path `/api/chunks/{chunk_id}/lineage` with `GET` operation, `operationId: get_chunk_lineage_api_chunks__chunk_id__lineage_get`, security `OAuth2PasswordBearer`. ✓
- Schema `ChunkLineageEntry`: all 7 fields, all marked `required` (chunk_id non-nullable; remaining 6 as `anyOf [type, null]`). ✓
- Schema `ChunkLineageResponse`: 4 fields (`chunk`, `source`, `document_variant`, `lineage_chain`), all `required`; `document_variant` is `anyOf [$ref DocumentVariantRead, null]`. ✓
- Schema `DocumentVariantRead`: already present from prior sprints; no collision. ✓
- OpenAPI 3.1.0 (unchanged). ✓

#### #2 — Storage separation (PASS ✓)

Lineage chain traversal stays entirely in Lance (`_fetch_chunk` via `get_or_create_chunks_table()`).  Only `Source` and `DocumentVariant` rows come from Postgres.  No blob bytes stored in Postgres; no Lance content written to Postgres.

#### #4 — LLM gateway (N/A ✓)

No LLM calls anywhere in this diff.

---

### 3. Correctness of traversal

**Single-quote escaping** (`routers/chunks.py` line 394):
```python
safe_cid = cid.replace("'", "''")
```
Applied inside `_fetch_chunk` before every DataFusion predicate.  Both the initial fetch (`asyncio.to_thread(_fetch_chunk, chunk_id)`) and every chain-step fetch (`asyncio.to_thread(_fetch_chunk, parent_id)`) call the same helper.  Test 12 (`test_lineage_escapes_single_quote_in_chunk_id`) verifies the escaped form `"chunk_id = 'it''s'"` reaches the mock's `.where()`. ✓

**`asyncio.to_thread()` wrapping** (lines 420, 477):  
Initial fetch: `await asyncio.to_thread(_fetch_chunk, chunk_id)`.  
Each chain-step fetch: `await asyncio.to_thread(_fetch_chunk, parent_id)`.  
`_fetch_chunk` is a sync closure; Lance/DataFusion I/O never runs on the event loop. ✓

**Cycle guard** (lines 443–448):  
`seen_ids` set check fires **before** any append.  
Test 8 verifies cycle detection for A→B→A with 3-call mock (call 3 returns `row_a` again, which has `chunk_id='chunk-A'` already in `seen_ids`).  
Detail string: `"Cycle detected in augmented_from chain at chunk_id='chunk-A'"`. ✓

**Tip→root ordering** (lines 179–181):  
`current_row = initial_row` on entry; each iteration appends current before fetching parent; `lineage_chain[0]` is the requested chunk; `lineage_chain[-1]` is the root (`augmented_from is None`).  
Tests 1, 2, 9a all verify ordering and/or root-terminal `augmented_from=None`. ✓

---

### 4. Document_variant resolution

`routers/chunks.py` lines 535–542:
```python
dv_result = await session.execute(
    select(DocumentVariant)
    .where(DocumentVariant.source_id == root_source_id)
    .where(DocumentVariant.is_canonical.is_(True))
    .limit(1)
)
dv_orm = dv_result.scalar_one_or_none()
dv_read = DocumentVariantRead.model_validate(dv_orm) if dv_orm is not None else None
```

- Uses `lineage_chain[-1].source_id` (root chunk's source_id), per OQ-1 canonical-variant rule. ✓
- `.is_(True)` (SQLAlchemy boolean comparison, not `== True`). ✓
- `.limit(1)` consistent with `idx_doc_canonical` partial unique index (at most 1 canonical per source). ✓
- Returns `null` (not error) when no canonical variant exists. Test 11 covers this. ✓
- No cross-source variant resolution: filter is scoped to `root_source_id` only. ✓

---

### 5. Test coverage

All 14 tests from agreed.md §6 are present in `test_chunks_lineage.py` (numbered 1–14, with 9 split into 9a and 9b = 15 test functions total, matching commit message count).

| Test | Present | Meaningful |
|------|---------|------------|
| 1 non-augmented 200 | ✓ line 224 | Asserts len==1, augmented_from==None |
| 2 augmented 3-deep | ✓ line 256 | Asserts [C,B,A] ordering, tip/root values |
| 3 required top-level keys | ✓ line 298 | Asserts all 4 keys present |
| 4 source + dv fields | ✓ line 328 | Asserts source.id==99, dv.id==55 |
| 5 404 chunk not found | ✓ line 356 | Asserts 404 + "not found" |
| 6 401 no token | ✓ line 379 | No auth override → real 401 path |
| 7 400 Lance error | ✓ line 391 | side_effect=Exception → 400 + "Lance query error" |
| 8 cycle detection | ✓ line 414 | 3-call mock (A→B→A), 500 + "Cycle detected" |
| 9a 32-chain 200 | ✓ line 450 | 32 entries, root at [31], asserts len==32 |
| 9b depth cap 500 | ✓ line 492 | 33 distinct rows, real 33rd call, asserts detail string |
| 10 404 source missing | ✓ line 543 | Postgres returns None → 404 |
| 11 null document_variant | ✓ line 569 | dv_stub=None → 200 + body["document_variant"] is None |
| 12 quote escaping | ✓ line 598 | Asserts `.where("chunk_id = 'it''s'")` called once |
| 13 broken parent ref | ✓ line 629 | 2-call mock (A, empty) → 500 + "Broken augmented_from chain" |
| 14 null source_id | ✓ line 659 | row["source_id"]=None → 500 + "null source_id" |

No skipped tests.  No trivially-passing tests.  All assertions are on both status code and detail/body content where applicable. ✓

---

### 6. F-035 pattern regressions

- **Auth guard:** `Depends(get_current_user)` present at `routers/chunks.py` line 355.  Test 6 confirms 401 without token. ✓
- **FPath length cap 256:** `FPath(..., max_length=256)` at line 354. ✓
- **LanceQueryError → 400:** initial fetch (lines 419–425) and chain-step fetches (lines 476–482) both catch `LanceQueryError` and raise HTTP 400. ✓
- **404 on chunk not found:** initial fetch null-check at lines 427–431. ✓
- **`get_chunk_by_id` unchanged:** the F-035 handler at lines 553–594 is byte-for-byte unchanged by this diff (only import additions above it). ✓

---

## Findings

There are no blockers, no high-severity, no medium-severity issues.  All F1/F2/F3/F4 Mode A findings confirmed closed in code, not just in agreed.md.

**B1 — NIT:** `ChunkLineageResponse` marks `document_variant` as `required` in the OpenAPI schema (confirmed via `"required": ["chunk", "source", "document_variant", "lineage_chain"]`).  Per OAS 3.1, `required` means the key must be present; `anyOf [ref, null]` means the value may be null.  This is correct: the field is always serialised (even when `null`), so marking it `required` is accurate for a JSON:API consumer.  No change needed — noted here only for completeness.

---

## Verdict

**APPROVED**

Implementation faithfully matches `agreed.md` in every material detail.  All Mode A findings (F1 depth-cap ordering, F2 broken-parent test, F3 null-source-id test, F4 route-ordering clarity, NIT OQ-2 comment, NIT 9b detail-string assertion) are verifiably closed in the committed code and test suite.  All six hard invariants are satisfied.  Traversal logic, escaping, async discipline, cycle guard, and Postgres resolution are correct.  Test suite is complete and meaningful.  OpenAPI regenerated and committed in the same commit.  No regressions against F-035 patterns detected.
