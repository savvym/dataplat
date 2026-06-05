# Sprint S052-F-052 — Proposal Contract (rev-2)
# WebSocket Notification Subscription: /api/ws/notifications

**Sprint ID:** S052-F-052  
**Feature:** F-052 (infra, Phase 1, P1)  
**Author:** implementer  
**Date:** 2026-06-05  
**Revision:** 2 (addresses H1, H2, M1, M2, M3, L1, L2, NIT-1 from round-1 feedback)  
**Depends on:** F-051 ✓ (passes: true — RunEventBroker, ws_runs.py pattern, dagster_events.py broker.publish hook all in place)

---

## §1 Goal & Verification Criteria

> Verbatim from `spec/feature_list.json`:

1. **Connect** to `/api/ws/notifications`
2. **Trigger** an extraction run; receive a message:
   ```json
   {"type": "asset.materialized", "asset_key": "extract_mineru", "partition_key": "src_..."}
   ```
3. **Receive** a message after chunking completes:
   ```json
   {"type": "chunks.added", "source_id": <id>, "count": <N>}
   ```

All three criteria are achieved entirely in the unit-test layer (no live Dagster required) by injecting the broker directly, exactly as T5/T6 in F-051.

---

## §2 Scope Boundaries

### IN SCOPE

- `GET /api/ws/notifications` — authenticated stream; delivers all notifications for the connected user.
- `AssetMaterializedEvent` — fired when `extract_mineru` (or any `sources_partitions` asset) completes.
- `ChunksAddedEvent` — fired when the `chunks` asset completes for a partition.
- `POST /api/dagster/events` extension — accept a new `event_type` family (`ASSET_MATERIALIZATION`) for both events above.
- `NotificationBroker` — parallel broker keyed by `user_id` (distinct from `RunEventBroker` keyed by `run_id`).
- JWT-via-query-param auth (same as F-051 — no client subscribe/unsubscribe protocol; all notifications for the user are streamed automatically).
- Unit tests (backend layer only).

### EXPLICITLY OUT OF SCOPE

| Deferred item | Authority |
|---|---|
| Per-asset or per-run filtering by the client | CLAUDE.md scope discipline — MVP uses simplest delivery |
| Message replay / backfill on reconnect | F-051 §9 deferred; not in MVP |
| Cross-worker fan-out / Redis pub-sub | CLAUDE.md §Hard invariants "Scope discipline" — Redis already deferred in F-051 |
| All `attr_quality`, `attr_lang`, `attr_minhash` materialization events | Not in F-052 verification criteria; add only what is tested |
| `dataset.materialized` event | Separate concern; not in F-052 criteria |
| Per-user ACL on asset events beyond `run.triggered_by` ownership | CLAUDE.md §11.6 deferred |
| MFA / OAuth / social login | CLAUDE.md §Scope discipline |
| Replay-on-reconnect | F-051 §9 deferred |

---

## §3 Architecture

### 3.1 Broker Shape

**Choice: `NotificationBroker` keyed by `user_id` (integer), separate from `RunEventBroker`.**

Rationale:
- F-052 events are scoped to a user (the user who triggered the underlying run). They are not per-run-id objects (a single user may have multiple inflight runs), so a user_id key is the natural routing dimension.
- Keeping it a separate class from `RunEventBroker` avoids polluting `RunEventBroker`'s `run_id` namespace and allows independent evolution (e.g., NotificationBroker may later receive broadcast events).
- Structurally identical to `RunEventBroker`: `dict[int, list[asyncio.Queue[dict]]]`, drop-oldest maxsize=100, sync `publish()` called from the async webhook handler.

**File:** `apps/api/dataplat_api/realtime/notification_broker.py` — NEW file, parallel to `broker.py`. Do NOT extend `broker.py`; keeping them separate avoids breaking F-051 tests and keeps class responsibilities clean.

### 3.2 Publish Hook Points

**Chosen pattern: Extend `POST /api/dagster/events` to accept new `event_type` values.**

Alternatives considered:
- **New endpoint `POST /api/dagster/asset-events`** — cleaner schema isolation but doubles authentication plumbing (another secret check) and requires the Dagster sensor to be wired to two different URLs. Adds complexity for marginal benefit.

