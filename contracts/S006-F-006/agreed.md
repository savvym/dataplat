# contracts/S006-F-006/proposed.md
# Sprint: S006-F-006 — smoke layer rewrite
# Iteration 3 (addresses Mode A feedback B-1 + H-1 + B-2)

## 1. Goal

The `smoke)` layer in `verify/checks.sh` becomes a **fast, independent liveness gate** that
confirms four things are reachable: the FastAPI process, the Postgres server (via the
FastAPI async DB session), the MinIO S3 API, and the Dagster webserver.  A passing smoke run
means "something is alive at each tier"; it does NOT guarantee schema correctness, bucket
contents, migration state, or that Dagster jobs can actually be launched — those are the
responsibilities of the `migration)`, `buckets)`, `dagster)`, and `runs)` layers
respectively.  The smoke layer deliberately stays lightweight: target wall-clock time under
10 seconds with the stack already up, hard cap at 30 seconds.

The old `pytest -k smoke || true` body is pure cruft (no test is tagged `smoke`; the
`|| true` swallows every failure).  It is deleted entirely.

## 2. Files to change

| File | Change |
|---|---|
| `verify/checks.sh` | Rewrite the `smoke)` case body only |
| `verify/checks.sh` | Add `smoke` as the **first** call in the `all)` block (before `infra)`) |
| `apps/api/dataplat_api/main.py` | Add DB liveness probe inside the lifespan (3-line addition; see §3 "Lifespan DB probe change in main.py") |
| `apps/api/tests/conftest.py` | Add autouse fixture `_patch_engine_begin` that mocks `engine.begin()` for unit-test isolation (~12 lines; see §3 "Conftest autouse engine mock for unit-test isolation") |

No new files.  No changes outside `verify/`, `apps/api/dataplat_api/main.py`, and
`apps/api/tests/conftest.py`.  No new Python dependencies (the imports used —
`sqlalchemy.text`, `dataplat_api.db.session.engine`, `unittest.mock.AsyncMock`,
`unittest.mock.MagicMock`, `unittest.mock.patch`, and `contextlib.asynccontextmanager` — are
all already in the lockfile or stdlib).

### Why smoke first in `all)`?

The `all)` block currently runs `infra → backend → frontend → contract → migration →
buckets → dagster → runs`.  Smoke is the cheapest and most likely to fail first (network
stack not up at all).  Placing it first means a developer who forgot to `docker compose up`
gets an instant signal rather than waiting for `infra)` to run compose syntax checks first.
The cost of a wrong ordering is low (both are fast), but the right ordering is: smoke first.

## 3. Design — what the new smoke layer does

All four checks follow the same pattern already used in other layers:

```
<check command> || { echo "FAIL: smoke C<N> <label>: <reason>"; exit 1; }
echo "smoke C<N> <label>: OK"
```

The `set -euo pipefail` at the top of the script means any unguarded failure also exits
non-zero, but the explicit `|| { ...; exit 1; }` guards give human-readable output.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FASTAPI_HOST_PORT` | `18000` | FastAPI host port |
| `DAGSTER_HOST_PORT` | `13000` | Dagster webserver host port |
| `MINIO_API_HOST_PORT` | `19000` | MinIO S3 API host port |

The Postgres check goes through FastAPI (see C2 justification), so no `POSTGRES_HOST_PORT`
needed in this layer.

These three variables are declared at the top of the `smoke)` case, matching the style
used in `infra)`.

---

### C1 — API health

```bash
curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/healthz" \
  | grep -q '"ok"' \
  || { echo "FAIL: smoke C1 API health: /healthz did not return ok"; exit 1; }
