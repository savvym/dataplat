# Sprint Contract S005-F-005 — Hello-World Dagster Job + GET /api/runs/{run_id}

**Status:** PROPOSED (Iter 2 — addressing Mode A CHANGES_REQUESTED)  
**Date drafted:** 2026-05-22  
**Last revised:** 2026-05-22 (Iter 2)  
**Author:** Leader (Claude)  
**Depends on:** S004-F-004 (passes: true)

---

## 1. Goal

F-005 proves that the Dagster orchestration layer can execute real work and that the FastAPI layer can observe that work through the DagsterGateway abstraction established in F-004. The sprint delivers:

1. A `hello_world_job` (plain `@op` + `@job`, not a partitioned asset) registered in `dagster_platform.definitions`.
2. Two new `DagsterGateway` methods: `launch_hello_world() -> str` (returns a `dagster_run_id`) and `get_run_status(run_id: str) -> dict` (returns `{"status": "<lowercase>", "dagster_run_id": str}`).
3. Two new FastAPI routes: `POST /api/admin/runs/hello-world` (trigger) and `GET /api/runs/{run_id}` (poll).
4. A `runs)` layer in `verify/checks.sh` that launches the job and polls until status `"success"` within 60 seconds.

### 1.1 Non-goals (explicit deferrals — see §8)

- Partitioned assets, dynamic partition management, or any asset from the design doc §5 asset graph.
- Inserting a row into the business `run` Postgres table (that requires auth + source context from F-007/F-008/F-009).
- WebSocket run notifications (§9.3 — deferred to F-051).
- A generic `POST /api/runs` surface (design doc §9.1 — deferred to F-018).
- `GET /api/runs` (list) and `GET /api/runs/{id}/logs` (deferred to F-049 and beyond).
- JWT authentication on the new routes (deferred to F-008).

---

## 2. Architecture Decisions

### 2.1 Trigger surface: `POST /api/admin/runs/hello-world`

**Decision:** The verifier triggers the job by calling `POST /api/admin/runs/hello-world` (option a from the spec). This route calls `gateway.launch_hello_world()` internally and returns `{"dagster_run_id": "<dagster_run_id>"}` with HTTP **201 Created**.

**Rationale:** This mirrors the design doc §9.1 route surface (`POST /api/runs` is listed as the generic trigger; the admin variant follows the pattern already established by `POST → /api/admin/dagster-status` in F-004). It exercises the full FastAPI → DagsterGateway → Dagster GraphQL stack end-to-end and gives the `checks.sh runs)` layer a clean HTTP-level entry point. A CLI entry point (option b) or a direct gateway call from pytest (option c) would skip the FastAPI layer and leave the route untested. The route lives under `/api/admin/` rather than `/api/runs/` because the generic `POST /api/runs` surface is reserved for F-018 (which requires auth and source_ids); hello-world is an admin smoke test, not a user-facing trigger.

