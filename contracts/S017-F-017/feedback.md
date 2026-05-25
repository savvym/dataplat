# S017-F-017 — Reviewer Feedback (Mode A)

**Reviewer:** Claude (independent)
**Date:** 2026-05-25
**Verdict:** APPROVED (with one MEDIUM fix required before implementation)

---

## DECISION: CHANGES_REQUESTED

### Numbered findings

**1. [MEDIUM] `verify/checks.sh` — MINERU_ID derivation swallows python3 exit code**

The derivation block (`proposed.md §5, "Deriving the mineru id dynamically"`) uses:

```bash
MINERU_ID=$(python3 -c "
...
    sys.exit(1)
...
" 2>&1)
rm -f "$MINERU_ID_BODY"
```

`$()` captures stdout+stderr but the non-zero exit from `sys.exit(1)` is silently swallowed by the assignment. The shell does not exit; `$MINERU_ID` contains the error string `"FAIL: mineru not found in extractor list"`; `rm -f` runs normally; and the subsequent V1 curl is called with a junk URL (producing a confusing 422 or curl error, not a clear failure). Fix: capture stdout and stderr separately, then explicitly check the exit code:

```bash
MINERU_ID=$(python3 -c "..." 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$MINERU_ID" ]; then
    echo "FAIL: mineru not found in operator list — run 'bash $0 operators' seed step first"
    rm -f "$MINERU_ID_BODY"
    exit 1
fi
rm -f "$MINERU_ID_BODY"
```

Alternatively, `2>&1` may be removed and `|| exit 1` appended directly to the assignment expression using `|| { ...; exit 1; }` pattern. The core requirement: a failed python3 exit must immediately halt the checks.sh block with a clear message before V1 runs.

**2. [LOW] `proposed.md §5` — V1 default_config assertion is unnecessarily tolerant**

The assertion `body['default_config'] is None or isinstance(body['default_config'], dict)` (proposed.md line 260-261) accepts None as a valid outcome. However, the handler issues a fresh `SELECT` via `scalar_one_or_none()`, which loads the actual DB row. The `server_default` `'{}'::jsonb` fires at INSERT time — the DB value is `{}`, not NULL. A fresh read therefore always returns a dict (possibly empty), never None (absent an ORM bug). The tolerant assertion would silently pass even if the server_default was broken. Recommended: assert `isinstance(body['default_config'], dict)`, which is the truthful invariant post-SELECT. This is LOW because the test will not produce a false-positive pass in the expected happy path, and the contract author already flagged this in OQ-2.

**3. [NIT] `verify/checks.sh` line 1067 — `# F-015` comment becomes stale**

The `all)` chain entry reads `bash "$0" operators   # F-015`. After F-016 and now F-017, this comment is two versions stale. Remedy: update to `# F-015/F-016/F-017`. Not a blocker; purely cosmetic.

---

## Calibration checks (verify/reviewer-calibration.md)

- **CAL-1 (Async session):** PASS — proposed.md §4 specifies `async def`, `await session.execute(select(Operator).where(...))`, `result.scalar_one_or_none()`. No `session.query()` or sync session pattern proposed. Confirmed the F-016 router (operators.py:75-76) uses this pattern; F-017 mirrors it identically.

- **CAL-2 (LLM gateway):** N/A — no LLM call in this feature. Detail endpoint is a pure DB read.

- **CAL-3 (OpenAPI sync):** PASS — proposed.md §2 and §6 both list `packages/api-types/openapi.json` as a required same-commit change. §7 step 5 specifies the exact regeneration command. §6 invariant #6 states the diff must contain `/api/operators/{operator_id}` and `OperatorDetail`.

- **CAL-4 (Lineage completeness):** N/A — no Commit object created. This is a GET endpoint with no processor or lineage record.

- **CAL-5 (CAS path discipline):** N/A — no blob storage involved. No MinIO writes.

- **CAL-6 (Schema freeze post-publish):** N/A — no Silver/Gold repo schema touched.

- **CAL-7 (Bronze faithfulness):** N/A — no Bronze adapter.

- **CAL-8 (MVP scope discipline):** PASS — no banned features introduced. No Celery, no Docker-in-Docker, no ACL beyond global auth, no training framework. The 4-file change set is exactly what MVP scope allows for a detail endpoint.

- **CAL-9 (Plugin isolation):** N/A — no plugin code.

- **CAL-10 (Test coverage):** N/A by established project convention — this codebase uses `verify/checks.sh` integration tests as its test layer, not pytest unit tests. All prior sprints (F-013, F-014, F-016) follow the same pattern. F017-V1 (200 + full field check) and F017-V2 (404) cover the two required cases (happy path + one failure mode).

- **CAL-11 (Bias check):** Verified concretely. CAL-1 checked against proposed.md §4 handler body. CAL-3 checked against proposed.md §2, §6, §7. Field fidelity cross-checked column-by-column against models.py:165-207. Two real defects found and reported above.

---

## Field fidelity result

All 19 ORM columns are represented in §3.4. No invented columns, no missing columns. Every Python type and nullability declaration in the contract matches the ORM `Mapped[...]` annotation exactly. The ORM has `sa.BigInteger` for `id` — Python-side this is `int`, which the contract correctly states. The `created_at` type annotation in the ORM is `Mapped[Optional[sa.DateTime]]` with `sa.DateTime(timezone=True)` — the contract maps this to `datetime | None`, which is correct (SQLAlchemy's `DateTime` maps to Python `datetime`; the `from datetime import datetime` import in the proposed schema class is required and noted in §7 step 1).

## output_schema / default_config V1 adjudication

- **output_schema:** Asserting key presence only (value may be None) is CORRECT. The seed CLI never sets `output_schema`; the DB value is NULL; a fresh SELECT returns None. Requiring a dict would mandate scope-creep seeding. No change needed.
- **default_config:** The `is None or isinstance(dict)` tolerance is unnecessarily loose (see finding #2). A strict `isinstance(body['default_config'], dict)` is the correct assertion because the server_default `'{}'::jsonb` fires at INSERT and a fresh SELECT returns `{}`. Tightening this is LOW, not a blocker, but recommended.

## Risks for implementer to watch during build

- Ensure `from datetime import datetime` is added to `schemas/operators.py` — the import is not currently present and is required for `OperatorDetail.created_at: datetime | None`.
- The openapi.json regeneration requires `DATABASE_URL` + `SECRET_KEY` env vars (established in F-016); confirm these are set in the shell before running the regen command.
- Confirm the new route `/{operator_id}` is registered AFTER `""` in the router file (consistent with convention, even though shadowing is not a concern here per §3.2 analysis).

---

## Iter 2 convergence — 2026-05-25

**DECISION: APPROVED**

Both findings from iter 1 are resolved:

1. **MEDIUM (MINERU_ID derivation) — RESOLVED.** `proposed.md:216-223`: `2>&1` is gone; the assignment ends with `|| { echo "FAIL: F017 could not derive mineru id from extractor list"; rm -f "$MINERU_ID_BODY"; exit 1; }`. Python stderr goes to the terminal, `$MINERU_ID` is never populated with error text on failure, and the block exits immediately before V1 runs. Junk-URL issue is eliminated.

2. **LOW (default_config assertion) — RESOLVED.** `proposed.md:264-269`: strict `assert isinstance(body['default_config'], dict)` with no `is None or` tolerance. OQ-2 marked DECIDED with correct reasoning (fresh SELECT returns stored `{}`, not insert-buffer None). `output_schema` remains key-presence-only — confirmed correct per iter 1.

Contract is ready for implementation.
