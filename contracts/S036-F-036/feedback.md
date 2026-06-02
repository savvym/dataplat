# S036-F-036 — Reviewer Feedback (Mode A)

**Verdict: CHANGES_REQUESTED**

---

## F1 — HIGH: Off-by-one in depth-cap check causes HTTP 500 for a valid chain of exactly 32 entries

**What** (§4 implementation sketch, lines ~260–273):

```python
lineage_chain.append(ChunkLineageEntry(...))

# Depth cap check (AFTER appending the current entry).
if len(lineage_chain) >= _MAX_LINEAGE_DEPTH:   # fires when len == 32
    raise HTTPException(status_code=500, ...)

parent_id: str | None = current_row.get("augmented_from")
if parent_id is None:
    break  # root reached
```

**Why it matters**: The depth cap fires *before* the root-reached check. For a legitimate, non-cyclic chain of exactly 32 entries whose 32nd node is the root (`augmented_from = null`), the handler raises HTTP 500 instead of returning 200. The effective maximum supported depth is 31, not 32 — contrary to the stated intent in §5.4 ("if the chain grows to 32 entries *without reaching a null augmented_from*").

Test 9 uses a "chain of 33 distinct chunks", which triggers the cap on entry 32 with entry 33 still un-fetched — so the test passes for the wrong depth. A chain of exactly 32 entries whose last is root is not tested, and would incorrectly 500.

**Concrete fix**: Swap the ordering so the root check comes first:

```python
lineage_chain.append(ChunkLineageEntry(...))

parent_id: str | None = current_row.get("augmented_from")
if parent_id is None:
    break  # root reached — always succeeds even at depth 32

# Depth cap: only fire when we have NOT reached the root yet.
if len(lineage_chain) >= _MAX_LINEAGE_DEPTH:
    raise HTTPException(status_code=500, ...)

# ... fetch parent_row
```

Also update test 9 to supply exactly 32 distinct chunks (31 augmented + 1 root) to assert 200 is returned, *and* a chain of 33+ to assert 500 — covering both sides of the boundary.

---

## F2 — MEDIUM: No test for §5.9 broken `augmented_from` reference

**What** (§6 test table): The 12-test plan covers cycle, depth cap, 404 chunk not found, and 400 Lance error, but does **not** include a test for the case where a non-null `augmented_from` value resolves to a missing Lance row (§5.9 — "broken chain").

**Why it matters**: This is a distinct 500 branch in the handler:

```python
if parent_row is None:
    raise HTTPException(status_code=500, detail=f"Broken augmented_from chain: ...")
```

Unlike the 404-chunk-not-found case (initial lookup fails), this branch fires mid-traversal when a parent referenced by `augmented_from` simply does not exist in Lance. It is reachable in practice when an augmenter writes a child row but the parent was subsequently compacted/deleted. Without a test, a refactor could silently drop this branch or change the status code to 404.

**Concrete fix**: Add test 13:
```python
def test_lineage_500_broken_augmented_from_chain():
    """Chunk A.augmented_from = 'B', but 'B' does not exist → 500."""
    # Lance mock: first call returns chunk A (augmented_from='B'),
    #             second call returns None (B not found).
    ...
    assert resp.status_code == 500
    assert "Broken augmented_from chain" in resp.json()["detail"]
```

---

## F3 — MEDIUM: No test for §5.5 null `source_id` on root chunk

**What** (§6 test table): Edge case §5.5 — root chunk has `source_id = null` → HTTP 500 — has no corresponding test entry.

**Why it matters**: The handler raises an explicit 500 here:

```python
if root_source_id is None:
    raise HTTPException(status_code=500, detail=f"Root chunk ... has null source_id ...")
```

This is a data-integrity sentinel whose status code (500 vs 422 vs 404) is a deliberate design choice. Without a test this branch is invisible to CI and the status code could drift.

