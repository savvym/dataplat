# Sprint S050-F-050 — Review Final (Mode B)

**Reviewer**: leader (inline Mode B)  
**Date**: 2026-06-05  
**Commit reviewed**: 7c24743 (feat) + 5f6a2dc (progress log)  
**agreed.md revision**: 2  

---

## Source material read

- `contracts/S050-F-050/agreed.md` (487 lines) — binding contract, rev-2
- `contracts/S050-F-050/feedback.md` — round-1 (CHANGES_REQUESTED, 7 findings M1/M2/L1/L2/NIT-1/NIT-2/NIT-3) + round-2 (APPROVED)
- `git show 7c24743` — 11 files, +2001 lines
- `apps/api/dataplat_api/routers/dagster_events.py` (100 lines) — full read
- `apps/api/dataplat_api/schemas/dagster_events.py` (52 lines) — full read
- `apps/api/dataplat_api/config.py` — full read
- `apps/api/dataplat_api/main.py` — full read
- `apps/api/tests/test_dagster_events.py` (361 lines) — full read
- `dagster/dagster_platform/definitions.py` (654 lines) — full read
- `docker/docker-compose.dev.yml` — targeted grep (DAGSTER_WEBHOOK_SECRET, FASTAPI_WEBHOOK_URL)
- `packages/api-types/openapi.json` — targeted grep for dagster endpoint + schemas

---

## Checkpoint evaluations

### B1 — M1/OQ-1 fix: backfill-ID extraction landed correctly ✅

**`dagster/dagster_platform/definitions.py` lines 612–615:**
```python
dagster_run_id: str = (
    context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id
)
```

Exact match to agreed.md §6 pseudocode lines 241–244 and §13 DoD citation. The sensor fires on `DagsterRunStatus.STARTED/SUCCESS/FAILURE/CANCELED` (lines 577–584), builds `_EVENT_TYPE_MAP` correctly (lines 568–573), and passes `dagster_run_id` in the POST body (line 617). The inline comment on line 611 (`# M1 fix: use the backfill tag…`) and the docstring (lines 593–607) both explicitly explain why the backfill tag is used.

**No test exercises the sensor-side tag extraction** — this is correct and expected per agreed.md §9 note on OQ-1: "sensor-level tag extraction is validated only via integration (deferred per §11)." T6 tests handler behavior when the run is not found; the sensor extraction is a separate concern.

**PASS.**

---

### B2 — M2 fail-closed guard is FIRST handler check ✅

**`apps/api/dataplat_api/routers/dagster_events.py` lines 58–62:**
```python
if not settings.DAGSTER_WEBHOOK_SECRET:
    raise HTTPException(
        status_code=500,
        detail="Webhook secret not configured on this server",
    )
```

This is the **first executable statement** in the handler body, preceding the `compare_digest` call at lines 65–68 by 3 lines with no intervening logic. The module docstring at lines 10–17 even documents the ordering explicitly: "1. Fail-closed guard … 2. secrets.compare_digest…".

**T10** (`test_unconfigured_webhook_secret_returns_500`, lines 341–360): monkeypatches `settings.DAGSTER_WEBHOOK_SECRET = ""`, sends a header that would match `""` if the guard were absent, asserts `resp.status_code == 500` and `"Webhook secret not configured" in resp.json()["detail"]`. Test uses `raise_server_exceptions=False` (correct — 500 is the expected outcome, not an unhandled exception). ✅

**PASS.**

---

### B3 — Auth uses `secrets.compare_digest`, correct bytes handling, empty header → 401 ✅

**Lines 65–68:**
```python
if x_dagster_webhook_secret is None or not secrets.compare_digest(
    x_dagster_webhook_secret, settings.DAGSTER_WEBHOOK_SECRET
):
    raise HTTPException(status_code=401, detail="Invalid webhook secret")
```

Both arguments are `str`. `secrets.compare_digest` accepts homogeneous `str | bytes` pairs; both-str is valid Python 3.6+. No encoding issue. The `None` check is first in the compound `or`, so a missing header short-circuits before `compare_digest` is called.

**Empty-header with secret configured**: `x_dagster_webhook_secret = ""` (not None), `compare_digest("", "test-secret-f050")` → False → 401. The guard does NOT fire (secret is non-empty). Correctly 401, not 200.

**T7** (no header → `x_dagster_webhook_secret is None` → 401) ✅  
**T8** (wrong value → `compare_digest` fails → 401) ✅

**PASS.**

---

### B4 — Event-type → status mapping is total; `ended_at` parsing correct ✅

**`routers/dagster_events.py` lines 86–94:**

| `event_type` | `run.status` | Timestamp field | agreed.md §3.3 |
|---|---|---|---|
| `RUN_START` | `"running"` | `run.started_at = body.timestamp` | ✅ |
| `RUN_SUCCESS` | `"success"` | `run.ended_at = body.timestamp` | ✅ |
| `RUN_FAILURE` | `"failure"` | `run.ended_at = body.timestamp` | ✅ |
| `RUN_CANCELED` | `"failure"` | `run.ended_at = body.timestamp` | ✅ |

