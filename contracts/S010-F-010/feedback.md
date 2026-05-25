# S010-F-010 — Contract Review (Mode A, Iteration 2)

**Reviewer:** Independent reviewer (Claude)
**Date:** 2026-05-25
**Contract reviewed:** `contracts/S010-F-010/proposed.md` (post-iter-1 edits)
**Prior verdict:** CHANGES_REQUESTED (iter 1, two required changes + two NITs)

---

## Required-reading sweep (iter 2)

Full re-read of `contracts/S010-F-010/proposed.md`. Cross-checked against prior iter-1 findings and verified no new problems were introduced by the edits.

---

## Calibration Sweep

CAL-1 through CAL-11 outcomes are unchanged from iter 1 — the edits do not touch any area that would flip a prior PASS/N/A. Abbreviated here with only the change-relevant items re-confirmed:

- **CAL-1 (async session):** PASS — unchanged, §3 D6 and §7 still mandate `await session.execute`.
- **CAL-3 (OpenAPI sync):** PASS — unchanged, §7 mandates same-commit regen with the verified precedent command.
- **CAL-8 (MVP scope):** PASS — no new scope introduced by the edits.
- **CAL-10 (test coverage):** PASS — 12 tests unchanged, owner-filter test now has a concrete assertion prescription.
- **CAL-11 (bias check):** Actively suppressed — see finding sweep below.

---

## Iter-1 Finding Resolution

### HIGH #2 — LIST-V1 tautological assertion — RESOLVED

The vacuous `len(items) <= total` assertion has been removed. In its place (§5, lines 224-225):

```python
assert body['total'] == len(body['items']), \
  f'with no limit param, total should equal items count; got total={body["total"]}, items={len(body["items"])}'
```

This is correct. Verification: a broken implementation where `total = len(items)` (page-size confusion) would make this assertion pass when no limit is applied — because both sides would be equal. However, the contract correctly documents this limitation in the new prose block (§5, lines 250-251) and notes that LIST-V2's `len(items)==2, total>=3` check is what independently exposes that class of bug. The combination is sound: LIST-V2 will FAIL for any implementation that sets `total = len(items)` when limit=2 (since `total` would be 2, not >= 3). Confirmed by logic analysis.

### MEDIUM #3 — `test_list_collections_owner_filter` vague assertion — RESOLVED

§6 now prescribes the exact pattern (line 271):
> `first_stmt = session_mock.execute.call_args_list[0].args[0]`; `compiled = str(first_stmt.compile(compile_kwargs={"literal_binds": True}))`; `assert "owner_id" in compiled`; `assert str(_MOCK_USER.id) in compiled`

This is concrete and implementable. The warning against stringifying the raw Select is present. No ambiguity remains.

### NIT #5 — MagicMock vs AsyncMock for result proxies — RESOLVED

§6 (lines 262-263) now explicitly states: "The two `side_effect` items (page_result_mock, count_result_mock) MUST be plain `MagicMock`, NOT `AsyncMock`." The rationale is correctly given. This removes the subtle trap.

### LOW #4 — OQ-1 limit cap — RESOLVED in §4

§4 correctly marks OQ-1 CLOSED with the decision `le=200 accepted`. No change needed.

---

## New Issues Introduced by the Edits

### NIT-A: §9 recap table still shows OQ-1 as open

**Location:** `contracts/S010-F-010/proposed.md` line 342.

The §9 recap table still reads: `OQ-1 | limit upper bound: 200 or 100? | Either works; 200 is proposed. Reviewer to confirm or adjust.`

§4 correctly marks OQ-1 CLOSED, but §9 was not updated to reflect this. This is a documentation inconsistency — an implementer reading only §9 would think the limit cap is still unresolved. Not a blocker; the §4 decision is authoritative. The implementer should update the §9 row to read "CLOSED — le=200 accepted" before this becomes `agreed.md`.

This is the only new issue introduced by the iter-2 edits. It is cosmetic and does not affect correctness.

---

## Hard Invariant Compliance (unchanged)

All six invariants verified against the updated contract — no change from iter 1. All PASS or N/A.

---

## Summary

All iter-1 CHANGES_REQUESTED items (HIGH #2, MEDIUM #3, NIT #5, LOW #4) are resolved correctly. No new BLOCKER or HIGH issues introduced. One NIT (§9 OQ-1 stale row) does not require a further review round — the implementer should fix it when promoting this to `agreed.md`.

The implementation plan is sound: two-query async handler, owner filter on both queries, deterministic ORDER BY id ASC, schema narrowing with OpenAPI regen, 12 unit tests with concrete mock shapes and a prescriptive owner-filter assertion, integration checks that correctly distinguish `total` from `len(items)` via the V2 step.

APPROVED