**Concrete fix**: Add test 14:
```python
def test_lineage_500_null_source_id_on_root_chunk():
    """Root chunk has source_id=None → 500."""
    # Lance mock: non-augmented chunk with source_id=None.
    ...
    assert resp.status_code == 500
    assert "null source_id" in resp.json()["detail"]
```

---

## F4 — NIT: OQ-4 route-ordering concern is overstated; agreed.md wording should reflect reality

**What** (§9, OQ-4): The proposal warns that `GET /{chunk_id}/lineage` "must register before `GET /{chunk_id}` catch-all" because FastAPI matches in registration order.

**Why it matters (or doesn't)**: `/{chunk_id}/lineage` (two path segments after the prefix) and `/{chunk_id}` (one path segment) differ in segment count. Starlette/FastAPI path-matching is structural first — it will never confuse a two-segment path for a one-segment path regardless of registration order. The actual routing ambiguity would only arise if both routes had the *same* segment count (e.g. `/lineage` vs `/{chunk_id}` at the same level under the prefix — that is not the case here).

**Concrete fix**: Reword OQ-4 / the agreed.md note to: "`GET /{chunk_id}/lineage` is structurally unambiguous (two segments vs one segment); route ordering relative to `GET /{chunk_id}` is not a concern. However, for clarity the `get_chunk_lineage` handler should still be defined first in source order." This prevents the implementer wasting time on a non-problem and avoids the confusion of cargo-culting the wrong invariant.

---

## Summary — what to fix to flip to APPROVED

1. **F1 (HIGH, required):** Fix the depth-cap check ordering so the `if parent_id is None: break` executes before `if len(lineage_chain) >= _MAX_LINEAGE_DEPTH`. Update test 9 to assert both the valid-boundary case (chain of 32, last is root → 200) and the overflow case (chain of 33+ → 500).

2. **F2 (MEDIUM, required):** Add test 13 for the broken `augmented_from` parent-not-found branch → HTTP 500.

3. **F3 (MEDIUM, required):** Add test 14 for the null `source_id` on root chunk → HTTP 500.

4. **F4 (NIT, optional):** Correct the OQ-4 / agreed.md wording to accurately describe that the route-ordering concern does not apply to this particular pattern.

Everything else in the proposal is solid:
- All `ChunkLineageEntry` fields match the actual `CHUNKS_SCHEMA` columns (`augmented_from`, `source_id`, `producer_asset`, `producer_version`, `augmenter_id`, `augmenter_config_hash` are all `pa.string()` or `pa.int64()` scalars — no structs, no lists).
- The canonical-variant resolution rule for `document_variant` is correct given the absence of a direct FK from chunks to variants.
- Async SQLAlchemy usage is consistent with the codebase pattern (`get_session`, `await session.execute(select(...))`, `scalar_one_or_none()` — no sync sessions).
- `make codegen` + committed diff in the same change is explicitly called out (invariant #6 ✓).
- Single-quote escaping is applied consistently inside `_fetch_chunk` on every call.
- Both V1 and V2 verification criteria are mapped to named tests.

---

## Mode A re-review (round 2)

**Verdict: APPROVED**

All four findings from round 1 are closed. Detailed confirmation below.

---

### F1 — Depth-cap ordering: CLOSED ✓

**Trace — chain of exactly 32 entries, entry #32 is root (`augmented_from=None`):**

`range(32)` yields _depth=0…31 (32 iterations).

- _depth=0 through _depth=30: append entry, `parent_id is None` → False, fetch parent, loop continues.
- _depth=31 (32nd iteration): append entry 32, then immediately evaluate `parent_id = current_row.get("augmented_from")` → **None** → `break` fires. The `for…else` clause does **not** execute. Returns HTTP 200. ✓

**Trace — chain of 33+ entries, no root within first 32:**

- _depth=0 through _depth=31: all 32 entries have non-null `augmented_from`. After appending entry 32 at _depth=31, `parent_id is None` → False, parent is fetched, `current_row` advances. `range(32)` is now exhausted → `else` clause fires → HTTP 500 cap-exceeded. ✓

The root check (`if parent_id is None: break`) now sits **before** any depth-cap logic within the loop body, and the for…else pattern correctly handles the overflow case. Both boundary invariants hold. Section §5.4 commentary matches the code.

Tests 9a and 9b split the old single test 9 to cover both sides of the boundary explicitly. ✓

---

### F2 — Test 13 (broken augmented_from chain): CLOSED ✓

Test 13 (`test_lineage_500_broken_augmented_from_chain`) is present in the test table (§6, row 13). It mocks Lance so that the first `_fetch_chunk` call returns chunk A with `augmented_from='B'`, and the second call (for B) returns `None`. The assertion is `status_code == 500` and `"Broken augmented_from chain" in detail`. This matches the exact handler branch at lines 278–287 of the sketch. ✓

---

### F3 — Test 14 (null source_id on root): CLOSED ✓

Test 14 (`test_lineage_500_null_source_id_on_root_chunk`) is present (§6, row 14). It mocks a non-augmented root chunk (so `lineage_chain[-1]` is the only entry) with `source_id=None`. The assertion is `status_code == 500` and `"null source_id" in detail`. This exercises the guard at lines 306–313 of the sketch. The test correctly targets the root (last chain entry), not a mid-chain entry. ✓

---

### F4 — OQ-4 route-ordering: CLOSED ✓

OQ-4 has been rewritten as a non-issue. The revised text (§9, OQ-4) correctly states that `/{chunk_id}/lineage` (two path segments) and `/{chunk_id}` (one path segment) differ in structural segment count and that Starlette/FastAPI will never confuse them regardless of registration order. The note that defining `get_chunk_lineage` first is a style preference only — not a correctness requirement — is accurate and prevents cargo-culting the wrong invariant. ✓

---

### Re-confirmation of other items (no regressions)

- **Pydantic shapes:** `ChunkLineageEntry` (7 fields) and `ChunkLineageResponse` (4 top-level fields) are unchanged and correct; all types remain scalar-compatible with `CHUNKS_SCHEMA`.
- **Single-quote escaping:** `_fetch_chunk` applies `.replace("'", "''")` inside the helper before every DataFusion predicate; used for both the initial fetch and all chain-step fetches. ✓
- **Async SQLAlchemy:** all Postgres reads use `await session.execute(select(...))` + `scalar_one_or_none()`; no `session.query()`, no sync sessions. ✓
- **codegen / invariant #6:** §7 explicitly requires `make codegen` and the diff committed in the same Git commit. ✓
- **tip→root ordering:** index 0 is the requested chunk; final index is the root (`augmented_from=None`). Stated throughout §2.2.4 and §5.1/5.2. ✓
- **canonical-variant rule:** `is_canonical = true` filter with `.limit(1)` on `DocumentVariant`; leverages `idx_doc_canonical` partial unique index; returns `null` (not error) when absent. ✓

---

**NITs (non-blocking):**

1. The `_fetch_chunk` helper selects all 24 columns for every chain step even though `ChunkLineageEntry` only needs 7. This is noted as an accepted MVP trade-off in OQ-2 — no action required, but the implementer should keep the comment so it surfaces as a natural post-MVP optimisation.
2. Test 9b description says "chain of 33+ distinct chunks (no cycle, no null `augmented_from` within the first 32 iterations)". The mock will need to return a valid (non-None) row 33 for `current_row` to advance to the `else` — make sure the mock side-effects list at least 33 entries or returns a non-None value for the 33rd fetch; otherwise the `parent_row is None` branch (→ 500 broken chain) fires before the `for…else` branch. Both branches yield 500, so the test assertion on status code still passes, but the `detail` message would be wrong. Implementer should assert the cap-exceeded detail string specifically to pin the right branch.

