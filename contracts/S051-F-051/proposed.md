# Sprint S051-F-051 — Proposed Contract

**Feature**: F-051 — WebSocket run subscription: connecting to `/api/ws/runs` and subscribing to a `run_id` receives status-change events when the run transitions (e.g., pending → running → success)  
**Depends on**: F-050 (`passes: true`)  
**Sprint directory**: `contracts/S051-F-051/`  
**Author**: implementer (proposed)  
**Date**: 2026-06-05  
**Revision**: 2

---

## §1 Goal & Scope

Add a WebSocket endpoint at `ws://localhost:8000/api/ws/runs` that lets authenticated frontend clients subscribe to status-change events for specific runs. After a client connects (presenting a valid JWT via query param), it sends a `{"type":"subscribe","run_id":<int>}` message. When `POST /api/dagster/events` updates a `Run` row (F-050, lines 86–97 of `routers/dagster_events.py`), the endpoint calls an in-process broker that fans the `run.status_changed` event to all subscribers of that run. The client receives a JSON payload matching the schema specified in `docs/data_platform_design.md` §9.3 (lines 912–939). Disconnecting is clean (no server errors). No Redis dependency is introduced: a single-worker Docker Compose deployment permits in-process async broadcast.

---

## §2 Architecture

```
Browser / TestClient
  │  ws://host/api/ws/runs?token=<jwt>          (HTTP → 101 Upgrade)
  │
  ▼
ws_runs.py  websocket_runs_endpoint()
  │  1. decode JWT from query_params['token']
  │  2. Validate; close 1008 on failure
  │  3. Enter message loop (subscriptions: dict[int, asyncio.Queue] = {})
  │
  │  ── client sends ──────────────────────────────────────────────────
  │  {"type":"subscribe","run_id":N}
  │    │  4a. SELECT run WHERE id=N (existence check only)
  │    │  4b. If None → send {"type":"error","code":"not_found","run_id":N}; continue
  │    │  4c. If run.triggered_by != user.id → send {"type":"error","code":"unauthorized","run_id":N}; continue
  │    │  4d. subscriptions[N] = queue; broker.subscribe(run_id=N, queue=queue)
  │    │      → send {"type":"subscribed","run_id":N}
  │    ▼
  │  {"type":"unsubscribe","run_id":N}
  │    │  4e. broker.unsubscribe(run_id=N, queue=queue); del subscriptions[N]
  │    │  4f. → send {"type":"unsubscribed","run_id":N}
  │
  │  ── server fan-out path ────────────────────────────────────────────
  │
  │  dagster_events.py  post_dagster_event()
  │    │  (after await session.commit(), lines 97–100)
  │    │  try: broker.publish(run_id=run.id, event=RunStatusChangedEvent(...))
  │    │  except Exception: logger.warning(...)   # never break HTTP 200
  │    │    │
  │    │    ├─► queue_A.put_nowait(event)   (subscriber A)
  │    │    ├─► queue_B.put_nowait(event)   (subscriber B, same run_id)
  │    │    └─► (drop oldest if queue full)
  │    │
  │  ws_runs.py  _event_sender_task()
  │    │  asyncio.Queue.get() → websocket.send_text(event.model_dump_json())
  │    ▼
  │
Browser receives {"type":"run.status_changed","run_id":N,"kind":"extract",
                  "from":null,"to":"success","metadata":{}}
```

**In-process broker rationale**: Design doc line 941 mentions Redis pub/sub for multi-worker fan-out. However, `spec/tech-direction.md` and CLAUDE.md §Scope discipline explicitly restrict MVP to single-worker Docker Compose. Redis is absent from `pyproject.toml` (confirmed: no `redis` entry). Adding Redis solely for WS fan-out would expand infra scope beyond MVP boundaries for zero practical gain. An `asyncio.Queue`-per-subscriber `RunEventBroker` stored on `app.state` is sufficient, correct, and zero-dependency. Redis deferral is documented in §12.

---

## §3 Files Changed

