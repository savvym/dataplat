# Mode A review iter 2 — S005-F-005

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Artifact under review:** `contracts/S005-F-005/proposed.md` (iter 2)

---

VERDICT: APPROVED

---

## Summary

All nine "ADDRESSED" claims from iter 1 are verified against the iter 2 proposed.md text. The two blockers (B-1 grep coverage, B-2 field naming), two highs (H-1 status code, H-2 exception hierarchy), one high (H-3 test enumeration), three mediums, two lows, and two nits are each concretely resolved in the revised text. No new BLOCKER or HIGH issues were introduced by the iter 2 edits. The only new finding is a LOW (fixed `/tmp` filenames in checks.sh — minor operational concern, not a correctness issue). The contract may be promoted to `agreed.md` verbatim.

---

## Calibration checks

- CAL-1 (async session enforcement): N/A — no DB session code introduced; gateway uses async httpx only.
- CAL-2 (LLM gateway enforcement): N/A — no LLM SDK imports; Dagster gateway boundary is enforced by V2 grep in checks.sh.
- CAL-3 (OpenAPI sync): N/A for Mode A contract review. The contract correctly acknowledges the `packages/api-types/` deferral at §3 and §9, consistent with S004-F-004 agreed.md carry-over. The `contract)` layer in checks.sh already exits 0 gracefully when `packages/api-types/` is absent.
- CAL-4 (lineage completeness): N/A — no Commit objects created; `run` Postgres table is not written to this sprint.
- CAL-5 (CAS path discipline): N/A — no blob storage.
- CAL-6 (schema freeze post-publish): N/A — no Silver/Gold schema.
- CAL-7 (Bronze faithfulness): N/A — no Bronze adapter code.
- CAL-8 (MVP scope discipline): PASS — no Celery, DinD, OAuth, granular ACL, or training framework code. Auth deferred with explicit `# TODO(F-008)` inline markers documented in §2.3.
- CAL-9 (plugin isolation): N/A — no plugin code.
- CAL-10 (test coverage): PASS — §3 enumerates 5 named test functions: `test_launch_hello_world_201`, `test_launch_hello_world_503_on_gateway_error`, `test_get_run_status_200_success`, `test_get_run_status_404_when_not_found`, `test_get_run_status_503_on_gateway_error`. Two endpoints, multiple success and failure paths each. CAL-10 satisfied.
- CAL-11 (bias check): Checked — findings below are specific with section references.

---

## Verification of "ADDRESSED" claims

### B-1: V2 grep covers both import forms — VERIFIED

`proposed.md §5.2`: `grep -rln -E '(import httpx|from httpx import)'` covers both `import httpx` and `from httpx import AsyncClient`. The INFO comment in §5.2 explains the existing `dagster)` layer's narrower pattern is a future-revision item, correctly scoped out of this sprint to avoid touching S004-F-004 artifacts. Acceptable.

### B-2: Field naming standardized on `dagster_run_id` — VERIFIED

`proposed.md §2.1` specifies response `{"dagster_run_id": "<dagster_run_id>"}`. `§3` file table specifies `LaunchHelloWorldResponse(dagster_run_id: str)`. `§5.2` checks.sh python3 snippet asserts `'dagster_run_id' in body` and extracts `body['dagster_run_id']`. `§5.1` V1a row updated to match. Internally consistent with `RunStatusResponse(dagster_run_id: str)` and design doc §4.1 column name.

### H-1: 201 Created pinned — VERIFIED

`proposed.md §2.1`: "HTTP status code: 201 Created" with explicit rationale. Route decorator carries `status_code=201`. `§5.2` trigger block asserts `test "$STATUS_CODE" = "201"` and prints body on failure. 2-step curl pattern implemented for both trigger and poll loop.

### H-2: DagsterRunNotFoundError subclass — VERIFIED

`proposed.md §4.2` defines:
```python
class DagsterRunNotFoundError(DagsterGatewayError):
    """Raised by get_run_status() when Dagster reports the run does not exist."""
```
Catch ordering in the route handler is documented explicitly: `except DagsterRunNotFoundError → 404` before `except DagsterGatewayError → 503`. `§3` file table confirms `gateway.py` is modified to add this subclass.

### H-3: 5 named test functions — VERIFIED

`proposed.md §3` file table for `test_runs_hello_world.py` enumerates all five by function name, including the 404-path test (`test_get_run_status_404_when_not_found`) that was absent in iter 1. CAL-10 satisfied.

### M-1: OQ-2 resolution required before commit — VERIFIED

`proposed.md §6 OQ-2` says "OPEN — MUST be resolved and the answer added to `agreed.md` §4 as a numbered addendum BEFORE the implementer commits." Introspection command given inline. `§4.2` repeats: "confirmed field name AND the confirmed not-found type name must both be pinned in `agreed.md` as an addendum."

### M-2: CANCELING row added — VERIFIED

