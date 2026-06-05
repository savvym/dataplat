# Sprint S052-F-052 — Reviewer Mode B (Post-Implementation Diff Review)

**Reviewer:** reviewer (Mode B — post-implementation)  
**Date:** 2026-06-05  
**Commit reviewed:** `43349fc`  
**Agreed contract:** `contracts/S052-F-052/agreed.md` (rev-2, 417 lines)  
**Files touched in diff:** 10 files, +1349/-55  
**Test delta:** 349 → 365 (+16)

---

## VERDICT: APPROVED

All critical calibration checks pass. Zero blocking findings. Two non-blocking NITs from Mode A remain cosmetic. Two new non-blocking NITs noted below.

---

## Calibration Checks

### Check 1 — Wire schemas literal match ✓

**`schemas/realtime.py`** (lines 83–100):
- `AssetMaterializedEvent`: `type: Literal["asset.materialized"] = "asset.materialized"`, `asset_key: str`, `partition_key: str` — matches verification criterion §1.2 verbatim ✓
- `ChunksAddedEvent`: `type: Literal["chunks.added"] = "chunks.added"`, `source_id: int`, `count: int` — matches verification criterion §1.3 verbatim ✓

**`routers/dagster_events.py` `_build_notification_event()`** returns raw dicts:
- `{"type": "asset.materialized", "asset_key": asset_key, "partition_key": partition_key}` — field names exact ✓
- `{"type": "chunks.added", "source_id": source_id, "count": chunk_count}` — field names exact ✓

Wire JSON serialized via `json.dumps(event)` in `_event_sender_task`. No `.model_dump()` involved — dicts match spec byte-exactly.

### Check 2 — Auth path correct ✓

**`routers/ws_notifications.py`** (BEFORE accept):
1. `token = websocket.query_params.get("token")` → if falsy: `await websocket.close(code=1008); return` ✓
2. `jwt.decode(...)` in `try/except Exception` → `await websocket.close(code=1008); return` on failure ✓
3. `select(User).where(User.id == user_id)` + `scalar_one_or_none()` → `await websocket.close(code=1008); return` if None ✓
4. `await websocket.accept()` only after all three guards pass ✓
5. `broker.unsubscribe(...)` + `sender_task.cancel()` in `finally` block ✓

Close-1008-before-accept invariant fully respected. T2, T3, T4 cover the three rejection paths.

### Check 3 — Ownership routing ✓

**`routers/dagster_events.py`** ASSET_MATERIALIZATION branch:
- `Run` looked up via existing `WHERE dagster_run_id = body.dagster_run_id` query (same as RUN_START path) ✓
- `run is None` → `return DagsterEventResponse(processed=False, reason="unknown_run")` (line ~95, shared with other event types) ✓
- `user_id = run.triggered_by; if user_id is None:` → logs WARNING, `return DagsterEventResponse(processed=True)` — silent drop with observability ✓
- `notification_broker.publish(user_id=user_id, event=notification_event)` only on known, owned runs ✓

T10 (unknown run → processed=False) and T13 (correct user_id passed to publish) both cover this path.

### Check 4 — §8a broker.publish exception isolation ✓

**`routers/dagster_events.py`** (lines ~138–147):
```python
try:
    request.app.state.notification_broker.publish(
        user_id=user_id, event=notification_event
    )
except Exception as exc:
    logger.warning("notification_broker.publish failed for user %s: %s", user_id, exc)
```
Explicit `try/except Exception` wrapping present. T16 (`test_notification_broker_publish_exception_still_returns_200`) uses `raise_server_exceptions=False`, mocks publish to raise `Exception("boom")`, and asserts HTTP 200 + `processed=True`. ✓

### Check 5 — chunks.added wire shape and T15 ✓

**`_build_notification_event()`** in `dagster_events.py`:
- `source_id = int(partition_key.removeprefix("src_"))` — parses `src_{N}` correctly ✓
- Malformed: `ValueError` caught → `return None` with WARNING log ✓
- Null: `if not partition_key:` → `return None` with WARNING log ✓
- `chunk_count: int = int(body.metadata.get("chunk_count", 0))` — reads from metadata ✓