| File | Status | Est. lines | Description |
|---|---|---|---|
| `apps/api/dataplat_api/realtime/__init__.py` | **NEW** | 1 | Empty package marker |
| `apps/api/dataplat_api/realtime/broker.py` | **NEW** | ~80 | `RunEventBroker` class: subscribe / unsubscribe / publish; includes "single-worker only" docstring (see §8) |
| `apps/api/dataplat_api/schemas/realtime.py` | **NEW** | ~50 | Pydantic models: `RunStatusChangedEvent`, `ClientCommand`, `ServerAck`, `ServerError` |
| `apps/api/dataplat_api/routers/ws_runs.py` | **NEW** | ~120 | `GET /api/ws/runs` WebSocket endpoint + `_event_sender_task` helper; per-connection `subscriptions` dict; `try/finally` cleanup |
| `apps/api/dataplat_api/routers/dagster_events.py` | **EDIT** | +8 | Capture `prev_status`; call `broker.publish()` after `await session.commit()` wrapped in `try/except` (§4.5) |
| `apps/api/dataplat_api/main.py` | **EDIT** | +6 | Instantiate `RunEventBroker` on `app.state.run_broker` in `lifespan`; `include_router(ws_runs_router)` |
| `apps/api/tests/test_ws_runs.py` | **NEW** | ~220 | WebSocket unit tests T1–T11 (see §10) |
| `packages/api-types/openapi.json` | **GENERATED** | delta | `make codegen` run; expected no diff since `RunStatusChangedEvent` is not referenced by any HTTP route (WS-only schema). Any diff that does appear must be committed in the same commit per invariant #6. |

### §3a OpenAPI note

WebSocket endpoints do not appear in OpenAPI. `RunStatusChangedEvent` is a named Pydantic model but is not referenced by any HTTP route, so `make codegen` will produce no diff and hard invariant #6 is satisfied trivially. No sentinel HTTP route will be added. `make codegen` MUST still be run and any diff (if any) committed in the same commit.

---

## §4 Wire Protocol — Server → Client

All server-to-client frames are UTF-8 JSON text frames.

### 4.1 `run.status_changed` (primary event)

Per design doc §9.3 (lines 912–921):

```json
{
  "type": "run.status_changed",
  "run_id": 42,
  "kind": "extract",
  "from": null,
  "to": "success",
  "metadata": {}
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | `"run.status_changed"` | yes | constant discriminator |
| `run_id` | `int` | yes | business Run PK (`Run.id`) |
| `kind` | `str` | yes | copied from `Run.kind` at publish time |
| `from` | `str \| null` | yes | previous status — **best-effort**: read `Run.status` before the state transition in `dagster_events.py` (requires a one-line read of the current value before overwriting; this is free since the row is already loaded in `run` ORM object — `run.status` holds the pre-update value at the point we call `broker.publish` because we haven't yet refreshed from DB). Set to `null` only if somehow unavailable. |
| `to` | `str` | yes | new status after transition |
| `metadata` | `dict` | yes | empty `{}` for MVP; extensible post-MVP |

**`from` field strategy (option b, enhanced)**: `dagster_events.py` already holds the loaded `run` ORM object at lines 86–94. The current (pre-update) value of `run.status` is available *before* the assignment `run.status = "running"` etc. We capture it as `prev_status = run.status` before the if/elif block and pass it to `broker.publish`. This is free — no extra DB query — and satisfies the design doc schema. Verification criterion only requires `to`, but including `from` is zero-cost here.

### 4.2 Acknowledgement frames

```json
{"type": "subscribed",   "run_id": 42}
{"type": "unsubscribed", "run_id": 42}
{"type": "error",        "code": "unauthorized", "run_id": 42}
{"type": "error",        "code": "not_found",    "run_id": 42}
{"type": "error",        "code": "bad_message",  "run_id": null}
```

### §4.5 `broker.publish` safety in `dagster_events.py` (OQ-4 resolved: MANDATORY)

`broker.publish()` is called after `await session.commit()` — the DB write is already durable at that point. However, an unhandled exception from `broker.publish()` would still propagate to FastAPI and return HTTP 500, breaking the F-050 SLA (the Dagster sensor expects HTTP 200). The call MUST be wrapped:

```python
try:
    request.app.state.run_broker.publish(run_id=run.id, event=event)
except Exception as exc:
    logger.warning("broker.publish failed for run %s: %s", run.id, exc)
