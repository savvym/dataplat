# Sprint S050-F-050 — Proposed Contract

**Feature**: F-050 — Dagster event webhook: Dagster posts run status change events to a FastAPI endpoint; FastAPI updates the corresponding Run row in Postgres  
**Depends on**: F-018 (`passes: true`), F-004 (`passes: true`)  
**Sprint directory**: `contracts/S050-F-050/`  
**Author**: leader (inline)  
**Date**: 2026-06-05  
**Revision**: 2 (round-1 reviewer findings folded in)

---

## §1 Goal

Implement a FastAPI webhook endpoint that Dagster's run-status sensor calls whenever a run's status changes. The endpoint receives a structured JSON event, looks up the matching `Run` row by `dagster_run_id`, updates `status` (and `ended_at` for terminal events), and commits. After this sprint, `GET /api/runs/{id}` will show `status='success'` or `status='failure'` instead of being permanently stuck on `'pending'` or `'running'`.

Two pieces of work are required:

1. **FastAPI side** — a new `POST /api/dagster/events` endpoint in a new `apps/api/dataplat_api/routers/dagster_events.py` router, plus a new `apps/api/dataplat_api/schemas/dagster_events.py` Pydantic model. The endpoint is **unauthenticated** (no Bearer JWT) but protected by a shared-secret header (`X-Dagster-Webhook-Secret`). See §7.

2. **Dagster side** — a new `run_status_sensor` in `dagster/dagster_platform/definitions.py` that fires on every Dagster run status change, builds the event payload, and POSTs it to the FastAPI webhook URL. See §5.

The design doc (line 195) explicitly shows `Dagster → FastAPI (event webhook)` as the canonical event path and (line 941) says _"FastAPI listens to Dagster's webhook (or polls GraphQL)"_. The webhook approach is therefore the design-doc-aligned choice.

**Dagster's built-in webhook support**: Dagster does **not** have a native HTTP push webhook. The canonical way to achieve Dagster→FastAPI HTTP calls is a **Dagster run-status sensor** (`@run_status_sensor`) that runs inside the `dagster-daemon` process, polls Dagster's event log, and calls an external HTTP endpoint for each status change. This is the intended mechanism per Dagster docs and is confirmed by the existing daemon service in `docker/docker-compose.dev.yml` (line 1120: `command: dagster-daemon run`). The sensor uses the synchronous `requests` library (same pattern as `quality_tagger.py`, `sft_synthesis_qa.py`), not `httpx`, since Dagster assets and sensors are fully synchronous.

---

## §2 Spec References

| Source | Relevant excerpt |
|---|---|
| `docs/data_platform_design.md` line 195 | `Dagster → FastAPI (event webhook)` — run-status events flow via webhook to FastAPI |
| `docs/data_platform_design.md` line 941 | _"来源:FastAPI 监听 Dagster 的 webhook(或轮询 GraphQL),转换成业务事件,通过 Redis pub/sub 广播给所有 WebSocket 连接"_ |
| `docs/data_platform_design.md` §4.1 (line 352–370) | `run` table schema: `dagster_run_id TEXT UNIQUE NOT NULL`, `status TEXT NOT NULL`, `ended_at TIMESTAMPTZ` |
| `docs/data_platform_design.md` §9.3 (line 913–938) | WebSocket event shape: `run.status_changed` with `from`/`to` fields — this is the downstream consumer; F-051 is deferred (see §11) |
| `spec/tech-direction.md` | Async SQLAlchemy; no Celery/Dagster-eventing buses; sensor approach is consistent with Dagster multiprocess MVP |
| `contracts/S018-F-018/` | F-018 inserts Run rows with `status='pending'`; this sprint flips them to terminal status |
| `contracts/S048-F-048/` | F-048 provides `GET /api/runs/{id}` — the primary V-map target |

---

## §3 Schema

### 3.1 Run ORM columns updated by this sprint

From `apps/api/dataplat_api/db/models.py` (lines 285–327), the `Run` table has:

