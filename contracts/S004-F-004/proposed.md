# Sprint Contract S004-F-004 — DagsterGateway Abstraction + Admin Status Route

**Status:** PROPOSED (iteration 2 — addressing Mode A findings 1–5)  
**Date drafted:** 2026-05-22  
**Last updated:** 2026-05-22  
**Author:** Implementer (Claude)

---

## 1. Goal

F-004 establishes the `DagsterGateway` abstraction — the single chokepoint through which all FastAPI code reaches Dagster. This mirrors the design-doc mandate in §9.2: _"永远不让前端直接接触 Dagster GraphQL, 所有 Dagster 调用走 FastAPI 包装"_ (the frontend never touches Dagster GraphQL directly; all Dagster calls go through the FastAPI wrapper). The abstraction parallels Hard Invariant #4 (LLM calls go through the gateway) — DagsterGateway plays the same role for Dagster interaction. This sprint delivers the minimal surface (`get_dagster_version()`) to prove the end-to-end wiring: FastAPI container starts, resolves `dagster-webserver:3000` via compose-internal DNS, hits the GraphQL endpoint, and returns the version string through a typed Pydantic response at `GET /api/admin/dagster-status`. All future Dagster calls (launch_run, add_dynamic_partition, etc.) will extend this abstraction without touching the routes directly.

---

## 2. Scope — Files to Change

| File | Action | Purpose |
|---|---|---|
| `apps/api/dataplat_api/dagster/__init__.py` | create | Python package init — re-exports `DagsterGateway`, `DagsterGatewayError` |
| `apps/api/dataplat_api/dagster/gateway.py` | create | `DagsterGateway` class: async httpx client, `get_dagster_version()`, `DagsterGatewayError` |
| `apps/api/dataplat_api/dagster/dependencies.py` | create | `get_dagster_gateway()` FastAPI dependency — retrieves singleton from `request.app.state` |
| `apps/api/dataplat_api/schemas/__init__.py` | create | Python package init for schemas module |
| `apps/api/dataplat_api/schemas/admin.py` | create | `DagsterStatusResponse(BaseModel)` Pydantic response model |
| `apps/api/dataplat_api/routers/admin.py` | create | Admin router — `GET /api/admin/dagster-status` |
| `apps/api/dataplat_api/main.py` | modify | Add lifespan context manager (init/close `DagsterGateway`); wire `admin_router` |
| `apps/api/dataplat_api/config.py` | modify | Add `DAGSTER_GRAPHQL_URL: str` setting with default `http://dagster-webserver:3000/graphql` |
| `apps/api/pyproject.toml` | modify | Add `httpx==0.28.1` to `[project.dependencies]` (not a transitive dep of FastAPI; must be explicit) |
| `apps/api/uv.lock` | modify | Regenerate after dep addition (`cd apps/api && uv lock`) |
| `docker/docker-compose.dev.yml` | modify | Rename `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` in `fastapi` env block; add `dagster-webserver` to `depends_on` |
| `docker/.env.example` | modify | Rename `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` (line 25) to match Settings field; prevents silent misconfiguration |
| `verify/checks.sh` | modify | Add `dagster)` layer (V1 + V2); add `bash "$0" dagster` to `all)` |