```

This is required in `dagster_events.py` and is a DoD item (§14).

---

## §5 Wire Protocol — Client → Server

All client-to-server frames are UTF-8 JSON text frames.

```json
{"type": "subscribe",   "run_id": 42}
{"type": "unsubscribe", "run_id": 42}
```

**Bad JSON**: if the received text cannot be parsed as JSON, or the parsed object does not have a recognised `type`, the server replies with `{"type":"error","code":"bad_message","run_id":null}` and continues (does NOT close the connection). Rationale: closing on bad JSON would be needlessly harsh for transient UI bugs; the client can recover by re-sending.

**`run_id` type**: `int` (the business `Run.id` / `Run.id` PK, `BigInteger` — model line 293). Clients should not send `dagster_run_id` strings here.

---

## §6 Auth Flow

**Decision: query-param token (`?token=<jwt>`)**

Rationale:
1. Verification criterion says "Connect to `ws://localhost:8000/api/ws/runs` with a valid JWT" — the token must flow *at connection time*.
2. Browsers' native `WebSocket` constructor does not support custom headers. Query param is the universal browser-compatible approach.
3. FastAPI's `Depends(get_current_user)` uses `OAuth2PasswordBearer` which reads `Authorization: Bearer`. WS upgrade requests carry no such header from browsers.
4. First-message protocol (alternative) requires the server to buffer messages until auth, complicating the implementation.
5. Subprotocol header (alternative) works in some browsers but is non-standard and not supported by `TestClient.websocket_connect`.

**Production note**: tokens in query params may appear in server access logs. For production, the subprotocol approach (`Sec-WebSocket-Protocol: bearer.<jwt>`) should be adopted. This is documented in §12 (out of scope for MVP).

**Implementation** — manual JWT decode in the WS handler, reusing existing JWT logic from `auth/dependencies.py`:

```python
from fastapi import WebSocket, WebSocketDisconnect
import jwt
from dataplat_api.config import settings
from dataplat_api.db.models import User
from sqlalchemy import select

async def websocket_runs_endpoint(
    websocket: WebSocket,
    session: AsyncSession = Depends(get_session),
) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)   # Policy Violation
        return
    try:
        payload = jwt.decode(token, settings.SECRET_KEY,
                             algorithms=[settings.JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=1008)
        return
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    # ... message loop with subscriptions: dict[int, asyncio.Queue] = {}
```

WebSocket close code 1008 ("Policy Violation") is the correct code for auth failures per RFC 6455. Code 1003 is for "unsupported data type" (used for unparseable binary frames).

**No per-message reauth**: the JWT is validated only at connection time. If it expires mid-session, the session remains open until the client disconnects. This is a known limitation (see §9 and §12).

---

## §7 Owner-Scope Check

On each `subscribe` command, a **two-step query** is used so that `not_found` and `unauthorized` can be distinguished (T10):

```python
# Step 1: check existence
result = await session.execute(select(Run).where(Run.id == cmd.run_id))
run = result.scalar_one_or_none()
if run is None:
    await websocket.send_text(
        ServerError(code="not_found", run_id=cmd.run_id).model_dump_json()
    )
    continue  # keep connection open

# Step 2: check ownership
if run.triggered_by != user.id:
    await websocket.send_text(
        ServerError(code="unauthorized", run_id=cmd.run_id).model_dump_json()
    )
    continue

# Happy path — register subscription
queue = asyncio.Queue(maxsize=100)
subscriptions[cmd.run_id] = queue          # per-connection tracking (§8 L2)
broker.subscribe(run_id=cmd.run_id, queue=queue)
await websocket.send_text(
    ServerAck(type="subscribed", run_id=cmd.run_id).model_dump_json()
)
```

**`run.triggered_by != user.id` with NULL**: evaluates correctly when `triggered_by IS NULL` — Python `None != int` is `True` → denied. Admin-created runs (no `triggered_by`) are correctly blocked without an explicit NULL check (§7 para 3 intent).

**Two-step trade-off — existence leakage**: this query pattern reveals to authenticated users whether a given integer run ID exists at all (a `not_found` vs `unauthorized` response distinguishes the two cases, providing a timing oracle for run-ID enumeration). This trade-off is accepted because every authenticated user can already enumerate their own runs via `GET /api/runs?...` (F-049 list endpoint scopes by `triggered_by`), so cross-user run-ID enumeration is already blocked at the HTTP layer. The WS `not_found`/`unauthorized` distinction mirrors that same semantic and adds no new attack surface.

