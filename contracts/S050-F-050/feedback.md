# Sprint S050-F-050 — Reviewer Feedback (Mode A)

**Reviewer**: leader (inline Mode A)  
**Date**: 2026-06-05  
**proposed.md revision reviewed**: 2 (round-2); revision 1 reviewed previously  
**Verdict**: **APPROVED**

---

## Round-2 review

**proposed.md revision reviewed**: 2  
**Round-1 findings fold-check**: all 7 items (M1, M2, L1, L2, NIT-1, NIT-2, NIT-3) addressed — see annotations below.

### Round-1 findings — fold status

| ID | Round-1 finding | Status | Citation in proposed.md rev-2 |
|---|---|---|---|
| M1 | Sensor pseudocode sent partition-run UUID instead of backfill ID | ✅ FOLDED | §6 lines 241–244: `tags.get("dagster/backfill") or context.dagster_run.run_id`; §12 OQ-1 CLOSED with backfill-tag decision + last-write-wins limitation fully documented; §9 T6 note reworded to say tag extraction is validated only via integration |
| M2 | Handler fail-open when `DAGSTER_WEBHOOK_SECRET=""` | ✅ FOLDED | §5 step 0 and §7 handler sketch both show `if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(500, ...)` as the **first** check; §12 OQ-2 CLOSED; T10 covers it |
| L1 | Sensor docstring incorrectly claimed "retries on next tick" | ✅ FOLDED | §6 docstring now reads "best-effort, NOT at-least-once … permanently dropped — it will NOT be retried"; `except` block comment matches; §11 adds "Sensor-side at-least-once delivery" deferral row |
| L2 | §12 OQs left open "reviewer should confirm" | ✅ FOLDED | §12 renamed to "Resolved decisions (round-1 addenda)"; all four OQs carry CLOSED headings with concrete decisions and no further deferred questions |
| NIT-1 | Unused `RunFailureSensorContext` import in §6 | ✅ FOLDED | §6 imports now list only `RunStatusSensorContext`, `run_status_sensor`, `DagsterRunStatus` — `RunFailureSensorContext` absent |
| NIT-2 | V-map V2 incorrectly cited T3 (`started_at`) instead of T1/T2 | ✅ FOLDED | §9 V-map V2 row now cites "T1 … T2 … T4" — T3 removed from V2 coverage |
| NIT-3 | §13 DoD item for definitions.py underspecified | ✅ FOLDED | §13 DoD item now cites exact code: `context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id` |

### New-issue scan on rev-2 edits

Re-scanned the entire 487-line proposed.md rev-2 for consistency issues introduced by the edits.

**No new M or L issues found.**

Three advisory cosmetic observations (not blocking; for implementer awareness only):

- **ADVISORY-A** (§11 stale text): The "Retries from FastAPI side" row in §11 still contains the sentence "the sensor does the pushing; if it fails, it retries on its next tick." This contradicts the corrected §6 docstring and the immediately-following §11 row ("Sensor-side at-least-once delivery: …permanently dropped"). Not ambiguous to an implementer who reads both rows together and the §6 docstring; no action required before coding begins.

- **ADVISORY-B** (§8 field-count blurb): §8 describes `DagsterRunEventPayload` as a "4-field Pydantic model" but §3.2 defines 3 fields (`event_type`, `dagster_run_id`, `timestamp`). §3.2 is the authoritative schema; not ambiguous to implementer.

- **ADVISORY-C** (§11 stale OQ reference): The "Backfill-level status aggregation" deferral row in §11 says "deferred to OQ-1 resolution" but OQ-1 is now closed. Should read "deferred post-MVP". Not ambiguous; §12 OQ-1 CLOSED section is authoritative.

None of these make agreed.md ambiguous to the implementer. They can be cleaned up passively if the implementer notices, but are not required.

### Consistency cross-checks (round-2)