**Decision:** Extend `DagsterRunEventPayload` to accept one new `event_type` literal:
- `"ASSET_MATERIALIZATION"` — sent by new Dagster `@asset_sensor` instances (added to `definitions.py`) after each asset materializes.
- The payload carries two new optional fields: `asset_key: str | None` and `partition_key: str | None`, plus `metadata: dict[str, Any]` for carrying `chunk_count`.

The `POST /api/dagster/events` handler dispatches:
- On `event_type in ("RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED")` → existing Run-status path (no change).
- On `event_type == "ASSET_MATERIALIZATION"` → new branch:
  - Look up the `Run` row by `dagster_run_id` (same query as existing RUN_START/SUCCESS handler).
  - Map `asset_key` to event type: `"chunks"` → `ChunksAddedEvent`; anything else → `AssetMaterializedEvent`.
  - Publish to `notification_broker.publish(user_id=run.triggered_by, event=...)`.

### 3.3 Run Lookup (unchanged from round-1 §3.3)

**Payload for `ASSET_MATERIALIZATION`:**
```json
{
  "event_type": "ASSET_MATERIALIZATION",
  "dagster_run_id": "<backfill-id>",
  "asset_key": "extract_mineru",
  "partition_key": "src_42",
  "metadata": {"chunk_count": 17},
  "timestamp": "2026-06-05T10:00:00+00:00"
}
```

The `dagster_run_id` is the backfill ID (same field already used by `fastapi_run_status_sensor`). The `@asset_sensor` inside Dagster calls `context.instance.get_run_by_id(event.run_id)` to retrieve the full `DagsterRun`, then reads `.tags.get("dagster/backfill") or event.run_id` — identical extraction logic to F-050's sensor. FastAPI looks up `Run.triggered_by` via `WHERE dagster_run_id = payload.dagster_run_id` (no new DB access pattern).

### 3.4 Subscribe Flow (No Subscribe Protocol)

Unlike `ws_runs.py` (which requires an explicit `subscribe` message), `/api/ws/notifications` is a **fire-and-forget stream**:

1. Client connects: `ws://host/api/ws/notifications?token=<jwt>`
2. Server decodes JWT, looks up user, accepts connection.
3. Server registers `queue = asyncio.Queue(maxsize=100)` with `notification_broker.subscribe(user_id, queue)`.
4. Server spawns `_event_sender_task` draining `queue` → `websocket.send_text`.
5. Client passively receives all events fired for that `user_id`.
6. On disconnect: `notification_broker.unsubscribe(user_id, queue)`, `sender_task.cancel()`.

**No client subscribe/unsubscribe messages** — all relevant events for the authenticated user are streamed. This is simpler than `ws_runs.py` (no subscription dict, no subscribe/unsubscribe handler, no `ServerAck`). Server MAY send `ServerError(code="bad_message")` if the client sends unexpected text, but the connection stays open.

### 3.5 Dagster Sensor: Committed Path — Two `@asset_sensor` Instances

> **H1 resolution:** Live container probe conducted against Dagster 1.11.16 running in the dev cluster. All three candidate paths evaluated. One path chosen. The other two are eliminated below.

#### Live probe results (2026-06-05, Dagster 1.11.16)

**OQ-3 answered — YES:** `DagsterRun.asset_selection` is `Optional[AbstractSet[AssetKey]]`. On two live backfill runs in the cluster, `asset_selection` was `frozenset({AssetKey(["extract_mineru"])})` — NOT None. Path 1 (extend `fastapi_run_status_sensor` using `context.dagster_run.asset_selection`) is technically viable from the context-attribute standpoint.

**Critical blocker for Path 1 (run_status_sensor + asset_selection):** `RunStatusSensorContext` exposes `context.dagster_run.asset_selection: frozenset[AssetKey]` but does NOT provide access to per-asset materialization metadata (the `chunk_count` field recorded by `context.add_output_metadata({"chunk_count": MetadataValue.int(len(rows))})` in `definitions.py:236`). A `@run_status_sensor` fires on run lifecycle events, not on asset-level events, and has no `EventLogEntry` argument — it cannot read asset metadata. Therefore Path 1 can only produce `count=0` for `chunks.added`. This was the concern raised in feedback finding H1.