**Justification for owner-scope**: a run contains `config` (potential secrets) and `trigger_context` (JSONB). Without this check, any authenticated user could subscribe to any run's status stream by guessing integer IDs. The check is one async SELECT and is mandatory for MVP security.

---

## §8 Broker Semantics

**Class**: `RunEventBroker` in `apps/api/dataplat_api/realtime/broker.py`

```python
class RunEventBroker:
    """
    In-process asyncio event broker for run status notifications.

    # Single-worker only: this is an in-process asyncio structure.
    # Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
    # event loss for WS connections on a different worker than the one receiving
    # the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.
    """
    def __init__(self) -> None:
        self._subscribers: dict[int, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: int, queue: asyncio.Queue) -> None:
        self._subscribers.setdefault(run_id, []).append(queue)

    def unsubscribe(self, run_id: int, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: int, event: RunStatusChangedEvent) -> None:
        for queue in list(self._subscribers.get(run_id, [])):
            if queue.full():
                try:
                    queue.get_nowait()   # drop oldest
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
```

**Key design decisions**:

| Property | Decision | Rationale |
|---|---|---|
| Queue type | `asyncio.Queue(maxsize=100)` | Bounded; prevents unbounded memory growth per subscriber |
| Overflow policy | Drop oldest | Client that falls too far behind loses old events; acceptable (events are idempotent status transitions) |
| Thread safety | Single-threaded asyncio event loop — no locks needed | MVP is single uvicorn worker; no threading concerns |
| `publish()` is sync | Yes — called from the `post_dagster_event` async handler via `request.app.state.run_broker.publish(...)` after commit. `put_nowait` is sync on asyncio.Queue. No `asyncio.create_task` needed since `put_nowait` is non-blocking. | |
| Delivery semantics | Best-effort (fire-and-forget, no ack, no replay) | Consistent with F-050's best-effort sensor delivery (contracts/S050-F-050/agreed.md §11) |
| No module-level state | Broker instance lives on `app.state.run_broker` | Avoids import-time side effects; clean for testing |

**Delivery semantics — Race window (L1)**: No race-free subscription guarantee. Events published between the moment the client sends `{"type":"subscribe","run_id":N}` and the moment `broker.subscribe()` completes (i.e., during the async DB ownership check round-trip) are permanently lost. This is MVP-acceptable (best-effort). Frontend MUST poll `GET /api/runs/{id}` for authoritative current status; WS events are notification-only supplements, not the source of truth. See §9 failure modes.

**Per-connection subscription tracking (L2)**: The WS handler MUST maintain a local `subscriptions: dict[int, asyncio.Queue]` mapping `run_id → queue` for all subscriptions active on this connection. On each `subscribe` command: `subscriptions[cmd.run_id] = queue`. On `unsubscribe`: `del subscriptions[cmd.run_id]`. The main message loop MUST use a `try/finally` block:

```python
try:
    # ... message loop
except WebSocketDisconnect:
    pass
finally:
    for run_id, q in subscriptions.items():
        broker.unsubscribe(run_id, q)
    sender_task.cancel()
```

This ensures cleanup on ALL exit paths (WebSocketDisconnect, generic Exception, and normal return). Without this, queues leak permanently in `broker._subscribers`.

**Single-worker constraint (L3)**: `RunEventBroker` is an in-process asyncio structure. Running the API with `uvicorn --workers 2+` or gunicorn multiproc will silently drop all WS events for connections on a different worker than the one receiving the webhook (Worker A holds subscriber queues; Worker B receives `POST /api/dagster/events` → `broker.publish()` on Worker B finds no subscribers → events silently lost, no log, no error). The `RunEventBroker` class docstring (above) MUST state this constraint explicitly. The mandatory V2 fix when scaling is Redis pub/sub (§12).

**Event sender task per connection**: each WS connection spawns `asyncio.create_task(_event_sender_task(websocket, queue))` which loops on `queue.get()` and calls `websocket.send_text()`. On disconnect, the task is cancelled (via `task.cancel()` in the finally block above).

---

## §9 Failure Modes

