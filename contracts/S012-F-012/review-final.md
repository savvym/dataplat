# S012-F-012 — Mode B Review Final

**Reviewer:** Independent reviewer (Claude Sonnet 4.6)
**Commit reviewed:** dc45dab (parent d43c61e)
**Agreed.md:** contracts/S012-F-012/agreed.md
**Date:** 2026-05-25

---

## Calibration checks (verify/reviewer-calibration.md CAL-1..CAL-11)

- **CAL-1 (async session):** PASS — No new DB operations are added. The two new gateway calls at `routers/sources.py:229,239` are `await`-ed. No `session.query`, no `.commit()` without `await`, no sync session patterns anywhere in the diff.
- **CAL-2 (LLM gateway):** N/A — No LLM calls in this feature.
- **CAL-3 (OpenAPI sync):** PASS — `routers/sources.py` is modified (docstring only; no route or schema change). `packages/api-types/openapi.json` is in the same commit `dc45dab`. The diff is a single-line description field change — confirmed: `git diff d43c61e..dc45dab -- packages/api-types/openapi.json | grep "^+" | grep -v "^+++" | grep -v "description"` returns nothing. Regen confirmed, committed in same commit.
- **CAL-4 (lineage):** N/A — No `Commit` object created. The `Source` table row was written in F-011; this sprint only adds Dagster notifications.
- **CAL-5 (CAS path):** N/A — No new blob/artifact storage writes.
- **CAL-6 (schema freeze):** N/A — No Silver/Gold repo commit.
- **CAL-7 (bronze faithfulness):** N/A — No Bronze adapter code.
- **CAL-8 (MVP scope):** PASS — No MVP-forbidden patterns. No Celery, no DinD, no auth flows, no granular ACL, no training frameworks. Dagster usage is within approved scope (F-004/F-005 `passes:true`).
- **CAL-9 (plugin isolation):** N/A — No plugin code.
- **CAL-10 (test coverage):** PASS — 21 tests in `test_dagster_notify.py` (confirmed by grep). 9 gateway unit tests covering success + 7 distinct failure paths (Duplicate=no-op, UnauthorizedError, PythonError, ConnectError, TimeoutException, HTTPError, HTTP503, GraphQL errors, payload shape). 7 handler integration tests covering success, partition format, storage URI arg, 201-on-add-fail, 201-on-report-fail, report-still-fires-after-add-fail, commit-before-gateway ordering. Exceeds CAL-10 minimum.
- **CAL-11 (bias check):** Applied. Specific file:line evidence provided for every finding. One real gap found (finding 1 below).

---

## Contract criteria (agreed.md)

### 1. Best-effort notify correctness

**PASS.** Verified at `routers/sources.py:219-253`:
- Notify block begins at line 221, after `await session.commit()` at line 219. Ordering correct.
- Two separate `try/except` blocks: lines 228-237 for `add_source_partition`, lines 238-250 for `report_source_materialization`. Each catches only `DagsterGatewayError` and calls `logger.warning(...)`. If the first raises, the second block is still entered.
- Handler returns 201 at line 253 regardless of either block's outcome.
- `add_source_partition` called at line 229 before `report_source_materialization` at line 239.
- `import logging` at `routers/sources.py:16`, `logger = logging.getLogger(__name__)` at line 39. No NameError risk.

### 2. Gateway methods

**PASS.** Verified in `gateway.py` diff:
- `add_source_partition` (lines 465–561): follows `launch_hello_world` pattern exactly — TimeoutException→DagsterGatewayError (line 512), ConnectError→DagsterGatewayError (line 514), HTTPError→DagsterGatewayError (line 516), non-2xx→DagsterGatewayError (lines 519-521), non-JSON→DagsterGatewayError (lines 523-525), top-level `errors` key→DagsterGatewayError (lines 527-529), unexpected typename→DagsterGatewayError (line 559).
- `_REPOSITORY_LOCATION_NAME` (line 497) and `_REPOSITORY_NAME` (line 498) reuse the existing module-level constants defined at lines 30-31. Not redefined.
- `DuplicateDynamicPartitionError`→DEBUG log+return None (lines 538-544). Idempotent no-op confirmed.
- `UnauthorizedError`→DagsterGatewayError (lines 546-553). `PythonError`→DagsterGatewayError (lines 555-558). Unexpected typename→DagsterGatewayError (line 559).
- `report_source_materialization` (lines 562–648): same layered error handling pattern. Variables at lines 589-598: `eventType: "ASSET_MATERIALIZATION"`, `assetKey: {"path": ["source"]}`, `partitionKeys: [partition_key]`, `description: f"uri={storage_uri} size={size_bytes}"`. Confirmed by `test_report_source_materialization_payload_shape` test at lines 330-357.
- Both methods are `async def` with `await self._client.post(...)`.

### 3. Boundary invariant

**PASS.**
- `routers/sources.py`: no `import httpx` anywhere in the file (grep confirms no output). All Dagster interaction goes through `DagsterGateway` imported at line 27.
- Both GraphQL mutations (`_ADD_SOURCE_PARTITION_MUTATION`, `_REPORT_SOURCE_MATERIALIZATION_MUTATION`) defined only in `gateway.py` at module level (lines 89-150 of the diff).
- Existing `dagster)` layer boundary grep (checks.sh line 238-248) still passes — no httpx call to dagster outside the gateway module.

### 4. Dagster definitions

