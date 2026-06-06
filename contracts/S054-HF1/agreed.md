# S054-HF1 — hotfix Dagster code-location loading

**Type:** hotfix (out-of-cycle; not driven by spec/feature_list.json — fixes regressions in F-050 & F-052 that the existing verifier could not detect because the Dagster code repo is not exercised by `apps/api/` unit tests).

**Approved by:** human via `/start-sprint F-054` interrupt at 2026-06-06T00:30+08:00.

## 1. Problem

The Dagster code location at `dagster/dagster_platform/definitions.py` has been failing to load since 2026-06-05. `GET /graphql {repositoriesOrError {nodes {name}}}` returns `[]`. Two cascading bugs:

### Bug A (latent since F-050, commit 7c24743):

```python
@run_status_sensor(
    run_status_list=[                    # ← Dagster 1.11.16 takes singular `run_status=`
        DagsterRunStatus.STARTED,
        DagsterRunStatus.SUCCESS,
        DagsterRunStatus.FAILURE,
        DagsterRunStatus.CANCELED,
    ],
    ...
)
```

`TypeError: run_status_sensor() got an unexpected keyword argument 'run_status_list'`

The agreed.md for F-050 was written from an outdated (pre-1.0) Dagster API reference. The installed signature (verified via `inspect.signature`) is `(run_status: DagsterRunStatus, ...)` — **one status per decorator instance**. Multi-status monitoring requires N separate decorators each delegating to a shared helper.

### Bug B (introduced by F-052, commit 43349fc):