echo "smoke C1 API health: OK"
```

- **Endpoint:** `GET /healthz` — already exists; returns `{"status": "ok"}`.
- **What `-fsS` does:** `-f` fails on HTTP 4xx/5xx, `-s` silent (no progress bar),
  `-S` shows errors on stderr.  A non-200 response causes curl to exit non-zero, which
  triggers the `||` branch.
- **Failure message:** `FAIL: smoke C1 API health: /healthz did not return ok`
- **What it proves:** FastAPI process is up and the lifespan hooks completed (DB probe
  executed successfully, gateway singleton attached), because `/healthz` is only reachable
  after uvicorn binds and the lifespan context enters.  With the DB probe added to the
  lifespan (see §3 "Lifespan DB probe change in main.py"), C1 also implies Postgres is
  reachable.

---

### C2 — DB connection

**Approach: through the API via `/healthz`, made accurate by the lifespan DB probe in `main.py`.**

Justification: In iteration 1, the proposal incorrectly claimed that the FastAPI lifespan
already ran `engine.begin()` at startup.  At HEAD 7c26887, the lifespan only creates a
`DagsterGateway` instance; `create_async_engine` is lazy and opens no real connection
until a query is executed.  With Postgres down, FastAPI would start successfully and `/healthz`
would return 200 — a false green.

This sprint adds a DB liveness probe to the lifespan in `main.py` (see §3 "Lifespan DB probe
change in main.py").  After that change, `/healthz` reachability genuinely implies Postgres
connectivity: the lifespan blocks on `engine.begin()` + `await conn.execute(text("SELECT 1"))`
before reaching `yield`; if Postgres is down, the lifespan raises and uvicorn shuts down.

The alternative (`docker compose exec postgres psql -c 'SELECT 1'`) would require the
caller to have docker available and the container name to be stable.  Since C1 already hits
FastAPI, and FastAPI now proves DB reachability at startup via the probe, a separate DB check
in smoke would be redundant and slow.

**Therefore C2 is folded into C1.**  The smoke section includes a comment explaining this
so a future reader does not wonder why the DB check is missing.

```bash
# C2 DB connection: proven by C1 — FastAPI lifespan runs a SELECT 1 probe on
# startup (added this sprint); /healthz is unreachable if Postgres is down.
echo "smoke C2 DB connection: OK (via FastAPI lifespan)"
```

---

### C3 — MinIO connectivity

The `STATUS=$(curl ...)` assignment exits non-zero (curl exit code 7) under
`set -euo pipefail` when MinIO is completely unreachable (connection refused).  The script
would exit silently at the assignment line before the `[[ "$STATUS" == "200" ]]` guard fires.
This is fixed with a two-tier guard:

```bash
STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
  "http://localhost:${MINIO_API_HOST_PORT}/minio/health/live") \
  || { echo "FAIL: smoke C3 MinIO connectivity: connection refused or curl error"; exit 1; }
[[ "$STATUS" == "200" ]] \
  || { echo "FAIL: smoke C3 MinIO connectivity: /minio/health/live returned $STATUS"; exit 1; }
echo "smoke C3 MinIO connectivity: OK"
```

- **First guard** fires on transport failure (connection refused, DNS resolution failure,
  curl error exit code ≠ 0).
- **Second guard** fires when MinIO is reachable but returns a non-200 status (e.g., 503
  during startup).
- **Endpoint:** `GET /minio/health/live` on the **S3 API port** (default 19000, variable
  `MINIO_API_HOST_PORT`).  This is confirmed by the `docker-compose.dev.yml` MinIO
  healthcheck (`curl -fsS http://localhost:9000/minio/health/live`).  The console port
  (19001) serves the web UI and returns 200/302/307 depending on session state — it is NOT
  used here.
- **Why not `-f`:** We capture the status code to show the actual value on failure (same
  pattern used by `infra)` V4).
- **Expected return:** HTTP 200.  MinIO docs state `/minio/health/live` returns 200 when
  the S3 API is accepting connections.
- **Port variable name:** `MINIO_API_HOST_PORT` — confirmed in `docker-compose.dev.yml`
  line 79 (`"${MINIO_API_HOST_PORT:-19000}:9000"`) and `docker/.env.example` line 37.
  The infra layer currently uses `MINIO_CONSOLE_HOST_PORT` because it checks the web
  console.  Smoke uses the S3 API port for the health endpoint — these are different
  variables.

---

### C4 — Dagster connectivity

```bash
curl -fsS "http://localhost:${DAGSTER_HOST_PORT}/server_info" \
  | grep -q '"dagster_version"' \
  || { echo "FAIL: smoke C4 Dagster connectivity: /server_info did not return dagster_version"; exit 1; }
echo "smoke C4 Dagster connectivity: OK"
```