**HTTP status code: 201 Created.** Each POST creates a new Dagster run resource (the run immediately exists in Dagster's queue). 201 is the correct REST semantic. F-018 will return 202 Accepted (async backfill). F-005's synchronously-queued run uses 201. The route decorator carries `status_code=201`.

**Field naming:** `dagster_run_id` is the canonical field name in both the trigger response and the status response, matching design doc §4.1 (`dagster_run_id TEXT UNIQUE NOT NULL`) and the `RunStatusResponse` field. F-018 will return `{"dagster_run_id": "<uuid>", "run_id": <int>}` (both the Dagster UUID and the Postgres integer `run_id`). F-005's response is a strict subset — it omits the Postgres `run_id` because F-005 does not write to the `run` table.

### 2.2 Status mapping from Dagster `RunStatus` enum

The `get_run_status()` gateway method maps Dagster's `RunStatus` enum values to a three-value internal set:

| Dagster RunStatus values | Mapped to |
|---|---|
| `SUCCESS` | `"success"` |
| `FAILURE`, `CANCELED` | `"failure"` |
| `QUEUED`, `NOT_STARTED`, `STARTING`, `STARTED`, `MANAGED` | `"running"` |
| `CANCELING` | `"running"` |

**Rationale:** The verification spec requires only that `"success"` is eventually returned. However, the poll loop in `checks.sh` must distinguish terminal failure (`"failure"`) from still-in-progress (`"running"`) to avoid polling indefinitely after a crash. Three values are sufficient and cleanly representable as a `Literal` in the Pydantic response schema. Any `RunStatus` value not in the mapping table above is treated as `"running"` with a logged warning — this handles future Dagster enum additions gracefully.

### 2.3 Auth posture

**Decision:** Both new routes are unauthenticated this sprint. They carry `# TODO(F-008): require admin role` inline comments.

**Rationale:** F-007/F-008 (JWT) has not shipped. The pattern is identical to `GET /api/admin/dagster-status` from F-004, which is also unauthenticated. Both new routes sit under `/api/admin/`, signalling their operational-rather-than-user-facing nature. When F-008 ships, it will add a single JWT dependency to the entire `/api/admin/` prefix in `admin.py` or `main.py`, retroactively securing all admin routes including these. No special precautions are needed in this sprint beyond the inline TODO.

### 2.4 Worker pickup and executor

**Decision:** The hello-world job uses Dagster's default in-process executor. No worker tags or custom `ExecutorDefinition` are specified.

**Rationale:** The compose stack has `dagster-daemon` and `dagster-worker-cpu`/`dagster-worker-heavy` services, but which executor actually runs the job depends on whether the code location is configured to use the daemon's queue-based executor or the in-process executor. For a code location loaded via `python_module` in `workspace.yaml`, Dagster 1.11.x defaults to the `multiprocess_executor` when launched via `launchRun` (the mutation used here) — the daemon picks it up from the run queue and dispatches to a worker process on the same host. For a no-op job like hello-world, this completes in under 5 seconds even with queue latency. The 60-second poll window in the verification is generous enough to accommodate either in-process or daemon/worker dispatch. The implementer MUST confirm actual executor behaviour against the running instance and document it in the agreed.md addendum if it differs from this assumption.

### 2.5 Polling vs synchronous response

**Decision:** `GET /api/runs/{run_id}` returns the current Dagster run status at the moment of the request — it does NOT block, long-poll, or subscribe. The verifier polls this endpoint in a loop with a 1-second sleep and a 60-second total timeout.

**Rationale:** Long-polling or blocking until terminal status would require server-side run tracking in Postgres (deferred to F-018/F-050). The simple poll pattern is sufficient for the smoke test and directly matches the verification spec wording ("polling GET /api/runs/{run_id}"). The gateway's `get_run_status()` call uses the same 10-second httpx timeout as `get_dagster_version()`, which is safe for a GraphQL query that returns synchronously.

---

## 3. Files to Change / Create

| File | Action | Purpose |
|---|---|---|
| `dagster/dagster_platform/definitions.py` | modify | Add `@op hello_op` (logs "hello world") + `@job hello_world_job` + register in `Definitions(jobs=[hello_world_job])` |
| `apps/api/dataplat_api/dagster/gateway.py` | modify | Add `launch_hello_world() -> str` and `get_run_status(run_id: str) -> dict`; add `DagsterRunNotFoundError(DagsterGatewayError)` subclass; full `DagsterGatewayError` discipline |
| `apps/api/dataplat_api/routers/runs.py` | create | Module docstring cites "S005-F-005", references F-018 (generic `POST /api/runs`) and F-008 (admin auth) as deferral sprints. Defines **two** `APIRouter` instances: `admin_runs_router = APIRouter(prefix="/api/admin/runs", tags=["admin", "runs"])` hosting `POST /hello-world` (status_code=201), and `runs_router = APIRouter(prefix="/api/runs", tags=["runs"])` hosting `GET /{run_id}`. Route handlers catch `DagsterRunNotFoundError → 404` before the catch-all `DagsterGatewayError → 503`. |
| `apps/api/dataplat_api/schemas/runs.py` | create | `LaunchHelloWorldResponse(dagster_run_id: str)` and `RunStatusResponse(dagster_run_id: str, status: Literal["running","success","failure"])` Pydantic models |
| `apps/api/dataplat_api/main.py` | modify | Two `include_router` calls: `app.include_router(admin_runs_router)` and `app.include_router(runs_router)` — both imported from `routers/runs.py`; no lifespan changes needed (gateway already on `app.state`) |
| `apps/api/tests/test_runs_hello_world.py` | create | pytest cases that mock `DagsterGateway` methods at the method level (not HTTP transport level — the existing `conftest.py` autouse MockTransport from S004-F-004 applies unchanged; mocking at the gateway-method level is correct and compatible with it). Required test functions: (1) `test_launch_hello_world_201` — gateway returns UUID → 201 + body has `dagster_run_id`; (2) `test_launch_hello_world_503_on_gateway_error` — gateway raises `DagsterGatewayError` → 503; (3) `test_get_run_status_200_success` — gateway returns `{"status": "success", "dagster_run_id": "..."}` → 200 + body matches schema; (4) `test_get_run_status_404_when_not_found` — gateway raises `DagsterRunNotFoundError` → 404; (5) `test_get_run_status_503_on_gateway_error` — gateway raises `DagsterGatewayError` → 503. Note: future test authors who need real HTTP must explicitly pass `transport=` to bypass the autouse mock. |
| `verify/checks.sh` | modify | Add `runs)` layer (V1: launch + poll until success or 60s; V2: gateway boundary grep extension); add `bash "$0" runs` inside the existing `all)` block BEFORE its closing `;;` (i.e., after `bash "$0" dagster`) |

**Total: 7 files** (5 create/modify in `apps/api/`, 1 in `dagster/`, 1 in `verify/`).

**No migration required.** F-005 is API + orchestration only. No Postgres schema changes. No alembic file.

**No `make codegen` required this sprint.** `packages/api-types/` does not yet exist; the `contract)` layer in `checks.sh` already exits 0 when that directory is absent (line 82). This is an acknowledged carry-over from S004-F-004 CAL-3. The first web-facing sprint that needs TypeScript types must establish `make codegen` before merging.

---

## 4. GraphQL Queries (Verbatim Strings)

The implementer MUST confirm both queries against `http://dagster-webserver:8000/graphql` introspection on the running Dagster 1.11.16 instance before committing. If either query requires adjustment, the change is documented as an addendum to `agreed.md`, not made silently.

### 4.1 `LAUNCH_HELLO_WORLD_MUTATION`

```graphql
mutation LaunchHelloWorld(
  $repositoryLocationName: String!,
  $repositoryName: String!,
  $jobName: String!
) {
  launchRun(
    executionParams: {
      selector: {
        repositoryLocationName: $repositoryLocationName,
        repositoryName: $repositoryName,
        jobName: $jobName
      }
      runConfigData: {}
    }
  ) {
    __typename
    ... on LaunchRunSuccess {
      run {
        runId
      }
    }
    ... on PythonError {
      message
    }
    ... on InvalidSubsetError {
      message
    }
    ... on RunConflict {
      message
    }
  }
}
```

**Variables at call time:**

```python
{
    "repositoryLocationName": "dagster_platform.definitions",  # OQ-1: verify
    "repositoryName": "__repository__",                        # OQ-1: verify
    "jobName": "hello_world_job"
}
```

**Success path:** `data.launchRun.__typename == "LaunchRunSuccess"` — extract `data.launchRun.run.runId`.

**Error path:** any other `__typename` value, or `errors` key present, or non-2xx HTTP response — raise `DagsterGatewayError` with the message extracted from the response body.

### 4.2 `GET_RUN_STATUS_QUERY`

The query field name `pipelineRunOrError` was the legacy name in Dagster < 1.0. Dagster 1.x may have renamed it to `runOrError`. The implementer MUST confirm the correct field name via GraphQL introspection before commit (OQ-2). The not-found type name (`PipelineRunNotFoundError` vs `RunNotFoundError`) must also be confirmed — if the field is `runOrError`, the not-found type is likely `RunNotFoundError`. The confirmed field name AND the confirmed not-found type name must both be added to `agreed.md` as a numbered addendum BEFORE the implementer commits.

**Introspection command to resolve OQ-2:**
```bash
curl -X POST http://localhost:13000/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"{__schema{queryType{fields{name}}}}"}' | jq
```

**Candidate query (using `pipelineRunOrError` — verify or replace with `runOrError`):**

```graphql
query GetRunStatus($runId: ID!) {
  pipelineRunOrError(runId: $runId) {
    __typename
    ... on Run {
      id
      status
    }
    ... on PipelineRunNotFoundError {
      message
    }
    ... on PythonError {
      message
    }
  }
}
```

**Variables at call time:** `{"runId": "<dagster_run_id>"}`

**Success path:** `data.pipelineRunOrError.__typename == "Run"` — extract `data.pipelineRunOrError.status` (a `RunStatus` enum string) and apply the mapping table from §2.2.

**Not-found path:** `__typename == "PipelineRunNotFoundError"` (or `"RunNotFoundError"` — confirm via OQ-2) — raise `DagsterRunNotFoundError("run not found: <runId>")`. The route catches `DagsterRunNotFoundError` BEFORE the catch-all `DagsterGatewayError` and returns 404.

**Error path:** `__typename == "PythonError"`, or `errors` key present, or non-2xx HTTP response — raise `DagsterGatewayError`. The route returns 503.

**Exception hierarchy and catch ordering (must be explicit):**

`gateway.py` defines:
```python
class DagsterGatewayError(Exception):
    """Base error for all Dagster gateway failures."""

class DagsterRunNotFoundError(DagsterGatewayError):
    """Raised by get_run_status() when Dagster reports the run does not exist.
    Route handlers catch this BEFORE DagsterGatewayError and return 404.
    """
```

Route handler catch order:
```python
except DagsterRunNotFoundError:
    raise HTTPException(status_code=404, detail="run not found")
except DagsterGatewayError as exc:
    raise HTTPException(status_code=503, detail=str(exc))
```

The `launch_hello_world()` route handler only needs `except DagsterGatewayError → 503` (no 404 case for launch).

**Explicit note:** These strings MUST be confirmed against `http://dagster-webserver:8000/graphql` introspection during implementation. If a query needs adjustment, the change goes into `agreed.md` as an addendum, not silently.

---

## 5. Verification

### 5.1 Acceptance Criteria Table

| Criterion | Method | Exact command | Expected output |
|---|---|---|---|
| V1a: POST trigger returns 201 + dagster_run_id | curl from host (2-step: capture body + status) | See §5.2 `runs)` layer V1 block | HTTP 201; body contains `dagster_run_id` with a non-empty UUID string |
| V1b: GET poll reaches success within 60s | curl from host in loop | See §5.2 `runs)` layer poll block | `"success"` (eventually; timeout 60s) |
| V2: gateway boundary intact (both import forms) | grep from repo root | `grep -rln -E '(import httpx\|from httpx import)' apps/api/dataplat_api --include='*.py' \| grep -v dataplat_api/dagster/` | Empty output (no files match); `apps/api/tests/` is outside the grep root `dataplat_api/` so no test-exclusion clause is needed |

**Not-found case (not in feature_list.json verification but required for completeness):**

| Criterion | Method | Exact command | Expected output |
|---|---|---|---|
| V3: GET unknown run_id returns 404 | curl from host | `curl -o /dev/null -w '%{http_code}' http://localhost:18000/api/runs/nonexistent-run-id` | `404` |

### 5.2 `checks.sh runs)` Layer (literal commands)

```bash
runs)
  # F-005: hello-world Dagster job launch + status poll
  COMPOSE="docker/docker-compose.dev.yml"
  [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

  FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

  echo "--- runs V1: trigger hello-world job via FastAPI ---"
  # 2-step pattern: capture body to /tmp/launch_body, write status code to RESP.
  # This ensures non-201 responses print the body for debugging rather than
  # silently failing. curl -sS shows connection errors on stderr without -f
  # suppressing the body.
  RESP=$(curl -sS -X POST "http://localhost:${FASTAPI_HOST_PORT}/api/admin/runs/hello-world" \
    -w '\n%{http_code}' -o /tmp/launch_body)
  STATUS_CODE=$(echo "$RESP" | tail -n1)
  BODY=$(cat /tmp/launch_body)
  test "$STATUS_CODE" = "201" || { echo "FAIL: expected 201 got $STATUS_CODE: $BODY"; exit 1; }
  RUN_ID=$(echo "$BODY" | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_run_id' in body, f'missing dagster_run_id key: {body}'
assert body['dagster_run_id'], f'dagster_run_id is empty: {body}'
print(body['dagster_run_id'], end='')
")
  test -n "$RUN_ID" || { echo "FAIL: no dagster_run_id returned from trigger"; exit 1; }
  echo "  triggered run: $RUN_ID"

  echo "--- runs V1: poll GET /api/runs/{run_id} until success or timeout ---"
  STATUS="unknown"
  for i in $(seq 1 60); do
    RESP=$(curl -sS "http://localhost:${FASTAPI_HOST_PORT}/api/runs/${RUN_ID}" \
      -w '\n%{http_code}' -o /tmp/status_body)
    STATUS_CODE=$(echo "$RESP" | tail -n1)
    BODY=$(cat /tmp/status_body)
    test "$STATUS_CODE" = "200" || { echo "GET /api/runs/$RUN_ID -> $STATUS_CODE: $BODY"; exit 1; }
    STATUS=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'), end='')")
    [ "$STATUS" = "success" ] && break
    [ "$STATUS" = "failure" ] && { echo "FAIL: hello-world run reached failure status: $BODY"; exit 1; }
    sleep 1
  done
  test "$STATUS" = "success" || { echo "FAIL: timeout waiting for success (last status=$STATUS)"; exit 1; }
  echo "  V1 OK: hello-world run reached success in ~${i}s"

  echo "--- runs V2: gateway boundary — no raw httpx import outside gateway module ---"
  # Covers both 'import httpx' and 'from httpx import ...' import forms.
  # apps/api/tests/ is outside the grep root (dataplat_api/) so no test-exclusion
  # clause is needed.
  # NOTE: This same stronger pattern should also be applied to the existing
  # dagster) layer's boundary grep when that layer is next revised — keeping
  # the boundary check uniform across both layers (INFO, not a blocker for F-005).
  RAW_HTTPX=$(grep -rln -E '(import httpx|from httpx import)' \
    apps/api/dataplat_api --include='*.py' \
    | grep -v 'dataplat_api/dagster/' \
    || true)
  test -z "$RAW_HTTPX" || { echo "FAIL: raw httpx import outside gateway: $RAW_HTTPX"; exit 1; }
  echo "  V2 OK: gateway boundary intact"
  ;;
```

The `all)` block must include `runs` after `dagster`. Insert `bash "$0" runs` BEFORE the closing `;;` of the `all)` block (not after it — a copy-paste error adding it after `;;` would silently skip the `runs` layer):

```bash
  all)
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" buckets
    bash "$0" dagster
    bash "$0" runs
    ;;
```

---

## 6. Risks and Open Questions

**OQ-1 — code location name and repository name in `launchRun` selector (OPEN — must resolve before commit).**
The `repositoryLocationName` and `repositoryName` fields in the `launchRun` `selector` depend on how Dagster 1.11.16 registers the code location loaded from `python_module: dagster_platform.definitions` in `workspace.yaml`. Common patterns:
- `repositoryLocationName = "dagster_platform.definitions"`, `repositoryName = "__repository__"` (most common for single-repo code locations).
- `repositoryLocationName = "dagster_platform"`, `repositoryName = "__repository__"` (if the location name strips `.definitions`).

The implementer MUST query the running Dagster instance via `{ repositoriesOrError { ... on RepositoryConnection { nodes { name location { name } } } } }` before writing the mutation variables. If the field values differ from those listed above, the agreed.md must carry an addendum with the confirmed values. A wrong value causes `launchRun` to return `{errors: [{message: "..."}]}`, which the gateway maps to a clean `DagsterGatewayError` (503 from the route) — visible as a test failure, not silent corruption.

**OQ-2 — GraphQL field name: `pipelineRunOrError` vs `runOrError` in Dagster 1.11.16 (OPEN — MUST be resolved and the answer added to `agreed.md` §4 as a numbered addendum BEFORE the implementer commits).**
Dagster renamed the status query field at some point during the 1.x series. The implementer must confirm via introspection (`{ __schema { queryType { fields { name } } } }`) which field name exists in the running 1.11.16 instance. The candidate query in §4.2 uses `pipelineRunOrError`; if the actual field is `runOrError`, that name must be used instead. The confirmed field name AND the confirmed not-found type (`PipelineRunNotFoundError` vs `RunNotFoundError` — the latter is likely if the field is `runOrError`) must both be pinned in `agreed.md` as an addendum. The implementer can introspect against the running Dagster instance:
```bash
curl -X POST http://localhost:13000/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"{__schema{queryType{fields{name}}}}"}' | jq
```

**OQ-3 — worker pickup behaviour: in-process vs daemon queue (OPEN — informational).**
The hello-world job has no op tags restricting it to a specific worker. Whether it runs in-process within `dagster-webserver` or is dispatched to `dagster-daemon` → `dagster-worker-cpu` depends on the executor configured for the code location. The 60-second poll window accommodates either path. The implementer should note in the agreed.md addendum which executor was observed during testing (check the Dagster UI run detail page or the webserver logs for `Executing with the executor: ...`).

**OQ-4 — idempotency of `POST /api/admin/runs/hello-world` (acknowledged, by design).**
Every POST creates a new Dagster run. There is no dedup or idempotency key. This is intentional for a smoke job: the endpoint is an admin-only trigger with no user-visible side effects. Documentation in the route docstring is sufficient.

**OQ-5 — `GET /api/runs` (list) is explicitly deferred.**
The design doc §9.1 lists `GET /api/runs?status=running` as part of the `/api/runs/` surface. F-005 only implements `GET /api/runs/{run_id}` (single-run status). The list endpoint is deferred to F-049, which depends on F-048, which depends on F-018 (the full business run table). Reviewer should not flag the absence of `GET /api/runs` as a gap — it is an explicit deferral.

**OQ-6 — `runs.py` router prefix (RESOLVED — dual-router pattern).**
`routers/runs.py` defines **two** `APIRouter` instances:
- `admin_runs_router = APIRouter(prefix="/api/admin/runs", tags=["admin", "runs"])` — hosts `POST /hello-world` (full path: `POST /api/admin/runs/hello-world`).
- `runs_router = APIRouter(prefix="/api/runs", tags=["runs"])` — hosts `GET /{run_id}` (full path: `GET /api/runs/{run_id}`).

`main.py` does **two** `include_router` calls:
```python
app.include_router(admin_runs_router)
app.include_router(runs_router)
```
Both routers are imported from `routers.runs`. This is the binding pattern; the implementer must not use a single-router approach.

---

## 7. Migration / Data Model Impact

**None.** F-005 is API + orchestration only. No Postgres schema changes. No alembic migration file. The `run` business table (§4.1 of the design doc) is NOT written to this sprint — the `GET /api/runs/{run_id}` route queries Dagster directly via the gateway, not the local `run` table. This is a deliberate simplification: the local `run` table write requires a `triggered_by` user reference (and therefore JWT auth from F-007/F-008), which is out of scope.

---

## 8. Out-of-Scope (Explicit Deferrals)

The following are explicitly NOT part of this sprint. Reviewer should not flag their absence.

- **Persisting Dagster runs to the `run` Postgres table.** Requires auth context (`triggered_by` FK) from F-007/F-008 and a more complete gateway method (see F-009/F-018 sprint series).
- **JWT / auth on the new routes.** `POST /api/admin/runs/hello-world` and `GET /api/runs/{run_id}` are unauthenticated. F-008 adds auth middleware.
- **WebSocket notifications on run status.** Design doc §9.3 describes `run.status_changed` events; that is F-051.
- **Generic `POST /api/runs` surface.** Design doc §9.1 lists this as the user-facing trigger; F-005 uses a narrower admin route for the smoke test. F-018 implements the generic trigger once source_ids and auth are available. When F-018 ships, its response will be `{"dagster_run_id": "<uuid>", "run_id": <int>}` — the Dagster UUID plus the Postgres integer run_id. F-005's `POST /api/admin/runs/hello-world` response is a strict subset (`{"dagster_run_id": "<uuid>"}` only) because F-005 does not write to the `run` table.
- **Run logs proxy (`GET /api/runs/{id}/logs`).** Not in F-005 verification spec; deferred beyond F-049.
- **TS type sync via `make codegen`.** `packages/api-types/` still does not exist. Acknowledged carry-over from S004-F-004; the `contract)` layer in `checks.sh` already handles the absence gracefully.
- **The full `/api/runs/` route group.** `GET /api/runs` (list, paginated, filterable by status) is F-049; it depends on the `run` table being populated, which depends on F-018.

---

## 9. Hard Invariant Alignment

| # | Invariant | Status in this sprint |
|---|---|---|
| 1 | Lineage is mandatory (Commit must record parents + processor identity) | IRRELEVANT — no Commit objects or Repository writes |
| 2 | Storage separation + CAS (metadata in Postgres; content in MinIO by sha256) | IRRELEVANT — no blob storage |
| 3 | Schema frozen post-publish | IRRELEVANT — no Silver/Gold schema |
| 4 | LLM calls through gateway | IRRELEVANT — no LLM calls; this sprint EXTENDS the Dagster gateway analogue established in F-004 |
| 5 | Async SQLAlchemy from day one | SATISFIED — no new DB session code; gateway uses async httpx only |
| 6 | OpenAPI ↔ TS type sync | DEFERRED (same acknowledgement as S004-F-004) — `packages/api-types/` does not exist; `contract)` layer in `checks.sh` exits 0 gracefully |

---

## 10. Files Summary

```
dagster/
  dagster_platform/
    definitions.py          (modify — add @op hello_op + @job hello_world_job + Definitions(jobs=[...]))

apps/api/
  dataplat_api/
    dagster/
      gateway.py            (modify — add launch_hello_world(), get_run_status();
                             add DagsterRunNotFoundError(DagsterGatewayError) subclass)
    routers/
      runs.py               (create — module docstring cites S005-F-005, F-018, F-008;
                             two APIRouter instances: admin_runs_router + runs_router)
    schemas/
      runs.py               (create — LaunchHelloWorldResponse(dagster_run_id: str),
                             RunStatusResponse(dagster_run_id: str, status: Literal[...]))
    main.py                 (modify — two include_router calls: admin_runs_router + runs_router)
  tests/
    test_runs_hello_world.py (create — 5 named test functions; mocks at gateway-method level;
                             existing conftest.py autouse MockTransport applies unchanged)

verify/
  checks.sh                 (modify — add runs) layer; insert bash "$0" runs before ;; in all))
```

Total: **7 files** (2 modify existing, 5 create new). No alembic migration. No `make codegen`.
