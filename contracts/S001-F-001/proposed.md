# Sprint Contract S001-F-001 — Docker-Compose Dev Stack

**Status:** PROPOSED (updated with leader answers 2026-05-22)  
**Date drafted:** 2026-05-21  
**Last updated:** 2026-05-22  
**Author:** Leader (Claude)

---

## 1. Feature

> Verbatim from `spec/feature_list.json` F-001:

**docker-compose dev stack (postgres, redis, minio, fastapi, dagster-webserver, dagster-daemon, dagster-worker-cpu, dagster-worker-heavy, frontend) starts cleanly with a single command and all health checks pass**

Verification list (verbatim):
1. Run `docker compose -f docker/docker-compose.dev.yml up -d` exits 0
2. GET http://localhost:8000/healthz returns 200 with `{"status":"ok"}`
3. GET http://localhost:3000/dagster_version returns 200
4. MinIO console is reachable at http://localhost:9001
5. `psql -c 'SELECT 1'` using configured credentials returns 1

---

## 2. Scope IN

The following files and directories will be created in this sprint:

### Docker / compose
- `docker/docker-compose.dev.yml` — full dev stack definition (9 services)
- `docker/.env.example` — template for required secrets (POSTGRES_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, OPENAI_API_KEY placeholder)
- `docker/postgres/init.sql` — runs `CREATE DATABASE platform_dagster;`; the file is mounted into the Postgres container at `/docker-entrypoint-initdb.d/01-init.sql` (read-only) via a compose volume entry; the official `postgres:16` image executes all `*.sql` files in that directory **as the Postgres superuser** during first-boot initialization, so `CREATE DATABASE` succeeds without any `ALTER USER` or privilege grants; the `platform` database is created automatically by the `POSTGRES_DB=platform` env var before this script runs; **this script only executes on first startup with an empty `pg_data` volume** — re-running compose after the volume already exists does not re-execute the script
- `docker/dagster/Dockerfile` — Dagster image: installs `dagster`, `dagster-postgres`, `dagster-webserver`, `dagster-daemon`, and `psycopg2-binary` (required by `dagster-postgres` to connect to Postgres; without it the webserver crashes before the healthcheck can pass); copies `dagster/workspace.yaml`, `dagster/dagster.yaml`, and `dagster/dagster_platform/` to `/app/dagster/`; sets two ENV directives: `ENV PYTHONPATH=/app/dagster` (so that `dagster_platform` is importable when `workspace.yaml` declares `python_module: dagster_platform.definitions` — without this Python cannot find the package and the webserver fails with ModuleNotFoundError) and `ENV DAGSTER_HOME=/app/dagster` (tells Dagster where to find `dagster.yaml` and `workspace.yaml`; after COPY these files live at `/app/dagster/dagster.yaml` and `/app/dagster/workspace.yaml` — both are found automatically via DAGSTER_HOME, which wires the Postgres run/event storage config and the code-location declaration together); build context is the **repo root** (see build context note below)
- `docker/api/Dockerfile` — FastAPI image: installs `fastapi`, `uvicorn[standard]`, `asyncpg`, `sqlalchemy[asyncio]`; copies `apps/api/` relative to repo root; build context is the **repo root** (see build context note below)
- `docker/web/Dockerfile` — minimal `nginx:1.27-alpine` image; copies one static `index.html` placeholder; no Node, no build step, no React

**Build context convention (applies to both `docker/dagster/Dockerfile` and `docker/api/Dockerfile`):** The compose file specifies `context: .` (repo root) and `dockerfile: docker/dagster/Dockerfile` (or `docker/api/Dockerfile`) for each service. All `COPY` instructions inside those Dockerfiles are therefore written relative to the repo root. Examples:
- Dagster Dockerfile: `COPY dagster/ /app/dagster/` (packages are installed inline via `pip install dagster==1.9.10 ...`, not from a requirements.txt)
- FastAPI Dockerfile: `COPY apps/api/ /app/`

