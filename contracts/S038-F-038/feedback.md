# Reviewer Feedback — S038-F-038: List Recipes (GET /api/recipes)

**Verdict: APPROVED**

The proposal is correct, complete, and aligned with the F-038 spec, project invariants,
and the codebase patterns established by F-010/F-037. No blockers. Three minor findings
and answers to all three open questions are given below.

---

## Findings

### F1 — MEDIUM — Pagination omission creates codebase inconsistency (OQ-1 resolution)

**Location:** §3.1, OQ-1

Every other user-scoped list endpoint in this codebase (`list_collections`, `list_sources_by_collection`)
accepts `limit`/`offset` query params with `Query(default=20, ge=1, le=200)`.
Omitting them from `GET /api/recipes` creates a visible divergence in the API surface.

**Decision: Option A (no pagination) is ACCEPTABLE for MVP** under these conditions:
- The spec text says "returns _all_ recipes owned by the authenticated user" — "all" is explicit.
- The verification criterion only checks 2 records; no `?limit=` test is prescribed.
- Recipe counts per user are expected to be small (single digits to low tens) in MVP usage.
- Adding optional `limit`/`offset` params later is non-breaking (no existing callers to migrate).

**If the implementer wants to match codebase style exactly, Option B is also acceptable**
and the test plan already accounts for it ("if Option B is chosen … add a
`test_list_recipes_pagination` test case"). The choice must be documented in the
implementation commit message.

This is MEDIUM, not a BLOCKER. APPROVED either way.

---

### F2 — LOW — No SQL-structure inspection test for owner_id filter

**Location:** §6, `test_list_recipes_only_own_recipes`

The prior list-endpoint test (`test_list_collections_owner_filter` in
`test_sources_collections_list.py`) captures the first `session.execute()` call and
compiles the `Select` statement with `literal_binds=True` to confirm that `owner_id`
and the user's id literal appear in the emitted SQL. The proposed cross-user test
`test_list_recipes_only_own_recipes` is purely behavioural — it pre-loads two separate
session mocks and confirms the handler returns whatever the session provides. That
proves pass-through correctness but does NOT prove the emitted WHERE clause is actually
scoped to `current_user.id`.

**Required action:** Add a `test_list_recipes_owner_id_in_query` test (mirrors
`test_list_collections_owner_filter`) that captures the session mock's
`execute.call_args_list[0].args[0]`, compiles it with `literal_binds=True`, and asserts
both `"owner_id"` and the mock user's id appear in the compiled string.

This is LOW (not a BLOCKER) because the SQL is trivially simple and any mistake would
be caught by integration tests, but the prior pattern should be maintained for
structural assurance. Implementer SHOULD add this test before committing.

---

### F3 — NIT — File-change list mentions only `openapi.json`; should be `packages/api-types/`

**Location:** §4, File-by-File Change List, last row

The table entry reads:

> `packages/api-types/openapi.json` | **Regen** | …

`make codegen` regenerates the entire `packages/api-types/` directory — it produces
TypeScript type definitions (`.ts` files) alongside `openapi.json`. The committed diff
must cover all changed files under `packages/api-types/`, not just the JSON spec.
(This is what CLAUDE.md invariant #6 requires: "the resulting `packages/api-types/`
diff committed in the SAME commit".)

The prose in §9 correctly says "make codegen must be run" and is fine; this is
purely a precision issue in the file-list table. No code change needed — just
ensure the commit includes all codegen output, not only `openapi.json`.

---

### F4 — NIT — Note on mock row population

**Location:** §6, Notes, third bullet

The note says mocked rows must "populate all 7 ORM-mapped attributes (including
`definition`) to satisfy `model_validate`". This is slightly imprecise: Pydantic's
`from_attributes=True` only reads fields declared on `RecipeListItem` (the 5 slim
fields), so `definition` and `owner_id` are simply never accessed. The real reason
to populate them is to avoid `MagicMock` attribute-access surprises if the code path
changes. The note's intent is correct; the reasoning should just be "for completeness /
future-proofing", not "required by `model_validate`".

No code impact. Mention in implementation comments if desired.

---

## Open Questions — Decisions

**OQ-1: Pagination?**
→ **Option A (no pagination) for MVP.** Rationale above (F1). No change to the
SQL plan. If the implementer chooses Option B, they must add `test_list_recipes_pagination`.

**OQ-2: Include `definition` in list items?**
→ **No.** The slim `RecipeListItem` schema is correct. `definition` can be an
arbitrarily large JSONB blob. It belongs on the detail endpoint (F-039). This mirrors
the sources pattern (list vs. detail). `RecipeOut` stays intact for the POST 201 response.

**OQ-3: Ordering — `created_at DESC` vs `id ASC`?**
→ **`created_at DESC, id DESC` (proposal's choice) is accepted.** Newest-first is
natural for a "browse my recipes" UI. The `id ASC` ordering in `list_collections` was
chosen for stable pagination (oldest first across pages). Since F-038 has no pagination
(OQ-1), the stability argument is weaker. Tie-breaking on `id DESC` is correct;
`created_at` has `server_default=now()` and is never null in practice.

---

## Quick Checklist Recap

| Item | Status |
|---|---|
| Spec V1 covered (2 items → total==2) | ✅ |
| Spec V2 covered (5 required fields per item) | ✅ |
| Invariant #5 async SQLAlchemy (no `session.query()`) | ✅ |
| Invariant #6 `make codegen` in same commit | ✅ (NIT on scope) |
| Owner-scoped query (`owner_id == current_user.id`) | ✅ |
| Cross-user isolation test present | ✅ (LOW: SQL structural test missing) |
| Auth-gate test (no token → 401) present | ✅ |
| Empty list → 200 `{items:[], total:0}` test present | ✅ |
| Slim schema guards (`definition`, `owner_id` absent) | ✅ |
| No migration, no ORM model change needed | ✅ |
| `main.py` correctly excluded (router already wired) | ✅ |
| Pagination decision documented | ✅ (decided above) |

---

The proposal may proceed to implementation. Address F2 (add SQL-inspection test) and
confirm the commit includes the full `packages/api-types/` diff, not just `openapi.json`.

