# S016-F-016 — Reviewer Feedback (Mode A, pre-implementation)

**Verdict:** APPROVED

**Reviewed:** spec/feature_list.json (F-016, deps F-015/F-008), contracts/S016-F-016/proposed.md, CLAUDE.md, verify/reviewer-calibration.md, ground-truthed against models.py / routers/sources.py / main.py / verify/checks.sh / packages/api-types/openapi.json.

## Findings

1. **LOW** [§6 / CAL-10] No pytest unit tests proposed; only checks.sh integration checks (require live stack). Consistent with every prior sprint in this repo. Not blocking — `backend` layer runs lint/type only for the new router. Advisory.

2. **MEDIUM (adjudicated acceptable — no change required)** [§6 V3, OQ-3] The V3 `category=tagger` check is vacuously true (no tagger seeded → empty array → `all items have category=tagger` passes trivially). It does not independently prove the filter works. Adjudication: acceptable for MVP because (a) feature_list criterion 3 ("returns operators with category='tagger'") is satisfied vacuously by 200+empty array, (b) V2's `assert op['category']=='extractor'` on the extractor response is the stronger filter guard, (c) seeding a tagger row would be scope creep. No numbered change.

3. **NIT** [§3.4] `is_active: bool | None` uses `X | Y` union syntax (valid Py3.10+, container is 3.12). Other schemas use `Optional[...]`. Cosmetic; ruff will catch any issue.

4. **NIT** [§3.4] `image` correctly exposed as non-Optional `str` (ORM `Mapped[str]`). Confirmed no mismatch.

## Adjudication of the two flagged design questions

- **`is_active IS NOT FALSE` vs `= true`:** BLESS `IS NOT FALSE`. Seed uses flush() not refresh(), so ORM-side value may be None even after the server default fires in the DB. `IS NOT FALSE` correctly includes rows where is_active was never explicitly set (NULL) and excludes only genuinely deactivated (false) rows — the semantically correct "not deactivated" filter the (category, is_active) index anticipates. Do NOT change to `= true`.
- **V3 vacuous check:** Acceptable (see finding 2).

## Calibration (verify/reviewer-calibration.md)

- CAL-1 (async session): PASS — §4/§7.5 mandate async select + scalars().all(), forbid session.query().
- CAL-2 (LLM gateway): N/A — no LLM calls.
- CAL-3 (OpenAPI sync): PASS — §2/§7.6 commit to openapi.json regen same commit; no Makefile (checks.sh:111 guard real); export command matches indent=2 format.
- CAL-4 (lineage): N/A — read-only, no Commit.
- CAL-5 (CAS path): N/A — no blob storage.
- CAL-6 (schema freeze): N/A — no Silver/Gold schema.
- CAL-7 (Bronze faithfulness): N/A — no adapter.
- CAL-8 (MVP scope): PASS — no Celery/ACL/OAuth/Kafka/DinD; visibility untouched.
- CAL-9 (plugin isolation): N/A — no plugin.
- CAL-10 (test coverage): see finding 1 (consistent with repo convention).
- CAL-11 (bias check): applied — concrete file:line cites throughout, no LGTM shortcuts.

## Ground-truth confirmations

- is_active column: models.py:198–202 (nullable Boolean, server_default true); index (category, is_active) models.py:162. Accurate.
- OperatorRead fields all exist on Operator model (models.py:165–202); `image` is non-nullable str.
- No existing operators router in main.py (health/admin/admin_runs/runs/auth/sources only) — no conflict.
- Ordering mirrors sources.py:74–77 (id.asc(), async execute).
- Token-mint block matches checks.sh dagster/F012 layers; insertion before `;;` at checks.sh:963 correct; `all)` already calls `bash "$0" operators` (checks.sh:980).
