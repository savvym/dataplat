# S002-F-002 Mode B Review (Final)

**Reviewer:** reviewer sub-agent
**Date:** 2026-05-22
**Commit reviewed:** 44492b5
**Diff base:** db4034c
**Contract:** `contracts/S002-F-002/agreed.md`

---

## DECISION: APPROVED

All contract criteria are met. The migration faithfully reproduces §4.1. The async invariant is enforced. The deviation from agreed.md (bare `alembic` vs `uv run alembic`) is correct and necessary given the Dockerfile's pip-based install. No blockers or majors found.

---

## Deviation evaluation: bare `alembic` vs `uv run alembic`

**agreed.md §5** specifies `uv run alembic upgrade head` throughout. The shipped `checks.sh` uses bare `alembic`. This is a valid and necessary deviation:

- `docker/api/Dockerfile` (diff confirmed) installs via `pip install --no-cache-dir ... alembic==1.14.1`. There is no `uv` binary in the container — the base image is `python:3.12-slim` with no uv installed. Calling `uv run alembic` would fail with `command not found`.
- `pip install` places `alembic` on `PATH` at `/usr/local/bin/alembic` inside the container. Bare `alembic` is therefore the correct invocation.
- The Dockerfile also adds `&& pip install --no-cache-dir -e .` in the same RUN layer. This installs `dataplat_api` as a package into the container's site-packages, making `from dataplat_api.db.models import Base` importable when `env.py` is executed by Alembic. Without this `-e .` step, `env.py` would fail with `ModuleNotFoundError: No module named 'dataplat_api'`.
- The deviation introduces no invariant violation. It is purely an environment adaptation to the existing Dockerfile pattern.
- `checks.sh` line 94 documents the reason in a comment: `# The fastapi container installs via pip (not uv), so alembic is on PATH directly.`

**Action for leader:** record a brief deviation note in `agreed.md` or the sprint closing entry in `claude-progress.txt` noting that the verification command is `alembic upgrade head` (not `uv run alembic`) due to pip-based Dockerfile. No contract re-approval required — this is a faithful environment adaptation, not a scope change.

---

## Contract criteria (agreed.md §5 verification plan)

### Pre-flight (alembic in container)

checks.sh lines 93–96: `$API alembic --version || { echo "FAIL: ...rebuild instructions..."; exit 1; }`. Guard is present and actionable. PASS.

### V1 — migration command exits 0

checks.sh line 99: `run "$API alembic upgrade head"`. Executed inside container with `DATABASE_URL` available. PASS.

### V2 — all 8 tables present

checks.sh lines 102–108: per-table loop using `information_schema.tables` exact match (`table_schema='public' AND table_name='$TABLE'`), asserting `^1$`. Immune to substring collision (`source` vs `source_collection`). All 8 table names present in the loop. PASS.

### V3 — idempotent re-run

checks.sh line 111: second `run "$API alembic upgrade head"`. Alembic no-ops on already-applied revision. PASS.

### V4-extra — downgrade base + upgrade head round-trip

checks.sh lines 114–115. Downgrade order in migration `downgrade()` is `run → dataset → recipe → operator → document_variant → source → source_collection → users` (migration lines 390–397). FK dependency chain verified against §4.1: every table is dropped after all tables referencing it. PASS.

---

## Schema fidelity: migration vs §4.1

### users (migration lines 32–43)

| §4.1 | migration | verdict |
|---|---|---|
| id BIGSERIAL PK | BigInteger + Identity() + primary_key | PASS |
| email TEXT UNIQUE NOT NULL | Text, nullable=False, unique=True | PASS |
| name TEXT | Text, nullable=True | PASS |
| created_at TIMESTAMPTZ DEFAULT NOW() | DateTime(tz=True), server_default=text("now()") | PASS |

### source_collection (migration lines 51–74)

| §4.1 | migration | verdict |
|---|---|---|
| id BIGSERIAL PK | BigInteger + Identity() | PASS |
| name TEXT UNIQUE NOT NULL | Text, nullable=False, unique=True | PASS |
| owner_id BIGINT REFERENCES users(id) | BigInteger, ForeignKey("users.id"), nullable=True | PASS |
| dataset_card_md TEXT | Text, nullable=True | PASS |
| created_at TIMESTAMPTZ DEFAULT NOW() | server_default=text("now()") | PASS |
| updated_at TIMESTAMPTZ DEFAULT NOW() | server_default=text("now()") | PASS |
| No ON DELETE on owner_id | ForeignKey with no ondelete kwarg | PASS |