**T15** iterates over `["BAD_FORMAT", None]`, verifies `notification_broker.publish` NOT called in either case, asserts HTTP 200 + `processed=True`. ✓

### Check 6 — Dagster sensor ✓

**`definitions.py`** additions:
- `@asset_sensor(asset_key=AssetKey(["extract_mineru"]), name="extract_mineru_notification_sensor", minimum_interval_seconds=5)` ✓
- `@asset_sensor(asset_key=AssetKey(["chunks"]), name="chunks_notification_sensor", minimum_interval_seconds=5)` ✓
- Both return `SkipReason("notification sent; no run to trigger")` — correct choice; self-documenting in Dagster UI ✓
- `_post_asset_notification()`: `context.instance.get_run_by_id(asset_event.run_id)` → `dagster_run.tags.get("dagster/backfill") or asset_event.run_id` (backfill-tag pattern from F-050) ✓
- POSTs to `_FASTAPI_WEBHOOK_URL` with `{"event_type": "ASSET_MATERIALIZATION", "dagster_run_id": ..., "asset_key": ..., "partition_key": ..., "metadata": ...}` ✓
- Best-effort: HTTP POST wrapped in `try/except Exception` with `context.log.warning()` ✓
- `chunks_notification_sensor` reads real `chunk_count`: `int(am.metadata["chunk_count"].value)` with `try/except (ValueError, TypeError, AttributeError)` fallback to 0 ✓
- Both registered in `defs.sensors=[fastapi_run_status_sensor, extract_mineru_notification_sensor, chunks_notification_sensor]` ✓

### Check 7 — Hard Invariants

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit row created |
| 2 | Storage separation + CAS | N/A — no blob writes; notification events are ephemeral in-process dicts |
| 3 | Schema frozen post-publish | N/A — no Silver/Gold publish |
| 4 | LLM calls via gateway | N/A — no LLM call |
| 5 | Async SQLAlchemy | ✓ — `ws_notifications.py`: `AsyncSession = Depends(get_session)` + `await session.execute(select(User)...)` + `scalar_one_or_none()`; `dagster_events.py` ASSET_MATERIALIZATION branch reuses existing `await session.execute(select(Run)...)` pattern; no new `session.query()` anywhere |
| 6 | OpenAPI ↔ TS sync | ✓ — `packages/api-types/openapi.json` present in commit `43349fc` (confirmed `git show --stat 43349fc | grep openapi.json` → `packages/api-types/openapi.json | 37 +-`); diff is additive-only: `ASSET_MATERIALIZATION` added to `event_type` enum, `asset_key`/`partition_key`/`metadata` optional fields added, description strings updated (cosmetic); zero field removals; same commit as `schemas/dagster_events.py` ✓ |

### Check 8 — Out-of-scope ✓

- No Redis ✓
- No client-side subscribe filters by asset_key ✓
- No replay-on-reconnect ✓
- No per-asset ACL beyond Run.triggered_by owner-scope ✓
- `gateway.py` correctly untouched (§5.2 escape hatch applied; tag-injection Path 3 eliminated) ✓

### Check 9 — Test count ✓

- `test_ws_notifications.py` (NEW): `grep -c "^def test_"` → **10** (T1–T10) ✓
- `test_dagster_events.py` (MODIFY): 6 new test functions (T11–T16) ✓
- Total new: +16; reported delta 349→365 is consistent with code ✓
- T16 uses `raise_server_exceptions=False` (correct for exception-isolation test) ✓

### Check 10 — File table deviations

10 out of 11 agreed.md §5 file table entries touched in the commit:

| File | In table | In diff | Status |
|---|---|---|---|
| `realtime/notification_broker.py` | CREATE | ✓ | Match |
| `routers/ws_notifications.py` | CREATE | ✓ | Match |
| `schemas/realtime.py` | MODIFY | ✓ | Match |
| `schemas/dagster_events.py` | MODIFY | ✓ | Match |
| `routers/dagster_events.py` | MODIFY | ✓ | Match |
| `main.py` | MODIFY | ✓ | Match |
| `definitions.py` | MODIFY | ✓ | Match |
| `packages/api-types/openapi.json` | MODIFY | ✓ | Match |
| `test_ws_notifications.py` | CREATE | ✓ | Match |
| `test_dagster_events.py` | MODIFY | ✓ | Match |
| `verify/checks.sh` | MODIFY | ✗ not touched | See NIT-B1 |

No files in the diff are absent from the file table. ✓

### Check 11 — Round-2 Mode A NITs

**NIT-R2-1** (T1 conflates two behaviors): **REMAINING cosmetic** — `test_connect_with_valid_jwt_accepted` tests both connection acceptance and bad-message error in one test. Implementer kept them combined. Non-blocking per agreed Mode A ruling; test coverage intent is clear.

**NIT-R2-2** (ServerError.run_id optionality): **RESOLVED more cleanly** — `ws_notifications.py` does NOT use `ServerError` model at all; it emits `json.dumps({"type": "error", "code": "bad_message"})` as a raw dict literal (line 131), so `run_id` never appears in notification error responses. `ServerError.run_id: int | None` in `realtime.py` (line 53) is correctly Optional for `ws_runs.py` use, and `ws_notifications.py` avoids the model entirely. ✓

### Check 12 — New diagnostic noise

**NIT-B2** (non-blocking): `realtime.py:33` — `model_dump_json(self, **kwargs: Any)` does not forward `kwargs` to `model_dump()`. This is pre-existing code from F-051 (`RunStatusChangedEvent`), unchanged in this sprint. ruff and mypy both pass clean. Pyright-only diagnostic, consistent with S037–S051 precedent. No action required in this sprint.

---

## Numbered Findings

**B1** (NON-BLOCKING) — `verify/checks.sh` not modified  
*Location:* Agreed.md §5, `verify/checks.sh` row  
The file table entry reads "Verify `ws_notifications` case runs backend layer (or confirm `backend` already picks up new tests)." The implementer correctly applied the parenthetical escape: `checks.sh backend` runs `uv run pytest -q` in `apps/api/`, which discovers `test_ws_notifications.py` automatically. No new `ws_notifications` case is needed. Confirming the `backend` layer suffices per the agreed contract language.  
**Verdict:** Acceptable; no action required.

**B2** (NON-BLOCKING) — `_build_notification_event()` uses dict literals instead of Pydantic model_dump  
*Location:* `routers/dagster_events.py`, `_build_notification_event()` function  
The wire format is constructed as raw dicts rather than calling `AssetMaterializedEvent(…).model_dump()` or `ChunksAddedEvent(…).model_dump()`. The fields match the Pydantic models exactly today. Risk: future model changes won't auto-propagate to the dict. For MVP this is acceptable — the shapes are verified by T5/T6/T7/T8. Cosmetic refactor opportunity for a future sprint.  
**Verdict:** Non-blocking; document for future cleanup.

**B3** (NON-BLOCKING — Mode A NIT-R2-1 carry-forward) — T1 conflates connect + bad_message  
*Location:* `tests/test_ws_notifications.py`, `test_connect_with_valid_jwt_accepted`  
Approved as non-blocking in Mode A round-2. Remains cosmetic; coverage intent is clear and correct.

---

## Summary

The implementation is a faithful, disciplined execution of agreed.md with zero structural deviations. All 11 agreed.md §5 files are accounted for (10 committed, 1 correctly deferred via escape hatch). Wire schemas match verification criteria byte-exactly. Auth closes 1008 before accept on all three failure paths. Ownership routing correctly scopes events to `Run.triggered_by` and handles NULL without crashing. §8a broker exception isolation is present and verified by T16. chunks.added parsing is correct with full defensive handling for malformed/null partition_key (T15). Two `@asset_sensor` instances with real `chunk_count` from Dagster metadata are registered in definitions.py. Hard invariants #5 (async SQLAlchemy) and #6 (OpenAPI ↔ TS sync, additive-only diff) both ✓. No out-of-scope features introduced. Test count +16 (349→365) as reported.

