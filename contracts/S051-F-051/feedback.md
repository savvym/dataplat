# Sprint S051-F-051 — Reviewer Feedback (Mode A)

**Reviewer**: reviewer (Mode A — pre-implementation contract review)  
**Date**: 2026-06-05  
**proposed.md revision reviewed**: 1  
**Verdict**: **CHANGES_REQUESTED** (1 M, 3 L, 3 NIT; 4 OQs settled below)

---

## Source material read

- `contracts/S051-F-051/proposed.md` (364 lines) — full read
- `spec/feature_list.json` F-051 entry and F-050 entry — verification criteria confirmed; F-050 `passes: true` confirmed
- `docs/data_platform_design.md` §9.3 (lines 845–943) — WebSocket event spec, Redis pub/sub mention
- `CLAUDE.md` — hard invariants, scope discipline, MVP boundary
- `spec/tech-direction.md` — stack; Redis listed as in-stack but per `pyproject.toml` absent for MVP; single-worker compose
- `spec/product-spec.md` — MVP boundary, scope discipline
- `apps/api/dataplat_api/routers/dagster_events.py` — full read (F-050 implementation, upstream event source)
- `apps/api/dataplat_api/auth/dependencies.py` — JWT decode pattern, `_CREDENTIALS_EXCEPTION`, `oauth2_scheme`
- `apps/api/dataplat_api/db/models.py` lines 280–328 — `Run` ORM: `id BigInteger`, `kind TEXT NOT NULL`, `status TEXT NOT NULL`, `triggered_by BigInteger FK users.id NULLABLE`, `trigger_context JSONB NULLABLE`
- `apps/api/dataplat_api/db/session.py` — `expire_on_commit=False` confirmed (critical for `from` field strategy)
- `contracts/S050-F-050/agreed.md` and `contracts/S050-F-050/feedback.md` — rigor/format reference
- `contracts/S049-F-049/agreed.md` — rigor reference
- `verify/reviewer-calibration.md` — CAL-1 through CAL-11

---

## Calibration checklist (quick-pass)

| Check | Result |
|---|---|
| CAL-1: Async SQLAlchemy | ✓ Both DB accesses use `await session.execute(select(...))` + `scalar_one_or_none()`; no `session.query()`; `await session.commit()` only in dagster_events.py (unchanged). |
| CAL-2: LLM gateway | ✓ N/A — no LLM calls anywhere in this feature. |
| CAL-3: OpenAPI sync | ✓ Proposal adds `routers/ws_runs.py` and `schemas/realtime.py`; WS endpoint does not appear in OpenAPI; Pydantic models not referenced by any HTTP route; make codegen expected to produce no diff. DoD requires running make codegen regardless. |
| CAL-4: Lineage completeness | ✓ N/A — no Commit objects created. |
| CAL-5: CAS path discipline | ✓ N/A — no MinIO interaction. |
| CAL-6: Schema freeze | ✓ N/A — no Silver/Gold schema touched. |
| CAL-7: Bronze faithfulness | ✓ N/A — not a plugin. |
| CAL-8: MVP scope discipline | ✓ No Redis, no ACL, no Celery, no DinD, no OAuth, no asset.materialized, no chunks.added. All deferred in §12. |
| CAL-9: Plugin isolation | ✓ N/A — not a plugin. |
| CAL-10: Test coverage | ✓ T1–T10 cover happy path + multiple failure modes (T2, T3, T5, T9, T10). |
| CAL-11: Bias check | Applied — specific findings with line citations below. |

---