| Check | Result |
|---|---|
| Test count: §8 "10 unit tests", §9 "Test count: 10", §13 "all 10 tests (T1–T10)" | ✓ All three agree |
| §5 pseudocode step 0 and §7 handler sketch both show fail-closed guard as first check | ✓ Consistent |
| §12 OQ-1 decision matches §6 sensor pseudocode lines 241–244 | ✓ Consistent |
| §12 OQ-2 decision matches §5 step 0 + §7 handler sketch + T10 | ✓ Consistent |
| §13 DoD sensor item matches §6 pseudocode | ✓ Consistent |
| Hard invariant #5 (async SA): `async def`, `AsyncSession`, `await session.execute`, `await session.commit`, no `session.query()` | ✓ |
| Hard invariant #6 (codegen): `make codegen` required in §8 + §13 DoD | ✓ |
| Scope discipline: no Celery, no DinD, no ACL, no self-registration, no WebSocket (F-051 deferred) | ✓ |
| V-map V1 covered by T1+T2 (status) + T5 (commit confirms DB write) | ✓ |
| V-map V2 covered by T1+T2 (ended_at not None on terminal events) + T4 (RUN_CANCELED) | ✓ |
| §6 imports: only `RunStatusSensorContext`, `run_status_sensor`, `DagsterRunStatus` | ✓ |

---

**APPROVED**

*This proposed.md (rev-2) is ready to be promoted to `contracts/S050-F-050/agreed.md` without further changes. The three advisory cosmetic items above are not blockers.*

---

---

## Round-1 review (preserved for audit)

**proposed.md revision reviewed**: 1  
**Round-1 verdict**: CHANGES_REQUESTED (2 M, 2 L, 3 NIT)

---

### Source material read (round-1)

- `contracts/S050-F-050/proposed.md` (448 lines) — full read
- `spec/feature_list.json` entry F-050 — verification criteria confirmed
- `docs/data_platform_design.md` — grep for webhook / sensor / backfill / event (lines 142, 195, 370, 528, 846, 889, 941)
- `spec/tech-direction.md` — full read; async SA + RQ + subprocess constraints confirmed
- `apps/api/dataplat_api/db/models.py` lines 285–327 — `Run` ORM: `dagster_run_id TEXT UNIQUE NOT NULL`, `status TEXT NOT NULL`, `started_at TIMESTAMPTZ nullable`, `ended_at TIMESTAMPTZ nullable` confirmed
- `apps/api/dataplat_api/routers/runs.py` — confirmed `backfill_id` (from `launchPartitionBackfill` → `backfillId`) is stored as `dagster_run_id` on `Run` row (lines 148, 204, 222)
- `apps/api/dataplat_api/dagster/gateway.py` — confirmed every `launch_*_backfill()` returns the GraphQL `backfillId` string, not a partition-run UUID
- `apps/api/dataplat_api/config.py` — Settings model, `extra="ignore"`, no existing `DAGSTER_WEBHOOK_SECRET` field
- `dagster/dagster_platform/definitions.py` — confirmed path; `Definitions` object currently has `jobs`, `assets`, `resources` only; no `sensors`
- `contracts/S048-F-048/agreed.md` and `contracts/S049-F-049/agreed.md` — rigor reference
- `claude-progress.txt` — read in full (665 lines)

---

### Calibration checklist (round-1 quick-pass)

| Check | Result |
|---|---|
| Hard invariant #5 (async SA) | ✓ `async def`, `AsyncSession`, `await session.execute`, `await session.commit`, no `session.query()` |
| Hard invariant #6 (codegen) | ✓ Required and documented in §8 + §13 DoD |
| Hard invariant #4 (no LLM) | ✓ N/A |
| Hard invariants #1–#3 | ✓ N/A (no Commit objects, no blob bytes in Postgres, no Silver/Gold schema edits) |
| Scope discipline — nothing from the deferred list snuck in | ✓ Celery, Docker-in-Docker, WebSocket, ACLs all absent |
| F-050 V-criteria (V1 status flip, V2 ended_at set) | covered by T1/T2 (V1) and T3/T4 (V2) — contingent on M1 resolution |
| OpenAPI regen baked into §8 + §13 DoD | ✓ |
| Dagster sensor file path | ✓ `dagster/dagster_platform/definitions.py` confirmed |

---

### Round-1 numbered findings

#### M1 ✅ FOLDED — OQ-1 unresolved: sensor pseudocode sends the wrong `dagster_run_id` for all backfill-triggered runs

**Severity**: MUST-FIX — blocks V1 and V2 in integration  
**Location**: §6 lines 222–226; §12 OQ-1; §9 T6 note (line 367)

**Problem (precise)**:

`apps/api/dataplat_api/routers/runs.py` (confirmed) stores `backfill_id` — the Dagster `backfillId` from `launchPartitionBackfill` — in `Run.dagster_run_id`. In Dagster 1.11.16, a backfill with `n` source partitions creates `n` individual worker runs. Each worker run has its own UUID (`context.dagster_run.run_id`). These UUIDs are **not** the backfill ID.