**Path 2 (`@asset_sensor`) is viable and gives real chunk_count:** In Dagster 1.11.16, `@asset_sensor` callback signature is `fn(context: SensorEvaluationContext, asset_event: EventLogEntry) -> RunRequest | SkipReason | None`. The `asset_event` is an `EventLogEntry` whose `.asset_materialization` property returns the `AssetMaterialization` object. The correct metadata access path is:
```python
asset_event.asset_materialization.metadata["chunk_count"].value
```
This was verified by inspecting the `EventLogEntry.asset_materialization` property source in the container. The `chunk_count` key is present in live `chunks` asset materializations (confirmed at `definitions.py:236`).

**Path 3 (tag injection via `gateway.py`)** is eliminated — it requires modifying `gateway.py` at backfill launch time and is a more invasive change with no benefit over Path 2.

#### Committed decision: Path 2 — Two dedicated `@asset_sensor` instances

Add to `dagster/dagster_platform/definitions.py`:
- `extract_mineru_notification_sensor` — `@asset_sensor(asset_key=AssetKey(["extract_mineru"]))`, fires on each `extract_mineru` materialization. POSTs `ASSET_MATERIALIZATION` with `asset_key="extract_mineru"` and `partition_key=asset_event.asset_materialization.partition`. No chunk_count needed.
- `chunks_notification_sensor` — `@asset_sensor(asset_key=AssetKey(["chunks"]))`, fires on each `chunks` materialization. POSTs `ASSET_MATERIALIZATION` with `asset_key="chunks"`, `partition_key`, and `metadata={"chunk_count": asset_event.asset_materialization.metadata["chunk_count"].value}` (fallback to 0 if key absent — defensive).

Both sensors:
- Return `SkipReason("notification sent; no run to trigger")` — no job is triggered, sensor is purely a side-effect notifier.
- Use `context.instance.get_run_by_id(asset_event.run_id)` to get the `DagsterRun`, then `dagster_run.tags.get("dagster/backfill") or asset_event.run_id` for the backfill ID (same pattern as `fastapi_run_status_sensor`).
- Wrap the HTTP POST in `try/except` with `context.log.warning(...)` — best-effort, event dropped on failure (same semantics as F-050).

**Why `@asset_sensor` and NOT extending `fastapi_run_status_sensor`:**
1. Only `@asset_sensor` provides `EventLogEntry` as a callback argument, giving access to per-asset materialization metadata including `chunk_count`.
2. Extending `fastapi_run_status_sensor` (Path 1) would produce `count=0` for ALL `chunks.added` events, making the `count` field semantically useless despite passing unit tests.
3. Two dedicated sensors is a small proliferation cost (total: 3 sensors in `definitions.py`) relative to a structural data quality limitation.

**`gateway.py` modification: NOT required.** The tag-injection fallback (Path 3) is eliminated. `apps/api/dataplat_api/dagster/gateway.py` is NOT touched in this sprint.

### 3.6 `chunks.added` Count — Real Value from Dagster Metadata

**Decision: `@asset_sensor` provides the real chunk count.** (M3 resolved — count=0 fallback NOT accepted for production path.)

The `chunks` asset calls `context.add_output_metadata({"chunk_count": MetadataValue.int(len(rows))})` (confirmed at `definitions.py:236`). The `@asset_sensor` receives the resulting `EventLogEntry` whose `.asset_materialization.metadata["chunk_count"].value` is the real integer chunk count.

**Defensive fallback:** If `chunk_count` is absent from metadata (e.g., future asset code change), the sensor falls back to `count=0` and logs at WARNING. This is an implementation-level guard, NOT an accepted design limitation — the normal code path produces the real count.

The `ASSET_MATERIALIZATION` payload includes `metadata: dict[str, Any]`. The chunks sensor passes `{"chunk_count": N}` (real value from materialization metadata). The `extract_mineru` sensor passes `{}`. The FastAPI handler reads `payload.metadata.get("chunk_count", 0)` to populate `ChunksAddedEvent.count`.

---

## §4 Wire Schemas

### 4.1 Server → Client Events

```python
class AssetMaterializedEvent(BaseModel):
    """Fired when a Dagster asset materializes successfully."""
    type: Literal["asset.materialized"] = "asset.materialized"
    asset_key: str                  # e.g. "extract_mineru"
    partition_key: str              # e.g. "src_42"

class ChunksAddedEvent(BaseModel):
    """Fired when the chunks asset completes for a source partition."""
    type: Literal["chunks.added"] = "chunks.added"
    source_id: int                  # parsed from partition_key "src_{N}"
    count: int                      # number of chunks produced (real value from Dagster metadata)
```

