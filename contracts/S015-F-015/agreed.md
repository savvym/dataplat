# S015-F-015 — Proposed Contract

**Status:** PROPOSED
**Date drafted:** 2026-05-25
**Author:** Leader (Claude)
**Sprint-id:** S015-F-015
**Depends on:** F-002 (passes: true — `operator` table exists)

---

## §1 Goal

F-015 seeds the initial MinerU extractor operator row into the `operator` table so the platform
has a known, queryable extractor from the moment the dev stack is brought up. The verification
criteria require:

1. `SELECT * FROM operator WHERE name='mineru'` returns exactly 1 row with
   `category='extractor'`, `input_kind='source'`, `output_kind='document'`.
2. The `config_schema` column is valid JSON (parseable as JSONB by Postgres).

This is implemented as an **idempotent async CLI seed subcommand** (`seed-operators`), NOT as an
Alembic migration. Rationale: Alembic migrations are schema-only operations; they should be
reversible and carry no reference/seed data. F-007 established the CLI-seed pattern with
`seed-admin`; this sprint extends `cli.py` following the same pattern exactly. Baking seed data
into migrations makes test DB teardown brittle and makes the intent of a migration ambiguous.

---

## §2 Files Changed

| Path | New / Modified | Summary |
|---|---|---|
| `apps/api/dataplat_api/cli.py` | MODIFIED | Add `seed-operators` argparse subcommand + `async def seed_operators()` function. |
| `verify/checks.sh` | MODIFIED | Add a new `operators)` layer; insert `bash "$0" operators` into `all)` after `runs` and before the closing `;;`. |

**Files NOT touched:**

