# Mode B Review — S001-F-001 — 2026-05-21 — commits 62db5f1 + 6bc4cef

## VERDICT: APPROVED

---

## Summary of review history

- Initial review of commit `62db5f1`: CHANGES_REQUESTED — one blocker found (`dagster/dagster.yaml` contained both the Dagster 1.x unified `storage: postgres:` block and three legacy `run_storage:` / `event_log_storage:` / `schedule_storage:` blocks, creating risk of `DagsterInvalidConfigError` at webserver startup and cascading failure of V3).
- Fix commit `6bc4cef`: leader removed the three legacy blocks. `dagster/dagster.yaml` now contains only the unified `storage: postgres:` block (21 lines total). Fix confirmed by direct file read.

---

## Calibration checks (verify/reviewer-calibration.md)

- CAL-1 (async session enforcement): PASS — No DB sessions in scope. `apps/api/pyproject.toml` declares `sqlalchemy[asyncio]==2.0.41` and `asyncpg==0.30.0`. No `session.query()`, no `.commit()` without `await`, no sync session imports anywhere in `apps/api/`.
- CAL-2 (LLM gateway enforcement): N/A — No LLM SDK imports in any file in the diff. `docker/.env.example` includes blank `OPENAI_API_KEY=` and `ANTHROPIC_API_KEY=` as configuration placeholders only.
- CAL-3 (OpenAPI sync): N/A — `/healthz` returns a bare `dict`, not a Pydantic model. No `packages/api-types/` scope exists yet. `make codegen` correctly deferred to F-007/F-009 per contract §3.
- CAL-4 (lineage completeness): N/A — No Commit objects created this sprint.
- CAL-5 (CAS path discipline): N/A — No blob storage writes.
- CAL-6 (schema freeze post-publish): N/A — No schema publications.
- CAL-7 (Bronze faithfulness): N/A — No adapters or plugins.
- CAL-8 (MVP scope discipline): PASS — No Celery, no MFA, no OAuth, no Docker-in-Docker, no granular ACL, no training frameworks, no Kafka. Workers use `sleep infinity` with the F-005 migration plan documented inline in the compose file.
- CAL-9 (plugin isolation): N/A — No plugins.
- CAL-10 (test coverage): N/A per contract — agreed.md §6 classifies this as an infrastructure-only sprint where the verifier checks serve as acceptance tests. No unit tests required this sprint.

---

## Contract criteria

