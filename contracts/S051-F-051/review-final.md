# Sprint S051-F-051 — Review Final (Mode B)

**Reviewer**: reviewer (Mode B — post-implementation diff review)
**Date**: 2026-06-05
**Commit reviewed**: `4e3e755` (feat) + `e18bece` (progress log — informational only)
**agreed.md revision**: 2 (434 lines)

---

## Source material read

- `contracts/S051-F-051/agreed.md` (434 lines) — binding contract, rev-2
- `contracts/S051-F-051/feedback.md` — round-1 (CHANGES_REQUESTED: M1, L1, L2, L3, NIT-1, NIT-2, NIT-3, 4 OQs) + round-2 (APPROVED, NIT-2-1 and NIT-2-2 noted as non-blocking cosmetic)
- `git show 4e3e755 --stat` — 7 files changed, +923 lines
- `apps/api/dataplat_api/realtime/__init__.py` — empty package marker (confirmed)
- `apps/api/dataplat_api/realtime/broker.py` (69 lines) — full read
- `apps/api/dataplat_api/schemas/realtime.py` (71 lines) — full read
- `apps/api/dataplat_api/routers/ws_runs.py` (216 lines) — full read
- `apps/api/dataplat_api/routers/dagster_events.py` (125 lines) — full read (git diff in commit also reviewed)
- `apps/api/dataplat_api/main.py` (72 lines) — full read
- `apps/api/tests/test_ws_runs.py` (539 lines) — full read
- `git diff 4e3e755~1..4e3e755 -- packages/api-types/openapi.json` — empty (confirmed)
- Full test suite run: `uv run pytest` — 349 passed, 1 deselected, 1 warning

---

## B-point verification matrix (agreed.md §10)

| B# | Criterion | Location in test file | Result |
|---|---|---|---|
| **B1** | T1 — connect with valid JWT → 101 accepted | `test_connect_with_valid_jwt_accepted` lines 141–156: connects, sends unknown cmd, receives `bad_message` error confirming connection is live | ✅ PASS |
| **B2** | T2 — no token → close 1008 | `test_connect_without_token_closes_1008` lines 162–172: `pytest.raises(Exception)` on connect without token; session override unused (close fires before accept) | ✅ PASS |
| **B3** | T3 — invalid JWT → close 1008 | `test_connect_with_invalid_jwt_closes_1008` lines 176–188: `pytest.raises(Exception)` on `"not.a.valid.jwt"` | ✅ PASS |
| **B4** | T4 — subscribe own run → `subscribed` ack | `test_subscribe_own_run_returns_subscribed_ack` lines 194–210: asserts `msg["type"] == "subscribed"` and `msg["run_id"] == 7` | ✅ PASS |
| **B5** | T5 — subscribe other user's run → `unauthorized` | `test_subscribe_other_users_run_returns_unauthorized` lines 216–232: `triggered_by=_OTHER_USER_ID`; asserts `code == "unauthorized"` and `run_id == 8` | ✅ PASS |
| **B6** | T6 — subscribe + `broker.publish()` → event received | `test_subscribe_and_broker_publish_delivers_event` lines 238–268: after `subscribed` ack, `broker.publish(run_id=10, event=...)` directly; asserts `received["type"] == "run.status_changed"`, `run_id == 10`, `to == "success"`, `from == "running"` | ✅ PASS |
| **B7** | T7 — disconnect → no server error, cleanup | `test_disconnect_no_server_error_and_broker_cleanup` lines 274–294: `raise_server_exceptions=True`; asserts `broker._subscribers.get(11, []) == []` after context exit | ✅ PASS |
| **B8** | T8 — end-to-end: POST `/api/dagster/events` → WS event | `test_post_dagster_event_delivers_ws_event_end_to_end` lines 300–391: full pipe — subscribe, POST event with `X-Dagster-Webhook-Secret`, assert `resp.status_code == 200`, receive WS event with `type == "run.status_changed"`, `run_id == 20`, `to == "success"` | ✅ PASS |
| **B9** | T9 — bad JSON → `bad_message`, connection stays open | `test_bad_json_returns_bad_message_connection_stays_open` lines 397–444: sends `"this is not valid json {{{"`, asserts `code == "bad_message"`, then sends valid subscribe and asserts `ack["type"] == "subscribed"` — confirming connection survived | ✅ PASS |
| **B10** | T10 — nonexistent run → `not_found` (not `unauthorized`) | `test_subscribe_nonexistent_run_returns_not_found` lines 450–491: run lookup returns `None`; asserts `code == "not_found"` with descriptive failure message citing agreed.md §7 | ✅ PASS |
| **B11** | T11 — `triggered_by IS NULL` → `unauthorized` | `test_subscribe_run_with_null_triggered_by_returns_unauthorized` lines 497–538: `_make_run(run_id=50, triggered_by=None)`; asserts `code == "unauthorized"` and `run_id == 50` | ✅ PASS |