The sensor pseudocode in §6, lines 222–226, sets:
```python
"dagster_run_id": context.dagster_run.run_id,
```
This is the partition-run UUID — it will **never** match `Run.dagster_run_id` for backfill-launched runs. The handler will always return `{"processed": false, "reason": "unknown_run"}` for every production run. V1 and V2 will permanently fail in integration.

The proposed.md acknowledges the correct fix in §6 lines 258–259: "The sensor can also lookup by using Dagster's run tags. When `launch_extract_backfill` launches a backfill, all partition runs share a `dagster/backfill` tag set to the backfill ID." This is the correct approach, confirmed by Dagster 1.x tag semantics. However, the sensor pseudocode is **not updated** to implement it, OQ-1 in §12 still says "Reviewer must decide", and §9 T6 note claims incidental coverage that does not actually test the tag-based path.

**Required change for agreed.md**:

1. **Mandate the backfill-tag approach in §6 sensor pseudocode.** Replace line 224 with:
   ```python
   "dagster_run_id": (
       context.dagster_run.tags.get("dagster/backfill")
       or context.dagster_run.run_id
   ),
   ```
   Rationale: all backfill-launched partition runs carry `dagster/backfill = <backfillId>` in their tags. For non-backfill runs (e.g., `hello_world_job`), the tag is absent and `run_id` is used as the fallback; those runs will correctly return `processed=false` since `hello_world_job` has no `Run` row.

2. **Document the acknowledged aggregate-status limitation.** With multiple partition runs sharing one `Run.dagster_run_id`, the last-arriving terminal event wins. For a 3-partition backfill where partition 1 fails but partitions 2 and 3 succeed (in that order), the final `Run.status` will be `'success'` — which is misleading. This is acceptable for MVP (single-source most common case; see §12 "Backfill-level status aggregation" deferral), but the agreed.md must state it explicitly as a known limitation so that V1/V2 verification understands what it is testing ("status reflects the last terminal event, not true aggregate backfill success").

3. **Close OQ-1 in §12 with the chosen option (a): backfill-tag with last-write-wins aggregate.** The "reviewer must decide" phrasing must be replaced with the decision.

4. **Correct §9 T6 note**: T6 tests handler behavior when `scalar_one_or_none` returns `None` (unknown ID). This is valid and useful, but it does NOT "cover the OQ-1 backfill mismatch scenario" specifically — the mismatch is now fixed in the sensor, not visible to handler tests. Reword the note to accurately state that sensor-level tag extraction is validated only via integration (deferred per §11).

---

#### M2 ✅ FOLDED — OQ-2 unresolved: handler is fail-open when `DAGSTER_WEBHOOK_SECRET` is empty

**Severity**: MUST-FIX — security defect, fail-open auth path  
**Location**: §7 lines 271–272 (table row "Empty secret config"), §7 lines 286–290 (handler sketch), §12 OQ-2

**Problem**:

`config.py` will have `DAGSTER_WEBHOOK_SECRET: str = ""` as the default. The handler sketch is:
```python
if x_dagster_webhook_secret is None or not secrets.compare_digest(
    x_dagster_webhook_secret, settings.DAGSTER_WEBHOOK_SECRET
):
    raise HTTPException(status_code=401, ...)
```

If `DAGSTER_WEBHOOK_SECRET` is not set in the environment, both `settings.DAGSTER_WEBHOOK_SECRET` and an inbound `X-Dagster-Webhook-Secret: ` (empty string value) equal `""`. `x_dagster_webhook_secret is None` → False. `secrets.compare_digest("", "")` → True. `not True` → False. The condition is False → **auth passes for any caller who sends an empty secret header**. This is fail-open.

The proposed.md §7 table explicitly states "the handler should return 500 (or the startup check should fail fast)" but the handler sketch does not implement this guard, and OQ-2 says "Reviewer should confirm" — the reviewer is confirming now.

**Required change for agreed.md**:

Add the following as the **first check** inside the handler (before the `compare_digest` call), and document it in §7:

```python
if not settings.DAGSTER_WEBHOOK_SECRET:
    raise HTTPException(
        status_code=500,
        detail="Webhook secret not configured on this server",
    )
```

