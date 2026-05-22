# S002-F-002 Mode A Review (Iteration 1)

**Reviewer:** reviewer sub-agent
**Date:** 2026-05-22
**Target:** `contracts/S002-F-002/proposed.md`

---

## Verdict: CHANGES_REQUESTED

Six concrete issues. Three are real schema-fidelity bugs against §4.1 that will produce drift if shipped as written. Two are verification-correctness issues. One concerns autogenerate readiness. The proposal's overall structure and approach (hand-written migration via `op.create_table`, async `env.py`, in-container verification) is sound — fix the items below in place.

---

## Findings

### 1. `document_variant.materialized_at` — missing `server_default=sa.text("now()")` — **blocker**

`proposed.md` line 65 groups `materialized_at` with `started_at, ended_at` as "nullable timestamps **without a default**" — but `docs/data_platform_design.md` line 277 explicitly defines:

```sql
materialized_at     TIMESTAMPTZ DEFAULT NOW(),
```

`DEFAULT NOW()` MUST be present in the migration (and in the ORM model). The column remains `nullable=True` (no `NOT NULL` in §4.1), but it does have a server default.

**Fix:** In §3.3, remove `materialized_at` from the "without a default" list. In the migration and `models.py`, declare it as:
```python
sa.Column("materialized_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True)
```

---

### 2. `run.partition_keys` — spurious `nullable=False` — **major**

`proposed.md` line 72:
```python
sa.Column("partition_keys", postgresql.ARRAY(sa.Text), server_default=sa.text("'{}'"), nullable=False)
```

§4.1 line 358:
```sql
partition_keys       TEXT[] DEFAULT '{}',
```

No `NOT NULL` in spec. Adding `nullable=False` is schema drift.

**Fix:** Drop `nullable=False`. The default ensures rows inserted without the column get `'{}'`; nullability should remain unspecified (i.e. `nullable=True`).

---

### 3. V2 table-presence check is substring-ambiguous for `source` — **major**

`proposed.md` line 233:
```bash
for TABLE in users source_collection source document_variant operator recipe dataset run; do
  $PG psql -U app -d platform -c '\dt' | grep -q "$TABLE" || ...
```

`grep -q "source"` matches the line containing `source_collection`. If the `source` table is missing entirely, this check still passes — a silent false-pass on a verification criterion that exists *exactly* to catch missing tables.

**Fix:** Use an exact-match query against `information_schema.tables`:
```bash
$PG psql -U app -d platform -tAc \
  "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$TABLE'" \
  | grep -q '^1$' \
  || { echo "FAIL: table '$TABLE' not found"; exit 1; }
```
This eliminates substring false-positives entirely.

---

### 4. `env.py` snippet ships with `target_metadata=None` — **major**

`proposed.md` line 140 hardcodes `target_metadata=None`. Prose on line 159 says the implementer "SHOULD" wire `Base.metadata`. Since this sprint creates `models.py` with the `DeclarativeBase`, there is no reason to defer — `target_metadata` MUST point at `Base.metadata` so future `alembic revision --autogenerate` works without re-editing `env.py`.

**Fix:** Update the snippet in §4 to:
```python
from dataplat_api.db.models import Base

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    ...
```
Change prose "SHOULD" → "MUST". This is a one-line change in the contract and the implementation.

---

### 5. `operator.example_output` JSONB column not explicitly enumerated — **minor**

§4.1 lines 300-301 define both `example_input JSONB` and `example_output JSONB`. The proposal's §3.2 line 58 lists only `example_input` in the "nullable JSONB without default" exemplar. The "e.g." prefix makes this technically non-exhaustive, but the implementer working from the column list could miss it. The migration MUST include both columns.

**Fix:** In §3.2, list both `example_input` and `example_output` explicitly. Add a one-line cross-check to the implementer checklist: "operator table has 19 columns total (id + 18 from §4.1 lines 286-316)."

---

### 6. `server_default=sa.func.now()` — switch to `sa.text("now()")` for consistency — **minor**

`proposed.md` line 63 uses `sa.func.now()` for `TIMESTAMPTZ DEFAULT NOW()` columns. The JSONB and ARRAY defaults in the same proposal use `sa.text(...)` (line 56, 72). Both `sa.func.now()` and `sa.text("now()")` work, but mixing styles is awkward and `sa.text("now()")` is more robust against future `--autogenerate` diff noise (Alembic compares server_defaults as rendered strings; `sa.text` is the documented canonical form).

**Fix:** Replace `server_default=sa.func.now()` with `server_default=sa.text("now()")` throughout migration and models.

---

