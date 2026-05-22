# S007-F-007 Mode A Review — Iter 3

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Artifact under review:** `contracts/S007-F-007/proposed.md` (Iter 3)

---

## Verdict: APPROVED

The single iter-2 BLOCKER (B-1: `make codegen` fires against missing Makefile once
`packages/api-types/` is created) is correctly and completely resolved.

---

## B-1 fix verification

**§5 `contract)` case snippet (lines 381–389):** The three-line block is present with
correct order:
1. `exists packages/api-types || { ... exit 0; }` — existing directory guard (line 384)
2. `[[ -f Makefile ]] || { echo "no Makefile yet (codegen deferred to web sprint)"; exit 0; }` — new Makefile guard (line 385), inserted AFTER the directory check and BEFORE `run "make codegen"` (line 386)
3. `;;` closes the case correctly (line 388)

The guard placement is exactly right. `make codegen` is only reached if both
`packages/api-types/` exists AND a `Makefile` is present.

**§3 `verify/checks.sh` row (line 105):** The purpose text covers both the `auth)` addition
AND the `contract)` guard: "Add `auth)` case with V1/V2/V3 checks; insert `bash "$0" auth`
into `all)` block between `migration` and `buckets`; add `Makefile`-existence guard to
`contract)` case to keep it inert until codegen is wired in a future web sprint." Complete.

**§6 invariant #6 (line 432):** The cell explicitly names the guard line, cross-references
§5, explains why it is needed (directory now present, Makefile not yet present), and describes
the forward path (web sprint scaffolds Makefile, guard releases, openapi.json becomes codegen
input). Honest and mechanically sound.

**No regressions:** §1–§5 (other than the new §5 subsection), §6 rows 1–5, §7–§9 are
byte-for-byte identical to iter-2-approved content.

---

## Calibration checks (spot-check, iter 3 scope)

- CAL-1 (async session): PASS — unchanged from iter 2; seed CLI uses `asyncio.run()` +
  `SessionLocal()`; endpoint uses `get_session` async generator.
- CAL-3 (OpenAPI sync): PASS — `packages/api-types/openapi.json` committed as binding
  partial deliverable; `contract)` guard prevents CI breakage; deferral documented.
- CAL-8 (MVP scope): PASS — no scope-discipline violations. Unchanged from iter 2.
- CAL-10 (test coverage): PASS — 7 named tests; `session.add()` never-called assertion
  explicit in `test_seed_admin_idempotent`. Unchanged from iter 2.
- CAL-11 (bias check): Approval is concrete — each check above cites file and line number.
  No vague sign-off.

---

## Non-blocking implementation notes (carry into post-agreed.md guidance)

1. **`_DUMMY_HASH` rounds consistency:** `bcrypt.gensalt()` in the dummy-hash line uses
   the default rounds (12 in bcrypt 4.x) rather than the explicit `rounds=12` used in
   `gensalt(rounds=12)` for real hashes. Functionally identical for bcrypt 4.x; consider
   making it explicit (`bcrypt.gensalt(rounds=12)`) for code-readability consistency.

2. **Mock session chain for `test_seed_admin_idempotent`:** The mock `execute()` must
   support `.scalars().first()` returning a `User` instance. The standard setup is:
   `mock_result = MagicMock(); mock_result.scalars.return_value.first.return_value = mock_user`.
   The contract describes the intent correctly; this is a reminder for the implementer.

3. **`pyproject.toml` marker registration:** The `@pytest.mark.integration` marker on
   `test_seed_admin_creates_one_row` should be registered in `[tool.pytest.ini_options]`
   `markers` in `apps/api/pyproject.toml` to suppress pytest's `PytestUnknownMarkWarning`.
   Example: `markers = ["integration: marks tests requiring a live database"]`.

4. **`packages/api-types/openapi.json` generation timing:** The generation command must be
   run AFTER `main.py` includes `auth_router` (so the `/api/auth/token` route appears in the
   spec) and with the dev stack's `SECRET_KEY` set in the environment (so `config.Settings()`
   constructs without error during the import). The simplest approach: run it from inside the
   `fastapi` container via `docker compose exec`.

5. **`claude-progress.txt` deferral note:** §6 invariant #6 requires a `claude-progress.txt`
   entry noting the TS codegen deferral. Suggested wording: "S007-F-007: packages/api-types/
   openapi.json committed; full TS codegen deferred to web sprint (no Makefile yet;
   contract) layer guarded with Makefile existence check)."

---

APPROVED