The `docker/web/Dockerfile` does NOT need repo-root context because its assets (`index.html`, `nginx.conf`) live alongside the Dockerfile in `docker/web/`; its compose entry can use `build: ./docker/web` with context `./docker/web`.
- `docker/web/index.html` — static placeholder page: "Dataplat frontend placeholder — see F-055 for real implementation"
- `docker/web/nginx.conf` — minimal nginx config listening on port 80; serves `index.html` as the root; includes healthcheck-friendly `location /` block

### Dagster code location

The Dagster webserver uses an **in-process python_module code location** (OQ-2, Option B). A single Dagster image serves webserver, daemon, and workers. `workspace.yaml` points directly at `dagster_platform.definitions` within the image; no gRPC code servers are used in this sprint. Migration to gRPC code-location servers is deferred to the sprint that introduces real Dagster assets (expected F-005 or later).

- `dagster/workspace.yaml` — declares a single `python_module` code location pointing at `dagster_platform.definitions`; no `grpc_server` entries
- `dagster/dagster.yaml` — configures Postgres storage backend (run storage, event log, schedule storage) pointing at the `platform_dagster` database
- `dagster/dagster_platform/__init__.py` — empty `__init__.py`; this is the minimal code location that `workspace.yaml` references; no assets yet (that is F-005+)
- `dagster/dagster_platform/definitions.py` — exports an empty `Definitions()` object; required for Dagster to load the code location without error

### FastAPI skeleton
- `apps/api/pyproject.toml` — project metadata and dependencies (`fastapi`, `uvicorn[standard]`, `asyncpg`, `sqlalchemy[asyncio]`)
- `apps/api/dataplat_api/__init__.py`
- `apps/api/dataplat_api/main.py` — creates the FastAPI `app` instance, mounts router, exposes `GET /healthz` returning `{"status": "ok"}`
- `apps/api/dataplat_api/routers/__init__.py`
- `apps/api/dataplat_api/routers/health.py` — the `/healthz` route; no DB query, no auth, returns static `{"status": "ok"}`

---

## 3. Scope OUT

This sprint explicitly will NOT produce:

- **Business DB migrations** — no `alembic/` setup, no `users`, `source`, `source_collection`, `document_variant`, `operator`, `recipe`, `dataset`, `run` tables. That is F-002.
- **MinIO bucket auto-creation** — no init container or mc script to create `sources`, `documents`, `documents_vlm`, `lance`, `datasets` buckets. That is F-003.
- **Dagster GraphQL gateway** — no `DagsterGateway` class, no `GET /api/admin/dagster-status` endpoint. That is F-004.
- **Hello-world Dagster job** — no Dagster assets, jobs, or ops. That is F-005.
- **Frontend React/Vite application** — no `apps/web/`, no React code, no TypeScript, no Vite config, no Node.js build step. The `frontend` service in this sprint is an `nginx:1.27-alpine` static placeholder only. The real frontend is F-055.
- **Auth** — no JWT, no user seed, no `POST /api/auth/token`. That is F-007.
- **packages/api-types codegen and Makefile** — `make codegen` and its Makefile target are deferred to the sprint that first introduces a real API schema (expected F-007 or F-009). No `Makefile` is created this sprint.
- **verify/checks.sh** — that is F-006.
- **Any business logic** — this sprint is infrastructure-only.
- **MinIO bucket creation** — no init container or mc script for bucket creation. That is F-003.
- **Dagster sensors, schedules, or assets** — see F-005+.
- **Dagster gRPC code-location servers** — the `dagster-worker-cpu` and `dagster-worker-heavy` containers that serve code over gRPC are deferred to the sprint that introduces real Dagster assets (F-005). In this sprint all Dagster processes share one image and the webserver loads the code location in-process.

---

## 4. Service Inventory