| Column | Type | Updated by this sprint |
|---|---|---|
| `status` | `Text NOT NULL` | Yes — set to `'running'`, `'success'`, or `'failure'` |
| `started_at` | `DateTime(tz=True) NULLABLE` | Yes — set on `RUN_START` event |
| `ended_at` | `DateTime(tz=True) NULLABLE` | Yes — set on terminal events (`RUN_SUCCESS`, `RUN_FAILURE`, `RUN_CANCELED`) |

**No Alembic migration needed**: all columns already exist in the baseline schema (F-002).

### 3.2 Pydantic event payload schema (new file)

**File**: `apps/api/dataplat_api/schemas/dagster_events.py`

```
DagsterRunEventPayload
├── event_type: Literal["RUN_START", "RUN_SUCCESS", "RUN_FAILURE", "RUN_CANCELED"]
├── dagster_run_id: str          # the Dagster backfill ID stored in Run.dagster_run_id (see §6 for sensor extraction)
└── timestamp: datetime          # event time from Dagster; used to set started_at / ended_at
```

**Rationale for event_type set**:
- `RUN_START` → set `status='running'`, `started_at=timestamp`
- `RUN_SUCCESS` → set `status='success'`, `ended_at=timestamp`
- `RUN_FAILURE` → set `status='failure'`, `ended_at=timestamp`
- `RUN_CANCELED` → set `status='failure'`, `ended_at=timestamp` (canceled maps to `'failure'` — same mapping as F-005 `RunStatusResponse` where `CANCELED → "failure"`)

`model_config = ConfigDict(extra="ignore")` — future Dagster event fields are silently dropped.

The `timestamp` field uses `datetime` (Pydantic parses ISO-8601 strings from JSON). The sensor will emit `datetime.utcnow()` as an ISO-8601 string.

### 3.3 State transition table

| Dagster event | Run.status → | Run.started_at | Run.ended_at |
|---|---|---|---|
| `RUN_START` | `'running'` | set to `timestamp` | unchanged (None) |
| `RUN_SUCCESS` | `'success'` | unchanged | set to `timestamp` |
| `RUN_FAILURE` | `'failure'` | unchanged | set to `timestamp` |
| `RUN_CANCELED` | `'failure'` | unchanged | set to `timestamp` |

**Idempotency**: If the same event arrives twice (sensor retry, network duplicate), the handler performs a no-op write: `status` and the timestamp field are set to the same value a second time. Because `MERGE`/`UPDATE ... WHERE id=...` is an idempotent assignment, no unique-constraint conflict can arise. No explicit deduplication is needed at MVP.

**Unknown `dagster_run_id`**: If no matching `Run` row is found, return **HTTP 200** with `{"processed": false, "reason": "unknown_run"}`. Rationale: the sensor fires for ALL Dagster runs, including the `hello_world_job` launched by admin smoke and any other run not tracked in the business `run` table. A 404 would cause the sensor to retry indefinitely; silently ignoring unknown IDs is the correct production behavior.

**Invalid event_type**: FastAPI's Pydantic `Literal` validation raises **422** automatically. The sensor only sends the four known event types, so 422 should never occur in normal operation but is a useful signal if the sensor payload drifts.

---

## §4 Endpoint Surface

### POST /api/dagster/events

```
POST /api/dagster/events
Content-Type: application/json
X-Dagster-Webhook-Secret: <DAGSTER_WEBHOOK_SECRET>

{
  "event_type": "RUN_SUCCESS",
  "dagster_run_id": "backfill-abc123",
  "timestamp": "2026-06-05T10:30:00Z"
}
```

**Response (HTTP 200)**:
```json
{"processed": true}
```
or:
```json
{"processed": false, "reason": "unknown_run"}
```

**Status codes**:

| Code | Condition |
|---|---|
| 200 | Event processed (or silently ignored for unknown `dagster_run_id`) |
| 401 | Missing or invalid `X-Dagster-Webhook-Secret` header |
| 422 | Pydantic validation failure (bad `event_type`, missing `dagster_run_id`, etc.) |
| 500 | Webhook secret not configured on server (see §7), or unhandled DB error (SQLAlchemy raises) |

