# S021-F-021 — Reviewer Feedback (Mode A, Round 2)

**Reviewer:** reviewer (Claude)
**Date:** 2026-05-26
**Verdict:** APPROVED

---

## Re-review summary

This is a re-review after the implementer addressed all five items raised in
Round 1 (two blocking, one required, two advisory). Each item has been
verified below by direct inspection of the updated `proposed.md`.

---

## Verification of Round-1 items

### ✅ Item 1 (was BLOCKING) — `$PLATFORM_DB_URL` → docker compose exec

All six psql call-sites (V2 COUNT, V2 extractor_name, V3a pg_indexes, V3b
INSERT probe, V3b UPDATE attempt, V3b DELETE cleanup) now use:

```bash
docker compose -f "$COMPOSE" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" …
```

Zero occurrences of `$PLATFORM_DB_URL` remain. The `-tAc` / bare `-c`
distinction is also correct: `-tAc` for queries whose output is piped to
`grep -q`, and `-c` (no tuple-mode) for the V3b UPDATE where stderr is
captured via `2>&1`.

**RESOLVED.**

---

### ✅ Item 2 (was BLOCKING) — V3b error detection via grep, not exit-code

The V3b UPDATE attempt block is now:

```bash
V3B_OUT=$(docker compose -f "$COMPOSE" exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -c \
    "UPDATE document_variant SET is_canonical=TRUE
     WHERE extractor_name='probe' AND source_id=$DOC_SRC_ID" \
  2>&1 || true)
echo "$V3B_OUT" | grep -qi "ERROR" \
  || { echo "FAIL: V3b — unique constraint was NOT violated; $V3B_OUT"; exit 1; }
```

This matches the pattern prescribed in Round 1 exactly. The inline note
("psql exits 0 on SQL errors by default; we detect the error via
`grep -qi "ERROR"`") makes the rationale explicit for future maintainers.

**RESOLVED.**

---

### ✅ Item 3 (was REQUIRED) — §8 closed: `session.refresh()` required

§8 has been renamed to "Closed Questions (Resolved)" and item 1 now reads:

> **`session.refresh()` after commit: DECIDED — required, not optional.**
> After `await session.commit()`, SQLAlchemy expires all ORM attributes
> (`expire_on_commit=True` default). On `AsyncSession`, accessing an expired
> attribute without an awaited load raises `MissingGreenlet` at runtime.
> `DocumentVariantRead.model_validate(target)` would trigger this on every
> field. `await session.refresh(target)` is the only correct approach.

The decision is unambiguous and the technical reasoning is complete. The
`await session.refresh(target)` call is also present in the §4 pseudocode
(line after `await session.commit()`), so the pseudocode and the decision
table are consistent.

**RESOLVED.**

---

### ✅ Item 4 (was ADVISORY) — `synchronize_session=False` on UPDATE calls

The Atomicity row in §3 now reads:

> Both UPDATEs use `.execution_options(synchronize_session=False)` since
> `refresh()` provides the authoritative post-commit state. … `synchronize_session=False`
> prevents fragile in-memory state manipulation.

This is the correct placement: the design decisions table is the authoritative
source for implementation-level options; pseudocode in §4 is intentionally
higher-level. An implementer reading §3 and §4 together will apply it.

**RESOLVED.**

---

### ✅ Item 5 (was ADVISORY) — `update` import explicitly called out in §2

§2, row 1 now contains:

> Add `update` to the `from sqlalchemy import func, or_, select` line →
> `from sqlalchemy import func, or_, select, update`.

Exact before/after is specified. No ambiguity for the implementer.

**RESOLVED.**

---

## Positive confirmations (carried forward from Round 1 — all still hold)

| Check | Result |
|---|---|
| Endpoint path `POST /{source_id}/documents/{extractor_name}/set-canonical` matches spec | ✅ |
| CLEAR-first atomicity prevents transient `idx_doc_canonical` violation | ✅ |
| CLEAR scope is `WHERE source_id=X AND is_canonical=TRUE` (not restricted to extractor_name) | ✅ |
| Idempotency: CLEAR sets target to FALSE, SET restores to TRUE; no transient double-TRUE | ✅ |
| HTTP 200 response satisfies V1 (`DocumentVariantRead` is a strict superset) | ✅ |
| V2 DB-level check: `grep -q '^1$'` on COUNT + `grep -q '^mineru$'` on extractor_name | ✅ |
| V3a index-existence check (pg_indexes query + `grep -q 'is_canonical'`) | ✅ |
| Owner-scoping 2-join LEFT JOIN consistent with F-020/F-013 | ✅ |
| Two distinct 404 messages ("Source not found" / "Variant not found") | ✅ |
| No path collision between 4-segment POST and 1-segment GET catch-all | ✅ |
| `make codegen` + same-commit `packages/api-types/` requirement (invariant #6) | ✅ |
| No LLM calls, no migration, no sync sessions (invariants #4, #5) | ✅ |
| `session.execute(update(...))` (core API) — not `session.query()` | ✅ |
| `target = highest id WHERE extractor_name=X` tie-breaking | ✅ |
| §8 items 2 and 3 (index predicate form, `source_id` absent from response) are appropriately noted | ✅ |

---

## Minor observation (no action required)

The §4 pseudocode intentionally omits `.execution_options(synchronize_session=False)`
from the UPDATE expressions — this is consistent with pseudocode style throughout
the document. Since §3 mandates it unambiguously ("Both UPDATEs use
`.execution_options(synchronize_session=False)`"), the implementer has full
guidance. No change needed.

---

## Overall assessment

All Round-1 blocking and required items are resolved. All advisory items are
addressed. The design is internally consistent, the verification plan is
executable, all six hard invariants are correctly dispositioned, and the
integration shell snippets are now safe to paste into `checks.sh`.

**This contract is ready to proceed to `agreed.md` and then to implementation.**

---

**APPROVED**