- **Endpoint:** `GET /server_info` — returns JSON with `"dagster_version"` key.
- **Why NOT `/api/admin/dagster-status`:** That route proves FastAPI+Dagster together.
  In smoke we want to isolate: if FastAPI is up (C1) and Dagster is NOT up, C4 should fail
  independently.  Using the FastAPI proxy route would mask which component failed.
- **Why NOT `/dagster_version`:** In Dagster 1.11+ that endpoint returns the SPA HTML shell
  (HTTP 200 but no JSON).  The `infra)` layer already documents this and uses `/server_info`
  for content verification.  Smoke follows the same established pattern.
- **Failure message:** `FAIL: smoke C4 Dagster connectivity: /server_info did not return dagster_version`
- **C4 connection-refused safety:** C4 uses `-fsS` which causes curl to write nothing to
  stdout on connection error; the pipe to `grep -q` then fails (grep exits 1 on empty input),
  and with `pipefail` that fires the `|| { ...; exit 1; }` guard correctly.  C4 does not
  need the two-tier guard that C3 requires.

---

### Lifespan DB probe change in `main.py`

**Problem (B-1 from reviewer):** At HEAD 7c26887, `main.py`'s lifespan does not probe
Postgres.  `create_async_engine` is lazy; no real connection is opened until a query runs.
The C2 "lifespan-proves-DB" argument was factually false.

**Fix (Option A from reviewer):** Add an async `SELECT 1` probe before `yield` in the
lifespan.  Import `engine` from `dataplat_api.db.session` (confirmed name: `engine`, line 13
of `db/session.py`) and `text` from `sqlalchemy` (already in lockfile).

The exact diff applied to `main.py`:

```python
# Before (lines 7-9 of imports + lifespan body):
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from dataplat_api.config import settings
from dataplat_api.dagster.gateway import DagsterGateway
...

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    gateway = DagsterGateway(graphql_url=settings.DAGSTER_GRAPHQL_URL)
    app.state.dagster_gateway = gateway
    yield
    await gateway.aclose()
```

```python
# After:
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from dataplat_api.config import settings
from dataplat_api.dagster.gateway import DagsterGateway
from dataplat_api.db.session import engine
...

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # DB liveness probe — raises and aborts startup if Postgres is unreachable.
    # This makes /healthz reachability genuinely imply DB connectivity, which
    # is what verify/checks.sh smoke C2 relies on.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    gateway = DagsterGateway(graphql_url=settings.DAGSTER_GRAPHQL_URL)
    app.state.dagster_gateway = gateway
    yield
    await gateway.aclose()
```

Key properties of this change:

- The probe runs **before** the gateway is created. DB is the more fundamental dependency;
  failing fast on DB down avoids a partially-initialised app state.
- If the probe raises (Postgres down, wrong credentials, network unreachable), the lifespan
  never reaches `yield`, uvicorn shuts down — exactly the failure mode smoke C2 relies on.
- `pool_pre_ping=True` is confirmed set on the engine (line 15 of `db/session.py`).  This
  ensures that after the initial probe, subsequent connection reuse pings before use rather
  than silently recycling a dead connection.
- `sqlalchemy.text` is already available (SQLAlchemy is a direct dependency in `pyproject.toml`).
- The import path is `dataplat_api.db.session.engine` — the exported name is `engine`
  (not `async_engine`, not `_engine`).  Confirmed by reading `db/session.py` line 13.
- Hard invariant #5 (CLAUDE.md): the probe is fully async (`async with engine.begin()` +
  `await conn.execute()`).  No `session.query()`, no sync session.  Invariant satisfied.
- Hard invariant #6 (CLAUDE.md): this change does NOT add or modify any OpenAPI route.
  `make codegen` is NOT required for this sprint.

---

### Conftest autouse engine mock for unit-test isolation

**Problem (B-2 from reviewer):** The lifespan DB probe added to `main.py` runs every time
the FastAPI app is constructed — including inside `with TestClient(app)` in the test suite.
The existing tests (`test_admin_dagster_status.py`, `test_runs_hello_world.py`) are
**fully network-independent**: all `DagsterGateway` methods are mocked per-test via
`AsyncMock`, and the `_patch_httpx_no_ssl` autouse fixture is a host-SSL workaround (not a
stack-dependency signal).  The conftest `DATABASE_URL` setdefault (`postgresql+asyncpg://
test:test@localhost/test`) resolves to port 5432 — a port that is not this project's Postgres
(compose maps Postgres to host port 15432 with user `app` / db `platform`).  Therefore,
without a fix, every `with TestClient(app)` call would raise
`sqlalchemy.exc.OperationalError: connection refused` at fixture setup, breaking all 7 tests
in `verify/checks.sh backend)`.