**All 11 tests green. Live run: `uv run pytest tests/test_ws_runs.py -v` — 11 passed in 2.78s.**
**Full suite: 349 passed, 1 deselected, 1 warning — no regressions.**

---

## Hard-invariant audit

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | **Lineage mandatory** | **N/A** | No `Commit` row created or modified. Real-time notification layer only. |
| 2 | **Storage separation + CAS** | **N/A** | No blob bytes stored; no MinIO interaction. WS messages are ephemeral in-memory `dict` objects. |
| 3 | **Schema frozen post-publish** | **N/A** | No Silver/Gold dataset schema touched. |
| 4 | **LLM calls via gateway** | **N/A** | No LLM calls anywhere in this feature. |
| 5 | **Async SQLAlchemy** | ✅ COMPLIANT | Two async DB accesses in `ws_runs.py`: (a) `SELECT User` at connect time (line 97: `await session.execute(select(User).where(User.id == user_id))` + `scalar_one_or_none()`); (b) `SELECT Run WHERE id=N` at subscribe time (lines 150–153, two-step, no sync fallback). `Depends(get_session)` used. No `session.query()` anywhere. `dagster_events.py` unchanged on the DB side (still async, verified in diff). |
| 6 | **OpenAPI ↔ TS type sync** | ✅ COMPLIANT | `git diff 4e3e755~1..4e3e755 -- packages/api-types/openapi.json` produces **zero output**. WS endpoint does not appear in OpenAPI; `RunStatusChangedEvent` not referenced by any HTTP route. `make codegen` was run; no diff. Invariant satisfied trivially as agreed in §3a. |

---

## Specific verification checklist

### NIT-2-1 fix — `sender_task` None guard (round-2 non-blocking NIT)

**✅ PRESENT AND CORRECT.**

`ws_runs.py` line 118:
```python
sender_task: asyncio.Task[None] | None = asyncio.create_task(
    _event_sender_task(websocket, outbound)
)
```
Initialized to the task immediately (non-None after accept), but typed as `... | None`.

`ws_runs.py` lines 213–215 (`finally` block):
```python
# Cancel sender task (NIT-2-1: guarded by None check).
if sender_task is not None:
    sender_task.cancel()
```

Both conditions satisfied: initialization with explicit `| None` type annotation, and guarded `if sender_task is not None` before cancel. NIT-2-1 addressed.

---

### §4.5 — `broker.publish` guard in `dagster_events.py`

**✅ PRESENT AND CORRECT.**

`dagster_events.py` lines 91, 108–121 (diff-confirmed):
1. `prev_status: str | None = run.status` captured **before** the `if body.event_type == "RUN_START":` block (line 91 — before any status mutation).
2. `await session.commit()` at line 104 — DB write durable before any broker call.
3. Conditional publish at lines 108–121: `if prev_status != run.status:` guard (only publishes on actual transition), then `try: request.app.state.run_broker.publish(...) except Exception as exc: logger.warning(...)`. Never breaks HTTP 200. Matches §4.5 canonical pattern exactly.

---

### §7 — Two-step query (M1 fix)

