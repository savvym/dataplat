# Mode B review — S005-F-005

VERDICT: **APPROVED**

## Summary

The implementation faithfully delivers all 7 files specified in `agreed.md` (addenda included). All contract criteria are met with concrete evidence. The three implementer deviations are each acceptable: the `RunNotFoundError` correction is a mandatory fix confirmed by introspection; the `run()` subshell change is a clean, non-regressive bugfix; the Dagster rebuild operational note is documentation-only. No hard invariant violations, no scope creep, no correctness gaps.

---

## Calibration checks (from `verify/reviewer-calibration.md`)

- **CAL-1: PASS** — No DB session code exists in this sprint. `gateway.py` is async httpx only. No `session.query`, no `.commit()`, no sync sessions anywhere in the diff. Verified at `apps/api/dataplat_api/dagster/gateway.py` (entire new section) and `apps/api/dataplat_api/routers/runs.py`.

- **CAL-2: PASS** — No LLM SDK imports anywhere in the diff. Confirmed by reading all 5 modified/created Python files. Not applicable (no LLM logic this sprint).

- **CAL-3: PASS (N/A — acknowledged carry-over)** — `packages/api-types/` does not exist. New routes and schemas are added, but `agreed.md §3` explicitly acknowledges the `make codegen` deferral and `checks.sh contract)` layer exits 0 gracefully when the package is absent. This is the same acknowledged carry-over as S004-F-004. Not a new gap introduced by this sprint.

- **CAL-4: PASS (N/A)** — No Commit objects created this sprint. Confirmed in `agreed.md §7` and no ORM writes anywhere in the diff.

- **CAL-5: PASS (N/A)** — No blob storage paths. No MinIO operations.

- **CAL-6: PASS (N/A)** — No Silver/Gold schema modifications.

- **CAL-7: PASS (N/A)** — No Bronze adapter code.

- **CAL-8: PASS** — No JWT, no `run` Postgres table write, no WebSocket, no generic `POST /api/runs`, no log-proxy route, no Celery, no Docker-in-Docker. All correctly deferred with inline `TODO(F-XXX)` comments referencing the correct future sprint IDs (`runs.py` lines 7, 10, 67, 104).

- **CAL-9: PASS (N/A)** — No plugin code modified.

- **CAL-10: PASS** — 5 named test functions covering: happy path (201 + 200-success), and multiple failure modes (503-on-gateway-error for both routes, 404-when-not-found for the GET route). All 5 confirm `response.status_code` assertions. Verified at `apps/api/tests/test_runs_hello_world.py`.

- **CAL-11: Applied** — This review includes `file:line`-specific evidence for each criterion below. No vague approvals.

---

## Contract criteria

**Criterion: `hello_world_job` in `definitions.py`**
PASS — `dagster/dagster_platform/definitions.py`: `@op hello_op` with `context.log.info("hello world")` and `@job hello_world_job` calling `hello_op()` both present. `Definitions(jobs=[hello_world_job])` registered. No regression to the prior empty-Definitions guarantee (`defs = Definitions()` replaced, not appended — correct, as an empty Definitions is now superseded). Verified in diff lines +4 to +30.

**Criterion: `launch_hello_world() -> str` method**
PASS — `gateway.py` (post-diff). Sends `_LAUNCH_HELLO_WORLD_MUTATION` with confirmed selector variables. Error hierarchy covers: `TimeoutException`, `ConnectError`, `HTTPError`, non-2xx HTTP, non-JSON body, GraphQL `errors` key, non-`LaunchRunSuccess` typename, absent/empty `runId`. Returns `run_id: str`. No network error path leaks raw exceptions — all converted to `DagsterGatewayError`.

**Criterion: `get_run_status(run_id: str) -> dict` method**
PASS — `gateway.py` (post-diff). Sends `_GET_RUN_STATUS_QUERY` using confirmed `pipelineRunOrError`. `RunNotFoundError` typename raises `DagsterRunNotFoundError`. `PythonError` raises `DagsterGatewayError`. Unexpected typename raises `DagsterGatewayError`. Status mapping per §2.2: `SUCCESS` → `"success"`, `{FAILURE, CANCELED}` → `"failure"`, all others (including `CANCELING` and unknown future values) → `"running"` with `logger.warning(...)`. Returns `{"dagster_run_id": run_id, "status": mapped_status}`.