**PASS.** `dagster/dagster_platform/definitions.py`:
- `sources_partitions = DynamicPartitionsDefinition(name="sources")` — present.
- `source_asset = AssetSpec(key="source", partitions_def=sources_partitions)` — present.
- `defs = Definitions(jobs=[hello_world_job], assets=[source_asset])` — both jobs and assets wired.
- `hello_world_job` (F-005) still present and unmodified. `hello_op` and `hello_world_job` definitions unchanged.

### 5. Bind-mount runtime artifacts (NEW FINDING)

**FAIL — see finding 1 below.**

### 6. Hard invariants

- **Invariant #5 (async):** PASS — Two new gateway calls at `routers/sources.py:229,239` are `await`-ed. No new sync DB ops. No Run row added (grep on sources.py for "Run\|run_id\|dagster_run" returns only a docstring reference at line 149).
- **Invariant #6 (OpenAPI sync):** PASS — `packages/api-types/openapi.json` committed in same commit `dc45dab`. Diff is description-field-only: the `upload_source_api_sources_upload_post` operation's `"description"` value gained the F-012 docstring paragraph. No endpoint path, no schema component, no HTTP method, no response code changed.
- **Invariants #1/#2/#3/#4:** N/A — no Commit object, no blob storage, no Silver/Gold schema, no LLM call.

### 7. Scope

**PASS.** No `GET /api/runs` endpoint added. No Run table row (confirmed: no migration file in diff, no alembic version added, grep on sources.py finds no Run ORM reference). No F-018 extraction logic. No schema change. Exactly the files listed in §2 of agreed.md are modified.

### 8. Implementer-reported deviations

- **D1 (`source.size or 0`):** ACCEPTABLE. `Source.size` is `Optional[int]`; in practice it is always set by the upload handler before commit (the `compute_sha256_and_size` step sets it). The `or 0` guard satisfies mypy without changing runtime behavior for any reachable code path. Correct approach.
- **D2 (removed unused imports from test file):** ACCEPTABLE. Ruff-driven cleanup; test semantics unchanged. The 21 specified tests are all present.
- **D3 (openapi docstring-only diff):** ACCEPTABLE per agreed.md §8-Invariant6 which explicitly anticipated this: "The `upload_source` signature gains a new `DagsterGateway` parameter but this is a FastAPI dependency — it is invisible to the OpenAPI schema." The description text change is a side-effect of the docstring update, not a schema change.
- **D4 (`|| echo "000"` guard in curl health loop):** ACCEPTABLE. The guard at `checks.sh:285` prevents `set -euo pipefail` from aborting the loop during the brief period when `dagster-webserver` is restarting and curl fails with a connection error. The loop's exit condition is `DAGSTER_READY=1` which is only set when `$STATUS == "200"`. The final check at line 289 (`[[ "$DAGSTER_READY" == "1" ]] || { ... exit 1; }`) still fails hard if the server never becomes healthy within 60 seconds. The `000` fallback is a non-200 string so the loop continues; it does NOT mask a real failure — it prevents a premature abort-before-loop-completes. This is the correct pattern for health polling under `set -e`.

---

## Additional findings

### Finding 1 [MEDIUM] — `dagster/storage/` and `dagster/.telemetry/` untracked; no .gitignore entry

**Evidence:** `git status --short` shows:
```
?? dagster/.telemetry/
?? dagster/storage/
```

The bind mount `- ../dagster:/app/dagster` (added in `docker-compose.dev.yml`) causes the running Dagster process to write runtime state into the repo working tree. `dagster/storage/` contains Dagster's local event log and run storage (fallback if Postgres is unreachable during init). `dagster/.telemetry/` contains Dagster's opt-in telemetry state file. Neither is tracked in git, and neither is gitignored.

**Impact:** These directories will appear as untracked files in `git status` on every developer machine after `docker compose up -d`. Any developer running `git add -A` or `git add .` will accidentally stage Dagster runtime state. The verifier's "working tree clean" assertion (if it exists or is added) will fail on these entries. The `git status` noise was mentioned in the sprint review as a known consequence of the bind-mount decision.

The root `.gitignore` has no entries for `dagster/storage/`, `dagster/.telemetry/`, or any dagster runtime pattern. `dagster/logs/event.log` is already handled by the `*.log` rule at `.gitignore:43`.

**Required fix:** Add a `dagster/.gitignore` (or append to the root `.gitignore`) with at minimum:
```
storage/
.telemetry/
```
This is a separate small commit. The bind-mount design is correct; the gitignore omission is the gap.

---

## Verdict

APPROVED (with one tracked finding)

**Rationale per criterion:**
- Best-effort notify correctness: PASS — commit→add_partition→report_mat ordering correct; two separate try/except; 201 returned on both failures; logger declared.
- Gateway methods: PASS — full error handling pattern implemented; existing repository constants reused; DuplicateDynamicPartitionError is a no-op; correct GraphQL mutation names and variable shapes.
- Boundary invariant: PASS — no httpx in sources.py; both mutations in gateway.py only.
- Dagster definitions: PASS — DynamicPartitionsDefinition + AssetSpec wired correctly; hello_world_job intact.
- Invariant #5 (async): PASS — all new calls awaited; no Run row inserted.
- Invariant #6 (OpenAPI): PASS — regen committed in same commit; diff is docstring-only.
- Scope: PASS — no forbidden endpoints, no migration, no extraction logic.
- Tests: PASS — 21 tests, full gateway + handler coverage.

Finding 1 (MEDIUM — missing gitignore for dagster runtime dirs) does not block correctness of the feature or CI, but MUST be addressed in a follow-on commit before verifier runs that check `git status`. The leader should log this as a tracked task.