**No Bearer JWT**: this endpoint is called by the Dagster daemon, not by an end-user. The daemon cannot obtain a JWT. It is protected instead by the shared `DAGSTER_WEBHOOK_SECRET` (see §7).

**Router**: a new `APIRouter(prefix="/api/dagster", tags=["dagster"])` in `apps/api/dataplat_api/routers/dagster_events.py`. This avoids modifying the existing `admin.py` or `runs.py` routers and keeps Dagster-internal concerns isolated.

### DagsterEventResponse schema

```python
class DagsterEventResponse(BaseModel):
    processed: bool
    reason: str | None = None   # populated when processed=False
```

---

## §5 Handler Logic (FastAPI side)

```
POST /api/dagster/events handler pseudocode:

0. Fail-closed guard: if settings.DAGSTER_WEBHOOK_SECRET is empty/unset
   → raise HTTPException(status_code=500, detail="Webhook secret not configured on this server")
   (This is the FIRST check — before any auth comparison — so a misconfigured deployment is
   immediately visible rather than silently accepting any caller that sends an empty header.
   See §7 for full rationale.)

1. Authenticate: check X-Dagster-Webhook-Secret header == settings.DAGSTER_WEBHOOK_SECRET
   → 401 {"detail": "Invalid webhook secret"} on mismatch or absence

2. Parse body → DagsterRunEventPayload (Pydantic; 422 on validation failure)

3. Query: SELECT run WHERE dagster_run_id == payload.dagster_run_id
   → via await session.execute(select(Run).where(Run.dagster_run_id == payload.dagster_run_id))
   → result.scalar_one_or_none()

4. If run is None → return HTTP 200 DagsterEventResponse(processed=False, reason="unknown_run")

5. Apply state transition (see §3.3):
   if event_type == "RUN_START":
       run.status = "running"
       run.started_at = payload.timestamp
   elif event_type in ("RUN_SUCCESS",):
       run.status = "success"
       run.ended_at = payload.timestamp
   elif event_type in ("RUN_FAILURE", "RUN_CANCELED"):
       run.status = "failure"
       run.ended_at = payload.timestamp

6. await session.commit()

7. Return HTTP 200 DagsterEventResponse(processed=True)
```

**Dependencies**: `Depends(get_session)` (async SQLAlchemy). No `Depends(get_current_user)` — auth is done inline via header check (see §7). No `Depends(get_dagster_gateway)` — this endpoint receives from Dagster, it does not call Dagster.

---

## §6 Dagster Sensor (Dagster side)

**File**: `dagster/dagster_platform/definitions.py` (extend)

The sensor uses Dagster's `@run_status_sensor` decorator, which fires whenever a run's status matches any of the specified `run_status_list` values. It runs inside `dagster-daemon`.