| # | Service | Image / Build | Internal port | Host port | depends_on (with condition) | Healthcheck | Verification line satisfied |
|---|---|---|---|---|---|---|---|
| 1 | `postgres` | `postgres:16` | 5432 | 5432 | — | `pg_isready -U app` (interval 5s, retries 5) | V5 (`psql -c 'SELECT 1'`) |
| 2 | `redis` | `redis:7-alpine` | 6379 | 6379 | — | `redis-cli ping` (interval 5s, retries 5) | — (indirect; needed by fastapi) |
| 3 | `minio` | `minio/minio:RELEASE.2025-04-22T22-12-26Z` | 9000 (API), 9001 (console) | 9000, 9001 | — | _none this sprint_ — MinIO image does not ship `wget` or `curl`. See note below. | V4 (MinIO console at :9001) |
| 4 | `dagster-webserver` | `context: .`, `dockerfile: docker/dagster/Dockerfile` | 3000 | 3000 | `postgres: service_healthy` | `wget -q -O- http://localhost:3000/dagster_version > /dev/null \|\| exit 1` (interval 10s, retries 6, start_period 30s) | V3 (`GET localhost:3000/dagster_version`) |
| 5 | `dagster-daemon` | `context: .`, `dockerfile: docker/dagster/Dockerfile` | — (no HTTP) | — | `postgres: service_healthy`, `dagster-webserver: service_healthy` | — | — (background process) |
| 6 | `dagster-worker-cpu` | `context: .`, `dockerfile: docker/dagster/Dockerfile` | — (no listen port this sprint) | — | `postgres: service_healthy`, `dagster-webserver: service_healthy` | — | — |
| 7 | `dagster-worker-heavy` | `context: .`, `dockerfile: docker/dagster/Dockerfile` | — (no listen port this sprint) | — | `postgres: service_healthy`, `dagster-webserver: service_healthy` | — | — |
| 8 | `fastapi` | `context: .`, `dockerfile: docker/api/Dockerfile` | 8000 | 8000 | `postgres: service_healthy` | `wget -q -O- http://localhost:8000/healthz > /dev/null \|\| exit 1` (interval 5s, retries 5) | V2 (`GET localhost:8000/healthz`) |
| 9 | `frontend` | `./docker/web` (`nginx:1.27-alpine` base) | 80 | **5173** | `fastapi: service_healthy` | `wget -q -O- http://localhost/ > /dev/null \|\| exit 1` (interval 5s, retries 5) | V1 (all services start cleanly; frontend healthcheck must pass) |

Notes:
- **Healthchecks use `wget`, not `curl`.** `curl` is not present in `nginx:1.27-alpine` (Alpine BusyBox) or in `python:3.12-slim` (Debian minimal) by default. `wget` is available in both images without any additional installation. The compose `healthcheck` directives for `dagster-webserver`, `fastapi`, and `frontend` use `wget -q -O- <url> > /dev/null || exit 1`. No Dockerfile changes are needed for this — it is purely a compose-file decision.
- **MinIO has no compose healthcheck this sprint.** The `minio/minio` image does not ship `wget` or `curl` by default, and we deliberately avoid adding either to the upstream image. No service declares `depends_on: minio: service_healthy`, so omitting the healthcheck does not break the startup chain. V1's "all services running or healthy" condition is satisfied because Docker reports the MinIO container as `running` (it has no healthcheck, so the `unhealthy` state is impossible). MinIO liveness is verified externally by V4 (host-side `curl http://localhost:9001`). A proper healthcheck using `mc ready local` or the built-in MinIO healthcheck endpoint will be added in a future sprint if/when something depends on MinIO health.
- **Workers this sprint use `command: sleep infinity`.** `dagster-worker-cpu` and `dagster-worker-heavy` do NOT run `dagster api grpc` in this sprint because the grpc command requires a module or workspace target, and without it the container crashes and exits — which would cause `docker compose ps` to show `exited`, failing V1. The placeholder `sleep infinity` keeps the containers alive. When real assets land (F-005), the command changes to `dagster api grpc -h 0.0.0.0 -p 4000 -m dagster_platform.definitions` (cpu) and `-p 4001` (heavy), and `workspace.yaml` is updated to reference them as gRPC code-location servers. The ports 4000/4001 are reserved in the inventory for this future use but are NOT exposed or listened on this sprint.
- `fastapi` does NOT depend on `dagster-webserver` this sprint. The DagsterGateway (F-004) is out of scope, so FastAPI has no startup dependency on Dagster. It only waits for `postgres: service_healthy`. This is intentional and reduces the startup dependency chain.
- `dagster-daemon` has no inbound HTTP port; it connects out to Postgres and the webserver.
- The `frontend` service uses `nginx:1.27-alpine` serving a static placeholder HTML at container port 80, mapped to host port 5173. No React, no Node, no Vite. The real frontend (F-055) will replace this entirely.
- Host port 5173 for the frontend is confirmed per leader answer to OQ-3; it does not conflict with Dagster on :3000.