**OpenAPI ↔ TS sync note (Hard Invariant #6):** This sprint adds a new route and a new Pydantic response model, which changes the OpenAPI schema. However, `packages/api-types/` does not yet exist — the codegen pipeline has not been built. The `contract` layer in `checks.sh` already guards this with `exists packages/api-types || { echo "no packages/api-types yet"; exit 0; }` (line 82). Therefore `make codegen` and TS type sync are deferred for this sprint. This interpretation **must be confirmed by the Mode A reviewer**. The admin route will include a comment marking it for codegen once the pipeline is wired (planned for the first web-facing sprint).

**Image rebuild required:** After adding `httpx` to `pyproject.toml` and regenerating `uv.lock`, the implementer MUST rebuild the fastapi image:

```bash
docker compose -f docker/docker-compose.dev.yml build fastapi
docker compose -f docker/docker-compose.dev.yml up -d fastapi
```

---

## 3. DagsterGateway Design

### 3.1 Class surface

```python
# apps/api/dataplat_api/dagster/gateway.py

import httpx


class DagsterGatewayError(Exception):
    """Raised when Dagster is unreachable or returns an unexpected response.

    Never exposed as HTTPException directly — the admin router's exception
    handler catches this and returns a 503 with {"detail": "Dagster unreachable"}.
    """


class DagsterGateway:
    """Single chokepoint for all FastAPI → Dagster GraphQL communication.

    Instantiated ONCE at application startup (lifespan event) and stored on
    app.state.dagster_gateway. All route handlers receive it via
    Depends(get_dagster_gateway). Never instantiate this class inside a route
    handler — doing so opens a new httpx.AsyncClient per request (wasteful and
    breaks connection pooling).

    Future methods (NOT this sprint):
        async def launch_run(self, ...) -> str
        async def get_run_status(self, run_id: str) -> RunStatus
        async def add_dynamic_partition(self, ...) -> None
        async def reload_code_location(self, ...) -> None
    """

    def __init__(self, graphql_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = graphql_url
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def get_dagster_version(self) -> str:
        """Return the Dagster version string from the GraphQL endpoint.

        GraphQL query: { version }
        Confirmed against running Dagster 1.11.16 instance — returns:
            {"data": {"version": "1.11.16"}}

        Raises DagsterGatewayError for ALL of the following:
            - httpx network error (ConnectError, TimeoutException, etc.)
              — wrap the original exception: raise DagsterGatewayError(...) from exc
            - HTTP response status is not 2xx
            - Response body is not valid JSON (json.JSONDecodeError)
            - Top-level "errors" key is present and its value is a non-empty list
              (standard GraphQL server-side error; HTTP status is still 200)
            - "data" key absent from parsed response
            - "data"["version"] absent, None, or empty string
            - Any KeyError / ValueError from response parsing — catch and re-raise
              as DagsterGatewayError so callers always see one exception type

        The implementer MUST NOT let any of these bubble as KeyError, ValueError,
        or httpx exceptions — the route handler only catches DagsterGatewayError.
        """
        ...

    async def aclose(self) -> None:
        """Close the underlying httpx.AsyncClient. Called in lifespan teardown."""
        await self._client.aclose()
```

### 3.2 Design decisions

**GraphQL version field:** Confirmed `query { version }` returns `{"data": {"version": "1.11.16"}}` against the running Dagster 1.11.16 instance in this repo's compose stack. No field renaming needed.

**httpx version:** `httpx==0.28.1` (latest stable as of 2026-05-22). Not a transitive dependency of FastAPI 0.115.12 (FastAPI requires only `starlette`, `pydantic`, `typing-extensions`). Must be added explicitly to `pyproject.toml`.

**Client lifetime — singleton via `app.state`:** One `httpx.AsyncClient` is created at app startup in a `lifespan` context manager and stored on `app.state.dagster_gateway`. The FastAPI dependency `get_dagster_gateway(request: Request)` returns `request.app.state.dagster_gateway`. This is the recommended pattern for shared resources in FastAPI 0.93+. Alternatives considered:

- Module-level singleton: breaks testability (cannot inject a mock client in unit tests).
- New client per request: wasteful; defeats connection pooling; TCP handshake on every call.
- `app.state` singleton via lifespan: chosen — clean lifecycle, testable, no global state.

**`lifespan` context manager pattern (FastAPI 0.115.x):**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    gateway = DagsterGateway(graphql_url=settings.DAGSTER_GRAPHQL_URL)
    app.state.dagster_gateway = gateway
    yield
    await gateway.aclose()

app = FastAPI(title="Dataplat API", version="0.1.0", lifespan=lifespan)
```

**Error handling:** `DagsterGatewayError` is a plain `Exception` subclass, NOT an `HTTPException` subclass. The admin router registers a custom exception handler (or a `try/except` in the route) that catches `DagsterGatewayError` and returns `JSONResponse(status_code=503, content={"detail": "Dagster unreachable"})`. This is cleaner than subclassing `HTTPException` because: (a) the gateway should not import FastAPI, keeping it as a standalone async I/O component; (b) future callers (background tasks, other gateways) can handle the error without FastAPI context.

**Timeout:** 10 seconds. The design doc does not specify a timeout; 10s is a safe default for GraphQL calls over a loopback network (compose internal DNS). This can be overridden via `DAGSTER_GRAPHQL_TIMEOUT` in a future sprint if needed.

**Async-only:** Every method is `async def`. Uses `httpx.AsyncClient` exclusively. Sync `httpx.Client` is never used in `apps/api/` per Hard Invariant #5.

### 3.3 Enforcement boundary

**All FastAPI → Dagster GraphQL calls MUST go through `apps/api/dataplat_api/dagster/gateway.py`. No other module in `apps/api/` — and no plugin — may import `httpx` to call Dagster directly.** This rule is the Dagster equivalent of CLAUDE.md Hard Invariant #4 ("LLM calls go through the gateway"). Any route handler, service, or background task that needs Dagster must receive a `DagsterGateway` instance via `Depends(get_dagster_gateway)`.

To enforce this at CI time, the `dagster)` layer in `verify/checks.sh` includes a grep guard that fails if any `.py` file outside `dataplat_api/dagster/` contains an `httpx` call whose URL argument mentions `dagster`:

```bash
# Grep for raw httpx calls targeting dagster outside the gateway module.
# Pattern: httpx.(get|post|AsyncClient) on the same line as "dagster"
# in any .py file not under dataplat_api/dagster/.
BAD_CALLS=$(grep -rn --include='*.py' -E 'httpx\.(get|post|AsyncClient)' \
  apps/api/dataplat_api/ \
  | grep -i 'dagster' \
  | grep -v 'apps/api/dataplat_api/dagster/' \
  || true)