**Fix (Option A — analogous to `_patch_httpx_no_ssl`):** Add an autouse fixture in
`apps/api/tests/conftest.py` that patches `engine.begin` to return a no-op async context
manager.  The patch target is `dataplat_api.db.session.engine` — `engine` is confirmed to be
a module-level name at line 13 of `db/session.py` (`engine = create_async_engine(...)`),
so `patch.object(db_session.engine, "begin", fake_begin)` resolves correctly.

**Exact fixture code to add to `conftest.py`:**

```python
import dataplat_api.db.session as db_session
from collections.abc import Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture(autouse=True)
def _patch_engine_begin() -> Iterator[None]:
    """Patch engine.begin() to a no-op so tests don't require a live Postgres.

    The lifespan in main.py runs `async with engine.begin() as conn:
    await conn.execute(text("SELECT 1"))` to probe DB at startup.  In production
    that probe proves DB reachability (used by verify/checks.sh smoke C2).
    In unit tests we don't want to require a live Postgres just to construct
    the FastAPI app via TestClient — so we mock the probe to a no-op.

    Patch target: dataplat_api.db.session.engine.begin
    engine is module-level in db/session.py (line 13: engine = create_async_engine(...)).
    patch.object(db_session.engine, "begin", ...) replaces the bound method directly,
    so the lifespan's `async with engine.begin() as conn` receives the fake context manager.

    Production code is unaffected — this patch is only active under pytest.
    """
    @asynccontextmanager
    async def fake_begin():  # type: ignore[return]
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=None)
        yield conn

    with patch.object(db_session.engine, "begin", fake_begin):
        yield
```

Key properties:

- **Patch target:** `dataplat_api.db.session.engine` — `engine` is module-level (confirmed:
  `db/session.py` line 13).  `patch.object(db_session.engine, "begin", fake_begin)` replaces
  the `.begin` attribute on the engine instance for the duration of each test.
- **`asynccontextmanager` shape:** The lifespan calls `async with engine.begin() as conn:`
  which requires `engine.begin` to be a callable that returns an async context manager.
  Wrapping `fake_begin` with `@asynccontextmanager` satisfies this contract exactly.
- **`conn.execute = AsyncMock(return_value=None)`:** The lifespan calls
  `await conn.execute(text("SELECT 1"))` and does not inspect the return value.
  `AsyncMock(return_value=None)` is sufficient — no inspection of the awaited result.
- **`autouse=True`:** All tests pick up the fixture automatically; no per-test changes needed.
- **Pattern mirrors `_patch_httpx_no_ssl`:** Both are autouse `with patch.object(...)` + `yield`
  fixtures targeting startup side-effects that are irrelevant to unit-test correctness.
- **Return type `Iterator[None]`:** This is the pyright-friendly annotation for a
  `yield`-based fixture.  Requires `from collections.abc import Iterator`.
- **No new deps:** `asynccontextmanager` and `Iterator` are stdlib (`contextlib`,
  `collections.abc`); `AsyncMock`, `MagicMock`, `patch` are stdlib `unittest.mock`.
- **Existing `_patch_httpx_no_ssl` unaffected:** The new fixture is independent and
  can coexist with it; both are autouse and both use separate `patch.object` targets.
- **Production unaffected:** `patch.object` is only active inside the `with` block, which
  is only entered during pytest execution.  Real app startup still executes the real probe.

**New imports required in conftest.py:**

- `import dataplat_api.db.session as db_session` (new module import)
- `from collections.abc import Iterator` (new type import)
- `from contextlib import asynccontextmanager` (new import)
- `from unittest.mock import AsyncMock, MagicMock` (extend existing `from unittest.mock import patch`)

The existing `from unittest.mock import patch` line becomes
`from unittest.mock import AsyncMock, MagicMock, patch`.