Both match the verification criteria verbatim:
- `{"type": "asset.materialized", "asset_key": "extract_mineru", "partition_key": "src_..."}`
- `{"type": "chunks.added", "source_id": <id>, "count": <N>}` where N is the real chunk count

### 4.2 Webhook Payload Extension

```python
class DagsterRunEventPayload(BaseModel):
    event_type: Literal[
        "RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED",
        "ASSET_MATERIALIZATION"  # NEW
    ]
    dagster_run_id: str
    timestamp: datetime
    asset_key: str | None = None       # NEW — populated for ASSET_MATERIALIZATION
    partition_key: str | None = None   # NEW — populated for ASSET_MATERIALIZATION
    metadata: dict[str, Any] = {}      # NEW — e.g. {"chunk_count": 42} for chunks asset
    model_config = ConfigDict(extra="ignore")
```

### 4.3 Client → Server Protocol

No subscribe/unsubscribe messages. Client simply connects and receives events. If client sends any text, server responds with:

```json
{"type": "error", "code": "bad_message"}
```

Connection stays open. Uses existing `ServerError` model (no `run_id` field needed — omit or set to `None`).

### 4.4 Server Acks/Errors on Connect

No `subscribed` ack on connect (no explicit subscribe step). On JWT failure or user-not-found: `websocket.close(code=1008)` before accept, same as `ws_runs.py`.

---

## §5 File Table

| File | Action | Purpose |
|---|---|---|
| `apps/api/dataplat_api/realtime/notification_broker.py` | **CREATE** | `NotificationBroker` keyed by `user_id: int`, parallel to `RunEventBroker`. Same asyncio.Queue(maxsize=100) + drop-oldest. |
| `apps/api/dataplat_api/routers/ws_notifications.py` | **CREATE** | `GET /api/ws/notifications` endpoint. JWT-via-?token= auth. No subscribe protocol. try/finally cleanup. |
| `apps/api/dataplat_api/schemas/realtime.py` | **MODIFY** | Add `AssetMaterializedEvent` and `ChunksAddedEvent`. No ack/error for notifications (reuse existing `ServerError`). |
| `apps/api/dataplat_api/schemas/dagster_events.py` | **MODIFY** | Extend `DagsterRunEventPayload`: add `"ASSET_MATERIALIZATION"` to `event_type` Literal; add optional `asset_key: str \| None`, `partition_key: str \| None`, `metadata: dict[str, Any]`. |
| `apps/api/dataplat_api/routers/dagster_events.py` | **MODIFY** | Add `ASSET_MATERIALIZATION` dispatch branch: look up `Run` by `dagster_run_id`, resolve `triggered_by`, publish `AssetMaterializedEvent` or `ChunksAddedEvent` to `notification_broker`. Wrap `notification_broker.publish` in try/except (§8a). |
| `apps/api/dataplat_api/main.py` | **MODIFY** | Import `NotificationBroker`; add `app.state.notification_broker = NotificationBroker()` in lifespan; `include_router(ws_notifications_router)`. |
| `dagster/dagster_platform/definitions.py` | **MODIFY** | Add two `@asset_sensor` instances: `extract_mineru_notification_sensor` and `chunks_notification_sensor`. Each fires on asset materialization and POSTs `ASSET_MATERIALIZATION` to `/api/dagster/events`. Backfill ID extracted via `context.instance.get_run_by_id(event.run_id).tags.get("dagster/backfill") or event.run_id`. |
| `packages/api-types/openapi.json` | **MODIFY (generated)** | Modified by `make codegen` / manual `app.openapi()` export in same commit as `schemas/dagster_events.py` change; hard invariant #6. Expect diff: new optional fields (`asset_key`, `partition_key`, `metadata`) and `"ASSET_MATERIALIZATION"` added to `event_type` enum. No removals. |
| `apps/api/tests/test_ws_notifications.py` | **CREATE** | Unit tests T1–T10 (see §10). |
| `apps/api/tests/test_dagster_events.py` | **MODIFY** | Add T11–T15: new `ASSET_MATERIALIZATION` event type tests including malformed partition_key defensive path (T15). |
| `verify/checks.sh` | **MODIFY** | Verify `ws_notifications` case runs backend layer (or confirm `backend` already picks up new tests). |