if [[ -n "$BAD_CALLS" ]]; then
  echo "FAIL: raw httpx call to Dagster outside gateway module:"
  echo "$BAD_CALLS"
  exit 1
fi
echo "  gateway boundary check: OK"
```

This is a CI tripwire, not a comprehensive static analyzer. It catches the most common violation pattern (a developer copy-pasting an `httpx.post` to the Dagster GraphQL URL into a route handler). The `gateway.py` module docstring MUST also state this rule so it is visible at point-of-reading.

Note: the leader should consider adding this as a named extension to Hard Invariant #4 in `CLAUDE.md` after this sprint merges (that edit is the human's call, not in scope for the implementer).

---

## 4. Route Shape

```
GET /api/admin/dagster-status
Authorization: none (TODO: wire JWT after F-008 adds auth middleware)
Response 200 OK:
    {"dagster_version": "1.11.16"}
Response 503 Service Unavailable:
    {"detail": "Dagster unreachable"}
```

**Pydantic model:**

```python
# apps/api/dataplat_api/schemas/admin.py
from pydantic import BaseModel

class DagsterStatusResponse(BaseModel):
    dagster_version: str
```

**Router:**

```python
# apps/api/dataplat_api/routers/admin.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from dataplat_api.dagster.gateway import DagsterGatewayError
from dataplat_api.dagster.dependencies import get_dagster_gateway
from dataplat_api.schemas.admin import DagsterStatusResponse

router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.get("/dagster-status", response_model=DagsterStatusResponse)
async def dagster_status(
    gateway=Depends(get_dagster_gateway),
) -> DagsterStatusResponse:
    # TODO(F-008): add JWT dependency here once auth middleware is wired
    try:
        version = await gateway.get_dagster_version()
        return DagsterStatusResponse(dagster_version=version)
    except DagsterGatewayError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Dagster unreachable"},
        )
```

**Note on return type annotation:** Returning `JSONResponse` from a route typed as `-> DagsterStatusResponse` is valid FastAPI/Starlette; FastAPI respects the raw `Response` subtype and bypasses serialisation. This is the standard pattern for error overrides without registering a global exception handler.

---

## 5. Config Change

```python
# apps/api/dataplat_api/config.py (addition)
class Settings(BaseSettings):
    DATABASE_URL: str
    DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"
    # default matches compose-internal DNS; can be overridden via env var
```

The env var `DAGSTER_GRAPHQL_URL` is already set in `docker-compose.dev.yml` as `DAGSTER_GRAPHQL: http://dagster-webserver:3000/graphql` (line 220). However the variable name in compose is `DAGSTER_GRAPHQL` while Settings will use `DAGSTER_GRAPHQL_URL`. There are two options:

- **Option A (chosen):** Rename the compose env var from `DAGSTER_GRAPHQL` to `DAGSTER_GRAPHQL_URL` to match the Settings field name. The implementer MUST update `docker-compose.dev.yml` line 220 AND `docker/.env.example` line 25 accordingly (see OQ-3 for the `.env.example` rename detail and migration note).
- Option B: Keep `DAGSTER_GRAPHQL` in compose and add an alias in Settings. Rejected — adds indirection and confusion.