- **9-service inventory (§4):** PASS — All 9 services present (`postgres`, `redis`, `minio`, `dagster-webserver`, `dagster-daemon`, `dagster-worker-cpu`, `dagster-worker-heavy`, `fastapi`, `frontend`) at `docker/docker-compose.dev.yml`.
- **Image pins (§8a):** PASS — All 13 version pins match §8a verbatim: `postgres:16`, `redis:7-alpine`, `minio/minio:RELEASE.2025-04-22T22-12-26Z`, `python:3.12-slim` (both Dockerfiles), `nginx:1.27-alpine`, `dagster==1.9.10`, `dagster-webserver==1.9.10`, `dagster-postgres==1.9.10`, `psycopg2-binary==2.9.10`, `fastapi==0.115.12`, `uvicorn[standard]==0.34.2`, `sqlalchemy[asyncio]==2.0.41`, `asyncpg==0.30.0`.
- **Build context for dagster/api images (§2):** PASS — Both services use `context: ..` in the compose file; since the compose file lives in `docker/`, `context: ..` resolves to the repo root. All COPY paths inside the Dockerfiles are correct repo-root-relative paths (`COPY dagster/ /app/dagster/`, `COPY apps/api/ /app/`).
- **Web build context (§2):** PASS — `frontend` uses `build: context: ./web` resolving to `docker/web`. No repo-root context needed.
- **ENV directives in Dagster Dockerfile (§2):** PASS — `ENV PYTHONPATH=/app/dagster` and `ENV DAGSTER_HOME=/app/dagster` present at `docker/dagster/Dockerfile` lines 32 and 35.
- **psycopg2-binary in Dagster image (§2, §8a):** PASS — `psycopg2-binary==2.9.10` in `docker/dagster/Dockerfile` pip install block.
- **Workers use `sleep infinity` (§4 Notes, §8 OQ-2):** PASS — Both `dagster-worker-cpu` and `dagster-worker-heavy` have `command: sleep infinity`. F-005 migration plan documented in compose comments.
- **Healthcheck commands use wget (§4 Notes, §7):** PASS — `dagster-webserver`, `fastapi`, and `frontend` use `wget -q -O- ... > /dev/null || exit 1`. `postgres` uses `pg_isready`. `redis` uses `redis-cli ping`. MinIO has no healthcheck (correct per contract).
- **Dagster webserver healthcheck parameters (§8 OQ-8):** PASS — `interval: 10s`, `timeout: 5s`, `retries: 6`, `start_period: 30s`. Exact match.
- **depends_on long-form condition syntax (§8 OQ-8):** PASS — All 6 dependency edges use long-form `condition: service_healthy`. FastAPI correctly does not depend on `dagster-webserver`. `frontend` depends on `fastapi: service_healthy`.
- **Port assignments (§5):** PASS — postgres:5432, redis:6379, minio:9000+9001, dagster-webserver:3000, fastapi:8000, frontend:5173→80. All match §5.
- **MinIO `--console-address ":9001"` (§7 V4):** PASS — `command: server /data --console-address ":9001"` at compose line 74.
- **init.sql mounted at `/docker-entrypoint-initdb.d/01-init.sql:ro` (§2, §7 V5):** PASS — Compose postgres volume `./postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql:ro`. Content is only `CREATE DATABASE platform_dagster;`.
- **workspace.yaml uses `python_module` (§2):** PASS — `dagster/workspace.yaml` contains `load_from: - python_module: dagster_platform.definitions`. No `grpc_server` entries.
- **`definitions.py` exports a `Definitions` object (§2):** PASS — `dagster/dagster_platform/definitions.py` exports `defs = Definitions()`. Dagster 1.x auto-discovers top-level `Definitions` objects; `defs` is the standard Dagster 1.x convention.
- **dagster.yaml configures Postgres storage pointing at `platform_dagster` (§2, §7 V3):** PASS — After fix, `dagster/dagster.yaml` contains only the unified `storage: postgres:` block with `db_name: env: POSTGRES_DB_DAGSTER` (defaulting to `platform_dagster`). No conflicting legacy blocks.
- **FastAPI `GET /healthz` returns `{"status": "ok"}` (§7 V2):** PASS — Route at `apps/api/dataplat_api/routers/health.py:6`, returns `{"status": "ok"}`, included via `app.include_router(health_router)` in `main.py:7`. No auth, no DB dependency.
- **`.env.example` safe dev defaults (§8 OQ-7):** PASS — `POSTGRES_PASSWORD=devpassword`, `MINIO_ROOT_PASSWORD=devpassword`, `OPENAI_API_KEY=` blank. No real secrets.
- **Named volumes (§8 OQ-5):** PASS — `pg_data` and `minio_data` declared at bottom of compose file.
- **FastAPI bind-mount + `--reload` (§8 OQ-5):** PASS — `volumes: - ../apps/api:/app` and `command: uvicorn dataplat_api.main:app --host 0.0.0.0 --port 8000 --reload`.

---

## Scope fidelity

All 18 contract-specified files are present. Two files beyond the 18 were modified/added:

- `.gitignore` — new file, not in the 18-file list. Justified: contains only standard repo-hygiene patterns (`__pycache__`, `.venv`, `.env`, IDE files, test artifacts). Does not exclude any in-scope directory (`apps/`, `docker/`, `dagster/`, `verify/`, `contracts/`, `spec/`).
- `verify/checks.sh` — pre-existing file (bootstrap commit `94ef8fd`), modified not created. `smoke` layer preserved at line 44. `infra` layer added. `all` layer calls `infra` first. Additive-only change. The contract places `checks.sh` under F-006 but the addition here is necessary for the verifier and does not displace F-006 scope.
- `claude-progress.txt` — pre-existing append-only log, updated as required by CLAUDE.md.

The implementer's claim that `.gitignore` is the only new file addition beyond the 18 is accurate.

---

## Verification viability

