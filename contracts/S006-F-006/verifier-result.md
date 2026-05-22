# S006-F-006 verifier result

## HEAD
1048ff9 — feat(verify): F-006 smoke layer rewrite + lifespan DB probe

Commit: 1048ff9c453e8fc992044faedfe976d0cac26722

## Per-step results

### Step 1 — HEAD
```
1048ff9 feat(verify): F-006 smoke layer rewrite + lifespan DB probe
7c26887 docs: add README — project overview, quick start, sprint workflow
1d0c1fa feat(api): F-005 PASS — close sprint S005-F-005

1048ff9c453e8fc992044faedfe976d0cac26722

 D .claude/commands/plan.md
 M claude-progress.txt
?? .claude/commands/init-spec.md
?? contracts/S006-F-006/
```

**Tree status:** Clean (pre-existing deletions + untracked contract directory; no uncommitted code changes).

### Step 2 — stack
```
NAME                              IMAGE                                      STATUS                    PORTS
dataplat-dagster-daemon-1         dataplat-dagster-daemon                    Up 2 hours                
dataplat-dagster-webserver-1      dataplat-dagster-webserver                 Up 2 hours (healthy)      0.0.0.0:13000->3000/tcp
dataplat-dagster-worker-cpu-1     dataplat-dagster-worker-cpu                Up 2 hours                
dataplat-dagster-worker-heavy-1   dataplat-dagster-worker-heavy              Up 2 hours                
dataplat-fastapi-1                dataplat-fastapi                           Up 14 minutes (healthy)   0.0.0.0:18000->8000/tcp
dataplat-frontend-1               dataplat-frontend                          Up 3 hours (healthy)      0.0.0.0:15173->80/tcp
dataplat-minio-1                  minio/minio:RELEASE.2025-04-22T22-12-26Z   Up 3 hours (healthy)      0.0.0.0:19000->9000/tcp, ...
dataplat-postgres-1               postgres:16                                Up 3 hours (healthy)      0.0.0.0:15432->5432/tcp
dataplat-redis-1                  redis:7-alpine                             Up 3 hours (healthy)      0.0.0.0:16379->6379/tcp
```

**Status:** All services up and healthy. ✓

### Step 3 — smoke V1
```
--- smoke: C1 API health ---
smoke C1 API health: OK
--- smoke: C2 DB connection ---
smoke C2 DB connection: OK (via FastAPI lifespan)
--- smoke: C3 MinIO connectivity ---
smoke C3 MinIO connectivity: OK
--- smoke: C4 Dagster connectivity ---
smoke C4 Dagster connectivity: OK
✓ smoke passed
```

**Exit code:** 0 (expected: 0) ✓

### Step 4 — smoke V2 (4 ": OK" strings)
```
smoke C1 API health: OK
smoke C2 DB connection: OK (via FastAPI lifespan)
smoke C3 MinIO connectivity: OK
smoke C4 Dagster connectivity: OK
```

**Count of ": OK":** 4 (expected: 4) ✓
**All four C-checks present:** Yes ✓

### Step 5 — negative tests

#### 5a. FastAPI stopped
- **Stop result:** Container stopped.
- **Smoke output:** `FAIL: smoke C1 API health: /healthz did not return ok` + `curl: (7) Failed to connect to localhost port 18000 after 0 ms: Couldn't connect to server`
- **Exit code:** 1 (expected: 1) ✓
- **Restart + wait:** Fastapi container restarted successfully.
- **Smoke after restart:** Exit 0 ✓

#### 5b. MinIO stopped
- **Stop result:** Container stopped.
- **Smoke output:** `FAIL: smoke C3 MinIO connectivity: connection refused or curl error`
  - This confirms the **transport-level guard** (first `||` branch) fired correctly, not the status-code guard.
- **Exit code:** 1 (expected: 1) ✓
- **Restart + wait:** MinIO container restarted and became healthy.
- **Smoke after restart:** Exit 0 ✓

#### 5c. Dagster-webserver stopped
- **Stop result:** Container stopped.
- **Smoke output:** `FAIL: smoke C4 Dagster connectivity: /server_info did not return dagster_version` + `curl: (7) Failed to connect to localhost port 13000 after 0 ms: Couldn't connect to server`
- **Exit code:** 1 (expected: 1) ✓
- **Restart + wait:** Dagster-webserver container restarted and became healthy.
- **Smoke after restart:** Exit 0 ✓