The default value `http://dagster-webserver:3000/graphql` in Settings means the app works correctly even if the env var is omitted (e.g. in unit tests running outside compose).

---

## 6. `docker-compose.dev.yml` Changes

Three changes in this sprint:

1. **`docker-compose.dev.yml`** — rename env var `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` in the `fastapi` service `environment` block (aligns with Settings field).
2. **`docker-compose.dev.yml`** — add `dagster-webserver` to the `fastapi` service `depends_on` with `condition: service_healthy`. The current compose file has a comment at line 208: _"Does NOT depend on dagster-webserver this sprint (DagsterGateway is F-004)."_ This sprint removes that deferral and wires the dependency. FastAPI will wait for dagster-webserver healthy before starting, preventing startup errors when the gateway tries to call Dagster before it is ready.
3. **`docker/.env.example`** — rename `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` on line 25 (confirmed: the old name is present). Add a comment: `# Renamed from DAGSTER_GRAPHQL in sprint S004-F-004 — update your local .env if you have the old name`.

```yaml
fastapi:
  ...
  environment:
    ...
    DAGSTER_GRAPHQL_URL: ${DAGSTER_GRAPHQL_URL:-http://dagster-webserver:3000/graphql}
  depends_on:
    postgres:
      condition: service_healthy
    minio-init:
      condition: service_completed_successfully
    dagster-webserver:          # added this sprint
      condition: service_healthy
```

---

## 7. Verification Plan

### V1 — `GET /api/admin/dagster-status` returns 200 with `{"dagster_version": "..."}`

From the **host** (not inside the container, to avoid the need for `curl` in the fastapi image):

```bash
FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"
curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
  | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('V1 OK:', body)
"
```

### V2 — Restart fastapi container; route still returns 200

```bash
COMPOSE="docker/docker-compose.dev.yml"
FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

docker compose -f "$COMPOSE" restart fastapi

# Wait for fastapi healthy (max 30s; uses python urllib to avoid curl dep in image)
for i in $(seq 1 30); do
  docker compose -f "$COMPOSE" exec -T fastapi \
    python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).getcode()==200 else 1)" \
    2>/dev/null && break
  sleep 1
done

# Re-run V1
curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
  | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('V2 OK (post-restart):', body)
"
```

V2 proves the gateway is correctly re-initialized on container restart via the lifespan event (not relying on Dagster-side state or stale process memory).

### Full `dagster)` block for `verify/checks.sh`

**Note on response handling:** The curl output is piped directly into `python3 -c "... json.load(sys.stdin)"` — never captured into a shell variable and interpolated. This avoids shell injection and Python syntax breakage from any single-quote, backslash, or `$` characters that could appear in Dagster error messages.

```bash
  dagster)
    COMPOSE="docker/docker-compose.dev.yml"
    [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

    FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

    echo "--- dagster V1: GET /api/admin/dagster-status returns 200 with dagster_version ---"
    curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
      | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('  V1 OK: dagster_version =', body['dagster_version'])
" || { echo "FAIL: V1 check failed (non-200, connection refused, or assertion error)"; exit 1; }

    echo "--- dagster boundary: no raw httpx→dagster calls outside gateway module ---"
    BAD_CALLS=$(grep -rn --include='*.py' -E 'httpx\.(get|post|AsyncClient)' \
      apps/api/dataplat_api/ \
      | grep -i 'dagster' \
      | grep -v 'apps/api/dataplat_api/dagster/' \
      || true)
    if [[ -n "$BAD_CALLS" ]]; then
      echo "FAIL: raw httpx call to Dagster outside gateway module:"
      echo "$BAD_CALLS"
      exit 1
    fi
    echo "  gateway boundary check: OK"

    echo "--- dagster V2: restart fastapi container; route still returns 200 ---"
    docker compose -f "$COMPOSE" restart fastapi

    # Wait for fastapi healthy (max 30s)
    READY=0
    for i in $(seq 1 30); do
      docker compose -f "$COMPOSE" exec -T fastapi \
        python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=2).getcode()==200 else 1)" \
        2>/dev/null && { READY=1; break; }
      sleep 1
    done
    [[ "$READY" == "1" ]] || { echo "FAIL: fastapi did not become healthy after restart"; exit 1; }

    curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status" \
      | python3 -c "
import json, sys
body = json.load(sys.stdin)
assert 'dagster_version' in body, f'missing dagster_version key: {body}'
assert len(body['dagster_version']) > 0, f'dagster_version is empty: {body}'
print('  V2 OK (post-restart): dagster_version =', body['dagster_version'])
" || { echo "FAIL: V2 check failed after restart"; exit 1; }
    ;;
```