| Scenario | Handling |
|---|---|
| Connect without `?token=` | `websocket.close(1008)` before accept |
| Connect with expired/invalid JWT | `websocket.close(1008)` before accept |
| Connect with valid JWT but user deleted | `websocket.close(1008)` before accept |
| Subscribe to nonexistent run | Reply `{"type":"error","code":"not_found","run_id":N}`; keep connection open |
| Subscribe to run owned by another user | Reply `{"type":"error","code":"unauthorized","run_id":N}`; keep connection open |
| Subscribe to run with `triggered_by IS NULL` | Reply `{"type":"error","code":"unauthorized","run_id":N}`; keep connection open (`None != user.id` → True → denied) |
| Bad JSON in client frame | Reply `{"type":"error","code":"bad_message","run_id":null}`; keep connection open; connection remains usable |
| `broker.publish` fires between subscribe command receipt and `broker.subscribe()` registration | Event silently lost. Client may miss transitions that occur during the async DB ownership-check round-trip. MVP-acceptable (best-effort). Frontend SHOULD poll `GET /api/runs/{id}` for authoritative current status; WS events are notification-only. |
| Client disconnects mid-session | `WebSocketDisconnect` caught → `finally` block iterates `subscriptions.items()` → `broker.unsubscribe(run_id, q)` for each → sender task cancelled. No WARNING/ERROR logged. |
| Broker queue full (100 events) | Oldest event dropped via `queue.get_nowait()`; new event enqueued. Connection preserved. |
| JWT expires mid-session | No reauth. Session remains open until natural disconnect. Known limitation. |
| `session.execute` raises | Generic `Exception` caught → `close(1011 Internal Error)` + `finally` cleanup |
| `broker.publish` raises | Caught by `try/except` in `dagster_events.py` (§4.5); logged as WARNING; HTTP 200 returned normally |

---

## §10 Verification Matrix

| Criterion / Test | What is verified | Pass condition |
|---|---|---|
| **VC-1** Connect to `ws://localhost:8000/api/ws/runs` with a valid JWT | T1 | HTTP 101 Switching Protocols (connection accepted) |
| **VC-2** Trigger a run; receive `{"type":"run.status_changed",...,"to":"success"}` within 2 min | T8 (unit); integration test note | `to` field equals `"success"`; `run_id` matches |
| **VC-3** Disconnecting does not cause server errors | T7 | No exception raised server-side; no ERROR log |
| **T1** connect with valid JWT | 101 accepted | `websocket.accepted` after connect |
| **T2** connect without token | close 1008 | `WebSocketDisconnect(1008)` or 403 from TestClient |
| **T3** connect with invalid/expired JWT | close 1008 | `WebSocketDisconnect(1008)` |
| **T4** subscribe to own run → ack | `subscribed` message received | `{"type":"subscribed","run_id":N}` |
| **T5** subscribe to another user's run → error, no events | `unauthorized` error | `{"type":"error","code":"unauthorized","run_id":N}`; subsequent `broker.publish` does NOT deliver to this client |
| **T6** subscribe + `broker.publish()` directly → event received | event fan-out | `{"type":"run.status_changed","run_id":N,"to":"success"}` received |
| **T7** client disconnect → no server error | clean teardown | `broker.unsubscribe` called for all subscriptions; no unhandled exception; sender task cancelled |
| **T8** end-to-end: POST `/api/dagster/events` → `broker.publish` → client receives event | full pipe via dagster_events router | event arrives on WS connection after HTTP POST |
| **T9** bad JSON command → `bad_message` error, connection stays open | graceful error | `{"type":"error","code":"bad_message"}` returned; subsequent valid `subscribe` command succeeds on same connection |
| **T10** subscribe to nonexistent run → `not_found` error | unknown run_id | `{"type":"error","code":"not_found","run_id":N}` (NOT "unauthorized") |
| **T11** subscribe to run with `triggered_by IS NULL` → `unauthorized` error | NULL ownership denied | `{"type":"error","code":"unauthorized","run_id":N}`; confirms NULL != user.id path |

**Test implementation note**: `fastapi.testclient.TestClient.websocket_connect("/api/ws/runs?token=...")` is the correct pattern (confirmed by FastAPI docs and TestClient WS support in Starlette). Session dependency override via `app.dependency_overrides[get_session]` applies to WS endpoints as well. The broker can be injected via `app.state.run_broker = RunEventBroker()` in test setup (no lifespan needed for unit tests; broker is instantiated directly).