---

## 5. Port Allocation

| Service | Internal listen | Host binding | Notes |
|---|---|---|---|
| postgres | 5432 | 5432 | Standard; the psql verification requires host access |
| redis | 6379 | 6379 (optional) | Only needs host exposure if developers want to `redis-cli` directly; can be internal-only |
| minio API | 9000 | 9000 | Required for SDK access from host during testing |
| minio console | 9001 | 9001 | V4 verifies this directly |
| dagster-webserver | 3000 | **3000** | Dagster webserver only — confirmed, no conflict with frontend |
| dagster-worker-cpu (reserved) | 4000 | — (not exposed) | Reserved for F-005 gRPC server; this sprint the container runs `sleep infinity`, not `dagster api grpc` |
| dagster-worker-heavy (reserved) | 4001 | — (not exposed) | Reserved for F-005 gRPC server; this sprint the container runs `sleep infinity`, not `dagster api grpc` |
| fastapi | 8000 | 8000 | V2 verifies this directly |
| frontend (nginx placeholder) | 80 | **5173** | nginx:1.27-alpine static placeholder; real frontend (F-055) will use this same port |

### Port :3000 — confirmed Dagster webserver only (OQ-3 resolved)

Verification item V3 (`GET http://localhost:3000/dagster_version`) targets the Dagster webserver. This is confirmed intentional. The design doc (§11.1) shows `dagster-webserver -h 0.0.0.0 -p 3000` and `DAGSTER_GRAPHQL: http://dagster-webserver:3000/graphql`.

**Port assignments are now finalized:**
- `:3000` — Dagster webserver (GraphQL API + built-in HTTP endpoints like `/dagster_version`)
- `:5173` — frontend (nginx placeholder this sprint; Vite dev server when F-055 lands)
- No conflict exists. The docker-compose file will include a comment on the `dagster-webserver` service confirming this assignment.

---

## 6. Hard Invariant Alignment

| # | Invariant | Status in this sprint |
|---|---|---|
| 1 | Lineage is mandatory (parents[], processor identity, config hash, input refs) | IRRELEVANT — no Commit objects, no Dagster assets, no data processing this sprint |
| 2 | Storage separation + CAS (metadata in Postgres, blobs in MinIO by sha256) | IRRELEVANT — no blob writes, no metadata inserts this sprint |
| 3 | Schema frozen post-publish | IRRELEVANT — no schema publications this sprint |
| 4 | LLM calls go through the gateway | IRRELEVANT — no LLM calls this sprint |
| 5 | Async SQLAlchemy from day one | PARTIALLY RELEVANT — the FastAPI skeleton does not connect to Postgres yet (F-002 adds Alembic and the session factory), so no DB code exists to violate this. The `apps/api/` skeleton must be structured so that when DB code is added (F-002+), it uses `AsyncSession` only. The `pyproject.toml` must declare `sqlalchemy[asyncio]` and `asyncpg` as dependencies. No `session.query()` style code may be written now or later. |
| 6 | OpenAPI ↔ TS type sync | IRRELEVANT THIS SPRINT — `/healthz` is a trivial response with no request schema worth generating types for. No `packages/api-types/` diff is expected and no `Makefile` is created this sprint. The `make codegen` mechanism will be introduced in the first sprint that adds a real API schema (expected F-007 or F-009). |

