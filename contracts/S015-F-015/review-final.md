# S015-F-015 — Code Review (Mode B)

**Reviewer:** Claude (independent)
**Date:** 2026-05-25
**Commit reviewed:** a0c907f — `feat(api): F-015 seed-operators CLI — MinerU extractor row`
**Diff range:** b407140..a0c907f
**Contract:** `contracts/S015-F-015/agreed.md`

---

## Calibration checks (verify/reviewer-calibration.md)

- **CAL-1 (async session):** PASS — `cli.py:68` uses `async with SessionLocal() as session`; `cli.py:71` uses `await session.execute(select(...).where(...).where(...))`. `cli.py:126` uses `await session.commit()`. No `session.query()` anywhere in the diff.
- **CAL-2 (LLM gateway):** N/A — no LLM call, no SDK import.
- **CAL-3 (OpenAPI sync):** N/A — no router or Pydantic schema touched. Confirmed: `openapi.json` does not appear in the diff.
- **CAL-4 (lineage):** N/A — no Commit object created or modified. Correctly N/A per agreed.md §6.
- **CAL-5 (CAS path):** N/A — no blob bytes written, no MinIO interaction.
- **CAL-6 (schema freeze):** N/A — no Silver/Gold commit, no in-place migration of a published schema.
- **CAL-7 (Bronze faithfulness):** N/A — not a Bronze adapter.
- **CAL-8 (MVP scope):** PASS — changes are narrowly scoped to one CLI subcommand and one checks.sh layer. No out-of-scope items (no Celery, no OAuth, no ACL, no Docker-in-Docker).
- **CAL-9 (plugin isolation):** N/A — no plugin code.
- **CAL-10 (test coverage):** LOW per agreed §8 — no pytest file. Accepted by contract: agreed.md §8 explicitly declines a unit test per F-007 precedent, with rationale accepted at Mode A review. Integration V3 covers the skip-path. No new finding.
- **CAL-11 (bias check):** Actively applied. Specific evidence cited for every criterion below.

---

## Contract criteria verification

### 1. `seed-operators` subcommand added; `main()` dispatches via `asyncio.run(seed_operators())`; argparse subparser registered

PASS — `cli.py:149-153`: `subparsers.add_parser("seed-operators", help="...")` registered. `cli.py:159-160`: `elif args.command == "seed-operators": asyncio.run(seed_operators())`. `--help` works because argparse handles it automatically via `add_parser`. `if/elif/else` dispatch structure is correct.

### 2. Row values exact: name, version, category, input_kind, output_kind, image, config_schema

PASS — `cli.py:82-123`:
- `name="mineru"` — present
- `version="0.1.0"` — present
- `category="extractor"` — present
- `input_kind="source"` — present
- `output_kind="document"` — present
- `image="dataplat/mineru:0.1.0"` — non-empty, satisfies `TEXT NOT NULL`
- `config_schema` is a Python `dict` with `"type": "object"`, 3 named properties, `"required": []` — will be serialized to valid JSONB by SQLAlchemy at insert time. Not a string.

### 3. Idempotency keys on (name, version), not name alone

PASS — `cli.py:71-74`:
```python
select(Operator)
.where(Operator.name == "mineru")
.where(Operator.version == "0.1.0")
```
Both conditions are present. Matches the `uq_operator_name_version` UNIQUE constraint. A guard on name alone would be wrong if future versions are seeded; this is correct.

### 4. Invariant #5: async session throughout

PASS — verified at `cli.py:68` (`async with SessionLocal()`), `cli.py:71` (`await session.execute`), `cli.py:76` (`.scalars().first()`), `cli.py:125` (`session.add(op)`), `cli.py:126` (`await session.commit()`). No `session.query()`. No sync session anywhere in the diff.

### 5. Invariant #6: No openapi.json / make codegen in diff

PASS — confirmed: the diff does not touch `packages/api-types/openapi.json`, `apps/api/dataplat_api/routers/`, or `apps/api/dataplat_api/schemas/`. Correct and expected per agreed.md §6.

### 6. checks.sh `operators)` layer: V1/V2/V3 structure, BRE comment, all) chain, `*)` wildcard intact

PASS — `verify/checks.sh:1011-1064` (diff) / lines 911-964 (file as read):

- **V1a** (`checks.sh:1025-1029` in diff): SQL concatenates with `'|'` separator, greps `'^extractor|source|document$'`. BRE `|` is literal — correct, and the comment at lines 1021-1024 explains this explicitly per Mode A NIT. Non-vacuous: requires all three column values correct.
- **V1b** (`checks.sh:1033-1037` in diff): `COUNT(*) WHERE name='mineru'` greps `'^1$'`. Row count exactly 1.
- **V2** (`checks.sh:1044-1048` in diff): `config_schema->>'type'` via JSONB `->>` operator, greps `'^object$'`. Non-vacuous: Postgres must have parsed the value as JSONB at INSERT time; NULL or malformed JSONB returns empty and fails the grep. This legitimately proves criterion 2.
- **V3** (`checks.sh:1053-1060` in diff): second `seed-operators` invocation, then re-checks COUNT = 1. Idempotency path covered.
- **`all)` chain** (`checks.sh:977` in diff): `bash "$0" operators   # F-015` inserted after `bash "$0" runs` before `;;`. Verified at file lines 980-981.
- **`*)` wildcard** (`checks.sh:982-985` in file): intact and unmodified. Case structure is clean: `operators)` block ends with `;;` at line 964; `all)` follows immediately; `*)` follows `all)`; `esac` closes correctly.

---

## Additional findings

No additional findings beyond what was already addressed in Mode A review. Specific verifications:

- **seed_admin not disturbed:** `cli.py:29-56` is unchanged in the diff. Behavior of the existing `auth)` layer is unaffected.
- **No scope creep:** Only `apps/api/dataplat_api/cli.py`, `verify/checks.sh`, and sprint artifact files (`contracts/`, `claude-progress.txt`) are touched. No model, router, schema, or migration files modified.
- **config_schema is a Python dict, not a string:** `cli.py:99-123` assigns a dict literal. SQLAlchemy's `JSONB` mapped column serializes this to valid JSON on INSERT. The V2 check correctly exercises the JSONB storage path.

---

APPROVED

All 6 contract criteria verified with specific file:line evidence. CAL-1 through CAL-11 checked; all PASS or N/A. No blocking or high findings. The one LOW (no unit test) was accepted in the agreed contract. Implementation matches agreed.md exactly.