The `Literal["RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED"]` in `DagsterRunEventPayload` means Pydantic raises 422 for any other string, so the `if/elif` chain is exhaustive over all valid inputs.

`body.timestamp` is a `datetime` parsed by Pydantic from an ISO-8601 string. The sensor emits `datetime.now(timezone.utc).isoformat()` (definitions.py line 619), which is a timezone-aware UTC string. Pydantic parses this to a timezone-aware `datetime`. The `# type: ignore[assignment]` comments on lines 88/91/94 address the SQLAlchemy `DateTime` vs Python `datetime` type narrowing issue — same pattern as prior ORM mutations.

**No transition guard**: correct for MVP (agreed.md §3.3 "last-write-wins; no transition guard for MVP").

**PASS.**

---

### B5 — Unknown `dagster_run_id` → 200 ignore, no `session.commit()` ✅

**`routers/dagster_events.py` lines 78–83:**
```python
if run is None:
    return DagsterEventResponse(processed=False, reason="unknown_run")
```

The `await session.commit()` (line 97) is only reached after the state-transition block. The early `return` on line 83 exits the handler without passing through commit.

**T6** (`test_unknown_dagster_run_id_returns_processed_false`, lines 253–288): captures the session mock, sets `scalar_one_or_none.return_value = None`, then asserts:
- `resp.status_code == 200` ✅
- `body["processed"] is False` ✅
- `body["reason"] == "unknown_run"` ✅
- `captured_session[0].commit.assert_not_called()` ✅

Both conditions checked per agreed.md §9 T6 spec. **PASS.**

---

### B6 — Test count: 10 tests T1–T10, all well-structured ✅

Test file header comment (lines 3–14) enumerates all 10 names; I verified each function is present:

| # | Function name | Line | What it exercises |
|---|---|---|---|
| T1 | `test_run_success_event_updates_status` | 123 | RUN_SUCCESS → status='success', ended_at not None |
| T2 | `test_run_failure_event_updates_status` | 146 | RUN_FAILURE → status='failure', ended_at not None |
| T3 | `test_run_start_event_updates_status_and_started_at` | 169 | RUN_START → status='running', started_at not None, ended_at still None |
| T4 | `test_run_canceled_maps_to_failure` | 195 | RUN_CANCELED → status='failure', ended_at not None |
| T5 | `test_session_commit_called_on_known_run` | 218 | commit called exactly once on known run |
| T6 | `test_unknown_dagster_run_id_returns_processed_false` | 253 | unknown ID → 200 + processed=False + no commit |
| T7 | `test_missing_secret_header_returns_401` | 294 | absent header → 401 |
| T8 | `test_wrong_secret_header_returns_401` | 310 | wrong value → 401 |
| T9 | `test_invalid_event_type_returns_422` | 326 | RUN_QUEUED → 422 Pydantic |
| T10 | `test_unconfigured_webhook_secret_returns_500` | 341 | empty secret → 500 before compare_digest |

All 10 tests use the established conftest autouse fixtures (`_patch_engine_begin`, `_patch_httpx_no_ssl`), `app.dependency_overrides[get_session]`, and `monkeypatch.setattr(settings, "DAGSTER_WEBHOOK_SECRET", ...)`. The session mock pattern (synchronous `MagicMock()` for the result proxy, `AsyncMock()` for the session) matches the established precedent from `test_runs_get.py`.

Tests T5 and T6 both capture the session instance explicitly to assert `.commit.assert_called_once()` / `.commit.assert_not_called()` — this is correct and not just mocking away the logic. The handler logic is genuinely exercised.

Commit message confirms "All 10 pass; full suite 328→338." **PASS.**

---

### B7 — OpenAPI regenerated SAME COMMIT ✅

`packages/api-types/openapi.json` is in the 11-file diff of commit 7c24743. Grep confirms:

- `/api/dagster/events` POST operation at line 1504 ✅
- `DagsterEventResponse` schema at ~line 2285 ✅
- `DagsterRunEventPayload` schema at ~line 2316 ✅
- `x-dagster-webhook-secret` header parameter at line 1514 ✅

Hard invariant #6 satisfied. **PASS.**

---

### B8 — Hard invariants audit ✅

| # | Invariant | Status | Verification |
|---|---|---|---|
| 1 | Lineage mandatory | N/A | No `Commit` object created; only `Run` lifecycle status updated. |
| 2 | Storage separation + CAS | ✅ | Only Postgres `run` row mutated. No MinIO/S3 interaction. |
| 3 | Schema frozen post-publish | N/A | `run` table `status`/`started_at`/`ended_at` columns already exist (F-002); no Alembic migration. |
| 4 | LLM calls through gateway | N/A | Zero LLM calls anywhere in this feature. |
| 5 | Async SQLAlchemy from day one | ✅ | Handler is `async def`; `AsyncSession = Depends(get_session)`; `await session.execute(select(Run).where(...))`; `result.scalar_one_or_none()` (sync method on result proxy — correct); `await session.commit()`. Zero uses of `session.query()`. |
| 6 | OpenAPI ↔ TS type sync | ✅ | `packages/api-types/openapi.json` regenerated in the same commit (B7 above). |