`proposed.md §2.2` table now has `| CANCELING | "running" |` as its own explicit row, separate from the catch-all fallback. Table is complete for all known Dagster 1.x RunStatus values.

### M-3: Dual-router wiring explicit — VERIFIED

`proposed.md §3` specifies `admin_runs_router = APIRouter(prefix="/api/admin/runs", tags=["admin", "runs"])` and `runs_router = APIRouter(prefix="/api/runs", tags=["runs"])` as two distinct instances. `main.py` row specifies "Two `include_router` calls: `app.include_router(admin_runs_router)` and `app.include_router(runs_router)`." `§6 OQ-6` RESOLVED with the same binding pattern. Implementer is explicitly told not to use a single-router approach.

### L-1: 2-step curl pattern for both verb/poll — VERIFIED

Both the trigger block (V1a) and the poll loop (V1b) use `-w '\n%{http_code}' -o /tmp/*_body` with explicit status code assertion before body parsing. Diagnostic quality satisfied.

### L-2: `runs` placed before `;;` in `all)` — VERIFIED

`proposed.md §5.2` shows the complete `all)` block with `bash "$0" runs` inside the block before `;;`, with an explicit warning comment: "a copy-paste error adding it after `;;` would silently skip the `runs` layer in `all)` runs."

### L-3: conftest applicability stated — VERIFIED

`proposed.md §3` test file note states the existing conftest `MockTransport` applies unchanged, why method-level mocking is compatible, and warns future test authors who need real HTTP to explicitly pass `transport=`.

### N-1: OQ-3 (informational) — unchanged per iter 1 instruction. Acceptable.

### N-2: Module docstring conventions named — VERIFIED

`proposed.md §3` `runs.py` create action specifies "Module docstring cites 'S005-F-005', references F-018 (generic `POST /api/runs`) and F-008 (admin auth) as deferral sprints."

---

## New findings from iter 2 edits

### LOW (new): Fixed `/tmp` filenames in checks.sh create a race on shared CI hosts

**Section:** `proposed.md §5.2`, checks.sh `runs)` V1 and poll blocks.

`/tmp/launch_body` and `/tmp/status_body` are fixed filenames with no PID/random suffix. If two CI runners execute `bash verify/checks.sh runs` concurrently on the same host (e.g., a parallel matrix build), the temp files could be clobbered between the `curl -o` write and the subsequent `cat`. In the single-machine dev workflow this repo uses, concurrent execution is unlikely. No correctness defect in the expected usage. The implementer may optionally use `$(mktemp)` for robustness, but this does not block approval.

---

## B-1 scope-narrowing evaluation (item 2 of the reviewer brief)

The implementer kept the `dagster)` layer's existing boundary grep unchanged (usage-based pattern: `httpx.(get|post|AsyncClient)` on same line as `dagster`). The new `runs)` V2 grep uses import-form detection (`import httpx|from httpx import`) against the whole `dataplat_api/` tree excluding the gateway module.

**Assessment: ACCEPTABLE as INFO.** The two greps serve genuinely different purposes:
- `dagster)` grep catches calls that target Dagster by URL co-occurrence — it is a usage-based check that would catch a developer who imports httpx elsewhere for another purpose but then calls Dagster.
- `runs)` V2 grep catches any httpx import outside the gateway module — it is a presence-based check that is stricter on imports but would miss a hypothetical case where httpx is aliased.

Neither is comprehensive. Together they cover the most common violation patterns. The implementer's rationale that tightening the `dagster)` layer would touch S004-F-004 artifacts is valid scope discipline. The INFO note in §5.2 documents the intent to harmonize in a future revision. Marking B-1 as RESOLVED — INFO.

---

## Contract promotion notes (risks for the implementer)

1. **OQ-1 and OQ-2 are hard gates before commit.** The introspection queries must be run against the live Dagster 1.11.16 instance with `hello_world_job` registered in `definitions.py` first. Wrong `repositoryLocationName` or wrong GraphQL field name will cause a runtime 503, not a startup error — it will only surface during the `runs)` verification layer.

2. **OQ-2 fragment type name.** If introspection confirms `runOrError`, the not-found type fragment `PipelineRunNotFoundError` in `§4.2` must be replaced with `RunNotFoundError` in both `gateway.py` and the candidate query string. This is an agreed.md addendum obligation, not a silent code change.

3. **`DagsterRunNotFoundError` must appear in `dagster/__init__.py` re-export** (by analogy with `DagsterGatewayError` already exported there per S004-F-004 §2). The proposed.md §3 files table does not mention updating `dagster/__init__.py`. The implementer should add it to the re-export list in that file, else `from dataplat_api.dagster import DagsterRunNotFoundError` in `routers/runs.py` will fail with an ImportError. This is a minor gap in the files table (the file is not listed as modified), but it is inferred from the existing S004 pattern. Low risk — the implementer will catch it at import time if missed.