### 5.1 Justification: Separate `notification_broker.py` vs. extending `broker.py`

`RunEventBroker` is keyed by `run_id: int`. `NotificationBroker` is keyed by `user_id: int`. The semantics differ (one is scoped to a run lifecycle, the other to a user session). Merging them would require a generic-keyed broker with a type-switching API. The cost (one extra 70-line file) is lower than the coupling risk. F-051's 11 tests remain unaffected.

### 5.2 Why `gateway.py` is NOT in the file table

H2 finding: the tag-injection fallback (Path 3) would have required modifying `apps/api/dataplat_api/dagster/gateway.py`. That path has been eliminated. The committed sensor path (`@asset_sensor`) does NOT require `gateway.py` modification — it reads the backfill tag from the Dagster run at sensor-fire time via `context.instance.get_run_by_id()`. `gateway.py` is unchanged.

---

## §6 Auth & Ownership Model

### 6.1 Who receives an `asset.materialized` event?

**Decision: Only the user who triggered the run (`Run.triggered_by`).**

Rationale:
- Consistent with F-051 (`ws_runs.py` only allows subscribing to runs you triggered).
- MVP uses `visibility = private|internal` (CLAUDE.md §11.6); no multi-user broadcast needed.
- Prevents leaking asset events from one user's run to another's session.

### 6.2 Lookup Flow in Webhook Handler

```
payload.dagster_run_id → SELECT Run WHERE dagster_run_id=... → run.triggered_by (user_id)
→ notification_broker.publish(user_id=run.triggered_by, event=...)
```

Same query already used by the RUN_START/SUCCESS handler — no new DB access pattern. If `run is None` (unknown `dagster_run_id`): return `DagsterEventResponse(processed=False, reason="unknown_run")` silently (same as existing behaviour for run-status events).

If `run.triggered_by is None` (no owner): event is **dropped** — publish is skipped, response is `processed=True` (event was recognized, just not routable). Log at WARNING.

### 6.3 Why No Global Broadcast?

Global broadcast would deliver asset events from User A's run to User B's connected client. That's an ACL violation. Even if both users are `admin`, the MVP model is per-user ownership, and the groundwork for ACL is laid here.

---

## §7 Race / Loss / Multi-worker Constraints

F-051 established these constraints; F-052 **inherits them all without relaxation**:

1. **In-process asyncio only** — `NotificationBroker` is a `dict[int, list[asyncio.Queue]]` in the same worker process. Running uvicorn `--workers N > 1` or gunicorn multiproc causes silent event loss for WS connections on a different worker than the one receiving `POST /api/dagster/events`. Swap for Redis pub/sub when scaling. Documented loudly in class docstring.

2. **Best-effort, fire-and-forget** — No ack, no replay. Events lost on queue full (drop-oldest) or on disconnect are permanently gone.

3. **Subscribe+publish race** — Events fired between the webhook arriving and the client connecting are lost. Frontend MUST poll REST endpoints to get current state on (re)connect.

4. **Single commit per** `dagster_run_id` for `ASSET_MATERIALIZATION` — the sensor fires once per asset materialization. If the same backfill materializes multiple partitions, each partition fires a separate `ASSET_MATERIALIZATION` event. All are correctly routed via `Run.triggered_by` from the `dagster_run_id` tag.

5. **No new infra** — Redis is explicitly excluded (CLAUDE.md scope discipline; not in `apps/api/pyproject.toml`).

---

## §8 Hard Invariants

| Invariant | Status | Notes |
|---|---|---|
| **#1 Lineage mandatory** | N/A | No Commit row created. Notification events carry no lineage data. Asset lineage is handled by Dagster + `document_variant` row at extract time. |
| **#2 Storage separation + CAS** | N/A | No blob bytes written. Notification events are ephemeral in-memory queue items. No Postgres write from the notification path. |
| **#3 Schema frozen post-publish** | N/A | No Silver/Gold publish triggered. |
| **#4 LLM calls via gateway** | N/A | No LLM call anywhere in this sprint. |
| **#5 Async SQLAlchemy** | ✓ | `ws_notifications.py`: `Depends(get_session)` + `await session.execute(select(User)...)` + `scalar_one_or_none()` on sync result proxy (same pattern as `ws_runs.py`). `dagster_events.py` extension uses existing `await session.execute(select(Run)...)` — no new session usage added. No `session.query()` anywhere. |
| **#6 OpenAPI ↔ TS sync** | ✓ | `DagsterRunEventPayload` is the request body of `POST /api/dagster/events` and IS in `openapi.json`. Schema change (new optional fields + new Literal value) WILL produce an OpenAPI diff. `packages/api-types/openapi.json` is in §5 file table. `make codegen` MUST be run and `packages/api-types/openapi.json` committed in the **same commit** as `schemas/dagster_events.py`. |