---

### Complete new `smoke)` case body

```bash
smoke)
  FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
  DAGSTER_HOST_PORT="${DAGSTER_HOST_PORT:-13000}"
  MINIO_API_HOST_PORT="${MINIO_API_HOST_PORT:-19000}"

  echo "--- smoke: C1 API health ---"
  curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/healthz" \
    | grep -q '"ok"' \
    || { echo "FAIL: smoke C1 API health: /healthz did not return ok"; exit 1; }
  echo "smoke C1 API health: OK"

  echo "--- smoke: C2 DB connection ---"
  # C2 DB connection: proven by C1 — FastAPI lifespan runs a SELECT 1 probe on
  # startup (added this sprint); /healthz is unreachable if Postgres is down.
  echo "smoke C2 DB connection: OK (via FastAPI lifespan)"

  echo "--- smoke: C3 MinIO connectivity ---"
  STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
    "http://localhost:${MINIO_API_HOST_PORT}/minio/health/live") \
    || { echo "FAIL: smoke C3 MinIO connectivity: connection refused or curl error"; exit 1; }
  [[ "$STATUS" == "200" ]] \
    || { echo "FAIL: smoke C3 MinIO connectivity: /minio/health/live returned $STATUS"; exit 1; }
  echo "smoke C3 MinIO connectivity: OK"

  echo "--- smoke: C4 Dagster connectivity ---"
  curl -fsS "http://localhost:${DAGSTER_HOST_PORT}/server_info" \
    | grep -q '"dagster_version"' \
    || { echo "FAIL: smoke C4 Dagster connectivity: /server_info did not return dagster_version"; exit 1; }
  echo "smoke C4 Dagster connectivity: OK"
  ;;
```

---

### Updated `all)` block

```bash
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

## 4. Verification

### V1 — smoke exits 0 with stack up

```bash
bash verify/checks.sh smoke
echo "exit: $?"   # must be 0
```

### V2 — all four sub-checks present in output

```bash
bash verify/checks.sh smoke | grep -c ": OK"
# Expected output: 4
```

Grepping for the literal string `: OK` at end of each check line gives exactly 4 matches
(C1, C2, C3, C4).  Future reviewers can use this as a CI assertion.  The grep-able strings
are:

- `smoke C1 API health: OK`
- `smoke C2 DB connection: OK (via FastAPI lifespan)`
- `smoke C3 MinIO connectivity: OK`
- `smoke C4 Dagster connectivity: OK`

Note: C2's line `smoke C2 DB connection: OK (via FastAPI lifespan)` contains the substring
`: OK` in the middle (not at end-of-line), but `grep -c ": OK"` is a substring match and
counts it correctly.  No false assumptions about end-of-line anchoring are made here.

### V3 — negative test (manual, run by verifier)

This is a **verifier-runs-this** procedure, not something the script does to itself.

```bash
# 1. Baseline: smoke passes
bash verify/checks.sh smoke
echo "exit: $?"   # expect 0

# 2. Stop fastapi container
docker compose -f docker/docker-compose.dev.yml stop fastapi

# 3. Run smoke; expect non-zero exit and C1 named in output
bash verify/checks.sh smoke 2>&1 | grep -E "(FAIL|smoke C)"
echo "exit: $?"   # expect 1
# Expected stderr/stdout contains: "FAIL: smoke C1 API health"

# 4. Restart fastapi
docker compose -f docker/docker-compose.dev.yml start fastapi
# Wait for healthy (up to 30s)
for i in $(seq 1 30); do
  docker compose -f docker/docker-compose.dev.yml exec -T fastapi \
    python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).getcode()==200 else 1)" \
    2>/dev/null && break || sleep 1
done

