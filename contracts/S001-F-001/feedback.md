# Mode A Review — S001-F-001 — 2026-05-21 (iteration 5 / final)

## VERDICT: APPROVED

---

## Iteration-4 fix confirmation

**MinIO healthcheck removed:** §4 row 3 (line 88) now reads `_none this sprint_ — MinIO image does not ship wget or curl. See note below.` §4 Notes line 97 no longer mentions MinIO. §4 Notes line 98 is a new bullet that correctly explains: no compose healthcheck, no `depends_on: minio: service_healthy` anywhere, Docker reports the container as `running` (not `unhealthy`) which satisfies V1's "running or healthy" pass condition, and V4 provides the host-side liveness check. The reasoning is sound and complete. RESOLVED.

---

## Evidence for approval

**Calibration checks (CAL-1 through CAL-10 against this contract):**
- CAL-1 (async sessions): No DB code in scope. `pyproject.toml` declares `sqlalchemy[asyncio]` and `asyncpg`, structurally enforcing async-only for F-002+. PASS.
- CAL-2 (LLM gateway): No LLM calls in scope. N/A.
- CAL-3 (OpenAPI sync): No Pydantic schema changes; `/healthz` returns a static dict. `make codegen` correctly deferred. N/A.
- CAL-4 (lineage): No Commit objects. N/A.
- CAL-5 (CAS paths): No blob storage writes. N/A.
- CAL-6 (schema freeze): No schema publications. N/A.
- CAL-7 (Bronze faithfulness): No adapters. N/A.
- CAL-8 (MVP scope discipline): No Celery, no MFA, no OAuth, no Docker-in-Docker, no granular ACL, no training frameworks. PASS.
- CAL-9 (plugin isolation): No plugins. N/A.
- CAL-10 (test coverage): Infrastructure-only sprint; no business logic or API endpoints beyond the static `/healthz`. The verifier checks are the acceptance tests. N/A for unit test requirement.

**Contract criteria:**
- All 9 F-001 services covered in §4, port assignments conflict-free. PASS.
- All 5 verification bullets have concrete runnable commands and pass conditions. PASS.
- All 6 hard invariants correctly classified (4 IRRELEVANT, 1 PARTIALLY RELEVANT with correct forward guidance on async SQLAlchemy, 1 IRRELEVANT with correct codegen deferral). PASS.
- Scope OUT is explicit, honest, and references the correct future features (F-002 through F-007, F-055). PASS.
- Version pins in §8a are specific and internally consistent across §2, §4, §5, §8, and §8a. PASS.
- Build context (`context: .`, repo-root COPY paths) specified in §2 and Appendix for both Dagster and FastAPI images. PASS.
- `ENV PYTHONPATH=/app/dagster` and `ENV DAGSTER_HOME=/app/dagster` present in §2 (line 33) and Appendix (lines 350, 352). PASS.
- `psycopg2-binary==2.9.10` present in §2, §8a, and Appendix. PASS.
- Workers use `command: sleep infinity` with migration plan to `dagster api grpc` at F-005. Specified in §4, §5, §8 OQ-2, and Appendix. PASS.
- In-container healthchecks use `wget`; host-side verifier commands use `curl`; distinction is documented at §7 V1 line 163. Services with healthchecks: dagster-webserver (`wget`, `python:3.12-slim`), fastapi (`wget`, `python:3.12-slim`), frontend (`wget`, `nginx:1.27-alpine`). MinIO: no healthcheck, shows `running`. postgres: `pg_isready`. redis: `redis-cli ping`. PASS.
- OQ-8 specifies all `depends_on` in long-form `condition:` syntax, covering all 6 service edges. PASS.
- init.sql mounted at `/docker-entrypoint-initdb.d/01-init.sql:ro`, runs as Postgres superuser. PASS.
- 18-file Appendix matches Scope IN enumeration. PASS.
- No deferred-MVP items (Celery, MFA, Docker-in-Docker, Kafka, granular ACL) introduced. PASS.

---

## Next step: leader copies proposed.md → agreed.md and instructs implementer to build.