This makes the handler fail-closed when the env var is absent: 500 is the correct status (misconfiguration, not auth failure). OQ-2 must be closed in §12 with this decision rather than deferred to the reviewer.

**Rationale note**: Docker internal network isolation IS the primary defense-in-depth, so this is not exploitable from outside. But the proposed.md explicitly states "any empty header value would match → misconfiguration" and promises a guard. Agreed.md must deliver on that promise.

---

#### L1 ✅ FOLDED — §6 sensor docstring claims retry semantics that don't exist

**Severity**: SHOULD-FIX — misleading documentation; implementer and future maintainers will misunderstand reliability guarantees  
**Location**: §6 lines 210–212 (sensor function docstring); §6 sensor `except` block comment

**Problem**:

The sensor docstring (§6 lines 210–212) says:
> "No exception is raised on HTTP failure — failures are logged and the sensor retries on its next tick."

This is **factually wrong**. In Dagster 1.x, a `@run_status_sensor` that completes without raising an exception marks the tick as **SUCCESS** and advances the internal cursor — the event is consumed. The next tick will process **new** events, not the failed one. To get retry semantics, the exception must propagate out of the sensor function (which marks the tick as ERROR/FAILURE; Dagster retries the same event on the next poll).

The current sensor body swallows the exception and returns `None`, meaning: HTTP failures result in **silently dropped events with no retry**. This is a valid best-effort design choice for MVP, but calling it "retries on its next tick" is incorrect and will mislead the verifier into expecting stronger semantics than are delivered.

**Required change for agreed.md**:

Correct the sensor docstring and the `except` block comment to accurately describe the behavior. The docstring should read something like:
> "No exception is raised on HTTP failure — failures are logged and the event is **silently dropped** (best-effort, no retry). If retry semantics are required, remove the try/except and let exceptions propagate; Dagster will re-attempt the tick on the next poll interval."

The §11 Out of Scope item "Retries from FastAPI side" is correctly deferred, but the agreed.md should also note that **sensor-side delivery is best-effort, not at-least-once**.

---

#### L2 ✅ FOLDED — OQ-2 must be closed in §12, not left for reviewer to confirm

**Severity**: SHOULD-FIX — open questions in proposed.md that are suitable for agreed.md must be resolved before promotion  
**Location**: §12 OQ-2 (line 413–414)

**Problem**:

OQ-2 ends with "Reviewer should confirm." Per the sprint workflow, the reviewer's feedback resolves OQs; the agreed.md must contain the decided answer, not another deferred question. M2 above gives the concrete resolution. When proposed.md is promoted to agreed.md, §12 OQ-2 must be replaced with the closed decision:

> **OQ-2 — CLOSED**: `DAGSTER_WEBHOOK_SECRET` defaults to `""` in CI environments. Handler must check `if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(500, ...)` before `compare_digest` (see §7). This makes misconfiguration visible immediately rather than silently accepting any empty-string credential.

Similarly, OQ-1 through OQ-4 should each have "CLOSED" status with the decided answer when promoted to agreed.md. OQ-3 (5s interval): **reviewer confirms 5s is acceptable for MVP**. OQ-4 (include in OpenAPI): **reviewer confirms include in schema** — the endpoint has a well-defined contract and benefits from TypeScript codegen.

---

#### NIT-1 ✅ FOLDED — §6 sensor imports an unused symbol

**Severity**: COSMETIC  
**Location**: §6 line 187

```python
from dagster import RunStatusSensorContext, RunFailureSensorContext, run_status_sensor
```

`RunFailureSensorContext` is imported but the sensor function signature uses only `RunStatusSensorContext`. Remove `RunFailureSensorContext` from the import to avoid a Pyright unused-import warning and reader confusion.

---

#### NIT-2 ✅ FOLDED — §9 V-map V2 incorrectly cited T3 (`started_at`) instead of T1/T2

**Severity**: COSMETIC  
**Location**: §9 V-map (lines 347–349) and test table (lines 355–363)

The V-map cites "T5 (commit called — confirms DB write)" as a V1 coverage test, but the test table shows:
- T5 = `test_session_commit_called_on_known_run`  
- T6 = `test_unknown_dagster_run_id_returns_processed_false`