### 7. `checks.sh` migration block needs an alembic pre-flight check — **minor**

If the `fastapi` image was not rebuilt after adding alembic to `pyproject.toml`, V1 fails with `ModuleNotFoundError: No module named 'alembic'`. The user has to debug what looks like a migration error but is really a build issue.

**Fix:** At the top of the `migration)` block in `checks.sh`, add:
```bash
echo "--- migration pre-flight: alembic installed in fastapi container ---"
$API uv run alembic --version \
  || { echo "FAIL: alembic not in fastapi container — run: docker compose build fastapi && up -d fastapi"; exit 1; }
```

---

## Items NOT raised as findings (deliberate)

- **No pytest tests for `models.py`/`session.py`** — reviewer suggested adding `tests/test_migration.py` per CAL-10. Leader judgment: for this sprint, `verify/checks.sh migration` exercises the migration as a system (V1–V4), and adding a pytest layer that also needs a running Postgres container creates a second, redundant verification path. CAL-10 will be re-evaluated when `session.py` is first *used* by a route (F-007). If the implementer wants to add a one-liner `test_db_imports.py` that asserts `from dataplat_api.db.models import Base` succeeds and lists 8 tables in `Base.metadata.tables`, that's welcome but not required.
- **Postgres user/db naming** — already RESOLVED in proposal §7 OQ-1; reviewer confirmed against compose file. PASS.

---

## Calibration cases checked

| Case | Status |
|---|---|
| CAL-1 (async session) | PASS — `create_async_engine`, `async_sessionmaker`, no `session.query()` |
| CAL-2 (LLM gateway) | N/A |
| CAL-3 (OpenAPI sync) | N/A — no routes touched |
| CAL-4 (lineage) | N/A — no Commit objects |
| CAL-5 (CAS paths) | N/A |
| CAL-6 (schema freeze) | N/A — no Silver/Gold |
| CAL-7 (Bronze faithfulness) | N/A |
| CAL-8 (MVP scope) | PASS |
| CAL-9 (plugin isolation) | N/A |
| CAL-10 (tests) | DEFERRED (see above) |
| CAL-11 (vague approval bias) | N/A (no approval issued) |

---

## Next action

Implementer: update `contracts/S002-F-002/proposed.md` addressing findings 1–7. Re-submit for Mode A iteration 2.

---

## Iteration 2

**Reviewer:** reviewer sub-agent
**Date:** 2026-05-22
**Target:** `contracts/S002-F-002/proposed.md` (revision 2 — addressing iter-1 findings 1–7)

---

## Verdict: APPROVED

All 7 iter-1 findings are resolved. No new issues were introduced by the revision. The contract is clear, internally consistent, and faithful to §4.1. The implementer may proceed.

---

## Finding-by-finding verification

### F-1 (blocker — materialized_at server_default)

RESOLVED. proposed.md §3.3 lines 70–74 now read:

> `document_variant.materialized_at` (§4.1 line 277) has `DEFAULT NOW()` in the spec and MUST include a `server_default`. The column is nullable (no `NOT NULL` in §4.1), so the correct declaration is:
> `sa.Column("materialized_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True)`
> It must NOT be grouped with `started_at`/`ended_at`.

`materialized_at` no longer appears in the "without a default" list at line 68 (`started_at`, `ended_at` only). PASS.

### F-2 (major — partition_keys nullable=False)

RESOLVED. proposed.md §3.4 line 81:

```python
sa.Column("partition_keys", postgresql.ARRAY(sa.Text), server_default=sa.text("'{}'"))
```

`nullable=False` removed. Line 83 explicitly explains: "`partition_keys` has no `NOT NULL` in §4.1 — only a `DEFAULT '{}'` — so `nullable=False` must NOT be added." PASS.

### F-3 (major — V2 grep substring ambiguity)

RESOLVED. proposed.md §5 V2 prose (lines 203–221) and the `checks.sh` block (lines 265–271) both use:

```bash
$PG psql -U app -d platform -tAc \
  "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$TABLE'" \
  | grep -q '^1$' \
  || { echo "FAIL: table '$TABLE' not found"; exit 1; }
```

Exact `table_name` match; `table_schema='public'` correctly scopes away the Alembic `alembic_version` table (which is also in `public` but is not in the 8-name iteration). The `run` table name cannot produce a false positive because `information_schema` matching is exact. PASS.

### F-4 (major — target_metadata=None)

RESOLVED. proposed.md §4 env.py snippet line 143: `from dataplat_api.db.models import Base`. Line 150: `context.configure(connection=connection, target_metadata=Base.metadata)`. Line 169 now reads "MUST import `Base`... `target_metadata=None` is not acceptable." PASS.