```python
# Pseudocode — not implementation

from dagster import RunStatusSensorContext, run_status_sensor
from dagster import DagsterRunStatus
import requests
import os
from datetime import datetime, timezone

_FASTAPI_WEBHOOK_URL = os.getenv(
    "FASTAPI_WEBHOOK_URL", "http://fastapi:8000/api/dagster/events"
)
_DAGSTER_WEBHOOK_SECRET = os.getenv("DAGSTER_WEBHOOK_SECRET", "")

@run_status_sensor(
    run_status_list=[
        DagsterRunStatus.STARTED,
        DagsterRunStatus.SUCCESS,
        DagsterRunStatus.FAILURE,
        DagsterRunStatus.CANCELED,
    ],
    name="fastapi_run_status_sensor",
    minimum_interval_seconds=5,
)
def fastapi_run_status_sensor(context: RunStatusSensorContext) -> None:
    """Post run status events to the FastAPI webhook.

    Fires for every Dagster run (all jobs + backfills). Unknown dagster_run_ids
    are silently ignored by FastAPI (HTTP 200 processed=False).

    DELIVERY SEMANTICS: best-effort, NOT at-least-once. The try/except swallows
    all HTTP exceptions and returns normally, so the Dagster daemon marks this
    tick SUCCESS and advances its cursor. A failed HTTP call means the event is
    permanently dropped — it will NOT be retried on the next tick. To enable
    retry semantics, remove the try/except and let exceptions propagate; the
    daemon will re-attempt the same tick on the next poll interval. For MVP,
    best-effort delivery is acceptable (logged failures, no data corruption).
    See §11 Out of Scope: sensor-side delivery is best-effort, not at-least-once.
    """
    event_type_map = {
        DagsterRunStatus.STARTED: "RUN_START",
        DagsterRunStatus.SUCCESS: "RUN_SUCCESS",
        DagsterRunStatus.FAILURE: "RUN_FAILURE",
        DagsterRunStatus.CANCELED: "RUN_CANCELED",
    }
    # M1 fix: use the dagster/backfill tag (the backfill ID stored in Run.dagster_run_id)
    # when the run was launched by a backfill. For non-backfill runs (e.g. hello_world_job),
    # the tag is absent and run_id is used as the fallback; those runs correctly return
    # processed=False since they have no matching Run row.
    payload = {
        "event_type": event_type_map[context.dagster_run.status],
        "dagster_run_id": (
            context.dagster_run.tags.get("dagster/backfill")
            or context.dagster_run.run_id
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            _FASTAPI_WEBHOOK_URL,
            json=payload,
            headers={"X-Dagster-Webhook-Secret": _DAGSTER_WEBHOOK_SECRET},
            timeout=5,
        )
        resp.raise_for_status()
    except Exception as exc:
        context.log.warning(f"fastapi_run_status_sensor: HTTP call failed: {exc}")
        # Do not raise — event is silently dropped (best-effort; see docstring above).
        # Sensor failure should not fail the run itself.
```

The sensor must be added to the `Definitions(...)` object in `definitions.py`:
```python
Definitions(
    assets=[...],
    sensors=[fastapi_run_status_sensor],   # ADD THIS
    resources={...},
)
```

**Environment variables added to `docker/docker-compose.dev.yml`**:
- `FASTAPI_WEBHOOK_URL=http://fastapi:8000/api/dagster/events` (on `dagster-daemon` service; resolved via Docker compose internal DNS)
- `DAGSTER_WEBHOOK_SECRET=<shared-dev-secret>` (on both `dagster-daemon` and `fastapi` services)

These must also be added to `apps/api/dataplat_api/config.py` as new `Settings` fields with safe defaults (see §8).

**Note on backfill-ID extraction (M1 resolution, OQ-1 closed)**:

`Run.dagster_run_id` stores the Dagster **backfill ID** (from `launchPartitionBackfill` → `backfillId`). A backfill with N source partitions creates N individual worker runs, each with its own UUID (`context.dagster_run.run_id`). These UUIDs would never match the stored backfill ID. The sensor instead reads `context.dagster_run.tags.get("dagster/backfill")`, which Dagster 1.x automatically stamps on every partition run inside a backfill, and uses that as the payload `dagster_run_id`. Non-backfill runs (e.g. `hello_world_job`) carry no such tag; `run_id` is used as the fallback, and those runs correctly return `processed=False` since they have no matching `Run` row.

**Last-write-wins aggregate limitation (MVP-accepted)**: When a backfill with N partitions completes, the sensor fires N times against the same `Run` row. The final event wins. For a 3-partition backfill where partition 1 fails and partitions 2 and 3 succeed (in that order), `Run.status` will end up as `'success'` — which does not reflect true aggregate backfill success. This is acceptable for MVP (single-source is the most common case; Dagster delivers events in roughly chronological order within a backfill; a "success" run that briefly shows "failure" then "success" on partition retry is a known, acceptable edge case). V1/V2 verification tests should understand that "`Run.status` reflects the last terminal event received, not the aggregate backfill outcome".