### §8a Additional Invariant: `notification_broker.publish` must be wrapped in try/except

**Invariant:** In `dagster_events.py`, `notification_broker.publish(...)` MUST be wrapped in `try: ... except Exception as exc: logger.warning(...)`, analogous to the `run_broker.publish` wrapping established in F-051 agreed.md §4.5. This is LOAD-BEARING: the DB `await session.commit()` has already run before the publish call. An uncaught exception in the publish step would propagate up and return HTTP 500, which would cause the Dagster daemon sensor to retry the same tick — violating F-050's best-effort SLA. Test T16 (see §10) asserts HTTP 200 is returned even when `notification_broker.publish` raises.

---

## §9 OpenAPI / TS Sync

**`DagsterRunEventPayload` is an HTTP request body schema** — it IS in `openapi.json`. The changes to this schema (new optional fields + new Literal value) WILL produce a diff in `packages/api-types/openapi.json`.

Required action by implementer:
1. After all changes, run `make codegen` (or `cd apps/api && uv run python -c "import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))" > packages/api-types/openapi.json`).
2. Inspect `git diff -- packages/api-types/openapi.json` — expect additions for `asset_key`, `partition_key`, `metadata` fields and `ASSET_MATERIALIZATION` in the `event_type` enum.
3. Commit the updated `openapi.json` in the **same commit** as the schema change.

Verification: `git diff HEAD~1 -- packages/api-types/openapi.json` in the CI contract layer shows ONLY additive changes (new optional fields, new enum value), no removals.

`AssetMaterializedEvent`, `ChunksAddedEvent`, `NotificationBroker` — all WS-only, not in OpenAPI, zero codegen diff.

---

## §10 Verification Plan

### Criteria-to-Test Mapping

| Verification criterion | Test(s) | Assertions |
|---|---|---|
| Connect to `/api/ws/notifications` | T1, T2, T3 | T1: valid JWT → HTTP 101; T2: no token → close 1008; T3: invalid JWT → close 1008 |
| Trigger extraction run; receive `asset.materialized` event | T5, T7 | T5: direct broker.publish inject; asset_key="extract_mineru" → client receives `{"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}`; T7: end-to-end via HTTP POST to `/api/dagster/events` |
| Receive `chunks.added` after chunking | T6, T8 | T6: direct broker.publish inject; asset_key="chunks", metadata={"chunk_count":5} → client receives `{"type":"chunks.added","source_id":42,"count":5}`; T8: end-to-end via HTTP POST with chunk_count=7 |

### Full Test List (T1–T16)

**`apps/api/tests/test_ws_notifications.py`** (NEW):

- **T1** `test_connect_with_valid_jwt_accepted` — valid JWT → WS accepted (HTTP 101). Send unexpected text → `{"type":"error","code":"bad_message"}` returned, connection stays open.
- **T2** `test_connect_without_token_closes_1008` — no `?token=` → `WebSocketDisconnect` code 1008 before accept.
- **T3** `test_connect_with_invalid_jwt_closes_1008` — garbage token → code 1008.
- **T4** `test_connect_with_unknown_user_closes_1008` — valid JWT but `user_id` not in DB → code 1008.
- **T5** `test_asset_materialized_event_delivered` — connect; inject `AssetMaterializedEvent` with `asset_key="extract_mineru"`, `partition_key="src_42"` via `notification_broker.publish(user_id, event_dict)`; verify received message matches verbatim: `{"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}`.
- **T6** `test_chunks_added_event_delivered` — connect; inject `ChunksAddedEvent` with `source_id=42`, `count=5` via `notification_broker.publish`; verify `{"type":"chunks.added","source_id":42,"count":5}`.
- **T7** `test_post_dagster_asset_event_delivers_to_ws_extract_mineru` — end-to-end: connect WS; POST `{"event_type":"ASSET_MATERIALIZATION","dagster_run_id":"bf-1","asset_key":"extract_mineru","partition_key":"src_42","timestamp":...}` to `/api/dagster/events`; verify WS client receives `{"type":"asset.materialized","asset_key":"extract_mineru","partition_key":"src_42"}`.
- **T8** `test_post_dagster_asset_event_delivers_to_ws_chunks` — same as T7 but `asset_key="chunks"` + `metadata={"chunk_count":7}` → WS client receives `{"type":"chunks.added","source_id":42,"count":7}`.
- **T9** `test_disconnect_no_error_and_broker_cleanup` — connect; verify `notification_broker._subscribers[user_id]` has one entry; disconnect; verify cleaned up.
- **T10** `test_asset_event_unknown_dagster_run_returns_processed_false` — POST `ASSET_MATERIALIZATION` for unknown `dagster_run_id` → HTTP 200, `processed=False`, no WS event delivered.

