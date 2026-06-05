# Sprint S052-F-052 — Reviewer Feedback (Mode A, Round 2)

**Reviewer:** reviewer (Mode A — pre-implementation contract review)  
**Date:** 2026-06-05  
**Proposal reviewed:** `contracts/S052-F-052/proposed.md` (rev-2, 417 lines)  
**Round-1 feedback read:** `contracts/S052-F-052/feedback.md` (round 1) ✓  
**Code files cross-checked:** `dagster_events.py`, `broker.py`, `ws_runs.py`, `schemas/realtime.py`, `schemas/dagster_events.py`, `definitions.py` ✓  
**Calibration file read:** `verify/reviewer-calibration.md` ✓

---

## VERDICT: APPROVED

All 7 round-1 findings (H1, H2, M1, M2, M3, L1, L2) and NIT-1 are RESOLVED. No new findings of MEDIUM severity or higher are introduced by the revision. The proposal is safe to promote to `agreed.md` and hand to the implementer.

---

## Per-Round-1-Finding Resolution

### H1: RESOLVED

**Probe conducted; path collapsed.** §3.5 now documents a single committed path — two dedicated `@asset_sensor` instances — with evidence from the live Dagster 1.11.16 container:
- OQ-3 (asset_selection): confirmed non-None for backfill runs, but Path 1 (`@run_status_sensor`) correctly eliminated because it cannot access per-asset materialization metadata.
- OQ-2 (chunk_count access): confirmed `asset_event.asset_materialization.metadata["chunk_count"].value` is the correct path (round-1's `context.asset_events[-1].materialization.metadata` was wrong — now corrected).
- Path 3 (tag injection) explicitly eliminated.
- The three-branch branching structure is gone; §3.5 is unambiguous.

The **residual implementer question on `@asset_sensor` return type** (`SkipReason` vs `None`): the proposal commits to `SkipReason("notification sent; no run to trigger")`. This is the **correct choice** — `SkipReason` is self-documenting and surfaces in Dagster's sensor tick history UI (visible in the Dagster webserver), making it far easier to diagnose whether the sensor is firing. `None` is also valid Dagster API but is less informative. The committed decision (`SkipReason`) is settled; no code-time deviation needed.

### H2: RESOLVED

`gateway.py` not in file table because Path 3 (tag injection) is eliminated. §5.2 explicitly explains the absence. Mode B reviewer has a clear "this file is not touched" statement to verify against.

### M1: RESOLVED

`packages/api-types/openapi.json` is now in §5 as **MODIFY (generated)** with the correct note: "Modified by `make codegen` / manual `app.openapi()` export in same commit as `schemas/dagster_events.py` change; hard invariant #6." CAL-3 is satisfied.

### M2: RESOLVED

§10 Criteria-to-Test Mapping table is now internally consistent:
- "Trigger extraction run; receive `asset.materialized`" → **T5** (direct inject), **T7** (end-to-end) ✓
- "Receive `chunks.added` after chunking" → **T6** (direct inject), **T8** (end-to-end) ✓

T6 is correctly described as a `chunks.added` inject test. Mode B reviewer can now verify criteria against the named tests without confusion.

### M3: RESOLVED

By committing to `@asset_sensor`, the real `chunk_count` is available from Dagster materialization metadata. §3.6 now states this clearly: "`count=0` fallback is ONLY a defensive implementation-level guard (missing metadata key), NOT an accepted semantic limitation." The verification criterion `count: <N>` will be satisfied with the real N in production. No `agreed.md` known-limitation annotation is needed.

### L1: RESOLVED

T15 added: POST `ASSET_MATERIALIZATION` with `asset_key="chunks"` and `partition_key="BAD_FORMAT"` (and `partition_key=null`) → asserts HTTP 200, `notification_broker.publish` NOT called, WARNING logged. Covers OQ-6's defensive `ValueError`/`None` handling.

### L2: RESOLVED

§8a added as an explicit load-bearing invariant: `notification_broker.publish` MUST be wrapped in `try: ... except Exception as exc: logger.warning(...)` in `dagster_events.py`, analogous to `run_broker.publish` wrapping in F-051 §4.5. T16 added asserting HTTP 200 even when `notification_broker.publish` raises. The §5 file table row for `dagster_events.py` now notes the try/except requirement. CAL-10 is satisfied.

### NIT-1: RESOLVED

Wrapper §12 eliminated. The addenda section is at `§13` (same level as all other top-level sections), matching F-051 agreed.md convention.

---

## Hard Invariant Audit (round-2 spot check)

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit row |
| 2 | Storage separation + CAS | N/A — no blob writes; notification events are ephemeral in-process dicts |
| 3 | Schema frozen post-publish | N/A — no Silver/Gold publish |
| 4 | LLM calls via gateway | N/A — no LLM call |
| 5 | Async SQLAlchemy | ✓ — `ws_notifications.py` uses `Depends(get_session)` + `await session.execute(select(User)...)` + `scalar_one_or_none()`; `dagster_events.py` extension uses existing `await session.execute(select(Run)...)` pattern; no `session.query()` anywhere |
| 6 | OpenAPI ↔ TS sync | ✓ — `packages/api-types/openapi.json` in §5 file table; §9 mandates `make codegen` + same-commit; expected diff is additive-only (new optional fields + new Literal enum value) |

---

## New Findings Introduced by Revision

None at MEDIUM or above. One LOW NIT below, non-blocking.

### NIT-R2-1 (LOW/non-blocking): T1 conflates two independent behaviours in one test

**Location:** §10 T1 `test_connect_with_valid_jwt_accepted`

T1 is described as: "valid JWT → WS accepted (HTTP 101). Send unexpected text → `{"type":"error","code":"bad_message"}` returned, connection stays open."

Combining connection acceptance AND bad-message error handling in one test means a failure in the bad-message path can mask a passing connect path or vice versa. F-051's T1 kept these separate. Recommend splitting into T1 (connect only) and T1b (bad-message error + connection stays open) at implementation time, but this does NOT block approval — the test coverage intent is clear and the split is a trivial implementation-time call.

### NIT-R2-2 (LOW/non-blocking): `ServerError` reuse — `run_id` field should be documented as optional/None

**Location:** §4.3

The proposal says `ServerError` is reused from `realtime.py` with `run_id` omitted or `None`. Since `realtime.py` is a MODIFY target in §5, the implementer should confirm `run_id` is `Optional[int] = None` in the model (not a required field). If it's required, a new minimal `NotificationError` model is cleaner. Non-blocking: this is verifiable in the MODIFY diff and Mode B will catch it.

---

## Summary

The revision is comprehensive and disciplined. All 7 findings from round 1 are fully resolved. The sensor path is committed (`@asset_sensor`), the file table is complete, the test mapping is consistent, the OpenAPI sync invariant is honoured, the broker try/except is explicit, and test coverage now includes the defensive partition_key and broker-raise paths. The proposal is ready for `agreed.md`.

**Promote `proposed.md` → `agreed.md` without further changes.**