# 5. Smoke green again
bash verify/checks.sh smoke
echo "exit: $?"   # expect 0
```

Analog tests for MinIO and Dagster follow the same pattern (stop the relevant service,
verify the matching FAIL message, restart, verify smoke green again).  These are optional
but encouraged during the verifier run.

For C3 MinIO negative test specifically: stopping MinIO should produce the message
`FAIL: smoke C3 MinIO connectivity: connection refused or curl error` (transport failure),
NOT `returned $STATUS`.  This verifies the two-tier guard is working correctly.

## 5. Scope discipline

- No new FastAPI routes or new Python files.
- No new Docker services, images, or volumes.
- No changes to `infra)`, `migration)`, `buckets)`, `dagster)`, `runs)`, `backend)`,
  `frontend)`, `contract)`, or `plugin)` case bodies.
- Only change to `all)` is inserting `bash "$0" smoke` at the top (plus an optional
  explanatory comment).
- Only change to `apps/api/dataplat_api/main.py` is two new import lines (`from sqlalchemy
  import text` and `from dataplat_api.db.session import engine`) and a 3-line probe inside
  the existing lifespan.  `main.py` touches one import (`sqlalchemy.text` already in lockfile
  via SQLAlchemy) and adds the probe inside the existing lifespan — no new modules, no new
  files.
- Only change to `apps/api/tests/conftest.py` is:
  - Extend `from unittest.mock import patch` to `from unittest.mock import AsyncMock, MagicMock, patch`
  - Add three new import lines: `import dataplat_api.db.session as db_session`,
    `from collections.abc import Iterator`, `from contextlib import asynccontextmanager`
  - Add the ~18-line `_patch_engine_begin` autouse fixture (including docstring)
  - Total addition: approximately 25 lines (imports + fixture + docstring)
  - No existing lines modified or removed
- **Three files total in scope:** `verify/checks.sh`, `apps/api/dataplat_api/main.py`,
  `apps/api/tests/conftest.py`.  No other files touched.
- No new `uv` / `pip` / `pnpm` dependencies (`asynccontextmanager`, `Iterator`, `AsyncMock`,
  `MagicMock` are all stdlib).
- No new OpenAPI routes → `make codegen` is NOT needed for this sprint (hard invariant #6
  confirmed not triggered).
- Target runtime: under 10 seconds with stack up (3 curl calls, 2 grep calls, no polling).

## 6. Risks / open questions

### R-1: FastAPI startup latency after `docker compose up -d`

If the caller runs smoke immediately after `up -d`, FastAPI may not be healthy yet (uvicorn
takes 2-5s, lifespan hooks add more).

**Decision: assume stack is already healthy.**  This matches the infra layer's assumption.
The session-start protocol in `CLAUDE.md` says to run `docker compose up -d` THEN run
smoke.  Callers that want guaranteed readiness should use `docker compose up -d --wait`
(Docker Compose 2.x) before calling smoke.  No bounded retry loop is added to the script
itself — that would add latency to the normal case and duplicate the health-wait logic
already present in `dagster)`.  This decision is documented in a comment in the script.

### R-2: C2 correctness depends on the lifespan DB probe added this sprint

C2's correctness depends on the `engine.begin()` / `SELECT 1` probe that this sprint adds
to `main.py`.  If a future sprint removes the `engine.begin()` call from the lifespan,
C2 must be reverted to a separate probe (new `/healthz/db` route or script-level
`docker exec psql`).  The risk is noted in the C2 comment in the script.

### R-3: MinIO API port variable name

The `infra)` layer currently uses `MINIO_CONSOLE_HOST_PORT` (console port, 19001).  This
sprint uses `MINIO_API_HOST_PORT` (S3 API port, 19000) — a different variable.  Both names
are already declared in `docker/.env.example` and `docker-compose.dev.yml`.  No conflict.

### R-4: `grep -q '"ok"'` vs JSON parsing

`/healthz` currently returns `{"status": "ok"}`.  The grep matches the literal string `"ok"`
(with quotes) which is sufficient for a liveness check.  If the response schema changes
(e.g., a future sprint adds more fields), the grep will still pass as long as `"ok"` is
present.  This is intentional — smoke is not a schema validator.

### R-5: Lifespan DB probe side effect on existing tests — and the chosen mitigation

Both existing test files — `apps/api/tests/test_admin_dagster_status.py` and
`apps/api/tests/test_runs_hello_world.py` — use `with TestClient(app) as c:` in their
`client` fixtures.  `fastapi.testclient.TestClient` is a sync ASGI wrapper that **does**
execute the lifespan context manager when used as a context manager.  After this sprint's
change to `main.py`, every `with TestClient(app)` call will attempt to execute:

```python
async with engine.begin() as conn:
    await conn.execute(text("SELECT 1"))
