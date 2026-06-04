# Reviewer Feedback — S045-F-045 (Mode A, Pre-implementation, Round 2)

**Reviewer**: Mode A (pre-implementation contract review)  
**Date**: 2026-06-04  
**Proposal revision reviewed**: Rev 2 (`contracts/S045-F-045/proposed.md`)  
**Prior round**: Round-1 returned CHANGES_REQUESTED with M1, M2, L1–L3, NIT1–4.

---

## Round-2 Review Method

For each finding from round-1, I located the exact body-text location stated in the §11 addenda, verified the new wording is concrete and unambiguous (not advisory), and checked that the §11 addenda entry is consistent with the body. I also scanned the full revised document for any new invariant violations.

---

## Finding-by-finding disposition

### M1 — SQL-structural test must assert owner filter on COUNT query as well
**Status: RESOLVED**

§5 test 6 (`test_list_datasets_materialized_by_in_query`) now reads (lines 146–147):

> *"Captures `session.execute.call_args_list[0].args[0]`, compiles with `literal_binds=True`, asserts `"materialized_by"` and the string representation of the mock user's id both appear in the compiled SQL string (row-list query). **Then** captures `session.execute.call_args_list[1].args[0]`, compiles with `literal_binds=True`, and asserts `"materialized_by"` and the mock user's id also appear in that compiled SQL string (COUNT query). This ensures the owner filter is applied to both the list and the total-count queries, so `total` cannot silently return a global row count."*

Both assertions are stated concretely. The addenda entry (§11, M1 row) matches this body text exactly. Resolved.

---

### M2 — Codegen step must be a hard requirement, not advisory
**Status: RESOLVED**

The advisory OQ-4 hedge has been replaced in three places — §3 codegen hard requirement paragraph, §7 OQ-4, and §8 invariant #6 — all now using the following unambiguous text:

> *"Implementer MUST run `make codegen` (or verify `packages/api-types/openapi.json` reflects the new `DatasetListItem` + `DatasetListResponse` schemas) and commit the resulting diff in the SAME commit. If `Makefile` is absent, the OpenAPI diff must be confirmed manually — it is not sufficient to rely on the `checks.sh contract` no-op guard."*

All three occurrences are identical in substance and non-advisory in phrasing. The §11 addenda entry correctly records §3, §7 OQ-4, and §8 invariant #6 as the fix locations. Resolved.

---

### L1 — `recipe_id` null-guard note for F-069
**Status: RESOLVED**

§4 field types table, `recipe_id` row now reads:

> *"FK to `recipe.id`; None only if row was orphaned — should not occur in practice. **Frontend/client must guard against null before constructing a recipe detail URL. F-046 and F-069 implementers should be aware.**"*

Note is present as required. Addenda entry matches. Resolved.

---

### L2 — Explicit statement that `status='failed'` rows are included
**Status: RESOLVED**

§4 Ordering rationale paragraph now contains:

> *"All dataset rows are returned regardless of status (pending, running, failed, done). Failed rows are tombstones (per F-042 agreed.md) and are included in the list for audit visibility."*

Text is explicit. Addenda entry matches. Resolved.

---

### L3 — `version_tag` non-null guarantee confirmed by F-042
**Status: RESOLVED**

§4 field types table, `version_tag` row now reads:

> *"`\"v1\"`, `\"v2\"`; F-042 always sets this before INSERT — non-null guarantee holds."*

Note present as required. Addenda entry matches. Resolved.

---

### NIT-1 — Import table note was internally contradictory
**Status: RESOLVED**

§3 files changed table, `routers/datasets.py` row now reads:

> *"Add `GET \"\"` route `list_datasets()` above the existing `POST /{recipe_id}/materialize` route; import `DatasetListItem`, `DatasetListResponse` from `dataplat_api.schemas.datasets`. `func`, `select` are already imported."*

The contradiction ("needs addition" vs. "already imported") is gone. Only the new schema imports are listed as additions. Addenda entry matches. Resolved.

---

### NIT-2 — `_make_dataset()` factory should use `MagicMock(spec=Dataset)`
**Status: RESOLVED**

§5 `_make_dataset()` description (line 127) now reads:

> *"Uses `MagicMock(spec=Dataset)`, consistent with `_make_recipe()` in `test_recipes_list.py`."*

Explicit and consistent with precedent. Addenda entry matches. Resolved.

---

### NIT-3 — Clarify 'array' in `verification[0]` refers to `items` key
**Status: RESOLVED**

§6 Verification Mapping, `verification[0]` first bullet now reads:

> *"mocks a `status='done'` dataset row and asserts the endpoint returns it in `items` with `status == \"done\"`. **(Note: 'array' in the spec means `items` key in the `{items, total}` envelope.)**"*

Parenthetical is present. Addenda entry matches. Resolved.

---

### NIT-4 — OQ-1 fallback note for `nullslast()` is outdated
**Status: RESOLVED**

§7 OQ-1 now reads:

> *"Confirmed: `Dataset.materialized_at.desc().nulls_last()` generates `ORDER BY dataset.materialized_at DESC NULLS LAST` on SQLAlchemy 2.0.41 + Postgres. This is the required implementation. No fallback needed."*

Fallback language is fully removed. Addenda entry matches. Resolved.

---

## New-finding scan

I scanned the full revised document for any issues not raised in round-1. None found. All previously-passing elements remain intact:

- §2 out-of-scope list unchanged; F-046 boundary respected.
- §4 field set (7 fields): id, recipe_id, version_tag, status, sample_count, size_bytes, materialized_at — unchanged and correct.
- §5 test 9 (schema guard) still excludes detail-level fields (recipe_snapshot, hf_repo_uri, dataset_card_md, dagster_run_id, stats, materialized_by).
- CAL-1 (async session), CAL-8 (MVP scope), CAL-10 (happy path + failure coverage) remain passing per round-1 analysis; no changes in the revised document affect those conclusions.
- No new LLM calls, no CAS paths, no schema mutations introduced.

---

## Calibration checks (re-verified for changed sections)

| CAL | Check | Finding |
|-----|-------|---------|
| CAL-1 | Async session enforcement | PASS — unchanged from round-1; `async def`, `AsyncSession`, `await session.execute()`. |
| CAL-3 | OpenAPI ↔ TS sync | PASS — M2 resolution makes this a concrete hard requirement in §3, §7, §8. |
| CAL-10 | Test coverage | PASS — test 6 now covers both queries (M1 resolution); all other 9 tests unchanged. |
| CAL-11 | Bias check | Applied — all 9 findings checked with body-text citation; no vague approval. |

---

## Invariant compliance summary

| # | Invariant | Verdict |
|---|---|---------|
| 1 | Lineage mandatory | N/A — pure SELECT, no Commit |
| 2 | Storage separation + CAS | PASS |
| 3 | Schema frozen post-publish | N/A |
| 4 | LLM calls via gateway | N/A |
| 5 | Async SQLAlchemy in `apps/api/` | PASS |
| 6 | OpenAPI ↔ TS sync | PASS — explicit hard requirement in §3, §7 OQ-4, §8 invariant #6 |

---

APPROVED