- `apps/api/dataplat_api/db/models.py` — `Operator` model already exists; no schema change.
- Any Alembic migration file — no DB schema change; this is seed data only.
- `apps/api/dataplat_api/routers/` — no new API endpoint.
- `apps/api/dataplat_api/schemas/` — no new Pydantic schema.
- `packages/api-types/openapi.json` — see §6 (invariant #6 N/A).
- `docs/data_platform_design.md` — read-only per hard rule.

---

## §3 Seed Function Design

### §3.1 Subcommand name

**`seed-operators`** — chosen over `seed-mineru` because the subcommand seeds the operator
registry in general; the first entry happens to be MinerU. This name scales gracefully if
additional operators are seeded in future sprints by the same subcommand (implemented as a loop
over a list of known operators, or by repeated invocations of the same subcommand). The CLI
invocation:

```
python -m dataplat_api.cli seed-operators
```

No additional CLI arguments are required. The set of operators to seed is hardcoded in the
function body (analogous to how `seed-admin` has its defaults baked in via `--email` / `--password`
arguments, but for a registry seed it is cleaner to hardcode the canonical operator definitions
rather than expose them as CLI flags).

### §3.2 Function signature

```python
async def seed_operators() -> None:
    """Insert the canonical operator registry entries into the operator table.

    Idempotent: if an operator with the same (name, version) pair already exists,
    prints a message and returns without modifying the database (exit code 0).

    Hard invariant #5 compliance: all DB interaction uses async session + select().
    """
```

### §3.3 Session and query pattern

Mirrors `seed_admin` exactly:

```python
async with SessionLocal() as session:
    result = await session.execute(
        select(Operator)
        .where(Operator.name == "mineru")
        .where(Operator.version == "0.1.0")
    )
    existing: Operator | None = result.scalars().first()
    if existing is not None:
        print("Operator 'mineru@0.1.0' already exists. Skipping.")
        return

    op = Operator(
        name="mineru",
        version="0.1.0",
        category="extractor",
        input_kind="source",
        output_kind="document",
        image="dataplat/mineru:0.1.0",
        description="MinerU PDF/document extractor — converts raw source files into structured documents.",
        reference_url="https://github.com/opendatalab/MinerU",
        config_schema={
            "type": "object",
            "properties": {
                "output_format": {
                    "type": "string",
                    "enum": ["markdown", "json"],
                    "default": "markdown",
                    "description": "Output format for extracted document content."
                },
                "language": {
                    "type": "string",
                    "default": "auto",
                    "description": "Hint for OCR language detection (ISO 639-1 code or 'auto')."
                },
                "enable_ocr": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to apply OCR on scanned pages."
                }
            },
            "required": []
        },
    )
    session.add(op)
    await session.commit()
    print("Operator 'mineru@0.1.0' created successfully.")
```

### §3.4 Argparse wiring

Add the `seed-operators` subparser inside `main()` immediately after the `seed-admin` subparser:

```python
subparsers.add_parser(
    "seed-operators",
    help="Seed the operator registry with built-in operator definitions (idempotent).",
)
```

Dispatch in the `if/elif` block:

```python
elif args.command == "seed-operators":
    asyncio.run(seed_operators())
```

Change the `else` branch from `if args.command == "seed-admin":` to use `if/elif` so the
structure stays clean as more subcommands are added.

---

## §4 Exact Row Values

| Column | Value | Rationale |
|---|---|---|
| `name` | `"mineru"` | Canonical lowercase name; verified by criterion 1. |
| `version` | `"0.1.0"` | Needed to satisfy `UNIQUE(name, version)` constraint; idempotency keys on this pair. |
| `category` | `"extractor"` | Verified by criterion 1; matches design doc §4.1 enum. |
| `input_kind` | `"source"` | Verified by criterion 1; MinerU consumes raw source files. |
| `output_kind` | `"document"` | Verified by criterion 1; MinerU produces structured documents. |
| `image` | `"dataplat/mineru:0.1.0"` | **Placeholder** — `image` is `nullable=False`, so a value is mandatory. The real MinerU worker image is not built until F-019. The placeholder follows the naming convention `dataplat/<name>:<version>` so it can be replaced with the real image by updating this row (or bumping the version) when F-019 ships. |
| `description` | `"MinerU PDF/document extractor — converts raw source files into structured documents."` | Human-readable; nullable, but useful for the UI operator list. |
| `reference_url` | `"https://github.com/opendatalab/MinerU"` | Nullable; links to the upstream project. |
| `config_schema` | See §3.3 above — a 3-property JSON Schema object | JSONB; must be valid JSON. |
| `default_config` | Not set (server_default `'{}'`) | Server default is sufficient. |
| `is_active` | Not set (server_default `true`) | Server default is sufficient; operator should be active by default. |
| `created_at` | Not set (server_default `NOW()`) | Server default is sufficient. |
| `output_schema` | Not set (`nullable=True`) | Deferred; the formal Arrow schema for MinerU output is not yet specified. |
| `entrypoint` | Not set (`nullable=True`) | Deferred until F-019 defines the actual entrypoint. |
| `estimated_cost_per_unit` | Not set (`nullable=True`) | Not known at MVP. |
| `rate_limit_per_minute` | Not set (`nullable=True`) | Not constrained at MVP. |
| `example_input` | Not set (`nullable=True`) | Deferred. |
| `example_output` | Not set (`nullable=True`) | Deferred. |

**config_schema rationale:** The design doc §1036-1058 shows a JSON Schema example with
`type: object`, `properties`, and `required`. The MinerU config schema is purposefully minimal —
three properties (`output_format`, `language`, `enable_ocr`) covering the most likely
user-facing knobs. `required: []` means all fields are optional (they all have defaults), which
is appropriate for an extractor that should work out-of-the-box with no config. This schema is
sufficient to satisfy criterion 2 (valid JSON) and to demonstrate that `config_schema` is a
real JSON Schema object, not a null or empty value.

---

## §5 Idempotency

The idempotency guard keys on the **(name, version) pair**, not on name alone. This mirrors the
`UNIQUE(name, version)` constraint on the `operator` table (`uq_operator_name_version`).

Behavior:

| Run state | Outcome |
|---|---|
| Row does not exist | Row inserted; prints `"Operator 'mineru@0.1.0' created successfully."`; exits 0. |
| Row already exists (same name + version) | No DB write; prints `"Operator 'mineru@0.1.0' already exists. Skipping."`; exits 0. |
| Row exists with same name but different version | A different version is a different operator; the existing row is untouched; the new version would be inserted (though this sprint only seeds one version). |

The guard uses `await session.execute(select(Operator).where(...).where(...))` before any
`session.add()`. No `INSERT ... ON CONFLICT` is used — the explicit SELECT + conditional add
pattern is used because it (a) mirrors `seed_admin` exactly, (b) prints a clear diagnostic
message on skip, and (c) does not require raw SQL. The risk of a TOCTOU race is acceptable in a
seed command context (this is a one-time setup operation, not a concurrent write path).

---

## §6 Hard-Invariant Compliance

| Invariant | Assessment |
|---|---|
| #1 Lineage mandatory | **N/A** — no `Commit` object is created or modified. This sprint inserts an `Operator` registry row only. |
| #2 Storage separation + CAS | **N/A** — no blob bytes written anywhere; no MinIO interaction. |
| #3 Schema frozen post-publish | **N/A** — no Silver/Gold commit. |
| #4 LLM calls through gateway | **N/A** — no LLM call. |
| #5 Async SQLAlchemy | **SATISFIED** — `seed_operators()` uses `async with SessionLocal()`, `await session.execute(select(...))`, `result.scalars().first()`, and `await session.commit()`. No `session.query()`. No sync session anywhere. |
| #6 OpenAPI ↔ TS type sync | **NOT APPLICABLE — `make codegen` is NOT required for this sprint.** There is no new route, no new Pydantic response schema, and no change to `apps/api/dataplat_api/routers/` or `apps/api/dataplat_api/schemas/`. The OpenAPI output is unchanged. The implementer MUST NOT run `make codegen` as a mechanical step and MUST NOT commit a spurious `packages/api-types/openapi.json` diff. |

---

## §7 Verification Plan

### §7.1 Criterion 1 — psql SELECT confirms the row with correct column values

The `operators)` layer in `checks.sh` runs the seed (idempotent), then queries Postgres directly
inside the container.

```bash
operators)
  COMPOSE="docker/docker-compose.dev.yml"
  [[ -f "$COMPOSE" ]] || { echo "no $COMPOSE yet"; exit 0; }

  echo "--- operators V1: seed-operators creates exactly one mineru row ---"
  docker compose -f "$COMPOSE" exec -T fastapi \
    python -m dataplat_api.cli seed-operators

  # Criterion 1a: row exists with correct category, input_kind, output_kind.
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT category || '|' || input_kind || '|' || output_kind
         FROM operator WHERE name='mineru' AND version='0.1.0'" \
    | grep -q '^extractor|source|document$' \
    || { echo "FAIL: operators V1 — row missing or wrong category/input_kind/output_kind"; exit 1; }
  echo "operators V1 row values: OK"

  # Criterion 1b: exactly 1 row with name='mineru'.
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT COUNT(*) FROM operator WHERE name='mineru'" \
    | grep -q '^1$' \
    || { echo "FAIL: operators V1 — expected exactly 1 mineru row"; exit 1; }
  echo "operators V1 row count: OK"

  echo "--- operators V2: config_schema is valid non-null JSONB ---"
  # Criterion 2: config_schema is non-null and parseable as JSONB.
  # Postgres stores it as JSONB; querying with ->> 'type' proves it is a valid JSON object.
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT config_schema->>'type' FROM operator WHERE name='mineru' AND version='0.1.0'" \
    | grep -q '^object$' \
    || { echo "FAIL: operators V2 — config_schema is null or not a JSON object with type=object"; exit 1; }
  echo "operators V2 config_schema valid JSONB: OK"

  # Idempotency: re-running must exit 0 and not create a second row.
  echo "--- operators V3: second run is idempotent (no duplicate row) ---"
  docker compose -f "$COMPOSE" exec -T fastapi \
    python -m dataplat_api.cli seed-operators
  docker compose -f "$COMPOSE" exec -T postgres \
    psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
      "SELECT COUNT(*) FROM operator WHERE name='mineru'" \
    | grep -q '^1$' \
    || { echo "FAIL: operators V3 — second seed run created a duplicate row"; exit 1; }
  echo "operators V3 idempotency: OK"
  ;;
```

### §7.2 Insertion into `all)` chain

The `operators` layer is inserted in `all)` **after `runs`** (the current last integration
layer) and before the closing `;;`:

```bash
all)
    bash "$0" smoke
    bash "$0" infra
    bash "$0" backend
    bash "$0" frontend
    bash "$0" contract
    bash "$0" migration
    bash "$0" auth
    bash "$0" collections
    bash "$0" sources
    bash "$0" buckets
    bash "$0" dagster
    bash "$0" runs
    bash "$0" operators   # NEW — F-015
    ;;
```

Rationale for position: the `operators` layer depends on the FastAPI container being healthy
(implicitly verified by `auth` and later layers) and the `operator` table existing (created by
`migration`). It has no dependency on `sources`, `buckets`, `dagster`, or `runs`. Appending it
at the end of the chain is safe and avoids reordering existing layers.

### §7.3 Criterion mapping

| Feature criterion | checks.sh assertion | Check label |
|---|---|---|
| "SELECT returns 1 row with category='extractor', input_kind='source', output_kind='document'" | V1a: `grep -q '^extractor\|source\|document$'` on concatenated columns; V1b: `COUNT(*) = 1` | operators V1 |
| "config_schema column is valid JSON" | V2: `config_schema->>'type'` returns `'object'` via JSONB operator (proves Postgres parsed it as valid JSONB) | operators V2 |
| Idempotency (implicit in "seed script") | V3: second run exits 0 and COUNT(*) remains 1 | operators V3 |

---

## §8 Unit Tests

**Decision: no new pytest file for this sprint.**

Rationale: The `seed_admin` function (F-007) has no dedicated unit test file in
`apps/api/tests/`; the auth integration in `verify/checks.sh auth)` is the authoritative
check for that seed. The F-015 seed function is structurally simpler than `seed_admin` (no
bcrypt, no argument parsing, no HTTP endpoint) — it is a single `SELECT` + conditional `INSERT`.
A unit test would require mocking `SessionLocal` and two `AsyncMock` result proxies, producing
tests that mirror the function body without adding coverage of anything that could realistically
fail in isolation. The integration check in `operators)` exercises the real Postgres JSONB
storage and the actual `UNIQUE(name, version)` constraint, which is the failure surface that
matters. Adding a unit test here would be padding.

If a reviewer disagrees, the implementer should add `apps/api/tests/test_cli_seed_operators.py`
following the `AsyncMock(side_effect=[...])` pattern from `test_sources_list_by_collection.py`,
with two tests: one for the create path and one for the skip-if-exists path.

---

## §9 Edge Cases

| Case | Behavior |
|---|---|
| `operator` table does not exist (migration not run) | `asyncpg` raises `UndefinedTableError`; seed exits non-zero. Operator: run `make migrate` first. |
| `mineru@0.1.0` already in DB | Prints skip message; exits 0. No duplicate row. |
| `mineru` exists with a different version | Different `(name, version)` pair; select returns no row for `version='0.1.0'`; a new `mineru@0.1.0` row is inserted. This is correct behavior. |
| `image` column rejection | Impossible — `"dataplat/mineru:0.1.0"` is a non-empty string; the column is `TEXT NOT NULL`. |
| `config_schema` not valid JSONB | Impossible at the Python level — SQLAlchemy's `JSONB` mapped column serializes a Python `dict` to valid JSON before sending to Postgres. If for any reason it is malformed, Postgres raises a `DataError` and the seed exits non-zero. |
| Re-running checks.sh `operators)` against a fresh test DB | First call creates the row; second call (operators V3) skips it; both exit 0. |

---

## §10 Open Questions for Reviewer

| ID | Question | Recommendation |
|---|---|---|
| OQ-1 | Should `seed-operators` accept `--dry-run` or similar flags? | No — MVP. The idempotency guard (skip if exists + exit 0) already makes it safe to run repeatedly. A dry-run flag adds complexity without value at this stage. |
| OQ-2 | Should `image="dataplat/mineru:0.1.0"` be replaced with `""` or `"PLACEHOLDER"` to make its placeholder status more explicit? | Recommend the versioned image name `"dataplat/mineru:0.1.0"`. An empty string is a contract violation (column is `NOT NULL` with no zero-length exemption in business logic). A string like `"PLACEHOLDER"` is not a valid Docker image reference and would confuse tooling. The versioned name is a valid Docker image reference that will simply not be pullable until F-019 builds it. |
| OQ-3 | Should `required: []` in the config_schema be omitted (Python omits the key) or included explicitly? | Recommend including it explicitly as `"required": []` so the JSON Schema is unambiguous — an absent `required` key is equivalent to `[]` per the JSON Schema spec, but explicit is clearer for UI form renderers. |