---

## 7. Verification Mapping

For each bullet in F-001's `verification` array:

### V1: `docker compose -f docker/docker-compose.dev.yml up -d` exits 0

**Command:** `docker compose -f docker/docker-compose.dev.yml up -d`  
**Pass condition:** Exit code 0. All containers reach their target state (running or healthy) within a reasonable timeout (suggest 120s). The verifier should also run:
```bash
docker compose -f docker/docker-compose.dev.yml ps
```
and confirm all 9 services show `running` or `healthy`, none show `exited` or `restarting`.

**Frontend healthcheck is included in V1.** The `frontend` service has a compose healthcheck (`wget -q -O- http://localhost/ > /dev/null || exit 1`). V1 passes only when this healthcheck also passes — meaning the nginx placeholder is up and serving HTTP 200 on its internal port 80. The verifier may additionally run from the host:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/
# expect: 200
```
Note: the host-side verifier command above uses `curl` (available on the developer's host machine); the in-container compose healthcheck uses `wget` (available inside `nginx:1.27-alpine`).

**What the implementer must ensure:** All Dockerfiles build successfully; all `depends_on` chains are satisfiable; `dagster-webserver` loads the code location in-process without error; the nginx placeholder container starts and passes its healthcheck before compose reports the stack healthy.

---

### V2: `GET http://localhost:8000/healthz` returns 200 with `{"status":"ok"}`

**Command:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/healthz
# expect: 200

curl -s http://localhost:8000/healthz
# expect: {"status":"ok"}
```
**Pass condition:** HTTP 200 and response body is exactly `{"status":"ok"}` (or parses to `{"status": "ok"}` with optional whitespace).

**What the implementer must ensure:** `GET /healthz` route exists in `apps/api/dataplat_api/routers/health.py`, returns `{"status": "ok"}`, requires no auth, and is mounted in `main.py`.

---

### V3: `GET http://localhost:3000/dagster_version` returns 200

**Command:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/dagster_version
# expect: 200
```
**Pass condition:** HTTP 200 with a non-empty body containing the string `1.9.10`. Confirmed by leader: `/dagster_version` is a valid built-in endpoint in Dagster 1.9.10 (present since pre-1.0). The response is `Content-Type: application/json` with body `"1.9.10"` — a JSON-encoded string, not an object. The verifier command (run from the host):
```bash
curl -s http://localhost:3000/dagster_version | grep -q "1.9.10" && echo "PASS" || echo "FAIL"
```
The implementer does NOT need to investigate the endpoint path independently; it is confirmed correct.

**What the implementer must ensure:** `dagster-webserver` container starts successfully, passes its compose healthcheck (`wget -q -O- http://localhost:3000/dagster_version > /dev/null || exit 1`), connects to Postgres (`platform_dagster` database), loads the empty `Definitions()` code location in-process from `workspace.yaml`, and binds to `:3000` on the host.

---

### V4: MinIO console is reachable at `http://localhost:9001`