**`apps/api/tests/test_dagster_events.py`** (MODIFY — add T11–T16):

- **T11** `test_asset_materialization_event_accepted_200` — POST valid `ASSET_MATERIALIZATION` with auth secret → HTTP 200, `processed=True`.
- **T12** `test_asset_materialization_invalid_payload_returns_422` — POST with `event_type="BAD_TYPE"` → 422. (Confirm `asset_key` optional → 200 on missing `asset_key`.)
- **T13** `test_asset_materialization_publishes_to_notification_broker` — mock `notification_broker.publish`; POST valid ASSET_MATERIALIZATION; assert `notification_broker.publish` called with correct `user_id` and event dict.
- **T14** `test_asset_materialization_unknown_run_returns_processed_false` — `scalar_one_or_none()` returns `None`; assert HTTP 200 + `processed=False`.
- **T15** `test_asset_materialization_malformed_partition_key_skips_publish` — POST `ASSET_MATERIALIZATION` with `asset_key="chunks"` and `partition_key="BAD_FORMAT"` (not `src_{N}`) → HTTP 200, no WS event delivered (`notification_broker.publish` NOT called), WARNING logged. Also test `partition_key=null` variant. (L1 fix: covers OQ-6 defensive ValueError path.)
- **T16** `test_notification_broker_publish_exception_still_returns_200` — mock `notification_broker.publish` to raise `Exception("boom")`; POST valid ASSET_MATERIALIZATION → HTTP 200, no exception propagated. (L2 / §8a fix.)

---

## §11 Risks / Open Questions

| # | Risk / Question | Severity | Status |
|---|---|---|---|
| OQ-1 | ~~Array containment query on `run.partition_keys`~~ | N/A | **CLOSED** — Use `dagster_run_id` for lookup (Option B §3.3). |
| OQ-2 | `chunk_count` metadata access path in `@asset_sensor` Dagster 1.11.16. | MEDIUM | **CLOSED** — Probed live container. `@asset_sensor` callback signature: `fn(context: SensorEvaluationContext, asset_event: EventLogEntry)`. Correct access: `asset_event.asset_materialization.metadata["chunk_count"].value`. (Note: proposed.md round-1 had wrong path `context.asset_events[-1].materialization.metadata` — corrected here.) Probe also confirmed `chunk_count` is set in definitions.py:236 for `chunks` asset. |
| OQ-3 | `RunStatusSensorContext.dagster_run.asset_selection` non-None for backfill? | HIGH | **CLOSED** — Probed live container. `DagsterRun.asset_selection` is `Optional[AbstractSet[AssetKey]]`. On two live backfill runs: `frozenset({AssetKey(["extract_mineru"])})` — NOT None. However, `@run_status_sensor` is rejected (Path 1) because it cannot read per-asset metadata (chunk_count). Committed to `@asset_sensor` (Path 2). |
| OQ-4 | T9 `test_invalid_event_type_returns_422` still passes after adding `"ASSET_MATERIALIZATION"` to Literal. | LOW | **CLOSED** — T9 uses `"RUN_QUEUED"` as invalid value, which remains invalid after the Literal extension. T9 still passes. |
| OQ-5 | `Request` already in `dagster_events.py` handler signature. | LOW | **CLOSED** — Confirmed: `Request` param already present at `dagster_events.py:45`. Just add `request.app.state.notification_broker.publish(...)`. |
| OQ-6 | `source_id` derivation in `ChunksAddedEvent`: parse `int(partition_key.removeprefix("src_"))`. None or bad-format → `ValueError`. | LOW | **CLOSED** — Implement defensively with try/except, log WARNING, skip publish. Test T15 covers this path. |
| R1 | ~~Dagster sensor complexity — fallback count=0~~ | MEDIUM | **RESOLVED** — `@asset_sensor` provides real chunk_count. count=0 fallback is ONLY a defensive guard for missing metadata key, not an accepted semantic limitation. |
| R2 | Test T7/T8 coordinate WS client + HTTP POST in same test. | LOW | Follow F-051 T8 pattern exactly. |