---

## §7 Auth / Security

| Concern | Decision | Rationale |
|---|---|---|
| Authentication mechanism | Shared-secret header `X-Dagster-Webhook-Secret` | Dagster daemon cannot hold a JWT; shared secret in an env var is the industry-standard pattern for service-to-service webhooks. Bearer JWT is for user-facing calls only. |
| Secret storage | `DAGSTER_WEBHOOK_SECRET` env var on both `fastapi` and `dagster-daemon` containers; added to `Settings` in `config.py` | Follows the existing pattern for `SECRET_KEY` (JWT secret). `Settings` model has `extra="ignore"` so no code change is needed to read new env vars once added. |
| Network scope | The webhook URL uses Docker internal DNS (`http://fastapi:8000`); not exposed on any public port. Even if the secret were absent, the endpoint would only be reachable from inside the compose network. | Defence-in-depth: secret + network isolation. |
| Timing-safe comparison | `secrets.compare_digest(received_secret, settings.DAGSTER_WEBHOOK_SECRET)` — prevents timing-oracle attacks. | Simple string `==` leaks timing information; `compare_digest` is correct even for low-value secrets. |
| Absent secret header | `X-Dagster-Webhook-Secret` header entirely missing → HTTP 401 | Prevents unauthenticated calls. |
| Empty secret config | If `DAGSTER_WEBHOOK_SECRET` is empty string in settings, `secrets.compare_digest("", "")` returns True, so any caller sending an empty header passes auth — this is fail-open. **Resolved (OQ-2)**: the handler checks `if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(status_code=500, ...)` as its FIRST check (before `compare_digest`). This is a fail-closed guarantee: a misconfigured server returns 500 rather than silently accepting any caller. |
| IP allowlist | Not used — Docker compose network isolation is sufficient for MVP. | |
| HTTPS | Not used — inter-service communication is plain HTTP inside Docker compose for MVP (same as all other FastAPI→Dagster calls). | |

**Handler implementation sketch (auth check)**:
```python
import secrets
from fastapi import Header

async def post_dagster_event(
    body: DagsterRunEventPayload,
    x_dagster_webhook_secret: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> DagsterEventResponse:
    # Fail-closed: if the server secret is not configured, return 500 immediately.
    # This prevents secrets.compare_digest("", "") silently accepting empty-header callers.
    if not settings.DAGSTER_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Webhook secret not configured on this server",
        )
    if x_dagster_webhook_secret is None or not secrets.compare_digest(
        x_dagster_webhook_secret, settings.DAGSTER_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    ...
```

---

## §8 Files Changed

| File | Status | Change description |
|---|---|---|
| `apps/api/dataplat_api/schemas/dagster_events.py` | **NEW** | `DagsterRunEventPayload` (4-field Pydantic model, `extra="ignore"`); `DagsterEventResponse` (2-field response model). |
| `apps/api/dataplat_api/routers/dagster_events.py` | **NEW** | `APIRouter(prefix="/api/dagster", tags=["dagster"])`; `POST /events` handler with secret auth, DB lookup by `dagster_run_id`, state-transition logic, `await session.commit()`. |
| `apps/api/dataplat_api/main.py` | **edit** | `from dataplat_api.routers.dagster_events import router as dagster_events_router`; `app.include_router(dagster_events_router)`. |
| `apps/api/dataplat_api/config.py` | **edit** | Add `DAGSTER_WEBHOOK_SECRET: str = ""` to `Settings`. A default of `""` allows CI tests to run without the env var; production must set a non-empty value. |
| `dagster/dagster_platform/definitions.py` | **edit** | Import `run_status_sensor`, `DagsterRunStatus`, `RunStatusSensorContext`; define `fastapi_run_status_sensor`; add it to `Definitions(sensors=[...])`. |
| `apps/api/tests/test_dagster_events.py` | **NEW** | 10 unit tests (see §9). |
| `packages/api-types/openapi.json` | **generated** | Updated by `make codegen` after new endpoint and schemas. Committed in the same commit per invariant #6. |
| `docker/docker-compose.dev.yml` | **edit** | Add `FASTAPI_WEBHOOK_URL` and `DAGSTER_WEBHOOK_SECRET` env vars to `dagster-daemon` service; add `DAGSTER_WEBHOOK_SECRET` env var to `fastapi` service. |