**Command:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:9001
# expect: 200 (or 302 redirect to /login — either counts as "reachable")
```
**Pass condition:** HTTP response received (200 or 302); not a connection refused error.

**What the implementer must ensure:** MinIO container runs with `command: server /data --console-address ":9001"`, and port 9001 is mapped to the host in the compose file.

---

### V5: `psql -c 'SELECT 1'` using configured credentials returns 1

**Command:**
```bash
PGPASSWORD=${POSTGRES_PASSWORD} psql -h localhost -U app -d platform -c 'SELECT 1'
# expect: output includes "1 row" and exit code 0
```
**Pass condition:** Exit code 0, output contains `1` row result.

**What the implementer must ensure:** Postgres container runs with `POSTGRES_USER=app`, `POSTGRES_DB=platform`, `POSTGRES_PASSWORD` from `.env`; port 5432 is mapped to the host. The compose file mounts `docker/postgres/init.sql` as `./docker/postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro`; the official `postgres:16` image runs this file as the Postgres superuser on first boot, executing `CREATE DATABASE platform_dagster;` successfully — no extra `ALTER USER` or CREATEDB grant is required. The `platform` database is auto-created by `POSTGRES_DB` before the init script runs. **Important:** The init script only runs when `pg_data` is an empty/new named volume. If the volume already exists from a previous run, delete it with `docker compose down -v` before testing a fresh init.

---

## 8. Risks, Open Questions, and Resolved Decisions

### OQ-1 — RESOLVED: Frontend nginx placeholder is IN scope

**Decision (2026-05-22):** Include a minimal `frontend` service in this sprint. Use `nginx:1.27-alpine` serving a static placeholder HTML on host port 5173 / container port 80. The page reads: "Dataplat frontend placeholder — see F-055 for real implementation". No Node, no React, no build step.

**Impact:** Adds `docker/web/Dockerfile`, `docker/web/index.html`, `docker/web/nginx.conf` to scope. The `frontend` healthcheck (`wget -q -O- http://localhost/ > /dev/null || exit 1`) must pass as part of V1 (all services start cleanly).

---

### OQ-2 — RESOLVED: In-process python_module code location (Option B)

**Decision (2026-05-22):** `workspace.yaml` uses `python_module: dagster_platform.definitions`. The Dagster webserver loads the code location in-process from within the single shared Dagster image. No `grpc_server` entries in `workspace.yaml` this sprint.

**Impact:** `dagster-worker-cpu` and `dagster-worker-heavy` containers exist in the compose file for structural completeness. **This sprint their command is `sleep infinity`** — this keeps the containers alive and showing `running` in `docker compose ps` without requiring a valid gRPC target. The webserver does NOT connect to them; it loads the code location in-process.

**Deferred migration note (MUST track):** When real Dagster assets are introduced (expected F-005 or the sprint that adds the first asset), the following changes are required:
1. Worker commands change from `sleep infinity` to `dagster api grpc -h 0.0.0.0 -p 4000 -m dagster_platform.definitions` (cpu worker) and `-p 4001` (heavy worker).
2. `dagster/workspace.yaml` is updated from `python_module` to `grpc_server` entries pointing at `dagster-worker-cpu:4000` and `dagster-worker-heavy:4001`.
3. `depends_on` on `dagster-webserver` is updated to add `dagster-worker-cpu: service_healthy` and `dagster-worker-heavy: service_healthy`.
4. Workers need healthchecks added (e.g., `dagster api grpc-health-check -p 4000`).
The leader must create a follow-up task for this migration before F-005 lands.

---

### OQ-3 — RESOLVED: Port :3000 is Dagster, :5173 is frontend

**Decision (2026-05-22):** Confirmed. `:3000` is exclusively the Dagster webserver. `:5173` is the frontend (nginx placeholder now; Vite dev server when F-055 lands). No conflict. The compose file will include inline comments making this explicit.

---

### OQ-4 — RESOLVED: Two-database strategy

**Decision:** Two separate databases within the same Postgres instance: `platform` (business tables, F-002+) and `platform_dagster` (Dagster run/event storage). `docker/postgres/init.sql` contains `CREATE DATABASE platform_dagster;` and is mounted at `/docker-entrypoint-initdb.d/01-init.sql:ro` in the compose file. The official `postgres:16` image runs all `*.sql` files in that directory as the superuser on first boot — no CREATEDB privilege grant is needed for `app`. The `platform` database is created automatically by `POSTGRES_DB=platform` before the init script runs. `dagster.yaml` points at `platform_dagster`. The FastAPI `DATABASE_URL` points at `platform`. This matches the design doc exactly.

---

### OQ-5 — RESOLVED: Named volumes + optional bind mount for API reload