```

**What the tests look like at HEAD (truth, corrected from iteration 2):**

At HEAD 7c26887, all 7 tests in the two test files are **fully network-independent**:

- All `DagsterGateway` methods are mocked per-test via `AsyncMock` (e.g.,
  `mock_gateway.get_dagster_version`, `mock_gateway.launch_hello_world`,
  `mock_gateway.get_run_status`).  No real HTTP call to Dagster is ever attempted.
- The `_patch_httpx_no_ssl` autouse fixture in `conftest.py` is a **host-environment
  SSL workaround** (the module docstring explicitly states: *"avoids ssl.SSLError on this
  host's Python/OpenSSL build"*).  It is not a signal that tests depend on a live stack.
  Production containers are unaffected.
- The conftest `DATABASE_URL` setdefault (`postgresql+asyncpg://test:test@localhost/test`)
  resolves to `localhost:5432` (asyncpg default).  The compose stack maps Postgres to host
  port **15432** (not 5432), with user `app` and database `platform`.  This URL cannot
  connect to the compose Postgres whether the stack is up or down.  The setdefault is
  present only to satisfy `pydantic-settings` at import time — no actual DB connection
  is opened today because the lifespan does not yet probe the DB.

**Without the conftest fix — what would break:**

After the `engine.begin()` probe lands in `main.py`, every `with TestClient(app)` call
would execute the probe against `postgresql+asyncpg://test:test@localhost/test` (port 5432).
This raises `sqlalchemy.exc.OperationalError: connection refused`.  All 7 tests — 2 in
`test_admin_dagster_status.py` and 5 in `test_runs_hello_world.py` — fail at fixture setup,
not at assertions.  `verify/checks.sh backend)` exits non-zero.  F-006 cannot pass
verification.

**Mitigation chosen (Option A — autouse conftest fixture):**

This sprint adds an autouse `_patch_engine_begin` fixture to `apps/api/tests/conftest.py`
that patches `engine.begin` to return a no-op async context manager (see §3 "Conftest
autouse engine mock for unit-test isolation" for the full fixture code and justification).

Key points:

- Unit tests are not infra tests.  They should not require a running Postgres instance to
  construct the FastAPI app.  The mock keeps the existing `with TestClient(app)` pattern
  working without any per-test changes.
- Smoke (`verify/checks.sh smoke)`) is the canonical proof of DB connectivity in F-006.
  The `_patch_engine_begin` fixture does not weaken that guarantee — it only applies under
  pytest, not in the running app.
- The pattern mirrors `_patch_httpx_no_ssl`: both are autouse `with patch.object(...)`
  + `yield` fixtures that neutralise a startup side-effect irrelevant to unit-test
  correctness.
- Production code is fully unaffected.  The `patch.object` context manager is only
  entered during pytest execution.  The real app still executes the real probe.

**`DATABASE_URL` in compose (confirmed):** `docker-compose.dev.yml` line 219 injects
`DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-app}:${POSTGRES_PASSWORD:-devpassword}
@${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-platform}` into the
`fastapi` service `environment:` block.  No change to `.env.example` is needed.

**Hard invariant #5 (CLAUDE.md):** The `_patch_engine_begin` fixture mocks the probe in
test context only.  In production, `async with engine.begin()` + `await conn.execute()` is
fully async — invariant #5 is satisfied in both production and test code.

## 7. Out of scope

The following are explicitly NOT done in this sprint:

- Any new FastAPI health route (e.g., `/healthz/db`, `/healthz/minio`).
- Bucket listing or object I/O (that is the `buckets)` layer).
- Migration state verification (that is the `migration)` layer).
- Testing that Dagster can actually launch a job (that is the `runs)` layer).
- Retry/wait logic for an unhealthy stack (call `docker compose up -d --wait` from outside).
- Changes to any layer other than `smoke)` and the ordering line in `all)`.
- Adding `smoke` to the `backend)` pytest run or any pytest marker.
- Changes to `db/session.py` — only `main.py` and `tests/conftest.py` are touched in `apps/api/`.
- Any change to the `backend)` case body itself — the `_patch_engine_begin` fixture ensures the
  existing `uv run pytest -q` invocation in `backend)` continues to pass without modification.