---

## §13 Round-1 Addenda

Each finding from `contracts/S052-F-052/feedback.md` and its resolution:

### H1 — Sensor path collapse (RESOLVED)

**Probe conducted.** Live Dagster 1.11.16 container probed for both OQ-2 and OQ-3. 

OQ-3 answer: `dagster_run.asset_selection` IS non-None for backfill runs (frozenset[AssetKey] confirmed on 2 live runs). However, `@run_status_sensor` is eliminated because it cannot access per-asset materialization metadata — `chunk_count` is only readable via `EventLogEntry.asset_materialization.metadata` which is the `@asset_sensor` callback argument.

OQ-2 answer: `@asset_sensor` in Dagster 1.11.16 uses callback `fn(context: SensorEvaluationContext, asset_event: EventLogEntry)`. The correct metadata access path is `asset_event.asset_materialization.metadata["chunk_count"].value` (NOT `context.asset_events[-1].materialization.metadata` as stated in round-1 — that was wrong).

**Committed path: `@asset_sensor` (Path 2) — two sensors, one per asset.** All three paths removed from proposal; §3.5 now documents only the committed path with probe evidence.

### H2 — `gateway.py` absent from file table (RESOLVED)

Tag-injection Path 3 is eliminated. `gateway.py` does NOT need to be modified. §5 now includes explicit §5.2 explaining why `gateway.py` is absent. No file table entry needed.

### M1 — `packages/api-types/openapi.json` absent from §5 (RESOLVED)

`packages/api-types/openapi.json` added to §5 file table as **MODIFY (generated)** with note: "Modified by `make codegen` / manual `app.openapi()` export in same commit as `schemas/dagster_events.py` change; hard invariant #6."

### M2 — Criteria-to-test mapping internally inconsistent (RESOLVED)

Criteria-to-Test Mapping table in §10 fixed:
- "Trigger extraction run; receive `asset.materialized`" → T5 (direct inject), T7 (end-to-end)
- "Receive `chunks.added` after chunking" → T6 (direct inject), T8 (end-to-end)
T6 is now correctly described as a `chunks.added` inject test (not mislabelled as asset.materialized end-to-end).

### M3 — `count=0` fallback not explicitly accepted (RESOLVED)

By committing to `@asset_sensor`, the real chunk_count IS available from Dagster metadata. `count=0` is now only an implementation-level defensive fallback (missing metadata key guard) — NOT an accepted semantic limitation. §3.6 updated to reflect this. The verification criterion `count: <N>` is satisfied with the real N from materialization metadata.

### L1 — Missing test for malformed `partition_key` (RESOLVED)

T15 added to test list: POSTs `ASSET_MATERIALIZATION` with `asset_key="chunks"` and `partition_key="BAD_FORMAT"` (and `partition_key=null`) → asserts HTTP 200, `notification_broker.publish` NOT called, WARNING logged. Covers OQ-6 defensive `ValueError`/`None` path.

### L2 — `notification_broker.publish` not explicitly required to be wrapped in try/except (RESOLVED)

§8a added as an explicit invariant: "`notification_broker.publish` MUST be wrapped in `try: ... except Exception as exc: logger.warning(...)` in `dagster_events.py`, analogous to `run_broker.publish` wrapping in F-051 agreed.md §4.5." T16 added to test list asserting HTTP 200 even when `notification_broker.publish` raises. §5 file table row for `dagster_events.py` updated to note the try/except requirement.

### NIT-1 — §12/§13 confusing double-numbering (RESOLVED)

Wrapper §12 eliminated. This round-1 addenda section is at `§13` (same level as other top-level sections), matching F-051 agreed.md convention.