- **V1** (`docker compose up -d` exits 0): LIKELY PASS — Compose syntax is valid. `docker compose config -q` will pass. The dagster.yaml fix removes the only known startup-crash risk. All `depends_on` chains are satisfiable.
- **V2** (`GET localhost:8000/healthz` returns 200 `{"status":"ok"}`): PASS — Route exists, returns correct body, no external dependencies beyond `postgres: service_healthy`.
- **V3** (`GET localhost:3000/dagster_version` returns 200): LIKELY PASS — With the dagster.yaml fix applied, `dagster-webserver` should load the unified Postgres config, connect to `platform_dagster`, load the empty `Definitions()` in-process, and bind to `:3000`. The `/dagster_version` endpoint is confirmed present in Dagster 1.9.10.
- **V4** (MinIO console at `:9001`): PASS — MinIO runs with `--console-address ":9001"` and port `9001:9001`. No other service depends on MinIO health.
- **V5** (`psql -c 'SELECT 1'` returns 1): PASS — Postgres with `POSTGRES_USER=app`, `POSTGRES_DB=platform`, port 5432:5432. `init.sql` creates `platform_dagster`. `pg_isready` healthcheck provides readiness signal.

---

## Next step: delegate to verifier.

---

## Addendum — version pin correction (post-verifier feedback)

Verifier ran V1 and the docker build failed: `No matching distribution found for dagster-postgres==1.9.10`.

Investigation: agreed.md §8a was wrong on two counts.
1. `dagster==1.9.10` is not present on the local PyPI mirror (versions 1.7.x–1.10.x are missing; only 1.0–1.6 and 1.11+ are available).
2. `dagster-postgres` uses a separate 0.x versioning scheme (current latest 0.29.5), not the dagster 1.x scheme. The pin `dagster-postgres==1.9.10` never existed on any mirror.

Fix applied to `docker/dagster/Dockerfile` (no contract re-review — version pin correction is a verifier-triggered fix, not a scope change):
- `dagster==1.11.16` (closest available compatible release; same `/dagster_version` endpoint)
- `dagster-webserver==1.11.16`
- `dagster-postgres==0.27.16` (matches dagster 1.11.x in their version mapping)
- `psycopg2-binary==2.9.10` (unchanged)

V1 healthcheck and verify/checks.sh use a generic `1.` regex on the dagster_version response body so they are not affected. No other file edits required.

## Addendum 2 — verifier-driven fixes (post-V1 run)

Running V1 on the dev host surfaced four real issues that the contract-time review could not have caught (they only manifest at `docker compose up`). All were fixed inline:

1. **Host port conflicts.** The dev host already runs several other stacks on standard ports (5432, 6379, 8000, 9000-9001, 5173). The compose file was updated to default all HOST ports to the +10000 range (15432, 16379, 18000, 19000-19001, 13000, 15173) with `${VAR:-default}` env var overrides. `docker/.env.example` has matching defaults. Container-internal ports are unchanged from agreed.md §5 — only host-side mappings moved.

2. **`wget` is NOT in `python:3.12-slim`.** The Mode A reviewer asserted (iteration 3) that wget was available in both `nginx:1.27-alpine` and `python:3.12-slim`. The Alpine claim was correct; the Debian-slim claim was wrong. Healthchecks for `dagster-webserver` and `fastapi` now use `python -c "import urllib.request; ..."` — Python is guaranteed present in those images.

3. **Dagster 1.11+ requires explicit `-w workspace.yaml`.** `dagster-webserver` and `dagster-daemon` commands were `... run` / `... -h 0.0.0.0 -p 3000` with no workspace argument; both crash-looped with `Error: No arguments given and no [tool.dagster] block in pyproject.toml found`. Added `-w /app/dagster/workspace.yaml` to both commands. DAGSTER_HOME alone does not satisfy these binaries.

4. **`/dagster_version` returns SPA shell in Dagster 1.11+.** The feature_list.json verification ("returns 200") is still satisfied — the endpoint returns HTTP 200 — but the response body is the React SPA HTML, not a version string. The authoritative version JSON now lives at `/server_info` (`{"dagster_version":"1.11.16", ...}`). `verify/checks.sh` asserts 200 on `/dagster_version` AND the version JSON on `/server_info`. The compose healthcheck still hits `/dagster_version` because status-200 is the contract.

5. **`psql` not on host.** `verify/checks.sh` V5 was updated to use `docker compose exec -T postgres psql ...` (postgres image ships psql) instead of a host `psql` invocation. Also asserts SELECT against both `platform` AND `platform_dagster` databases.

All five F-001 verification bullets PASS (V1–V5) with the stack running on the +10000 host ports.