**ALL PASS.**

---

### B9 — Scope discipline ✅

Reviewed `routers/dagster_events.py`, `schemas/dagster_events.py`, `definitions.py`, and `docker-compose.dev.yml` for scope creep:

- No WebSocket push or Redis pub/sub (F-051 deferred) ✅
- No event audit table / `run_event` model ✅
- No retry logic from FastAPI side ✅
- No run cancellation API ✅
- No Celery, no Dagster-eventing buses ✅
- No ACL changes ✅
- Sensor delivery semantics explicitly documented as best-effort (L1) in the docstring ✅

**PASS.**

---

### V-map confirmation

| V-criterion | Covered by | Assessment |
|---|---|---|
| **V1**: `Run.status` flips to `'success'` after RUN_SUCCESS | **T1** asserts `run_row.status == "success"` + `processed=True`; **T5** asserts `session.commit()` called once (confirms DB write path) | ✅ |
| **V1**: `Run.status` flips to `'failure'` after RUN_FAILURE | **T2** asserts `run_row.status == "failure"` | ✅ |
| **V2**: `ended_at` set after terminal events | **T1** (`run_row.ended_at is not None` for RUN_SUCCESS), **T2** (`run_row.ended_at is not None` for RUN_FAILURE), **T4** (`run_row.ended_at is not None` for RUN_CANCELED) | ✅ |

V-map matches agreed.md §9 exactly. T3 is NOT in the V2 map (T3 tests `started_at` for RUN_START, not `ended_at`) — NIT-2 from round-1 was correctly applied. **PASS.**

---

### Sensor reliability call-out — L1 fix confirmed ✅

`dagster/dagster_platform/definitions.py` lines 628–634:
```python
    except Exception as exc:
        context.log.warning(
            "fastapi_run_status_sensor: HTTP call failed (event dropped): %s", exc
        )
        # Do not raise — event is permanently dropped (best-effort; see docstring).
        # Sensor failure must not fail the run itself.
```

The function has no `raise`, so it returns `None` normally. The Dagster daemon marks the tick SUCCESS and advances its cursor. The docstring (lines 604–608) makes this explicit:

> "Delivery semantics: best-effort, NOT at-least-once. The try/except swallows all HTTP exceptions and returns normally so the Dagster daemon marks this tick SUCCESS and advances its cursor. A failed HTTP call means the event is **permanently dropped** — it will NOT be retried on the next tick."

This is the correct L1 fix. The round-1 L1 finding ("retries on its next tick" was incorrect) is fully remediated. The semantics, the comment, and the docstring are all internally consistent and accurate. ✅

---

### Process flag ✅ (positive note)

Commit 7c24743 does NOT contain `spec/feature_list.json` or a closing `claude-progress.txt` entry. Commit 5f6a2dc is an in-progress log update only. Neither flips `passes: true` for F-050 nor writes the closing sprint entry. Per the sprint workflow (CLAUDE.md §Sprint workflow steps 10), those are the **leader's** responsibility after verifier confirms green. This is correct process — a contrast to the S049 violation where the implementer prematurely updated `feature_list.json`.

---

## Summary of round-1 finding disposition

All 7 findings from round-1 are confirmed addressed in code:

| ID | Finding | Code evidence |
|---|---|---|
| M1 | Sensor sent partition UUID, not backfill ID | `definitions.py:612–615` uses `tags.get("dagster/backfill") or run_id` |
| M2 | Fail-open when `DAGSTER_WEBHOOK_SECRET=""` | `routers/dagster_events.py:58–62` guard is first check; T10 covers it |
| L1 | Sensor docstring claimed retries that don't exist | Docstring + comment at `definitions.py:604–608, 633` accurate: "permanently dropped" |
| L2 | OQ-2 left open "reviewer should confirm" | All OQs closed in agreed.md §12 with concrete decisions |
| NIT-1 | Unused `RunFailureSensorContext` import | Absent from `definitions.py` imports |
| NIT-2 | V2 map cited T3 (started_at) instead of T1/T2 | V-map in test file + agreed.md §9 cites T1, T2, T4 |
| NIT-3 | DoD item for definitions.py underspecified | Confirmed per code at `definitions.py:612–615` |

---

## APPROVED

All B1–B9 checkpoints pass. Hard invariants satisfied. Scope clean. 10 tests present and well-structured. OpenAPI regenerated in the same commit. Sensor L1 semantics correctly documented and implemented. Process followed correctly (no premature `passes` flip).

**Next step**: leader delegates to `verifier` → `bash verify/checks.sh backend` → if green, flip `spec/feature_list.json` F-050 `passes: true`, append closing entry to `claude-progress.txt`, `git push`.