### source (migration lines 86–118)

| §4.1 | migration | verdict |
|---|---|---|
| collection_id ... ON DELETE CASCADE | ForeignKey("source_collection.id", ondelete="CASCADE") | PASS |
| kind TEXT NOT NULL | nullable=False | PASS |
| original_name TEXT NOT NULL | nullable=False | PASS |
| storage_uri TEXT NOT NULL | nullable=False | PASS |
| sha256 TEXT NOT NULL | nullable=False | PASS |
| size BIGINT (nullable) | BigInteger, nullable=True | PASS |
| mime_type TEXT (nullable) | nullable=True | PASS |
| license TEXT (nullable) | nullable=True | PASS |
| source_metadata JSONB DEFAULT '{}' | server_default=text("'{}'::jsonb") | PASS |
| dagster_partition_key TEXT NOT NULL UNIQUE | nullable=False, unique=True | PASS |
| preferred_extractor TEXT (nullable) | nullable=True | PASS |
| uploaded_at TIMESTAMPTZ DEFAULT NOW() | server_default=text("now()") | PASS |
| idx_source_collection ON source(collection_id) | op.create_index line 117 | PASS |
| idx_source_sha256 ON source(sha256) | op.create_index line 118 | PASS |

### document_variant (migration lines 131–171)

| §4.1 | migration | verdict |
|---|---|---|
| source_id ... ON DELETE CASCADE | ForeignKey("source.id", ondelete="CASCADE") | PASS |
| extractor_name TEXT NOT NULL | nullable=False | PASS |
| extractor_version TEXT NOT NULL | nullable=False | PASS |
| config_hash TEXT NOT NULL | nullable=False | PASS |
| storage_prefix TEXT NOT NULL | nullable=False | PASS |
| page_count INT (nullable) | Integer, nullable=True | PASS |
| image_count INT (nullable) | Integer, nullable=True | PASS |
| is_canonical BOOLEAN DEFAULT FALSE | server_default=text("false") | PASS |
| materialized_at TIMESTAMPTZ DEFAULT NOW() | server_default=text("now()"), nullable=True (iter-1 blocker: FIXED) | PASS |
| dagster_run_id TEXT (nullable) | nullable=True | PASS |
| UNIQUE (source_id, extractor_name, config_hash) | UniqueConstraint lines 159–162 | PASS |
| idx_doc_variant_source | op.create_index line 164 | PASS |
| idx_doc_canonical UNIQUE WHERE is_canonical | unique=True, postgresql_where=text("is_canonical") lines 165–171 | PASS |

### operator (migration lines 186–228)

Column count: id + name + version + category + input_kind + output_kind + output_schema + config_schema + default_config + description + reference_url + example_input + example_output + image + entrypoint + estimated_cost_per_unit + rate_limit_per_minute + is_active + created_at = **19 columns**. PASS.

| §4.1 | migration | verdict |
|---|---|---|
| output_schema JSONB (nullable, no default) | postgresql.JSONB, nullable=True | PASS |
| config_schema JSONB (nullable, no default) | postgresql.JSONB, nullable=True | PASS |
| default_config JSONB DEFAULT '{}' | server_default=text("'{}'::jsonb") | PASS |
| example_input JSONB (nullable) | postgresql.JSONB, nullable=True | PASS |
| example_output JSONB (nullable) | postgresql.JSONB, nullable=True | PASS |
| image TEXT NOT NULL | nullable=False | PASS |
| is_active BOOLEAN DEFAULT TRUE | server_default=text("true") | PASS |
| created_at TIMESTAMPTZ DEFAULT NOW() | server_default=text("now()") | PASS |
| UNIQUE (name, version) | UniqueConstraint line 222 | PASS |
| idx_operator_category ON operator(category, is_active) | op.create_index lines 224–228 | PASS |

### recipe (migration lines 239–269)