## Hard-invariant audit

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | **Lineage mandatory** | **N/A** | No Commit created or modified. Real-time notification layer only. |
| 2 | **Storage separation + CAS** | **N/A** | No blob bytes stored; no MinIO interaction. WS messages are ephemeral in-memory. |
| 3 | **Schema frozen post-publish** | **N/A** | No Silver/Gold dataset schema touched. |
| 4 | **LLM calls via gateway** | **N/A** | No LLM calls anywhere in this feature. |
| 5 | **Async SQLAlchemy** | **✓ REQUIRED, COMPLIANT** | (a) `SELECT User WHERE id=user_id` at connect time: `await session.execute(select(User).where(User.id == user_id))` + `scalar_one_or_none()`. (b) `SELECT Run WHERE id=N [AND triggered_by=user.id]` at subscribe time: same pattern. No `session.query()`. No sync sessions. `Depends(get_session)` used. |
| 6 | **OpenAPI ↔ TS type sync** | **✓ REQUIRED, COMPLIANT** | WS endpoint does not appear in OpenAPI. `RunStatusChangedEvent` not referenced by any HTTP route → `make codegen` expected to produce no diff to `packages/api-types/openapi.json`. DoD (§14) requires running `make codegen` and committing the diff (if any) in the same commit. Invariant satisfied trivially if no diff. See OQ-1 decision below. |

---

## Numbered findings

### M1 — MUST-FIX: §7 code snippet (single combined WHERE query) contradicts §7 prose (two-step query); T10 will fail as written

**Severity**: MUST-FIX — T10 test will fail if implementer follows the shown code snippet.  
**Location**: §7 lines 197–206 (code snippet) vs. §7 lines 211–212 (prose); §10 T10 definition.

**Problem**:

The code snippet in §7 uses a single combined query:
```python
result = await session.execute(
    select(Run).where(Run.id == cmd.run_id, Run.triggered_by == user.id)
)
run = result.scalar_one_or_none()
if run is None:
    await websocket.send_text(ServerError(code="unauthorized", run_id=cmd.run_id)...)
```

This query returns `None` for **both** cases: (a) run_id does not exist at all, and (b) run exists but is owned by another user. Both cases send `code="unauthorized"`.

However, §7 prose immediately below says: *"Implementation will check with a two-step query: first fetch by `id` only, then check ownership. This gives cleaner error codes (T10)."* And T10 (§10) explicitly tests that a nonexistent run_id returns `{"type":"error","code":"not_found","run_id":N}` — not `"unauthorized"`.

**Consequence**: If the implementer follows the code snippet verbatim, T10 will fail (returns "unauthorized", not "not_found"). This creates an ambiguous specification that will produce a defective implementation.

**Required fix for agreed.md**: Remove the single-query code snippet from §7 and replace it with the two-step query the prose describes:
```python
# Step 1: check existence
result = await session.execute(select(Run).where(Run.id == cmd.run_id))
run = result.scalar_one_or_none()
if run is None:
    await websocket.send_text(ServerError(code="not_found", run_id=cmd.run_id).model_dump_json())
    continue  # keep connection open
# Step 2: check ownership
if run.triggered_by != user.id:
    await websocket.send_text(ServerError(code="unauthorized", run_id=cmd.run_id).model_dump_json())
    continue
# happy path:
queue = asyncio.Queue(maxsize=100)
broker.subscribe(run_id=cmd.run_id, queue=queue)
...
```

Note: `run.triggered_by != user.id` evaluates correctly when `triggered_by IS NULL` (Python `None != int` is True → denied), matching the documented behavior in §7 para 3.

---

### L1 — SHOULD-FIX: Subscribe+publish race is a known gap not listed in failure modes table

**Severity**: SHOULD-FIX — silent event loss scenario must be documented for verifiers.  
**Location**: §8 broker semantics (lines 247–258); §9 failure modes table (lines 266–276).

**Problem**:

The client connection flow is:
1. Client sends `{"type":"subscribe","run_id":N}`
2. Handler performs an `await session.execute(...)` (async DB round-trip)
3. Handler calls `broker.subscribe(run_id=N, queue=queue)`

If `POST /api/dagster/events` fires and calls `broker.publish(run_id=N, ...)` during step 2 (after client sent subscribe, but before broker.subscribe() completes), the event is permanently lost. The client has no way to know it missed a transition. This is the fundamental "subscribe-before-current-state" race present in all push-only notification systems.