```python
    return SkipReason("notification sent; no run to trigger")
    jobs=[hello_world_job],         # ← `defs = Definitions(` opener was deleted
    assets=[...],
    ...
)
```

`SyntaxError: unmatched ')'` at line 808.

### Why verifier missed both

`apps/api/tests/` — where verifier runs the test suite — POSTs directly to the `/api/dagster/events` route and calls `broker.publish()` directly. None of these tests import `dagster_platform.definitions`. The smoke layer's `/server_info` probe returns 200 from Dagster's admin layer regardless of user-code repo state, and the smoke layer never makes a GraphQL call. So both regressions were genuinely undetectable by the existing harness.

## 2. Fix

### 2a. Re-add the `Definitions(...)` opener

```diff
     return SkipReason("notification sent; no run to trigger")
+
+
+defs = Definitions(
     jobs=[hello_world_job],
```

### 2b. Replace single `run_status_list=[...]` decorator with 4 decorators sharing a helper

Refactor:

```python
def _post_fastapi_run_status_event(context: RunStatusSensorContext) -> None:
    """Shared body for all status-specific sensors. Builds the payload and
    POSTs to /api/dagster/events; best-effort delivery."""
    event_type = _EVENT_TYPE_MAP[context.dagster_run.status]
    dagster_run_id: str = (
        context.dagster_run.tags.get("dagster/backfill") or context.dagster_run.run_id
    )
    payload = {
        "event_type": event_type,
        "dagster_run_id": dagster_run_id,
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
        context.log.warning(
            "fastapi_run_status_sensor: HTTP call failed (event dropped): %s", exc
        )


@run_status_sensor(run_status=DagsterRunStatus.STARTED, name="fastapi_run_started_sensor", minimum_interval_seconds=5)
def fastapi_run_started_sensor(context: RunStatusSensorContext) -> None:
    _post_fastapi_run_status_event(context)


@run_status_sensor(run_status=DagsterRunStatus.SUCCESS, name="fastapi_run_success_sensor", minimum_interval_seconds=5)
def fastapi_run_success_sensor(context: RunStatusSensorContext) -> None:
    _post_fastapi_run_status_event(context)


@run_status_sensor(run_status=DagsterRunStatus.FAILURE, name="fastapi_run_failure_sensor", minimum_interval_seconds=5)
def fastapi_run_failure_sensor(context: RunStatusSensorContext) -> None:
    _post_fastapi_run_status_event(context)


@run_status_sensor(run_status=DagsterRunStatus.CANCELED, name="fastapi_run_canceled_sensor", minimum_interval_seconds=5)
def fastapi_run_canceled_sensor(context: RunStatusSensorContext) -> None:
    _post_fastapi_run_status_event(context)
```

Update `Definitions.sensors=[...]` to register all four.

The behavioural contract from F-050 (4 statuses → 4 webhook calls per Run lifecycle, best-effort delivery, backfill-tag fallback for `dagster_run_id`) is preserved byte-for-byte. The shared helper is unit-testable in the same way `_post_asset_notification` is.

### 2c. Harden `verify/checks.sh smoke` — new C5 check

Append to the smoke layer:

```bash
echo "--- smoke: C5 Dagster code location loaded ---"
# Probe GraphQL: repositoriesOrError must return at least one node.
# Catches: SyntaxError / TypeError / decorator misuse in user code.
NODES=$(curl -fsS -X POST "http://localhost:${DAGSTER_HOST_PORT}/graphql" \
  -H 'content-type: application/json' \
  --data '{"query":"{repositoriesOrError {... on RepositoryConnection {nodes {name}} ... on PythonError {message}}}"}' \
  | python3 -c "import sys, json; d=json.load(sys.stdin); r=d['data']['repositoriesOrError']; print(len(r.get('nodes',[]))) if 'nodes' in r else (sys.stderr.write(r.get('message','no message'))+exit(1))")
[[ "$NODES" -ge 1 ]] || { echo "FAIL: smoke C5 Dagster code location: 0 repositories loaded (definitions.py may have a syntax/import error)"; exit 1; }
echo "smoke C5 Dagster code location: OK ($NODES repository node(s) loaded)"
```

The probe is HTTP-only — no docker exec needed — and uses tools already in the existing smoke layer (curl + python3).

## 3. Verification

| ID | Check |
|---|---|
| H1 | `python -c "import ast; ast.parse(open('dagster/dagster_platform/definitions.py').read())"` returns 0 |
| H2 | `docker compose -f docker/docker-compose.dev.yml exec -T dagster-webserver python -c "from dagster_platform.definitions import defs; print(type(defs).__name__)"` prints `Definitions` |
| H3 | After `restart dagster-webserver`, GraphQL `repositoriesOrError {nodes {name}}` returns ≥1 node |
| H4 | `bash verify/checks.sh smoke` exits 0 (with new C5) |
| H5 | `bash verify/checks.sh backend` 365/365 tests still pass (no regression in F-050 unit tests, which test the FastAPI webhook handler directly — unaffected by the sensor refactor) |
| H6 | `cd apps/api && uv run ruff check . && uv run mypy dataplat_api` clean |

## 4. Files changed

- `dagster/dagster_platform/definitions.py` — re-add `defs = Definitions(` opener (line 789); split one `@run_status_sensor` into four, extract shared helper `_post_fastapi_run_status_event`; register all four sensors in `Definitions.sensors`. ~30 LOC delta.
- `verify/checks.sh` — append C5 to the `smoke` layer. ~10 LOC delta.
- No test changes (the existing test_dagster_events.py tests POST directly to the webhook handler and remain valid; the sensor body change is mechanical and behavior-preserving).

## 5. Out of scope

- Audit of OTHER Dagster decorators against installed 1.11.16 API (covered by user's choice; can be a follow-up sprint if H4-H6 surface more issues).
- A dedicated Dagster pytest layer (deferred — not justified by this hotfix's scope; the GraphQL probe is sufficient as a forward-looking guard).
- Backfilling F-050's verification record — the F-050 `passes:true` flag stays as-is on the assumption that this hotfix restores the originally-claimed behavior. If H3 reveals deeper breakage we revisit.

## 6. DoD

- [ ] All H1–H6 pass.
- [ ] Commit message: `fix(dagster): restore code-location load — re-add Definitions opener (regression from F-052) + split run_status_sensor into 4 statuses (regression from F-050) + harden smoke C5 to catch this class of bug`.
- [ ] No spec/feature_list.json changes (this is not a feature flip).
- [ ] Closing entry in claude-progress.txt with commit hash.
- [ ] After commit: resume S054-F-054 sprint workflow at step 7 (reviewer Mode A on the existing proposed.md).