**Decision:** Named volumes (`pg_data`, `minio_data`) for Postgres and MinIO data. For the FastAPI container, a bind mount of `apps/api/` is included in the dev compose file alongside `uvicorn --reload` so developers get live code reload without rebuilding. This is a dev-only convenience and does not affect verification.

---

### OQ-6 (standing): Compose default bridge network

No custom network. Docker Compose default bridge network (`dataplat_default` or similar) is sufficient. All services reach each other by service name. No action required.

---

### OQ-7 (standing): `.env` file handling

`docker/.env.example` is committed. Developer copies to `docker/.env` and fills in values before running compose. The compose file will set `env_file: .env.example` as a fallback with safe defaults for `POSTGRES_PASSWORD` (e.g., `devpassword`) and `MINIO_ROOT_PASSWORD` (e.g., `devpassword`) so that running with the example file directly produces a working dev stack without editing. `OPENAI_API_KEY` defaults to an empty string; it is not used in F-001.

---

### OQ-8 (standing): depends_on conditions — fully specified

All `depends_on` relationships in the compose file use the long-form `condition:` syntax (not the bare list form). The full specification:

- `dagster-webserver`: `depends_on: { postgres: { condition: service_healthy } }`
- `dagster-daemon`: `depends_on: { postgres: { condition: service_healthy }, dagster-webserver: { condition: service_healthy } }`
- `dagster-worker-cpu`: `depends_on: { postgres: { condition: service_healthy }, dagster-webserver: { condition: service_healthy } }`
- `dagster-worker-heavy`: `depends_on: { postgres: { condition: service_healthy }, dagster-webserver: { condition: service_healthy } }`
- `fastapi`: `depends_on: { postgres: { condition: service_healthy } }` — does NOT wait for `dagster-webserver`; DagsterGateway is F-004 and FastAPI has no startup dependency on Dagster in this sprint
- `frontend`: `depends_on: { fastapi: { condition: service_healthy } }`

This requires `dagster-webserver` to have a Docker-level `healthcheck:` in the compose file (not just a note). The healthcheck is: `wget -q -O- http://localhost:3000/dagster_version > /dev/null || exit 1`, interval 10s, timeout 5s, retries 6, start_period 30s. The 30s start_period accounts for Dagster's longer boot time while it connects to Postgres and loads the code location.

---

## 8a. Version Pins

These pins must be used verbatim in all Dockerfiles and the compose file. They ensure reproducibility of the `/dagster_version` endpoint and all healthchecks.

| Component | Pinned version | Notes |
|---|---|---|
| Python | **3.12** | Base image for both Dagster and FastAPI Dockerfiles (`python:3.12-slim`) |
| Postgres | **16** | `postgres:16` image tag |
| Redis | **7-alpine** | `redis:7-alpine` image tag |
| MinIO server | **RELEASE.2025-04-22T22-12-26Z** | `minio/minio:RELEASE.2025-04-22T22-12-26Z` — implementer must verify this tag exists on Docker Hub before building; if not found, use the most recent available `RELEASE.*` tag and note the substitution explicitly in a comment in the Dockerfile |
| MinIO client (mc) | **RELEASE.2025-04-08T15-39-49Z** | `minio/mc:RELEASE.2025-04-08T15-39-49Z` — used in F-003 for bucket init; not used this sprint but pinned here for consistency; same fallback rule applies |
| Dagster | **1.9.10** | `dagster==1.9.10`, `dagster-webserver==1.9.10`, `dagster-postgres==1.9.10` — as of 2026-05-22 this is the latest stable 1.9.x patch release |
| psycopg2-binary | **2.9.10** | `psycopg2-binary==2.9.10` — Dagster-only dependency; required by `dagster-postgres` / SQLAlchemy to connect to Postgres; NOT added to FastAPI image (FastAPI uses `asyncpg`) |
| FastAPI | **0.115.12** | `fastapi==0.115.12` — latest stable as of 2026-05-22 |
| uvicorn | **0.34.2** | `uvicorn[standard]==0.34.2` |
| nginx | **1.27-alpine** | `nginx:1.27-alpine` — pinned; consistent with §2 Scope IN |
| SQLAlchemy | **2.0.41** | `sqlalchemy[asyncio]==2.0.41` — must be 2.x for async support |
| asyncpg | **0.30.0** | `asyncpg==0.30.0` — FastAPI-only; not installed in Dagster image |