**Criterion: GraphQL strings correct (scrutiny point 1)**
PASS — `_LAUNCH_HELLO_WORLD_MUTATION` has `launchRun` with `executionParams.selector{repositoryLocationName, repositoryName, jobName}`, `runConfigData: {}`, and fragments `LaunchRunSuccess { run { runId } }`, `PythonError { message }`, `InvalidSubsetError { message }`, `RunConflict { message }`. No `InvalidStepError` — contract §4.1 does not require it. `_GET_RUN_STATUS_QUERY` uses `pipelineRunOrError(runId: $runId)` with `... on Run { id status }`, `... on RunNotFoundError { message }`, `... on PythonError { message }`. No `... on PipelineRunNotFoundError` — correctly replaced per Addendum 2. Confirmed in diff.

**Criterion: `DagsterRunNotFoundError` exception hierarchy**
PASS — Defined in `gateway.py` as `class DagsterRunNotFoundError(DagsterGatewayError)`. Route `get_run_status` handler at `routers/runs.py:109` catches `DagsterRunNotFoundError` on the first except clause, `DagsterGatewayError` on the second — subclass-first ordering is correct. Launch route at `routers/runs.py:72` only catches `DagsterGatewayError` (no 404 case for launch — correct per agreed.md §4.2).

**Criterion: Route shapes (scrutiny point 4)**
PASS — `admin_runs_router.post("/hello-world", ..., status_code=201)` at `runs.py:45-56`. `runs_router.get("/{run_id}", ...)` at `runs.py:79-90`. Both prefixes: `/api/admin/runs` and `/api/runs`. Both tags correct: `["admin","runs"]` and `["runs"]`. `main.py` imports both and calls `app.include_router(admin_runs_router)` and `app.include_router(runs_router)` — two separate calls confirmed.

**Criterion: `__init__.py` re-exports (scrutiny point 5)**
PASS — `dataplat_api/dagster/__init__.py` re-exports all three: `DagsterGateway`, `DagsterGatewayError`, `DagsterRunNotFoundError` in both the import statement and `__all__`. Tests import directly from `dataplat_api.dagster.gateway` (the module path), and `routers/runs.py` also imports from the module path — both work without issue.

**Criterion: Tests (scrutiny point 6)**
PASS — All 5 named cases present: `test_launch_hello_world_201`, `test_launch_hello_world_503_on_gateway_error`, `test_get_run_status_200_success`, `test_get_run_status_404_when_not_found`, `test_get_run_status_503_on_gateway_error`. Mocks use `patch.object(app.state.dagster_gateway, ...)` with `AsyncMock` — gateway-method level, not transport. The 404 test asserts `response.status_code == 404` at `test_runs_hello_world.py:103` and also asserts `body["detail"] == "run not found"`. The 201 test asserts both `status_code == 201` and `body["dagster_run_id"] == fake_run_id`.

**Criterion: `checks.sh runs)` layer (scrutiny point 7)**
PASS — Uses `$(mktemp)` for both `LAUNCH_BODY` and `STATUS_BODY` (Mode A LOW concern addressed). V2 grep uses `-E '(import httpx|from httpx import)'` excluding `dataplat_api/dagster/`. 60s poll loop via `seq 1 60`. `bash "$0" runs` appears at `checks.sh:300` inside `all)` block BEFORE closing `;;` (at line 301). Cleanup `rm -f` on both temp files (including on early-exit failure paths). Verified at `verify/checks.sh:243-291`.

**Criterion: Addenda discipline (scrutiny point 10)**
PASS — `agreed.md` was a new file in this commit (diff shows `--- /dev/null`), meaning the entire file including addenda was committed together. The file ends with Addenda section covering OQ-1, OQ-2, OQ-3 — all three open questions documented. Since `agreed.md` is new in this commit, there is no risk of silent rewriting of prior content.

**Criterion: Hard invariant #4 — LLM calls through gateway**
PASS — No LLM calls anywhere. V2 grep in `checks.sh` additionally validates that no raw httpx imports exist outside `dataplat_api/dagster/`. The gateway pattern is extended (not bypassed) by this sprint.