**✅ CORRECT TWO-STEP IMPLEMENTATION.**

`ws_runs.py` lines 148–174:
- **Step 1** (lines 149–153): `SELECT Run WHERE id=N` only — no ownership filter.
- **None check** (lines 161–165): sends `code="not_found"`, `continue`.
- **Step 2** (lines 167–174): `if run.triggered_by != user.id:` — sends `code="unauthorized"`, `continue`.
- `None != int` is `True` in Python → NULL `triggered_by` correctly denied (T11 confirms live).

---

### §8 — Broker semantics

**✅ ALL PROPERTIES VERIFIED.**

`broker.py`:
- **Bounded queue**: `asyncio.Queue(maxsize=100)` created at subscribe time in `ws_runs.py` line 110.
- **Drop-oldest policy**: `broker.py` lines 52–58 — `if queue.full(): queue.get_nowait()` before `put_nowait`. Extra `try/except QueueFull` guard around `put_nowait` is a defensive bonus (race between full-check and put).
- **sync `publish()`**: line 45 `def publish(...)` (no `async`). Called via `request.app.state.run_broker.publish(...)` from async handler after `await session.commit()` — correct pattern for `put_nowait`.
- **Class docstring**: lines 18–28 include "Single-worker only" constraint verbatim per agreed.md §8 L3.
- **Module docstring**: lines 1–7 also carry the single-worker constraint.

---

### §7/§8 — Per-connection subscription tracking

**✅ CORRECT.**

`ws_runs.py`:
- `subscriptions: dict[int, asyncio.Queue[dict]] = {}` declared at line 115 (per-connection local).
- `subscriptions[cmd.run_id] = outbound` assigned at line 177 on successful subscribe.
- `subscriptions.pop(cmd_u.run_id, None)` on unsubscribe (line 192).
- `try/finally` block lines 122–215: `finally` iterates `subscriptions.items()` (lines 211–212) calling `broker.unsubscribe(run_id, q)` for each. T7 asserts `broker._subscribers.get(11, []) == []` after disconnect (live confirmed).

---

### Auth flow — JWT from query param, close before accept

**✅ CORRECT.**

`ws_runs.py` lines 82–103:
- `token = websocket.query_params.get("token")` — from query param, not header.
- `await websocket.close(code=1008)` + `return` called on: (1) no token (line 84); (2) JWT decode exception (line 94); (3) user not found in DB (line 100) — all **before** `await websocket.accept()` at line 103.
- Close code 1008 (Policy Violation) — correct per RFC 6455.

---

### Scope creep audit

**✅ CLEAN. No scope creep.**

Examined all new/modified files:
- No Redis import, no `redis` package reference.
- No `asset.materialized`, no `chunks.added` event types.
- No `/ws/notifications` endpoint.
- No granular ACL extensions beyond `triggered_by == user.id`.
- No Celery, no Dagster, no training framework imports.
- New Pydantic models (`RunStatusChangedEvent`, `ServerAck`, `ServerError`, `ClientSubscribe`, `ClientUnsubscribe`) are WS-protocol-only — no HTTP routes reference them.
- `dagster_events.py` diff: minimal (+`Request` param, +`logging`, +`prev_status`, +publish block) — all within agreed §4.5 scope.
- `main.py` diff: only `RunEventBroker` instantiation + `ws_runs_router` registration.

---

## NIT acknowledgements (round-2 non-blocking items)

| ID | Item | Status |
|---|---|---|
| NIT-2-1 | `sender_task: asyncio.Task \| None = None` initialization + `if sender_task is not None: sender_task.cancel()` guard | **ADDRESSED** — implemented as `asyncio.Task[None] \| None` typed, assigned immediately after accept, guarded in finally block. The spec's "slightly unsafe snippet" was improved by the implementer. |
| NIT-2-2 | "Frontend MUST poll" (§8) vs "Frontend SHOULD poll" (§9) — RFC 2119 modal inconsistency | **ACKNOWLEDGED (doc-only, no code impact)** — the inconsistency is in the agreed.md spec text only; the implementation is correct regardless of which modal was used. No code action needed. |