---

## Verifier Results

### Check 1: Smoke Tests
```
Exit code: 0
✓ smoke passed (C1 API health, C2 DB connection, C3 MinIO, C4 Dagster)
```

### Check 2: Backend Full Suite
```
Exit code: 0
365 passed, 1 deselected, 41 warnings
Expected: ≥365 pass (baseline 349 + 16 new)
Result: 365/365 PASS ✓
```

### Check 3: New WS Notifications Tests
```
Exit code: 0
tests/test_ws_notifications.py: 10 passed
T1–T10 all PASS
Result: 10/10 PASS ✓
```

### Check 4: Extended Dagster Events Tests
```
Exit code: 0
tests/test_dagster_events.py: 16 passed (10 baseline + 6 new T11–T16)
Result: 16/16 PASS ✓
```

### Check 5: F-051 Regression (ws_runs)
```
Exit code: 0
tests/test_ws_runs.py: 11 passed
Result: 11/11 PASS ✓
```

### Check 6: Ruff Linting
```
Exit code: 0
All checks passed!
Result: CLEAN ✓
```

### Check 7: Mypy Type Checking
```
Exit code: 0
Success: no issues found in 50 source files
Result: CLEAN ✓
```

### Check 8: OpenAPI ↔ TS Sync Invariant
```
packages/api-types/openapi.json IN commit 43349fc: ✓
git show --stat 43349fc -- packages/api-types/openapi.json: 37 +-
Diff analysis: additive-only
  + "ASSET_MATERIALIZATION" to event_type enum
  + asset_key, partition_key, metadata optional fields
  - description text updates only (cosmetic)
No field/schema removals detected
Result: ADDITIVE ONLY ✓
Hard Invariant #6: PASS ✓
```

### Check 9: Async SQLAlchemy Invariant
```
git show 43349fc | grep -nE 'session\.query|sessionmaker\(.*[^a]\)'
Exit code: 0 (no matches)
Result: CLEAN ✓
Hard Invariant #5: PASS ✓
```

### Check 10: File Scope vs. Agreed.md §5
```
git show --stat 43349fc files modified:
1. apps/api/dataplat_api/realtime/notification_broker.py (CREATE) ✓
2. apps/api/dataplat_api/routers/ws_notifications.py (CREATE) ✓
3. apps/api/dataplat_api/schemas/realtime.py (MODIFY) ✓
4. apps/api/dataplat_api/schemas/dagster_events.py (MODIFY) ✓
5. apps/api/dataplat_api/routers/dagster_events.py (MODIFY) ✓
6. apps/api/dataplat_api/main.py (MODIFY) ✓
7. dagster/dagster_platform/definitions.py (MODIFY) ✓
8. packages/api-types/openapi.json (MODIFY) ✓
9. apps/api/tests/test_ws_notifications.py (CREATE) ✓
10. apps/api/tests/test_dagster_events.py (MODIFY) ✓

All 10 files in diff match expected file table.
verify/checks.sh: correctly deferred (escape hatch per agreed.md B1).
Result: SCOPE MATCH ✓
```

---

## Final Verdict

**VERIFIER: PASS**

All 10 checks exit 0:
- ✓ smoke: 0
- ✓ backend: 365/365 pass
- ✓ test_ws_notifications: 10/10 pass
- ✓ test_dagster_events: 16/16 pass (10 baseline + 6 new)
- ✓ test_ws_runs: 11/11 pass (F-051 regression check)
- ✓ ruff: clean
- ✓ mypy: clean
- ✓ codegen-drift: additive-only (no schema removals)
- ✓ async-sqla: clean (no sync session usage)
- ✓ file-scope: matches agreed §5 exactly

Hard invariants #5 (async SQLAlchemy) and #6 (OpenAPI ↔ TS sync) both satisfied.
No deviations from agreed.md. Implementation is production-ready.

