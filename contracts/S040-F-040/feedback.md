# S040-F-040 Mode A Review — feedback.md

## Round 2

## APPROVED

---

## Resolution check

**F1 — HIGH — `_UNSET` sentinel → `model_fields_set`**
**RESOLVED.**
§3 model definition (lines 56-78) fully replaces the `_UNSET` sentinel with `description: str | None = None` and explicitly documents the `model_fields_set` guard. The docstring calls out "no non-serializable sentinel, no PydanticJsonSchemaWarning, clean `default: null` in the OpenAPI schema." §4 step 4 (line 126) shows the handler guard: `if "description" in body.model_fields_set: recipe.description = body.description`. The semantics table (lines 82-86) correctly covers all three caller cases. The `test_update_recipe_description_unchanged_when_omitted` test description (§7 test list) confirms the guard is `"description" not in body.model_fields_set`. F1 is completely addressed.

**F2 — MEDIUM — V2 timestamp test must use a fixed historical constant**
**RESOLVED.**
§7 "V2 timestamp test strategy — fixed historical constant (F2)" (lines 188-205) mandates a module-level `_PAST = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)` constant. The mock recipe is set to `row.updated_at = _PAST`. The assertion `returned_updated_at > _PAST` will never flake for any test run in 2026 or later. The `_make_session_dep_for_update` pseudocode (lines 237 onward) echoes the same `_PAST` constant. F2 is completely addressed.

**F3 — MEDIUM — `func.now()` rationale corrected**
**RESOLVED.**
§6 (lines 162-174) now accurately states: "`func.now()` is technically valid in SQLAlchemy 2.0 — the ORM will render it in the `UPDATE SET` clause as a server-side SQL call … but it produces a SQL expression that requires a DB round-trip to resolve, complicating unit tests that do not hit a real database." The previously false claim ("SQLAlchemy will not call `now()` as a SQL function in that context") is gone. §4 step 5 (line 134) also uses the corrected framing: "technically valid but complicates unit tests." OQ3 in §11 (lines 317-319) uses the same accurate language. F3 is completely addressed.

**F4 — LOW — `exists()` instead of `count(*)`**
**RESOLVED.**
§4 step 3 (lines 118-122) now uses `select(exists().where(Dataset.recipe_id == recipe.id))` + `scalar_one()` and explicitly notes it "short-circuits at the first matching row — correct and more efficient than `count(*)` for a binary question." §10 Invariant #3 row (line 303) echoes the same formulation. F4 is completely addressed.

**F5 — LOW — `packages/api-types/` scope in Files Changed table**
**RESOLVED.**
§2 Files Changed table (line 42) now reads `packages/api-types/` (directory, not a single file). §9 (line 284) confirms `make codegen` regenerates the full directory including TypeScript type definitions and the JSON spec, and §1 (line 29) and §2 both refer to the full `packages/api-types/` directory. F5 is completely addressed.

**F6 — LOW — Freeze-leak observation for F-042 reviewer**
**RESOLVED.**
§13 (lines 351-353) adds a dedicated "Freeze-guard leak if `dataset.recipe_id` is ever null (F6)" risk note. It correctly identifies that `recipe_id` is `nullable=True` in the ORM, explains the failure mode, scopes the fix explicitly to F-042 ("flag for the F-042 (dataset commit) reviewer to mandate `recipe_id IS NOT NULL`"), and defers it as out of scope for F-040. F6 is completely addressed.

**F7 — NIT — 409 test must assert the exact detail string**
**RESOLVED.**
§7 test list row for `test_update_recipe_dataset_exists_returns_409` (line 223) now reads: "`response.json() == {"detail": "Recipe is locked: a dataset has been materialized from it"}` (exact string)". Note: the exact detail string in the test list ("Recipe is locked: a dataset has been materialized from it") is slightly shorter than the long form mentioned in the round-1 feedback ("…and its definition cannot be changed"), but it is consistent with what the handler actually raises at §4 step 3 (line 122: `detail="Recipe is locked: a dataset has been materialized from it"`). The implementer has normalised on the shorter string across both the handler and the test — exact-string assertion is mandated and the strings are self-consistent. F7 is completely addressed.

**F8 — NIT — `summary=` kwarg omitted for symmetry**
**RESOLVED.**
§1 code-level changes (line 27) states "no `summary=` kwarg, for symmetry with the other three handlers." §4 handler preamble (line 108) restates: "The `@router.put("/{id}")` decorator does **not** include a `summary=` kwarg, keeping it consistent with the other three recipe handlers (F8 resolved)." F8 is completely addressed.

---

## New findings

None. The revision addresses all eight findings cleanly and introduces no new structural, correctness, or invariant issues. The description-update semantics are internally self-consistent (§3 semantics table, §4 step 4, §7 test names all agree). The mock session helper (§7) correctly distinguishes the two-execute-call shape for 200-path tests vs the single-execute-call shape for 404 tests. Invariant compliance table (§10) is up to date. No new blockers.

---

## OQ resolutions

**OQ1 — 409 vs 422 for locked recipe:** Confirmed ACCEPTED in §5 and §11. The OQ1 note at the foot of §5 (line 157) reads "ACCEPTED by reviewer." No change.

**OQ2 — `description` updatable:** Confirmed ACCEPTED in §3 and §11. The `model_fields_set` design cleanly supports the full omit/null/string matrix documented in §3. No change.

**OQ3 — App-side UTC vs `func.now()`:** Confirmed ACCEPTED with accurate rationale in §6 and §11. The corrected rationale (testability, not impossibility) is present throughout. No change.

**OQ4 — `recipe_snapshot` not retroactively updated:** Confirmed ACCEPTED in §11 (line 321-322). Correct — immutable lineage record. No change.

---

## Summary

All eight round-1 findings are fully resolved in the revised contract. The critical F1 sentinel design is replaced with the canonical Pydantic v2 `model_fields_set` idiom with no behavioral regression. The V2 timestamp test is locked to the fixed `_PAST = datetime(2026, 1, 1, …)` constant, eliminating any flakiness risk. The `func.now()` rationale is now factually accurate. The dataset-freeze check correctly uses `exists()`. The Files Changed table now scopes `packages/api-types/` as a directory. The freeze-leak observation is properly forwarded to the F-042 reviewer. The 409 test asserts the exact detail string. The `summary=` kwarg is consistently absent across all four recipe handlers. No new issues were introduced by the revision. The contract is internally consistent, satisfies all six hard invariants, and the 12-test plan covers all verification criteria plus structural SQL assertions. The implementer may proceed.

**APPROVED**