| §4.1 | migration | verdict |
|---|---|---|
| name TEXT UNIQUE NOT NULL | nullable=False, unique=True | PASS |
| owner_id BIGINT REFERENCES users(id) (no CASCADE) | ForeignKey("users.id"), no ondelete | PASS |
| definition JSONB NOT NULL (no default) | nullable=False, no server_default (iter-2 note: CORRECT) | PASS |
| schema_template_operator_id REFERENCES operator(id) | ForeignKey("operator.id"), no ondelete | PASS |
| created_at / updated_at DEFAULT NOW() | server_default=text("now()") both | PASS |

### dataset (migration lines 285–314)

| §4.1 | migration | verdict |
|---|---|---|
| recipe_id REFERENCES recipe(id) (no CASCADE) | ForeignKey("recipe.id"), no ondelete | PASS |
| recipe_snapshot JSONB NOT NULL | nullable=False, no server_default | PASS |
| version_tag TEXT NOT NULL | nullable=False | PASS |
| hf_repo_uri TEXT NOT NULL | nullable=False | PASS |
| stats JSONB (nullable, no default) | postgresql.JSONB, nullable=True | PASS |
| status TEXT NOT NULL | nullable=False | PASS |
| materialized_by REFERENCES users(id) | ForeignKey("users.id"), no ondelete | PASS |
| materialized_at TIMESTAMPTZ (nullable, no default) | DateTime(tz=True), nullable=True, no server_default | PASS |
| UNIQUE (recipe_id, version_tag) | UniqueConstraint lines 310–312 | PASS |
| idx_dataset_recipe | op.create_index line 314 | PASS |

### run (migration lines 331–382)

| §4.1 | migration | verdict |
|---|---|---|
| dagster_run_id TEXT UNIQUE NOT NULL | nullable=False, unique=True | PASS |
| kind TEXT NOT NULL | nullable=False | PASS |
| asset_keys TEXT[] NOT NULL | ARRAY(Text), nullable=False | PASS |
| partition_keys TEXT[] DEFAULT '{}' (no NOT NULL) | server_default=text("'{}'"), nullable=True (iter-1 major: FIXED) | PASS |
| source_collection_id / dataset_id / recipe_id (no CASCADE) | ForeignKeys with no ondelete | PASS |
| config JSONB (nullable, no default) | nullable=True | PASS |
| status TEXT NOT NULL | nullable=False | PASS |
| started_at / ended_at TIMESTAMPTZ (nullable, no default) | nullable=True, no server_default | PASS |
| trigger_context JSONB (nullable, no default) | nullable=True | PASS |
| idx_run_status ON run(status, started_at DESC) | ["status", sa.text("started_at DESC")] — compiles to correct DDL (verified) | PASS |
| idx_run_triggered ON run(triggered_by, started_at DESC) | ["triggered_by", sa.text("started_at DESC")] — compiles correctly | PASS |

---

## ORM models vs migration agreement (models.py vs 0001_baseline_schema.py)

All 8 `__tablename__` values present: `users`, `source_collection`, `source`, `document_variant`, `operator`, `recipe`, `dataset`, `run`. PASS.

Spot-checked divergence points:

- `document_variant.materialized_at`: models.py line 138–142 — `server_default=text("now()"), nullable=True`. Matches migration line 153–157. PASS.
- `run.partition_keys`: models.py line 284–288 — `server_default=text("'{}'"), nullable=True`. No `nullable=False`. Matches migration lines 337–342. PASS.
- `recipe.definition`: models.py line 214 — `Mapped[dict] = mapped_column(postgresql.JSONB, nullable=False)`. No `server_default`. Matches migration line 250. PASS.
- `operator.example_output`: models.py line 179–181 — `Mapped[Optional[dict]] = mapped_column(postgresql.JSONB, nullable=True)`. Present. PASS.
- ON DELETE CASCADE: models.py `source.collection_id` line 75 — `ondelete="CASCADE"`. `document_variant.source_id` line 123 — `ondelete="CASCADE"`. All other FKs: no `ondelete`. PASS.
- Index expressions: `idx_run_status` in models.py line 271 uses `sa.text("started_at DESC")` consistent with migration. PASS.

---

## Hard invariant checks