---

## §11 Hard-Invariant Audit

| # | Invariant | Status | Justification |
|---|---|---|---|
| 1 | **Lineage mandatory** | **N/A** | No `Commit` object created or modified. This feature is a real-time notification layer, not a data lineage operation. |
| 2 | **Storage separation + CAS** | **N/A** | No blobs stored. No MinIO interaction. WS messages are ephemeral in-memory events. |
| 3 | **Schema frozen post-publish** | **N/A** | No Silver/Gold dataset schema touched. |
| 4 | **LLM calls go through gateway** | **N/A** | No LLM calls anywhere in this feature. |
| 5 | **Async SQLAlchemy** | **✓ Required** | Two async DB accesses: (a) `SELECT User` at WS connect (JWT → user lookup); (b) two-step `SELECT Run WHERE id=N` then ownership check at subscribe time. Both use `await session.execute(select(...))` + `result.scalar_one_or_none()`. No `session.query()`. `get_session` dependency used via `Depends`. |
| 6 | **OpenAPI ↔ TS type sync** | **✓ Required** | WS endpoint does not appear in OpenAPI. `RunStatusChangedEvent` is not referenced by any HTTP route → `make codegen` expected to produce no diff. `make codegen` MUST be run and any diff committed in the same commit regardless. Invariant satisfied trivially if no diff. |

---

## §12 Out-of-Scope Explicit Deferrals

| Item | Deferral reason |
|---|---|
| **Redis pub/sub multi-worker fan-out** | MVP is single-worker Docker Compose. Redis is not in `pyproject.toml`. Post-MVP when horizontal scaling is needed. Design doc line 941 mentions Redis; this is the V2 path. **Note**: running `uvicorn --workers N` (N > 1) causes silent WS event loss; single worker is the mandatory MVP deployment constraint (see §8 L3). |
| **`asset.materialized` and `chunks.added` WebSocket events** | Design doc §9.3 defines these as separate event types. Out of scope for F-051 (F-051 scope is run status only). |
| **Per-message JWT reauth** | Token is validated at connect time only. Mid-session expiry keeps the session open. Production should use short-lived tokens + reconnect-on-401 on the client side. |
| **Server-pushed run-cancellation API** | `POST /api/runs/{id}/cancel` is a separate feature; not triggered by this broker. |
| **Message replay / backfill on reconnect** | If a client disconnects and reconnects, it misses events that occurred during the gap. No persistence of events. Post-MVP concern. |
| **Subprotocol-header JWT for production** | `Sec-WebSocket-Protocol: bearer.<jwt>` is the log-safe alternative to query-param tokens. Deferred post-MVP. |
| **Admin-override subscription** | Admin users subscribing to runs they did not trigger. MVP uses `triggered_by == user.id` exclusively. |
| **`/ws/notifications` endpoint** | Design doc §9.1 (line 853) lists `ws/notifications`. Out of scope for F-051. |
| **Long-lived DB connection per WS** | `Depends(get_session)` holds a DB pool connection for the lifetime of each WS connection (not per-query). At default pool_size=20, this caps concurrent WS connections to ~20. Harmless for MVP (solo/small teams). Document in `ws_runs.py` docstring for future operators. |

---

## §13 Open Questions (closed)

All OQs settled in `feedback.md` round 1; see §15 Round-1 addenda for resolutions.

---

## §14 Definition of Done

A sprint is `done` iff **all** of the following hold:

- [ ] `contracts/S051-F-051/agreed.md` exists with every item addressed.
- [ ] `apps/api/dataplat_api/realtime/__init__.py` exists (empty package).
- [ ] `apps/api/dataplat_api/realtime/broker.py` contains `RunEventBroker` with `subscribe`, `unsubscribe`, `publish`; class docstring includes "Single-worker only" constraint (§8 L3).
- [ ] `apps/api/dataplat_api/schemas/realtime.py` contains `RunStatusChangedEvent`, `ClientCommand`, `ServerAck`, `ServerError`.
- [ ] `apps/api/dataplat_api/routers/ws_runs.py` contains `GET /api/ws/runs` WS endpoint; JWT auth via `?token=`; two-step owner-scope check (§7); per-connection `subscriptions: dict[int, asyncio.Queue]`; `try/finally` cleanup iterating all subscriptions (§8 L2); sender task; clean disconnect.
- [ ] `apps/api/dataplat_api/routers/dagster_events.py` captures `prev_status`; calls `broker.publish()` after `await session.commit()` wrapped in `try/except` (§4.5).
- [ ] `apps/api/dataplat_api/main.py` instantiates `RunEventBroker` on `app.state.run_broker` in `lifespan`; registers `ws_runs_router`.
- [ ] `apps/api/tests/test_ws_runs.py` contains all T1–T11 tests; all pass.
- [ ] T9 explicitly asserts the connection remains open after `bad_message` error (subsequent subscribe succeeds).
- [ ] T10 asserts `code="not_found"` (not `"unauthorized"`) for a nonexistent run_id.
- [ ] T11 asserts `code="unauthorized"` for a run with `triggered_by IS NULL`.
- [ ] `bash verify/checks.sh backend` exits 0.
- [ ] `make codegen` run; `packages/api-types/openapi.json` diff (if any) committed in the same commit.
- [ ] `bash verify/checks.sh all` exits 0.
- [ ] `contracts/S051-F-051/review-final.md` ends with `APPROVED`.
- [ ] `spec/feature_list.json` F-051 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.
- [ ] `git push` executed after sprint close.

---

## §15 Round-1 addenda

**Revision**: proposed.md rev 1 → rev 2. All changes below fold reviewer findings from `contracts/S051-F-051/feedback.md`.

| Finding | Severity | Where folded |
|---|---|---|
| **M1** — §7 single combined WHERE query contradicts two-step prose; T10 fails | MUST-FIX | §2 architecture diagram (4a/4b/4c updated); §7 code snippet replaced with two-step version + trade-off / existence-leakage justification; §11 invariant 5 updated; §14 DoD T10 assertion clarified |
| **L1** — Subscribe+publish race not documented | SHOULD-FIX | §8 "Delivery semantics — Race window" paragraph added; §9 failure modes table new row for race window; frontend poll requirement stated in both locations |
| **L2** — Per-connection subscription tracking not specified | SHOULD-FIX | §2 diagram updated (subscriptions dict shown in step 3); §7 happy-path snippet assigns `subscriptions[cmd.run_id] = queue`; §8 "Per-connection subscription tracking" paragraph added with explicit `try/finally` cleanup contract; §9 disconnect row updated; §14 DoD checklist items added |
| **L3** — Multi-worker silent event loss not documented | SHOULD-FIX | §8 `RunEventBroker` class docstring requirement added (single-worker note); §8 "Single-worker constraint" paragraph added; §12 Redis deferral row updated with multi-worker warning |
| **NIT-1** — §3 table openapi.json row contradicts §3a | COSMETIC | §3 table last row description corrected to "expected no diff"; §3a cleaned up (OQ-1 alternative text removed) |
| **NIT-2** — No test for `triggered_by IS NULL` → denied | COSMETIC | T11 added to §10 verification matrix; §9 new failure mode row for NULL triggered_by; §14 DoD T11 assertion added |
| **NIT-3** — Long-lived DB session per WS not acknowledged | COSMETIC | §12 new deferral row "Long-lived DB connection per WS" added |
| **OQ-1** — Sentinel route for OpenAPI exposure? | CLOSED | No sentinel route. §3a updated. §11 invariant 6 updated. Resolved: `make codegen` produces no diff; invariant #6 trivially satisfied. |
| **OQ-2** — Bad JSON: close 1003 or reply + continue? | CLOSED | Reply `{"type":"error","code":"bad_message"}` and continue. Already in rev 1; OQ-2 reference removed from §5. T9 updated to assert connection stays open. |
| **OQ-3** — Allow `visibility=internal` runs? | CLOSED | `triggered_by == user.id` only. Run has no visibility column; moot for MVP. No code change needed. |
| **OQ-4** — try/except around broker.publish in dagster_events? | CLOSED | MANDATORY. New §4.5 added; §2 diagram updated (try/except shown on fan-out path); §14 DoD item references §4.5; "(per OQ-4)" reference replaced with "(§4.5)". |