### Updated `all)` block for `verify/checks.sh`

`bash "$0" dagster` is inserted after `bash "$0" buckets`. The full updated block (implementer must match this exactly):

```bash
  all)
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" buckets
    bash "$0" dagster
    ;;
```

---

## 8. Out of Scope

- **JWT / auth on the admin route** — F-008 adds the JWT middleware. The admin route is intentionally unauthenticated this sprint. A `# TODO(F-008): add JWT dependency` comment is added to the route handler.
- **Other DagsterGateway methods** — only `get_dagster_version()` this sprint. `launch_run`, `get_run_status`, `add_dynamic_partition`, `reload_code_location` are future sprints (F-005, F-012, etc.). Stub comments in `gateway.py` document the intended future surface.
- **`packages/api-types/` codegen** — pipeline does not exist. Hard Invariant #6 (`make codegen`) is conditioned on `packages/api-types/` existing; the `contract` layer in `checks.sh` already guards with an early exit. Deferred to the first web-facing sprint.
- **Frontend dagster-status page** — web feature, out of scope.
- **Liveness/readiness probes wired to Dagster reachability** — out of scope; the health endpoint (`/healthz`) intentionally does NOT call Dagster. A separate deep health probe is a future concern.
- **`pytest` unit tests** — NOTE: the sprint description above does not explicitly call for unit tests. However, per CLAUDE.md implementer rules, the implementer MUST write or update tests alongside code. The implementer must add at minimum one pytest test using `httpx.AsyncClient` with an `AsyncMock` or `respx` mock to exercise the route without a live Dagster instance. If `pytest-asyncio` and `respx` are not already in `pyproject.toml` dev deps, they must be added.

---

## 9. Risks and Open Questions

### OQ-1 — Dagster GraphQL `version` field name (RESOLVED)

Confirmed by querying the running instance:

```bash
curl -s -X POST "http://localhost:13000/graphql" \
  -H "Content-Type: application/json" \
  -d '{"query": "{ version "}' 
# Returns: {"data":{"version":"1.11.16"}}
```

The query is `{ version }` (not `{ dagit_version }` or `{ dagster_version }`). The field name is `version` and the value is `"1.11.16"`.

### OQ-2 — `httpx` not a transitive dep of FastAPI (RESOLVED)

FastAPI 0.115.12 requires only `starlette`, `pydantic`, `typing-extensions`. httpx is NOT pulled in transitively (confirmed: `httpx` is absent from `apps/api/uv.lock`). Must be added explicitly as `httpx==0.28.1` (latest stable; confirmed from PyPI).

### OQ-3 — `DAGSTER_GRAPHQL` vs `DAGSTER_GRAPHQL_URL` env var name mismatch (RESOLVED)

`docker-compose.dev.yml` line 220 currently sets `DAGSTER_GRAPHQL`. `docker/.env.example` line 25 currently contains `DAGSTER_GRAPHQL=http://dagster-webserver:3000/graphql` (confirmed by grep). Settings field will be named `DAGSTER_GRAPHQL_URL` for clarity.

The implementer must rename `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` in ALL THREE of the following locations in the same commit:
1. `docker-compose.dev.yml` — the `fastapi` service `environment` block (line 220)
2. `docker/.env.example` — line 25
3. `apps/api/dataplat_api/config.py` — the new Settings field name

Any developer who has the old `DAGSTER_GRAPHQL` name in their local `.env` or shell environment will silently fall back to the `http://dagster-webserver:3000/graphql` default (because `config.py` sets `extra = "ignore"`). The default is correct for compose usage, so this silent fallback is safe. Developers with a custom Dagster URL must manually update their `.env` to use `DAGSTER_GRAPHQL_URL`. A comment in `.env.example` next to the renamed line will note: `# Renamed from DAGSTER_GRAPHQL in sprint S004-F-004`.

### OQ-4 — `app.state.dagster_gateway` vs alternative singleton patterns