**Note on Dagster 1.9.10 and `/dagster_version`:** Confirmed by leader. The `/dagster_version` endpoint has existed since pre-1.0 Dagster and is present in 1.9.10. It returns the version string as a JSON-encoded string body with `Content-Type: application/json` — the body is `"1.9.10"` (a JSON string, not an object). The verifier accepts HTTP 200 with any non-empty body containing `1.9.10`. The implementer does not need to verify this independently; it is confirmed.

---

## 9. Out-of-Band Items Requiring Human Input Before Implementation

All previously open questions have been resolved (see §8 above). No outstanding blockers.

The implementer may begin work immediately against this contract. The only standing reminder is:

- **Post-F-005 follow-up:** When the first real Dagster asset lands, migrate `dagster/workspace.yaml` from `python_module` (in-process) to `grpc_server` entries pointing at the worker containers. Track this as a contract item in the F-005 sprint.

---

## Appendix: Files-to-Create Summary

All open questions resolved. This is the final file list for the implementer.

```
contracts/S001-F-001/proposed.md          ← this file (contract only, not implementation)
docker/
  .env.example                            ← committed; developer copies to .env
  docker-compose.dev.yml                  ← full 9-service dev stack; uses context: . for
                                             dagster and api builds; context: ./docker/web
                                             for frontend
  postgres/
    init.sql                              ← CREATE DATABASE platform_dagster;
                                             mounted at /docker-entrypoint-initdb.d/01-init.sql
  dagster/
    Dockerfile                            ← python:3.12-slim; context: repo root;
                                             pip install dagster==1.9.10 dagster-webserver==1.9.10
                                             dagster-postgres==1.9.10 psycopg2-binary==2.9.10;
                                             COPY dagster/ /app/dagster/;
                                             ENV PYTHONPATH=/app/dagster
                                             (makes dagster_platform importable for workspace.yaml);
                                             ENV DAGSTER_HOME=/app/dagster
                                             (tells Dagster where to find dagster.yaml + workspace.yaml;
                                             wires Postgres storage config and code-location together)
  api/
    Dockerfile                            ← python:3.12-slim; context: repo root;
                                             pip install fastapi==0.115.12 uvicorn[standard]==0.34.2
                                             sqlalchemy[asyncio]==2.0.41 asyncpg==0.30.0;
                                             COPY apps/api/ /app/
  web/
    Dockerfile                            ← FROM nginx:1.27-alpine; context: ./docker/web;
                                             COPY index.html /usr/share/nginx/html/
                                             COPY nginx.conf /etc/nginx/conf.d/default.conf
    nginx.conf                            ← minimal config; root serves index.html on port 80
    index.html                            ← "Dataplat frontend placeholder — see F-055"
dagster/
  workspace.yaml                          ← python_module: dagster_platform.definitions (in-process)
  dagster.yaml                            ← postgres run/event/schedule storage; platform_dagster DB
  dagster_platform/
    __init__.py
    definitions.py                        ← empty Definitions()
apps/
  api/
    pyproject.toml                        ← declares fastapi, uvicorn, sqlalchemy[asyncio], asyncpg
    dataplat_api/
      __init__.py
      main.py                             ← FastAPI app, mounts health router
      routers/
        __init__.py
        health.py                         ← GET /healthz → {"status": "ok"}
```

Total: **18 files** created in this sprint. No Makefile. No `packages/` directory. No `apps/web/` directory.

**Worker command reminder:** `dagster-worker-cpu` and `dagster-worker-heavy` use `command: sleep infinity` in `docker-compose.dev.yml` this sprint. See §4 Notes and §8 OQ-2 for the full migration plan when F-005 lands.