**Codegen hard requirement (invariant #6):**
```bash
cd apps/api && uv run python -c "
import json
from dataplat_api.main import app
from fastapi.openapi.utils import get_openapi
spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
with open('../../packages/api-types/openapi.json', 'w') as f:
    json.dump(spec, f, indent=2)
"
```

---

## §9 Test Plan

**File**: `apps/api/tests/test_dagster_events.py` (new)

All tests follow the established pattern (`TestClient(app)`, `app.dependency_overrides[get_session]`, conftest autouse `_patch_engine_begin` + `_patch_httpx_no_ssl`). The secret header is supplied as `headers={"X-Dagster-Webhook-Secret": "test-secret"}` with `DAGSTER_WEBHOOK_SECRET` monkeypatched on `settings`.

**Session mock pattern** (single `execute()` call + optional `commit()`):
```python
run_row = MagicMock(spec=Run)
run_row.status = "pending"
run_row.started_at = None
run_row.ended_at = None

result_mock = MagicMock()
result_mock.scalar_one_or_none.return_value = run_row  # or None for unknown-run tests

session = AsyncMock()
session.execute = AsyncMock(return_value=result_mock)
session.commit = AsyncMock()
```

### V-map

| V-criterion | Test(s) |
|---|---|
| **V1**: `GET /api/runs/{id}` shows `status='success'` after RUN_SUCCESS event | T1 (status flips to 'success'), T5 (commit called — confirms DB write) |
| **V1**: `GET /api/runs/{id}` shows `status='failure'` after RUN_FAILURE event | T2 (status flips to 'failure'), T5 |
| **V2**: `ended_at` is set after terminal event | T1 (ended_at not None on RUN_SUCCESS), T2 (ended_at not None on RUN_FAILURE), T4 (ended_at not None on RUN_CANCELED) |

### Test cases

| # | Test name | What it verifies |
|---|---|---|
| T1 | `test_run_success_event_updates_status` | POST RUN_SUCCESS → `run.status` set to `'success'`, `processed=True` in response; `run.ended_at` is not None |
| T2 | `test_run_failure_event_updates_status` | POST RUN_FAILURE → `run.status` set to `'failure'`; `run.ended_at` is not None |
| T3 | `test_run_start_event_updates_status_and_started_at` | POST RUN_START → `run.status` set to `'running'`; `run.started_at` is not None; `run.ended_at` unchanged (None) |
| T4 | `test_run_canceled_maps_to_failure` | POST RUN_CANCELED → `run.status` set to `'failure'`; `run.ended_at` is not None |
| T5 | `test_session_commit_called_on_known_run` | POST RUN_SUCCESS with a known run → `session.commit` was called exactly once |
| T6 | `test_unknown_dagster_run_id_returns_processed_false` | `scalar_one_or_none` returns None → HTTP 200, `{"processed": false, "reason": "unknown_run"}`, `session.commit` NOT called |
| T7 | `test_missing_secret_header_returns_401` | No `X-Dagster-Webhook-Secret` header → HTTP 401 |
| T8 | `test_wrong_secret_header_returns_401` | `X-Dagster-Webhook-Secret: wrong-value` → HTTP 401 |
| T9 | `test_invalid_event_type_returns_422` | `event_type: "RUN_QUEUED"` (not in Literal) → HTTP 422 (FastAPI Pydantic validation, no DB call) |
| T10 | `test_unconfigured_webhook_secret_returns_500` | `settings.DAGSTER_WEBHOOK_SECRET` monkeypatched to `""` (or unset); any valid-looking request → HTTP 500 with detail "Webhook secret not configured on this server" |

**Test count: 10.**

**Note on sensor-side tag extraction (OQ-1 resolved)**: T6 tests handler behavior when `scalar_one_or_none` returns `None` (unknown `dagster_run_id`). It is a valid and useful handler-isolation test. The sensor-level `dagster/backfill` tag extraction (M1 fix) is sensor code; it is not exercised by FastAPI handler unit tests. Sensor-level tag extraction is validated only via integration testing (deferred per §11).

---

## §10 Hard Invariants Audit

| # | Invariant | Status | One-line reason |
|---|---|---|---|
| 1 | **Lineage mandatory** | **N/A** | This endpoint updates run lifecycle status; it does not create a `Commit` object, so lineage does not apply. |
| 2 | **Storage separation + CAS** | **✓ Respected** | Only Postgres `run` rows are updated. No MinIO/S3 interaction; no blob bytes stored. |
| 3 | **Schema frozen post-publish** | **N/A** | `run` table edits (status + timestamps) are not schema changes. The `run` table is not a Silver/Gold dataset; it is a mutable lifecycle-tracking table. |
| 4 | **LLM calls go through the gateway** | **N/A** | No LLM calls anywhere in this feature. |
| 5 | **Async SQLAlchemy from day one** | **✓ Required** | Handler uses `async def`, `AsyncSession = Depends(get_session)`, `await session.execute(select(...).where(...))`, `result.scalar_one_or_none()` (sync on proxy, per established pattern), `await session.commit()`. No `session.query()` anywhere. |
| 6 | **OpenAPI ↔ TS type sync** | **Required** | `DagsterRunEventPayload`, `DagsterEventResponse`, and `POST /api/dagster/events` extend the OpenAPI surface. Implementer MUST run `make codegen` and commit the diff in the **same** commit as Python changes. |

---

## §11 Out of Scope

The following are explicitly deferred:

| Item | Deferral reason |
|---|---|
| **WebSocket push to frontend** (F-051) | Design doc §9.3 shows `run.status_changed` events pushed via Redis pub/sub → WebSocket. F-051 is a distinct feature; not in F-050's spec criteria. |
| **Event log / audit table** | No `run_event` or audit table. Status transitions overwrite the single `run` row (last-write-wins). Full event log is a post-MVP concern. |
| **Retries from FastAPI side** | FastAPI does not call Dagster to poll. The sensor does the pushing; if it fails, it retries on its next tick. |
| **Sensor-side at-least-once delivery** | The sensor body swallows all HTTP exceptions and returns normally; Dagster advances the cursor on success. Failed HTTP calls result in permanently dropped events (best-effort delivery). If at-least-once semantics are required, the try/except must be removed and exceptions allowed to propagate so Dagster re-attempts the tick. This is deferred post-MVP (e.g. F-051 / event-log table approach). |
| **Run cancellation API** | `POST /api/runs/{id}/cancel` is a separate feature; not in this sprint. |
| **Backfill-level status aggregation** | Computing aggregate backfill status from multiple partition runs is a more complex problem deferred to OQ-1 resolution. |
| **Sensor unit tests in Dagster repo** | The Dagster sensor is a thin HTTP caller; testing it requires a Dagster instance. Deferred to integration testing. FastAPI side is fully unit-tested. |
| **HTTPS between Dagster and FastAPI** | All inter-service communication in MVP is plain HTTP inside Docker compose network. |

---

## §12 Resolved decisions (round-1 addenda)

All four open questions from revision 1 are closed below. This section records the decisions that implementer and verifier must follow.

---

**OQ-1 — CLOSED: Backfill-ID vs. partition-run UUID (use backfill tag)**

`Run.dagster_run_id` stores the Dagster backfill ID (confirmed: `routers/runs.py` lines 148, 204, 222; `dagster/gateway.py` every `launch_*_backfill` returns `backfillId`). A `@run_status_sensor` fires once per individual partition run; each partition run has its own UUID, not the backfill ID.

**Decision (Option a)**: The sensor MUST extract the backfill ID via `context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id`. All backfill-launched partition runs carry `dagster/backfill = <backfillId>` automatically. For non-backfill runs the tag is absent and the fallback `run_id` is used (those runs correctly return `processed=False`).

**Last-write-wins aggregate semantics (MVP-accepted)**: When N partitions in a backfill all complete, the sensor fires N events targeting the same `Run` row. The final event wins. For MVP this is acceptable: single-source is the most common case, Dagster delivers events in roughly chronological order, and a run that briefly shows "failure" then "success" on a partition retry is a known acceptable edge case. V1/V2 verifiers must understand that `Run.status` reflects the **last terminal event received**, not the true aggregate backfill outcome. Full backfill-level status aggregation is deferred post-MVP.

No new DB table is required for MVP.

---

**OQ-2 — CLOSED: `DAGSTER_WEBHOOK_SECRET` empty-default is fail-open → add fail-closed guard**

`config.py` will have `DAGSTER_WEBHOOK_SECRET: str = ""` as the CI-safe default. Without a guard, `secrets.compare_digest("", "")` returns `True`, so any caller sending an empty `X-Dagster-Webhook-Secret:` header passes auth — this is fail-open.

**Decision**: The handler MUST check `if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(status_code=500, detail="Webhook secret not configured on this server")` as its **first** operation, before any `compare_digest` call. This is a **fail-closed guarantee**: a misconfigured deployment returns HTTP 500 immediately rather than silently accepting unauthenticated callers. The `""` default remains for CI compatibility; production deployments must set a non-empty value. Unit test T10 covers this case.

---

**OQ-3 — CLOSED: `minimum_interval_seconds=5` is acceptable for MVP**

**Decision**: 5 seconds confirmed. The `dagster-daemon` process already runs on a tight tick. Clients can expect run status flips to be visible within ~5–10 seconds of Dagster completion. This satisfies the design doc's near-realtime status update goal for MVP.

---

**OQ-4 — CLOSED: `POST /api/dagster/events` MUST appear in the OpenAPI schema**

**Decision**: The endpoint is included in the OpenAPI schema (no `include_in_schema=False`). Unlike the LLM gateway (an internal call-forwarding proxy), this endpoint has a well-defined contract. TypeScript codegen (`packages/api-types/`) will generate matching types. `make codegen` must be run and the resulting diff committed in the same commit per hard invariant #6.

---

## §13 Definition of Done

A sprint is `done` iff **all** of the following hold:

- [ ] `contracts/S050-F-050/agreed.md` exists with every item addressed.
- [ ] `apps/api/dataplat_api/schemas/dagster_events.py` contains `DagsterRunEventPayload` and `DagsterEventResponse`.
- [ ] `apps/api/dataplat_api/routers/dagster_events.py` contains `POST /events` handler with secret auth, DB lookup, state-transition, `await session.commit()`.
- [ ] `apps/api/dataplat_api/main.py` registers `dagster_events_router`.
- [ ] `apps/api/dataplat_api/config.py` has `DAGSTER_WEBHOOK_SECRET: str`.
- [ ] `dagster/dagster_platform/definitions.py` has `fastapi_run_status_sensor` registered in `Definitions(sensors=[...])`. Sensor uses `context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id` as the event payload `dagster_run_id` (OQ-1 resolved).
- [ ] `docker/docker-compose.dev.yml` updated with `FASTAPI_WEBHOOK_URL` and `DAGSTER_WEBHOOK_SECRET` on relevant services.
- [ ] `apps/api/tests/test_dagster_events.py` contains all 10 tests (T1–T10); all pass.
- [ ] `bash verify/checks.sh backend` exits 0.
- [ ] `packages/api-types/openapi.json` regenerated and committed in the same commit.
- [ ] `bash verify/checks.sh all` exits 0.
- [ ] `contracts/S050-F-050/review-final.md` ends with `APPROVED`.
- [ ] `spec/feature_list.json` F-050 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.
- [ ] `git push` executed after sprint close.