**Criterion: Hard invariant #5 — Async SQLAlchemy**
PASS (N/A) — No DB sessions. All new code is async-httpx only.

**Criterion: Hard invariant #6 — OpenAPI ↔ TS sync**
PASS (acknowledged carry-over) — `packages/api-types/` absent, `contract)` layer exits 0, no regression. Same status as S004-F-004.

---

## Deviation rulings

1. **`RunNotFoundError` vs `PipelineRunNotFoundError`**: ACCEPTED. This is a mandatory correction, not a deviation from the contract's intent. Addendum 2 explicitly documents the introspection result showing `PipelineRunNotFoundError` is never returned by `pipelineRunOrError` for missing runs. The candidate query in §4.2 had the wrong type; the fix is correct. No `... on PipelineRunNotFoundError` remains in the production GraphQL string.

2. **`checks.sh run()` subshell fix**: ACCEPTED. The change from `eval "$*"` to `( eval "$*" )` is a clean, safe bugfix. Every `run()` call that `cd`s uses a compound command (`cd X && do-thing`) so the `cd` and the work are already in the same `eval`'d string. No layer in `checks.sh` passes a bare `cd X` as its entire `run()` call and then relies on the next `run()` to inherit that CWD — confirmed by reading all 6 `run "cd ..."` occurrences at lines 63, 70, 71, 72, 120, 121. Each is self-contained. The subshell isolation has zero behavioral impact on existing layers and fixes the `all)` chain CWD pollution correctly.

3. **Dagster image rebuild requirement (Addendum 1 operational note)**: ACCEPTED. This is documentation of an operational constraint imposed by the Dockerfile architecture (`COPY dagster/ /app/dagster/` with no bind-mount). No code change required; the note correctly alerts operators that `docker compose restart` is insufficient after `definitions.py` changes.

---

## Additional findings

**INFO-1 — `JSONResponse` pattern vs `raise HTTPException` in route handlers** (`routers/runs.py:73, 111, 115`): The agreed.md pseudocode at §4.2 shows `raise HTTPException(status_code=404, ...)`, but the implementation uses `return JSONResponse(status_code=404, ...)`. Both produce identical HTTP responses. The `return JSONResponse(...)` pattern is the established precedent from `admin.py` (F-004 sprint) at line 36, which already uses the same pattern with the same `# type: ignore[return-value]` comment. This is not a deviation — it is consistent with the codebase pattern. Not blocking.

**INFO-2 — Pyright LSP noise (carried from implementer's declaration)**: `dagster/dagster_platform/definitions.py:25` — `[Line 25:5] Argument missing for parameter context` is the standard Pyright false positive on `@op`-decorated functions (Dagster injects `context` via decorator machinery). Not a runtime defect. `pytest` passes. Not blocking.

**INFO-3 — `dagster)` boundary grep not upgraded to match `runs)` V2 pattern** (`verify/checks.sh:196-205`): The existing `dagster)` layer boundary grep checks for `httpx.(get|post|AsyncClient).*dagster` which is weaker than the `runs)` V2 grep pattern (`import httpx|from httpx import`). This gap was noted in the contract as an INFO item and acknowledged in the `checks.sh` comment at lines 283-284. Not a F-005 gap; carries forward to a future layer revision. Not blocking.

---

## Verdict summary

- BLOCKER: 0
- HIGH: 0
- MEDIUM: 0
- LOW: 0
- NIT: 0
- INFO: 3 (all pre-acknowledged or non-blocking)

Most important finding: none blocking. The closest scrutiny point was verifying that the `agreed.md` specified `raise HTTPException` but implementation uses `return JSONResponse` — resolved as an established codebase pattern, not a deviation.

Confidence: HIGH. All 12 scrutiny points from the task were checked against the actual diff with file:line evidence.

**APPROVED** — diff faithfully implements `agreed.md` including all addenda; all contract criteria verified with concrete evidence; 3 deviations are each acceptable; no hard invariant violations; no scope creep; 5/5 test cases present and correctly asserting status codes and response bodies.