### F-5 (minor — example_output omitted from operator column list)

RESOLVED. proposed.md §3.2 line 59 now reads: "nullable JSONB without a default (e.g. `output_schema`, `example_input`, `example_output`)". Line 61 adds the explicit 19-column cross-check: "the `operator` table must have exactly **19 columns** total." PASS.

### F-6 (minor — sa.func.now() inconsistency)

RESOLVED. proposed.md §3.3 line 66: `server_default=sa.text("now()")`. grep confirms zero occurrences of `sa.func.now` in the document. All timestamp server_defaults now use `sa.text("now()")`, consistent with JSONB and ARRAY defaults which already used `sa.text(...)`. PASS.

### F-7 (minor — alembic pre-flight absent from checks.sh)

RESOLVED. proposed.md §5 "Pre-flight" subsection (lines 179–189) documents the guard. The `checks.sh` block lines 257–259:

```bash
echo "--- migration pre-flight: alembic installed in fastapi container ---"
$API uv run alembic --version \
  || { echo "FAIL: alembic not in fastapi container — run: docker compose build fastapi && up -d fastapi"; exit 1; }
```

PASS.

---

## Additional checks performed in iter 2

Beyond re-verifying the 7 findings, the following were checked against the revised text:

**Downgrade order (§3.8):** `run → dataset → recipe → operator → document_variant → source → source_collection → users`. Traced FK dependencies for all 8 tables against §4.1. Every table is dropped after all tables that reference it:
- `recipe` references `operator` (via `schema_template_operator_id`) and `users`. It is dropped before both. PASS.
- `dataset` references `recipe` and `users`. It is dropped after `run` and before `recipe`. PASS.
- No circular FK paths exist. PASS.

**No new columns introduced:** The contract adds no column not present in §4.1. Checked against the full §4.1 text. PASS.

**No blob columns:** All JSONB columns are metadata; no `bytea`, no inline blob storage. Hard invariant 2 is satisfied. PASS.

**Scope discipline (CAL-8):** No Celery, no Docker-in-Docker, no OAuth, no granular ACL, no training framework code. PASS.

**CAL-1 (async session):** `create_async_engine`, `async_sessionmaker`, `conn.run_sync()` in env.py. No `session.query()`. PASS.

**CAL-3 (OpenAPI sync):** No routes added, no Pydantic schemas changed. `make codegen` not required. PASS.

**CAL-4, CAL-5, CAL-6, CAL-7, CAL-9:** N/A for this sprint (no Commits, no blob paths, no Silver/Gold schemas, no adapters, no plugins).

**CAL-10 (tests):** Deferred per iter-1 leader ruling. `verify/checks.sh migration` V1–V4 provides the operational coverage. Not re-raised.

---

## Notes (non-blocking observations for the implementer)

1. **OQ-2 image rebuild prerequisite:** The pre-flight guard in checks.sh (F-7) catches a missing rebuild at verification time, but it does not prevent the implementer from spending time debugging if they forget to rebuild during development. The contract correctly documents the rebuild step in §7 OQ-2; the implementer should run `docker compose build fastapi && docker compose up -d fastapi` immediately after editing `pyproject.toml`, before attempting any `alembic` command.

2. **`uv.lock` commit discipline (OQ-6):** The contract requires the lockfile to be committed alongside `pyproject.toml` changes. This is easy to forget. Implementer: run `cd apps/api && uv lock` before the final `git commit` and include `uv.lock` in the staged files.

3. **`alembic.ini` placeholder DSN (OQ-4):** The contract specifies `postgresql+asyncpg://placeholder/placeholder` as the `sqlalchemy.url` value. The placeholder driver prefix must be `+asyncpg` (not plain `postgresql+psycopg2`) so that any accidental connection attempt via the ini file uses the same driver as the actual `DATABASE_URL`, making the error message unambiguous.

4. **`definition JSONB NOT NULL` on `recipe`:** §4.1 marks `definition` as `NOT NULL`. The contract's §3.2 lists JSONB columns that have `DEFAULT '{}'` (e.g. `source_metadata`, `default_config`) and those that are nullable without a default (e.g. `output_schema`). `recipe.definition` is neither — it is `NOT NULL` with no default. The implementer must ensure `nullable=False` and no `server_default` on this column, which requires an explicit value on every INSERT. This case is not explicitly called out in the contract. It is straightforward from §4.1, but worth flagging so the implementer does not accidentally set a server_default of `'{}'::jsonb` on it.