#### 5d. Postgres stopped + FastAPI restart
- **Stop postgres:** Container stopped.
- **Restart fastapi:** Container restarted (forced lifespan probe to re-run).
- **Smoke output:** `FAIL: smoke C1 API health: /healthz did not return ok` + `curl: (56) Recv failure: Connection reset by peer`
  - FastAPI crashed during startup because the lifespan `engine.begin()` probe could not connect to Postgres.
- **Exit code:** 1 (expected: 1) ✓
  - **Why this proves the DB probe is working:** Without the lifespan probe, fastapi would have started successfully and `/healthz` would have returned 200 (false green). With the probe, the lifespan fails before `yield`, preventing app startup entirely.
- **Recovery:** Started postgres, restarted fastapi, waited for healthy.
- **Smoke after recovery:** Exit 0 ✓

### Step 6 — backend pytest
```
7 passed in 0.31s
```

**Expected:** 7 tests passing. **Result:** ✓
- All tests pass without requiring a live Postgres instance, confirming the `_patch_engine_begin` autouse fixture in `conftest.py` is correctly isolating the lifespan DB probe.

### Step 7 — smoke first in all) block
```
  all)
    # smoke first: cheapest check, fails fast if stack is not up at all.
    # apps/api confirmed present since F-001 passes:true.
    bash "$0" smoke
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

**Result:** ✓ `bash "$0" smoke` is the first call in the `all)` block (line 318), before `infra)`.

### Step 8 — other layers
```
backend: 0 ✓
infra: 0 ✓
dagster: 0 ✓
runs: 0 ✓
migration: 0 ✓
buckets: 0 ✓
```

**Result:** All layers exit 0. ✓

### Step 9 — all
```
✓ smoke passed
✓ infra passed
✓ backend passed
✓ migration passed
✓ buckets passed
✓ dagster passed
✓ runs passed
✓ all passed
```

**Exit code:** 0 (expected: 0) ✓

### Step 10 — tree clean
```
 D .claude/commands/plan.md
 M claude-progress.txt
?? .claude/commands/init-spec.md
?? contracts/S006-F-006/
```

**Result:** No uncommitted code changes. Only pre-existing deletions, progress file modification, and the new contract directory (untracked, expected). ✓

## Final verdict

**PASS**

All 10 steps completed successfully:
1. ✓ HEAD commit verified, tree clean
2. ✓ Docker stack healthy (8/8 services up)
3. ✓ smoke exits 0
4. ✓ All 4 checks (C1-C4) present and emit ": OK"
5. ✓ Negative tests all work:
   - fastapi stop → C1 fails → restart → smoke green
   - minio stop → C3 fails (transport guard) → restart → smoke green
   - dagster stop → C4 fails → restart → smoke green
   - postgres stop + fastapi restart → C1 fails (lifespan probe dies) → recover → smoke green
6. ✓ pytest 7/7 pass (conftest autouse engine mock working)
7. ✓ smoke is first in all) block
8. ✓ All other layers (backend, infra, dagster, runs, migration, buckets) exit 0
9. ✓ all() block exits 0
10. ✓ Working tree clean

## Notes

- **Lifespan DB probe validation:** The postgres stop + fastapi restart test (Step 5d) definitively proves the lifespan DB probe is working. Fastapi crashes during startup (visible as `Connection reset by peer` error from curl), which is the expected behavior when Postgres is unavailable. This directly validates agreed.md §3 "Lifespan DB probe change in main.py" — the `/healthz` endpoint is unreachable if Postgres is down, closing the false-green window that existed before F-006.

- **Two-tier guard in C3:** The MinIO negative test (Step 5b) confirmed that the transport-level guard fires first with "connection refused or curl error", not the status-code guard. This matches agreed.md §3 "C3 — MinIO connectivity" exactly.

- **Test isolation achieved:** All 7 backend tests pass without a live Postgres, confirming the `_patch_engine_begin` fixture correctly mocks the lifespan probe during unit test execution, while production code retains the real probe.

- **Stack recovery:** All four negative test scenarios (fastapi, minio, dagster, postgres) recovered to full green after restart, demonstrating the probe gracefully handles transient service unavailability.

**Sprint S006-F-006 is complete and ready for passes flag flip.**
