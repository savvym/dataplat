# Reviewer Mode A — S048-F-048 Feedback (Round 2)

**Reviewer**: reviewer-modeA-r2
**Date**: 2026-06-04
**Sprint**: S048-F-048 — `GET /api/runs/{id}` run status endpoint
**Input revision**: proposed.md Rev 2 (with §14 Round-1 Addenda)
**Prior verdict**: CHANGES_REQUESTED (Round 1 — 1 MEDIUM, 1 LOW, 2 NIT)

---

## Round-2 Finding Resolution

### M1 — RESOLVED ✓

**Finding**: §4 file table omitted `apps/api/tests/test_runs_hello_world.py` and `verify/checks.sh`.

**Rev 2 response** (§4, lines 136–137): Both files are now present in the §4 table with exact detail:
- `apps/api/tests/test_runs_hello_world.py`: **edit** — "Update three `client.get(f"/api/runs/{fake_run_id}")` calls (lines 110, 132, 148) to `client.get(f"/api/runs/dagster/{fake_run_id}")`."
- `verify/checks.sh`: **edit** — "Update `GET /api/runs/${RUN_ID}` at line 455 to `GET /api/runs/dagster/${RUN_ID}` (runs-layer smoke test polls Dagster-proxy by UUID string, not Postgres int)."

**Spot-check against source files**:
- `test_runs_hello_world.py` lines 110, 132, 148: all three call `client.get(f"/api/runs/{fake_run_id}")` with a UUID string ✓ (exact match to §4 claim)
- `verify/checks.sh` line 455: `RESP=$(curl -sS "http://localhost:${FASTAPI_HOST_PORT}/api/runs/${RUN_ID}"` ✓ (URL matches §4 claim; RUN_ID is a Dagster UUID extracted from hello-world response, confirmed by context at line 448)

§5 steps 6 and 7 also explicitly cover these changes. **Finding fully resolved.**

---

### L1 — RESOLVED ✓

**Finding**: `schemas/runs.py` docstring updates (module-level line 5 and `RunStatusResponse` class docstring line 34) were absent from §4, §5, and §13.

**Rev 2 response** (§4, line 134): `apps/api/dataplat_api/schemas/runs.py` now listed as **edit** with exact instructions:
> "(2) Update module-level docstring (line 5): change `GET  /api/runs/{run_id}           → RunStatusResponse` to `GET  /api/runs/dagster/{dagster_run_id} → RunStatusResponse`. (3) Update `RunStatusResponse` class docstring (line 34): change `"""Response body for GET /api/runs/{run_id} (HTTP 200 OK).` to `"""Response body for GET /api/runs/dagster/{dagster_run_id} (HTTP 200 OK).`"

§5 step 2 (lines 163–165) repeats these exactly. §13 DoD now includes a checklist item (line 407): `schemas/runs.py` module and `RunStatusResponse` docstrings updated.

**Spot-check against source file**: `schemas/runs.py` currently has the stale references on both lines as described — line 5: `GET  /api/runs/{run_id}           → RunStatusResponse`; line 34: `"""Response body for GET /api/runs/{run_id} (HTTP 200 OK).` — confirming the edit target is correct ✓. **Finding fully resolved.**

---

### NIT-1 — RESOLVED ✓

**Finding**: §3 opening sentence said "13 Mapped columns" before self-correcting to 14 at the end of the section.

**Rev 2 response** (§3, line 57): Opening sentence now reads "The `Run` ORM model has **14 Mapped columns**." Consistent throughout — the section now opens with 14, the table has 14 rows, and the closing paragraph reads "**Total: 14 Mapped columns** — not 13" (the "not 13" note retained for historical clarity, which is fine). No contradiction anywhere. **Finding fully resolved.**

---

### NIT-2 — RESOLVED ✓

**Finding**: §4 import addition claimed `AsyncSession` and `get_session` as new imports when both already existed in `runs.py`.

**Rev 2 response** (§4, line 135; §5 step 3, line 169): Both locations now state explicitly: "**`AsyncSession` (line 25) and `get_session` (line 35) are already imported — do not duplicate.**" Only `RunDetailResponse` is listed as a new import addition.

**Spot-check against source file**: `runs.py` line 25: `from sqlalchemy.ext.asyncio import AsyncSession` ✓; line 35: `from dataplat_api.db.session import get_session` ✓. Both pre-existing. **Finding fully resolved.**

---

## New Issue Scan

No new blockers or material issues introduced in Rev 2.

The §14 addenda table is clean and accurate. The §13 DoD checklist was expanded to include the two new file edits and the schemas.py docstring update. The implementation steps (§5) are now complete and non-contradictory. All spot-checked source line numbers match the proposal's claims.

One cosmetic observation (not a blocker): §9 OQ-1 third bullet still mentions "runs.py module docstring header comment (line 11): update `GET /{run_id}` to `GET /dagster/{dagster_run_id}`" — this router-file docstring change is not separately called out in §4's router row. It is covered implicitly by step 3 of §5 ("Update the docstring to reflect the new path") and does not change the completeness calculus of §4 at any material level. This is purely cosmetic; the implementer will not miss it given the §5 wording.

---

## Verification of Key Claims (carried forward from Round 1, all still confirmed)

All claims verified in Round 1 remain valid. No source files changed between rounds (Rev 2 is a proposal revision only). Summary:

| Claim | Status |
|---|---|
| Route collision analysis correct; Option A only clean solution | ✓ |
| `test_runs_hello_world.py` lines 110, 132, 148 all call old URL | ✓ confirmed by spot-check |
| `checks.sh` line 455 polls by Dagster UUID, not Postgres int | ✓ confirmed by spot-check |
| `schemas/runs.py` stale docstrings on lines 5 and 34 | ✓ confirmed by spot-check |
| `AsyncSession` at line 25, `get_session` at line 35 (pre-existing) | ✓ confirmed by spot-check |
| `triggered_by` is the correct owner FK; `Run` has no `owner_id` | ✓ |
| 14 ORM columns; all 14 in `RunDetailResponse` schema | ✓ |
| OpenAPI regeneration stated as hard requirement (invariant #6) | ✓ |
| SQL lynchpin test (Test 6) with `literal_binds=True` present | ✓ |
| No F-049 scope leak | ✓ |

---

## Summary

| Round 1 ID | Severity | Status |
|---|---|---|
| M1 | MEDIUM | ✅ RESOLVED |
| L1 | LOW | ✅ RESOLVED |
| NIT-1 | NIT | ✅ RESOLVED |
| NIT-2 | NIT | ✅ RESOLVED |

**New findings**: 0

---

VERDICT: APPROVED