| # | Invariant | Evidence | Verdict |
|---|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit objects | N/A |
| 2 | Storage separation / CAS | No `bytea`, `LargeBinary`, or blob columns anywhere in migration or models. grep confirmed 0 matches. | PASS |
| 3 | Schema frozen post-publish | N/A | N/A |
| 4 | LLM gateway | N/A — no LLM calls | N/A |
| 5 | Async SQLAlchemy from day one | `session.py`: `create_async_engine`, `async_sessionmaker(class_=AsyncSession)`, `AsyncSession` yield. No `session.query()`, no sync sessions. `env.py`: `create_async_engine`, `conn.run_sync(do_run_migrations)` — never `engine_from_config`. `do_run_migrations` is a sync function called inside `run_sync` — this is the correct and only pattern; it is not a violation. | PASS |
| 6 | OpenAPI ↔ TS sync | No routes added (main.py unchanged, no new router files). No `make codegen` required. | N/A |

---

## Calibration checks (CAL-N)

| Case | Verdict | Evidence |
|---|---|---|
| CAL-1 (async session) | PASS | `session.py:9` — `create_async_engine`, `async_sessionmaker`. `env.py:13` — `create_async_engine`, `conn.run_sync`. No `session.query` anywhere. `do_run_migrations` is intentionally sync inside `run_sync` callback — correct pattern, not a violation. |
| CAL-2 (LLM gateway) | N/A | No LLM calls in this diff. |
| CAL-3 (OpenAPI sync) | N/A | No routers changed. `main.py` not in diff. No Pydantic schema files touched. |
| CAL-4 (lineage completeness) | N/A | No Commit objects created. |
| CAL-5 (CAS path discipline) | N/A | No blob storage paths introduced. |
| CAL-6 (schema freeze post-publish) | N/A | No existing schema modified. |
| CAL-7 (Bronze faithfulness) | N/A | No adapters. |
| CAL-8 (MVP scope discipline) | PASS | No Celery, Docker-in-Docker, OAuth, granular ACL, or training frameworks. |
| CAL-9 (plugin isolation) | N/A | No plugins. |
| CAL-10 (test coverage) | DEFERRED | Per iter-1 and iter-2 leader ruling: `checks.sh migration` V1–V4 provides operational coverage. No new route or business logic code was added; all new files are infrastructure (migration DDL, session factory, config reader). |
| CAL-11 (bias check) | APPLIED | Verified every table column-by-column against §4.1 text before writing verdict. Found no violations. Index DDL compiled and confirmed correct. |

---

## Scope discipline

- No new routes in `main.py` or `routers/`. `routers/` directory existed before this sprint (`health.py` only); no new files added. PASS.
- No `schemas.py` added. No OpenAPI surface changes. PASS.
- `uv.lock` regenerated and committed (+925 lines). PASS (agreed.md OQ-6 satisfied).
- Only `alembic==1.14.1` and `pydantic-settings==2.7.0` added to `pyproject.toml`. No extra dependencies. PASS.

---

## Minor observations (non-blocking)

1. **`alembic.ini` logger config**: The `[logger_sqlalchemy]` and `[logger_alembic]` sections have `handlers =` (empty), which means log output goes nowhere by default. This is standard Alembic ini behaviour but means `alembic upgrade head` will not show INFO-level migration messages in the container unless the root logger captures them. The pre-flight and migration checks rely on exit codes, not log output parsing, so this is not a functional issue.

2. **`config.py` module-level `Settings()` instantiation**: `settings: Settings = Settings()` runs at import time. If `DATABASE_URL` is not set in the environment when the module is imported, this raises a `ValidationError` immediately. This is acceptable for the container (which always has `DATABASE_URL` set) but may cause confusing failures during `pytest` on the host if `DATABASE_URL` is absent. A `model_config = {"env_file": ".env"}` is already present, which mitigates this partially. No action required for this sprint.

3. **`agreed.md` deviation note**: The leader should append a one-line note to the sprint record documenting that `checks.sh` uses bare `alembic` (not `uv run alembic`) because the Dockerfile uses pip, not uv. This keeps the deviation traceable without requiring contract re-approval.

---

## Next action

Delegate to **verifier** sub-agent: run `bash verify/checks.sh migration` and report V1–V4 exit codes.