This scenario is possible in testing and production (e.g., a fast-completing run where the RUN_SUCCESS webhook arrives milliseconds after the WS subscribe command). The current §9 failure modes table has no entry for it, and §8 does not mention it.

**For MVP**: best-effort delivery is explicitly accepted (§8 "Best-effort (fire-and-forget)"). This race is MVP-acceptable **but must be explicitly documented** so that verifiers understand why VC-2 requires a 2-minute window and why the frontend should also poll `GET /api/runs/{id}` for current status rather than relying solely on WS events.

**Required fix for agreed.md**: Add to §9 failure modes table:

| Scenario | Handling |
|---|---|
| Broker.publish fires between subscribe command receipt and broker.subscribe() registration | Event silently lost. Client may miss transitions that occur during the async DB query at subscribe time. MVP-acceptable (best-effort). Frontend SHOULD poll GET /api/runs/{id} for authoritative current status; WS events are notification-only. |

Also add a one-line note to §8 broker semantics under "Delivery semantics": *"No race-free subscription: events published between subscribe command receipt and broker.subscribe() completion are permanently lost."*

---

### L2 — SHOULD-FIX: Connection handler must explicitly specify the per-connection subscription tracking mechanism for `finally`-block cleanup

**Severity**: SHOULD-FIX — without explicit specification, the "broker.unsubscribe() for all queues" DoD requirement is unimplementable by a careful reading of the contract alone.  
**Location**: §8 lines 257–258; §9 failure mode "Client disconnects mid-session"; §14 DoD "clean disconnect".

**Problem**:

§9 states: *"Client disconnects mid-session → WebSocketDisconnect caught in message loop → broker.unsubscribe() for all queues → sender task cancelled."*

The phrase "for all queues" requires the handler to track which `(run_id, queue)` pairs were registered for *this specific connection*. A client may subscribe to multiple run IDs during a session; the cleanup must unsubscribe all of them, not just the last one. This tracking is **not specified** anywhere in the proposed.md.

Without this specification, an implementer may:
- Only unsubscribe the last subscribed queue (bug: earlier queues leak in `broker._subscribers`)
- Not unsubscribe at all if the exception is not `WebSocketDisconnect` (e.g., `session.execute` raises)

**Required fix for agreed.md**: Add to §8 (or the ws_runs.py description in §3) an explicit statement:

> The WS handler MUST maintain a local `subscriptions: dict[int, asyncio.Queue]` mapping `run_id → queue` for all active subscriptions of this connection. The `finally` block of the main message loop MUST iterate `subscriptions.items()` and call `broker.unsubscribe(run_id, queue)` for each entry, then cancel the sender task. This ensures cleanup on ALL exit paths (WebSocketDisconnect, generic Exception, and normal return).

Update §14 DoD item for "clean disconnect" to cite this mechanism explicitly.

---

### L3 — SHOULD-FIX: Multi-worker silent failure mode not documented; no fail-fast or startup warning specified

**Severity**: SHOULD-FIX — silent data loss in a non-default-but-plausible deployment should be documented.  
**Location**: §8 broker semantics (lines 252–253); §12 Redis deferral row.

**Problem**:

§8 correctly notes the broker is "Single-threaded asyncio event loop" for single-worker MVP. §12 defers Redis to post-MVP. However, neither section states what happens if someone runs `uvicorn --workers 2` or uses gunicorn with multiple workers (a common production pattern that developers may try in staging). In that case:
- Worker A holds subscriber queues for connections on that process.
- `POST /api/dagster/events` arrives at Worker B → `broker.publish()` on Worker B → finds no subscribers (they're on Worker A) → events silently dropped.

This is a silent failure mode with no error logged and no observable symptom except "events not received." It could waste significant debugging time.

**Required fix for agreed.md**: Add to `broker.py` description in §3 or §8:

> **Single-worker constraint**: `RunEventBroker` is an in-process `asyncio` structure. Running the API with `--workers N` (N > 1) or multiple gunicorn processes will silently drop all WebSocket events for connections on a different worker than where the webhook fired. A startup `logger.warning` or `assert` in `main.py` lifespan SHOULD check that `workers == 1` (if detectable) OR the broker.py module docstring MUST state this constraint explicitly so operators don't silently mis-deploy.

Minimum acceptable: a comment in `broker.py` class docstring and in §12 Redis deferral row: *"Running --workers N (N>1) causes silent event loss; in-process broker only works correctly with a single uvicorn worker."*

---

### NIT-1 — §3 table row for `packages/api-types/openapi.json` contradicts §3a

**Severity**: COSMETIC  
**Location**: §3 table, last row (line 74).

The §3 table description reads: *"make codegen — WS endpoint itself is not in OpenAPI, but `RunStatusChangedEvent` schema is (used by HTTP response type; see §3a below)"*. But §3a immediately says the Pydantic models will NOT be referenced by any HTTP route, so `make codegen` will produce no diff and the schema will NOT be in OpenAPI. The table description says the opposite of §3a.

**Fix**: Change the §3 table description to: *"make codegen run; expected no diff since `RunStatusChangedEvent` is not referenced by any HTTP route (WS-only schema). Any diff that does appear must be committed in the same commit per invariant #6."*

---

### NIT-2 — No test for `triggered_by IS NULL` → access denied

**Severity**: COSMETIC  
**Location**: §10 test matrix (lines 283–296).

T5 tests subscription to "another user's run" (run.triggered_by set to a different user_id). `triggered_by IS NULL` (admin-created runs, §7 para 3) is a distinct code path: with the correct two-step query (M1 fix), a NULL triggered_by run would be fetched in step 1 (found), then fail the step-2 ownership check (`None != user.id` → True → denied). Without an explicit test, this path is not covered.

**Suggestion**: Add T11: `test_subscribe_to_run_with_null_triggered_by → unauthorized error`. This confirms the NULL path denies correctly per §7 para 3 and doesn't accidentally permit access.

---

### NIT-3 — Long-lived DB session per WS connection should be acknowledged

**Severity**: COSMETIC  
**Location**: §6 auth flow (lines 162–183); §3 ws_runs.py description.

`session: AsyncSession = Depends(get_session)` on a WS handler means a DB connection pool slot is held for the **entire lifetime of the WebSocket connection** (not just for individual queries). For the default pool size (20 connections), this limits concurrent WS connections. For MVP with small teams ("solo researchers, small teams"), this is harmless, but the limitation should be noted in §12 or a comment in ws_runs.py so a future operator doesn't wonder why the pool exhausts under load.

**Suggestion**: One-line note in §12 or ws_runs.py docstring: *"A single DB connection is held per WS connection for the connection lifetime (Depends limitation). Pool exhaustion occurs at ~pool_size concurrent connections; acceptable for MVP workloads."*

---

## Open Question decisions

All four OQs must be CLOSED (not open) in agreed.md. Decisions:

| OQ | Decision |
|---|---|
| **OQ-1** — Expose `RunStatusChangedEvent` in OpenAPI? | **CLOSED: trivial-diff approach. No sentinel route.** The WS protocol is its own contract orthogonal to OpenAPI. Adding a dummy HTTP route would pollute the schema and couple the WS event shape to the HTTP codegen cycle unnecessarily. `RunStatusChangedEvent` stays as an internal-only Pydantic model with no HTTP route referencing it. `make codegen` will produce no diff; invariant #6 is satisfied trivially. If the frontend TypeScript team needs the shape, hand-write a TS interface in the WS hook file — do not add an MVP sentinel endpoint. |
| **OQ-2** — Bad JSON: close 1003 or reply `bad_message` and continue? | **CLOSED: reply `{"type":"error","code":"bad_message","run_id":null}` and continue.** Closing on bad JSON is unnecessarily punitive for normal frontend dev cycles (e.g., mistyped message during development). The `bad_message` reply gives the client a recovery path. Close-on-bad-JSON would force a full reconnect+resubscribe flow for a transient parse error. The continue approach is correct for MVP. |
| **OQ-3** — Allow `visibility=internal` runs to any authenticated user? | **CLOSED: `triggered_by == user.id` only. Moot for MVP.** CLAUDE.md scope discipline says repository-level granular ACL is deferred (MVP uses `private\|internal` only for repos/datasets). The `Run` table has no `visibility` column (confirmed from models.py lines 285–328), so the question is technically unanswerable until the schema is extended. If a `visibility` column is added to `Run` post-MVP, revisit. For now, `triggered_by == user.id` is the sole ownership check. |
| **OQ-4** — `try/except` around `broker.publish()` in dagster_events.py? | **CLOSED: MANDATORY.** The F-050 webhook SLA (`POST /api/dagster/events` returns HTTP 200, the Dagster sensor must not fail) must not be broken by a broker bug. `broker.publish()` is called **after** `await session.commit()` — the DB write is already durable at that point. An unhandled exception from `broker.publish()` would still propagate to FastAPI and return HTTP 500, causing the Dagster sensor to log a warning and potentially retry. Wrap as follows: `try: broker.publish(...) except Exception as exc: logger.warning("broker.publish failed: %s", exc)`. This is REQUIRED in `dagster_events.py` and must appear in the agreed.md edit description for that file, AND in §14 DoD (already present — DoD item is correct). |

---

## What is NOT blocking (explicitly accepted)

- **Query-param `?token=` auth for MVP**: Acknowledged log-leakage risk in §6 (line 150); post-MVP subprotocol approach documented in §12. Browser WebSocket API has no custom header support; query-param is the universal browser-compatible approach. Acceptable for MVP. No referer-leakage risk (WS upgrade does not include Referer in practice). Short-lived JWTs are the recommended mitigation; noted in §12.
- **No mid-session JWT reauth**: Acceptable for MVP. Token validated at connect time only; expired sessions remain open until natural disconnect. Documented known limitation (§12).
- **In-process broker, no Redis**: Correctly justified in §2 (single-worker Docker Compose, no Redis in pyproject.toml). Design doc §9.3 line 941 mentions Redis; §12 defers to post-MVP. Justification is honest and correct.
- **`run.status_changed` schema conformance**: Fields `type`, `run_id`, `kind`, `from`, `to`, `metadata` match design doc §9.3 (lines 912–921). `run_id` type of `int` (the business `Run.id` PK) is correct and internally consistent with the subscribe command using integer `run_id`. The design doc shows `"..."` as a placeholder, not a type constraint.
- **`from` field capture strategy**: Capturing `prev_status = run.status` before the `if/elif` status-assignment block in `dagster_events.py` is correct and zero-cost. `expire_on_commit=False` (confirmed in `session.py`) means the ORM object is not refreshed after commit; `run.status` post-commit holds the updated value. Capturing *before* the assignment ensures `prev_status` holds the pre-transition value. ✓
- **Bounded queue drop-oldest policy**: `asyncio.Queue(maxsize=100)` + drop-oldest is correct for this use case. Status transitions are idempotent from the client's perspective (it can recover current state from `GET /api/runs/{id}`). Acceptable.
- **Fire-and-forget delivery semantics**: Consistent with F-050's best-effort sensor delivery (S050-F-050 agreed.md §11). Acceptable and explicit.
- **Async SQLAlchemy compliance**: Fully compliant — `await session.execute(select(...))`, `scalar_one_or_none()`, no `session.query()`. Matches CAL-1. ✓
- **Test coverage T1–T10**: Substantive tests covering the happy path (T1, T4, T6, T8), auth security (T2, T3), owner-scope security (T5), error codes (T9, T10), and clean disconnect (T7). T8 (end-to-end via dagster_events router) is particularly important for VC-2. ✓
- **Scope**: No `asset.materialized`, no `chunks.added`, no `/ws/notifications`, no Redis, no run-cancellation API. All correctly deferred in §12. ✓
- **`triggered_by IS NULL` handling** (§7 para 3): With the corrected two-step query (M1 fix), step 2 checks `run.triggered_by != user.id`; Python `None != int` is True → denied. Correctly blocks admin-created runs without explicit NULL check. ✓

---

## Summary table

| ID | Sev | Location | Issue | Required action |
|---|---|---|---|---|
| M1 | MUST-FIX | §7 ll.197–206, 211–212; §10 T10 | Single combined WHERE query in code snippet contradicts two-step query in prose; T10 will fail with the snippet | Replace code snippet with two-step query (fetch-by-id, then ownership check) |
| L1 | SHOULD-FIX | §8, §9 | Subscribe+publish race not documented as known gap | Add failure-mode row to §9 table; add one-line note to §8 broker semantics |
| L2 | SHOULD-FIX | §8 ll.257–258; §9; §14 DoD | "broker.unsubscribe() for all queues" requires a tracking mechanism not specified | Specify local `subscriptions: dict[int, asyncio.Queue]` in handler; explicit `finally` block cleanup contract |
| L3 | SHOULD-FIX | §8 ll.252–253; §12 | Multi-worker silent event loss not documented | Add comment/note to broker.py docstring and §12 deferral row |
| NIT-1 | COSMETIC | §3 table l.74 | openapi.json table description contradicts §3a | Correct to say "expected no diff" |
| NIT-2 | COSMETIC | §10 | No test for `triggered_by IS NULL` → denied | Add T11 |
| NIT-3 | COSMETIC | §6, §3 | Long-lived DB session per WS connection not acknowledged | One-line note in §12 or ws_runs.py docstring |

---

## Path to APPROVED

Address M1 (required) and L1–L3 (required) in a rev-2 proposed.md and resubmit. The four OQ decisions above must be incorporated as CLOSED items in §13. NIT-1 through NIT-3 are non-blocking and may be cleaned up passively. Once M1 and L1–L3 are addressed (and a fresh scan of the revised contract confirms no regressions), this proposed.md is ready for promotion to `contracts/S051-F-051/agreed.md`.

---

**CHANGES_REQUESTED**

---

## Round 2

**Reviewer**: reviewer (Mode A — pre-implementation contract review)
**Date**: 2026-06-05
**proposed.md revision reviewed**: 2 (434 lines)
**Round-1 findings addressed**: M1, L1, L2, L3, NIT-1, NIT-2, NIT-3, OQ-1 through OQ-4

---

**APPROVED**

---

### M1 — Was it fixed correctly?

**✓ FIXED CORRECTLY.**

§7 now contains a clean two-step query:

- **Step 1** (`SELECT Run WHERE id=N`, no ownership filter) → `scalar_one_or_none()` → if `None` → send `not_found`, `continue`.
- **Step 2** (`if run.triggered_by != user.id`) → send `unauthorized`, `continue`.
- **Happy path** → `queue = asyncio.Queue(maxsize=100)`, `subscriptions[cmd.run_id] = queue`, `broker.subscribe(...)`, send `subscribed`.

The code snippet (§7 lines 213–233) is unambiguous and matches the prose. T10 (nonexistent run_id → `not_found`) will now pass. T11 (NULL `triggered_by` → `unauthorized`) is now also covered by the step-2 check (`None != int` → `True` → denied). The §2 architecture diagram at steps 4a/4b/4c correctly reflects the two-step flow.

**Existence-leakage trade-off** acknowledged honestly in §7 under "Two-step trade-off — existence leakage." The argument — that cross-user run-ID enumeration is already possible via `GET /api/runs?...` and that `not_found` / `unauthorized` distinction adds no new attack surface for authenticated users — is accurate and calibrated. No hand-waving.

---

### L1 — Subscribe+publish race — §8 AND §9, frontend poll?

**✓ FIXED CORRECTLY, both locations present.**

- **§8** "Delivery semantics — Race window (L1)" paragraph: clearly documents the gap ("Events published between the moment the client sends `subscribe` and the moment `broker.subscribe()` completes are permanently lost"), labels it MVP-acceptable, and states: _"Frontend MUST poll `GET /api/runs/{id}` for authoritative current status; WS events are notification-only supplements, not the source of truth."_
- **§9** failure modes table: new row covers the race window scenario with the same frontend-poll advisory.

Both locations covered. The frontend poll requirement is present and loud in both places. ✓

**Minor inconsistency — NIT-2-1 (non-blocking)**: §8 uses `MUST` for the frontend poll advisory; §9 uses `SHOULD`. RFC 2119 modals diverge between the two sections. `MUST` in §8 (the authoritative spec section) is appropriate. `SHOULD` in §9 (failure modes table, inherently looser) is not harmful. A future rev could normalize, but this does not affect correctness.

---

### L2 — Per-connection subscription tracking explicit?

**✓ FIXED CORRECTLY.**

Three locations all consistent:

1. **§2 architecture diagram** (step 3): `subscriptions: dict[int, asyncio.Queue] = {}` is explicitly shown inline.
2. **§7 happy-path code snippet**: `subscriptions[cmd.run_id] = queue` assignment present.
3. **§8 "Per-connection subscription tracking (L2)"**: full specification — `subscriptions: dict[int, asyncio.Queue]` declaration, per-`subscribe` assignment, per-`unsubscribe` deletion, **and** a `try/finally` cleanup block with:
   ```python
   finally:
       for run_id, q in subscriptions.items():
           broker.unsubscribe(run_id, q)
       sender_task.cancel()
   ```
   The contract states this ensures cleanup on ALL exit paths (WebSocketDisconnect, generic Exception, normal return). ✓

§14 DoD has an explicit checklist item: "per-connection `subscriptions: dict[int, asyncio.Queue]`; `try/finally` cleanup iterating all subscriptions (§8 L2)." ✓

**Minor concern — NIT-2-2 (non-blocking)**: The `try/finally` code snippet in §8 calls `sender_task.cancel()` unconditionally. If an exception occurs before the sender task is spawned (e.g., during the very first `await websocket.receive_text()` before `asyncio.create_task(...)` is called), this raises `NameError`. The safe pattern is to initialise `sender_task: asyncio.Task | None = None` before the `try` block and guard `if sender_task is not None: sender_task.cancel()`. The architecture implies the sender task is always created immediately after `await websocket.accept()`, making this unreachable in practice, but the spec snippet is slightly unsafe. Not a blocker — the implementer will trivially handle this.

---

### L3 — Multi-worker silent loss — §8 AND §12 AND class docstring?

**✓ FIXED CORRECTLY, all three locations present.**

1. **`RunEventBroker` class docstring** (§8): the code block includes:
   ```
   # Single-worker only: this is an in-process asyncio structure.
   # Running uvicorn --workers N (N > 1) or gunicorn multiproc causes silent
   # event loss for WS connections on a different worker than the one receiving
   # the POST /api/dagster/events webhook. Swap for Redis pub/sub when scaling.
   ```
   The §14 DoD requires this docstring to be present in `broker.py`. ✓
2. **§8 "Single-worker constraint (L3)" paragraph**: clear narrative including "events silently lost, no log, no error." ✓
3. **§12 deferral table** "Redis pub/sub multi-worker fan-out" row: updated with the note "running `uvicorn --workers N` (N > 1) causes silent WS event loss; single worker is the mandatory MVP deployment constraint (see §8 L3)." ✓

---

### All 4 OQs settled per round-1 directives?

| OQ | Directive | Status |
|---|---|---|
| OQ-1 | No sentinel route; `make codegen`; trivial-diff approach | **✓ CLOSED** — §3a and §11 invariant 6 updated. No sentinel route added. |
| OQ-2 | Reply `bad_message` + continue | **✓ CLOSED** — §5 updated; T9 asserts connection stays open after `bad_message`. |
| OQ-3 | `triggered_by == user.id` only; `Run` has no visibility column | **✓ CLOSED** — documented in §12 "Admin-override subscription" deferral row. |
| OQ-4 | MANDATORY `try/except` around `broker.publish()` | **✓ CLOSED** — §4.5 added with the required pattern; §2 diagram shows the try/except on the fan-out path; §14 DoD references §4.5. |

All four closed, all four resolutions match the round-1 directives exactly. ✓

---

### §15 addenda section completeness

**✓ COMPLETE.**

§15 "Round-1 addenda" (lines 419–434) lists all 11 finding IDs (M1, L1, L2, L3, NIT-1, NIT-2, NIT-3, OQ-1, OQ-2, OQ-3, OQ-4) with a "Where folded" column pointing to specific sections for each. The table is honest — every entry can be verified against the body of rev 2. No findings were dropped or papered over. ✓

---

### Hard-invariant audit (§11) still honest?

**✓ STILL HONEST.**

§11 was updated for invariant 5 (Async SQLAlchemy): the description now correctly reflects the two-step SELECT pattern — "(b) two-step `SELECT Run WHERE id=N` then ownership check at subscribe time" — matching the M1 fix. All N/A items remain correctly N/A. No overstatement or evasion anywhere in the table. ✓

---

### Scope audit — no creep introduced?

**✓ CLEAN.** All new content in rev 2 (§4.5, L1/L2/L3 documentation in §8, new §9 failure-mode rows, T11 test, NIT-3 §12 row, §15 addenda) is strictly in response to round-1 findings. No new endpoints, no Redis dependency, no Celery, no ACL extensions, no `asset.materialized` or `chunks.added` work. §12 deferral table is unchanged in scope. ✓

---

### Cross-checks against upstream files

- **`dagster_events.py`**: The existing router (lines 86–97) sets `run.status = "running"` etc. in the if/elif block after `run` is loaded. The proposed edit captures `prev_status = run.status` **before** the if/elif block — this is zero-cost and correct because `run.status` holds the pre-transition value at that point (`expire_on_commit=False` confirmed in round 1). After `await session.commit()` the `broker.publish()` call uses `prev_status` as `from`. ✓
- **`auth/dependencies.py`**: The WS handler in §6 mirrors the `get_current_user` decode logic correctly (`jwt.decode`, `settings.SECRET_KEY`, `settings.JWT_ALGORITHM`, `int(payload["sub"])`). The only minor divergence is `payload["sub"]` (KeyError-raises) vs `payload.get("sub")` (None-safe) in `dependencies.py`. The broad `except Exception: close(1008)` catches the KeyError anyway. Not a defect.
- **Design doc §9.3**: `run.status_changed` payload fields (`type`, `run_id`, `kind`, `from`, `to`, `metadata`) match the design doc spec exactly. `run_id` typed as `int` (the `Run.id` BigInteger PK) is internally consistent. Design doc shows `"run_id": "..."` as a Python dict literal placeholder, not a string type constraint. ✓

---

### New non-blocking NITs (Round 2)

| ID | Sev | Location | Issue |
|---|---|---|---|
| NIT-2-1 | COSMETIC | §8 L2 `try/finally` snippet | `sender_task.cancel()` is unconditional; safe to initialise `sender_task: asyncio.Task \| None = None` and guard with `if sender_task is not None`. Unreachable in normal flow but the spec snippet is slightly unsafe. |
| NIT-2-2 | COSMETIC | §8 vs §9 | "Frontend MUST poll" (§8) vs "Frontend SHOULD poll" (§9) — minor RFC 2119 inconsistency across the two locations. Both communicate the right intent. |

Neither NIT is a blocker. No action required before implementation.

---

### Summary

All round-1 MUST-FIX (M1) and SHOULD-FIX (L1, L2, L3) findings are correctly addressed. All four OQs are closed. §15 addenda is complete. Hard-invariant audit is honest. No scope creep. The specification is unambiguous, internally consistent, and implementable.

**This proposed.md (rev 2) is approved for promotion to `contracts/S051-F-051/agreed.md`.**

---

**APPROVED**
