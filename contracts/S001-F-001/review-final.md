# Mode B Review — S001-F-001 — 2026-05-21 — commit 62db5f1

## VERDICT: CHANGES_REQUESTED

One finding requires attention before this can be approved. The remaining observations are flagged as risks for the verifier, not blockers.

---

## Calibration Checks

- **CAL-1 (async session enforcement):** PASS — No DB sessions exist yet. `pyproject.toml` declares `sqlalchemy[asyncio]==2.0.41` and `asyncpg==0.30.0`, establishing the correct async-only baseline for F-002+.
- **CAL-2 (LLM gateway enforcement):** N/A — No LLM imports anywhere in the diff. `OPENAI_API_KEY=` and `ANTHROPIC_API_KEY=` in `.env.example` are blank placeholders.
- **CAL-3 (OpenAPI sync):** N/A — `/healthz` returns a bare `dict`, not a Pydantic model.
- **CAL-4..7:** N/A.
- **CAL-8 (MVP scope discipline):** PASS — No Celery, MFA, OAuth, DinD, granular ACL, training frameworks, Kafka. Workers correctly `sleep infinity`.
- **CAL-9, CAL-10:** N/A.

---

## Required Change (Blocker)

### 1. `dagster/dagster.yaml` — dual conflicting configuration blocks

`dagster/dagster.yaml` contains BOTH the Dagster 1.x unified `storage:` block (lines 12–24) AND the legacy per-backend blocks `run_storage:` / `event_log_storage:` / `schedule_storage:` (lines 26–68). This is redundant and risks a `DagsterInvalidConfigError` at webserver startup — which would cascade to V3 failure and would also block daemon/workers via the `service_healthy` chain.

The Dagster 1.x parser treats `storage: postgres:` as the authoritative unified config. Having the legacy keys alongside it is at best ignored and at worst a config validation error in 1.9.10.

**Fix:** Remove the three legacy blocks (`run_storage:`, `event_log_storage:`, `schedule_storage:`) entirely. Keep only the unified `storage: postgres:` block.

Resulting file:

```yaml
storage:
  postgres:
    postgres_db:
      username:
        env: POSTGRES_USER
      password:
        env: POSTGRES_PASSWORD
      hostname:
        env: POSTGRES_HOST
      db_name:
        env: POSTGRES_DB_DAGSTER
      port: 5432
```

---

## Contract Criteria (all PASS unless noted)

- 9-service inventory, all images pinned per §8a, build contexts correct.
- `ENV PYTHONPATH=/app/dagster`, `ENV DAGSTER_HOME=/app/dagster`, `psycopg2-binary==2.9.10` present in Dagster Dockerfile.
- Workers use `command: sleep infinity` with F-005 migration plan in comments.
- Healthchecks use `wget` (dagster-webserver, fastapi, frontend); MinIO has none (correct).
- Long-form `depends_on: { condition: service_healthy }` on all 6 edges; fastapi does NOT depend on dagster-webserver (correct).
- init.sql at `/docker-entrypoint-initdb.d/01-init.sql:ro` creates `platform_dagster`.
- workspace.yaml: `python_module: dagster_platform.definitions`. `dagster_platform/definitions.py` exports `defs = Definitions()`.
- FastAPI `GET /healthz` returns `{"status": "ok"}` via router.
- `.env.example` has safe dev defaults; no real secrets in the diff.
- Named volumes `pg_data`, `minio_data` declared.
- FastAPI bind mount + `--reload` set up.

**Partial (non-blocking):** `redis` and `frontend` services do not declare `env_file: .env.example` — neither needs env vars from it, so this is cosmetic.

---

## Scope Fidelity

All 18 contract files present. Two acceptable extras: `.gitignore` (standard repo-hygiene, no in-scope dirs excluded) and modification of pre-existing `verify/checks.sh` (additive `infra` layer; `smoke` layer intact).

---

## Verification Viability

- **V1** (`docker compose up -d` exits 0): LIKELY PASS once dagster.yaml is fixed. `docker compose config -q` already validates compose syntax.
- **V2** (GET localhost:8000/healthz → 200 `{"status":"ok"}`): PASS — route exists, depends only on postgres.
- **V3** (GET localhost:3000/dagster_version → 200): AT RISK pending dagster.yaml fix. Once fixed, the endpoint is present in Dagster 1.9.10.
- **V4** (MinIO console :9001): PASS — `--console-address ":9001"` configured, port mapped.
- **V5** (`psql -c 'SELECT 1'` returns 1): PASS — Postgres 16 running with documented user/db; init.sql creates `platform_dagster` as superuser; pg_isready healthcheck wired.

---

## Next step: implementer removes the three legacy storage blocks from `dagster/dagster.yaml`, commits the fix, and re-submits for re-review (or leader fixes inline since it is a one-file surgical change).