Chosen: `app.state` set in a `lifespan` context manager. Rejected alternatives:
- Module-level singleton: not testable (cannot swap mock client in tests without monkey-patching).
- `lru_cache` / `functools.cache`: works for sync deps; awkward for async lifecycle (no clean shutdown).
- Per-request instantiation: opens a new `httpx.AsyncClient` on every request — wasteful and anti-pattern.

The `app.state` approach is consistent with how the design doc's `http_client` is passed to `DagsterGateway.__init__` (§9.2), and matches the FastAPI 0.115.x recommended pattern for shared resources.

### OQ-5 — `DagsterGatewayError` as plain Exception vs `HTTPException` subclass (RESOLVED)

Chosen: plain `Exception` subclass. The gateway module does NOT import FastAPI. The route handler catches `DagsterGatewayError` with a `try/except` and returns a `JSONResponse(status_code=503)`. This keeps the gateway as a pure async I/O component, independently testable and usable from non-HTTP contexts (e.g. a background task or CLI).

### OQ-6 — `fastapi` service `depends_on: dagster-webserver` (RESOLVED)

Adding this dependency means `docker compose up` will wait for dagster-webserver to pass its healthcheck before starting fastapi. This is the correct behavior for F-004: the gateway must be able to call Dagster on first startup. The 30s `start_period` on the dagster-webserver healthcheck already accommodates slow startup.

### OQ-7 — pytest setup for the new route (open — implementer to resolve)

The current `pyproject.toml` dev deps include only `mypy` and `ruff`. To write async route tests the implementer needs `pytest`, `pytest-asyncio`, `httpx` (as the test client), and optionally `respx` for mocking httpx calls. The implementer must:
1. Check if `pytest` is already installed (it likely is, given `checks.sh` backend layer runs `uv run pytest -q`).
2. Add missing test deps to `[dependency-groups] dev` in `pyproject.toml`.
3. Write at least one test: mock `DagsterGateway.get_dagster_version` returning a version string, call `GET /api/admin/dagster-status`, assert 200 and correct JSON body.
4. Write one test for the 503 path: mock raising `DagsterGatewayError`, assert 503 response.

---

## 10. Hard Invariant Alignment

| # | Invariant | Status in this sprint |
|---|---|---|
| 1 | Lineage is mandatory | IRRELEVANT — no Commit objects created |
| 2 | Storage separation + CAS | IRRELEVANT — no blob storage |
| 3 | Schema frozen post-publish | IRRELEVANT — no Silver/Gold schema |
| 4 | LLM calls through gateway | IRRELEVANT — no LLM calls; this sprint IMPLEMENTS the Dagster equivalent of this pattern |
| 5 | Async SQLAlchemy from day one | SATISFIED — no new session code; gateway uses async httpx only |
| 6 | OpenAPI ↔ TS type sync | DEFERRED (confirmed by Mode A iter 1) — `packages/api-types/` does not exist; `contract` layer in `checks.sh` already guards with early exit (line 82). First sprint that ships a TS consumer must establish `make codegen` before adding routes. |

---

## 11. Files Summary

```
apps/api/
  dataplat_api/
    dagster/
      __init__.py              (create — re-export DagsterGateway, DagsterGatewayError)
      gateway.py               (create — DagsterGateway class, DagsterGatewayError, enforcement boundary docstring)
      dependencies.py          (create — get_dagster_gateway() FastAPI dependency)
    schemas/
      __init__.py              (create — package init)
      admin.py                 (create — DagsterStatusResponse Pydantic model)
    routers/
      admin.py                 (create — GET /api/admin/dagster-status)
    main.py                    (modify — add lifespan; include admin_router)
    config.py                  (modify — add DAGSTER_GRAPHQL_URL field)
  pyproject.toml               (modify — add httpx==0.28.1; add test deps if missing)
  uv.lock                      (modify — regenerate)
docker/
  docker-compose.dev.yml       (modify — rename DAGSTER_GRAPHQL→DAGSTER_GRAPHQL_URL; add dagster-webserver dep)
  .env.example                 (modify — rename DAGSTER_GRAPHQL→DAGSTER_GRAPHQL_URL on line 25; add rename comment)
verify/
  checks.sh                    (modify — add dagster) layer with boundary grep + V1 + V2; update all) to include dagster)
```

Total: **13 files** modified or created (uv.lock counts as 1). New routes: 1. New Pydantic models: 1. API schema changes: yes (new route). `make codegen`: deferred (packages/api-types not yet built).