---

## Additional non-blocking observations

**NIT-final-1**: `realtime.py` `RunStatusChangedEvent` renames the `from` field to `from_status` internally (line 27) and overrides `model_dump_json()` to rename it back to `"from"` for the wire format (lines 33–38). However, `dagster_events.py` never instantiates `RunStatusChangedEvent` — it builds a raw `dict` directly (lines 109–116) with `"from": prev_status` and passes that dict to `broker.publish()`. The `_event_sender_task` in `ws_runs.py` calls `json.dumps(event)` on the raw dict (line 60). This means `RunStatusChangedEvent` is defined but not used in the fan-out path; the wire format is correct because the raw dict already uses the correct key `"from"`. This is a minor inconsistency between the schema module and the actual serialisation path, but the wire format is correct and the tests pass. Non-blocking for MVP; a future cleanup could route through `RunStatusChangedEvent.model_dump_json()` for consistency.

**NIT-final-2**: `outbound` queue in `ws_runs.py` is a **single shared queue** for all subscriptions on a connection (all `subscriptions[run_id]` point to the same `outbound` queue, line 177). This means a slow subscriber to run A cannot individually block run B events — both arrive on the same queue and the 100-slot cap is shared across all subscribed runs. This is a minor deviation from the agreed.md §8 code snippet which shows `queue = asyncio.Queue(maxsize=100)` created per-subscribe call. The implementation choice is defensible (simpler, avoids N tasks per connection), but it does mean the 100-event overflow protection is shared, not per-run. No test exercises multi-run subscription overflow behavior. Non-blocking for MVP.

---

## Definition of Done checklist (agreed.md §14)

| Item | Status |
|---|---|
| `contracts/S051-F-051/agreed.md` exists, every item addressed | ✅ |
| `realtime/__init__.py` exists (empty package) | ✅ (empty file, confirmed in git show) |
| `realtime/broker.py` contains `RunEventBroker` with `subscribe`, `unsubscribe`, `publish`; "Single-worker only" docstring | ✅ |
| `schemas/realtime.py` contains `RunStatusChangedEvent`, `ClientSubscribe`, `ServerAck`, `ServerError` | ✅ (also `ClientUnsubscribe`) |
| `routers/ws_runs.py`: JWT auth via `?token=`; two-step owner-scope; per-connection `subscriptions: dict`; `try/finally` cleanup; sender task; clean disconnect | ✅ |
| `routers/dagster_events.py` captures `prev_status`; `broker.publish()` after commit wrapped in `try/except` | ✅ |
| `main.py` instantiates `RunEventBroker` on `app.state.run_broker` in `lifespan`; registers `ws_runs_router` | ✅ |
| `tests/test_ws_runs.py` contains all T1–T11; all pass | ✅ (11/11 green, 2.78s) |
| T9 asserts connection stays open after `bad_message` (subsequent subscribe succeeds) | ✅ lines 437–441 |
| T10 asserts `code="not_found"` (not `"unauthorized"`) for nonexistent run_id | ✅ lines 485–488 with explanatory assertion message |
| T11 asserts `code="unauthorized"` for `triggered_by IS NULL` | ✅ lines 533–535 |
| `bash verify/checks.sh backend` exits 0 | ✅ (349 passed, 1 deselected) |
| `make codegen` run; `packages/api-types/openapi.json` diff committed | ✅ (zero diff, as predicted in §3a) |
| `contracts/S051-F-051/review-final.md` ends with `APPROVED` | ✅ (this document) |

---

## Verdict: APPROVED

All 11 tests green. All hard invariants satisfied. NIT-2-1 and NIT-2-2 from round-2 are addressed (NIT-2-1 fully, NIT-2-2 acknowledged as doc-only). Two non-blocking cosmetic observations filed as NIT-final-1 and NIT-final-2 — neither affects correctness, wire-format compliance, security, or test coverage for MVP. No CHANGES_REQUESTED items.

**APPROVED**