This is internally consistent (T5 IS the commit test). However, V2 (`ended_at` set) is listed as "T3, T4" in the V-map, while T3 actually tests `RUN_START` (started_at, NOT ended_at) and T4 tests `RUN_CANCELED`. The V2 verification should cite T1 and T2 (which both assert `run.ended_at is not None`) plus T4, not T3. Update the V-map to remove T3 from V2 coverage.

---

#### NIT-3 ✅ FOLDED — §13 DoD item for `dagster_platform/definitions.py` should cite specific sensor pseudocode fix

**Severity**: COSMETIC  
**Location**: §13 line 439

The current DoD item reads: "OQ-1 resolution implemented (backfill tag or alternative)." After M1 resolution, this should be concrete:
> "`fastapi_run_status_sensor` in `definitions.py` uses `context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id` as the event payload `dagster_run_id`."

This prevents a future implementer from choosing option (c) ("accept pending forever") and still ticking the DoD item.

---

### Round-1 summary table

| ID | Sev | Location | Issue | Required action |
|---|---|---|---|---|
| M1 | MUST-FIX | §6 ll.222–226; §12 OQ-1 | Sensor sends partition run UUID, not backfill ID → V1/V2 always miss in integration | Update sensor pseudocode to use backfill tag; close OQ-1 with backfill-tag decision; document last-write-wins aggregate limitation |
| M2 | MUST-FIX | §7 ll.271–272, 286–290; §12 OQ-2 | `DAGSTER_WEBHOOK_SECRET=""` default + no guard = fail-open auth | Add `if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(500, ...)` as first handler check; close OQ-2 |
| L1 | SHOULD-FIX | §6 ll.210–212 | Sensor docstring says "retries on next tick" — swallowing the exception means no retry, event is silently dropped | Correct docstring to say "best-effort, event dropped on failure; raise to enable retry" |
| L2 | SHOULD-FIX | §12 OQ-2 | OQ-2 left open "reviewer should confirm" — must be closed in agreed.md | Close all OQs with concrete decisions before promoting to agreed.md |
| NIT-1 | COSMETIC | §6 l.187 | Unused `RunFailureSensorContext` import | Remove |
| NIT-2 | COSMETIC | §9 V-map ll.347–349 | V2 V-map cites T3 (tests `started_at`, not `ended_at`) | Replace T3 with T1/T2 in V2 mapping |
| NIT-3 | COSMETIC | §13 l.439 | DoD OQ-1 item is underspecified | Make concrete: cite backfill-tag code specifically |

---

### Round-1 reviewer decisions on open questions

| OQ | Decision |
|---|---|
| OQ-1 | **Option (a): use backfill tag**. Sensor MUST use `context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id`. Acknowledge last-write-wins aggregate limitation for multi-partition backfills. No new DB table needed for MVP. |
| OQ-2 | **Add fail-closed guard in handler** (`if not settings.DAGSTER_WEBHOOK_SECRET: raise HTTPException(500, ...)`). Default `""` in config is acceptable for CI. |
| OQ-3 | **5 seconds confirmed** acceptable for MVP. The daemon already runs on a tight tick for sensor operation. |
| OQ-4 | **Include in OpenAPI schema**. This endpoint has a well-defined contract; TypeScript codegen and SDK discoverability outweigh the "internal only" concern. |

---

### What is NOT blocking (round-1, explicitly accepted)

- **Last-write-wins for idempotent duplicate events**: correct for MVP; duplicate terminal events for the same run are harmless.
- **Unknown `dagster_run_id` → HTTP 200 `processed=false`**: correct and intentional; prevents sensor retry loops on non-business runs (`hello_world_job` etc.).
- **No state-transition guards** (e.g., rejecting `running` after `success`): acceptable for MVP given Dagster delivers events in order within a run. Acknowledged in §11 Out of Scope.
- **Sensor unit tests deferred**: documented in §11; handler tests (T1–T9) adequately cover the FastAPI side.
- **HTTPS not used** between Dagster and FastAPI: Docker internal network is sufficient for MVP per design doc.
- **`started_at` update on `RUN_START`**: within scope; clean implementation; not required by V-criteria but not harmful.
- **`DagsterEventResponse` without `ConfigDict(extra="ignore")`**: correct; response models do not need it.
- **Async SA usage**: fully compliant — `await session.execute(select(...).where(...))`, `scalar_one_or_none()` on sync proxy, `await session.commit()`. Pattern matches F-048/F-049 precedent.
- **`minimum_interval_seconds=5`**: confirmed acceptable. See OQ-3 decision above.
