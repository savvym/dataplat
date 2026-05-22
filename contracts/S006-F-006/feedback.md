# S006-F-006 Mode A review — iteration 3

## Verdict

APPROVED

---

## Resolution of iter 1 + iter 2 findings

- **B-1 (iter 1): RESOLVED** — §3 "Lifespan DB probe change in main.py" shows the correct before/after diff. `from dataplat_api.db.session import engine` is verified against `db/session.py` line 13 (`engine = create_async_engine(...)`). The probe runs before `DagsterGateway()` is constructed. The C2 comment accurately states "FastAPI lifespan runs a SELECT 1 probe on startup (added this sprint); /healthz is unreachable if Postgres is down." `apps/api/dataplat_api/main.py` is in the §2 files-to-change table. Hard invariant #5 satisfied: `async with engine.begin()` + `await conn.execute()` — no sync session, no `session.query()`.

- **H-1 (iter 1): RESOLVED** — C3 uses the two-tier guard consistently in both the inline description and the "Complete new smoke) case body" code block. Assignment-level `|| { echo "FAIL: smoke C3 MinIO connectivity: connection refused or curl error"; exit 1; }` fires on transport failure; `[[ "$STATUS" == "200" ]]` guard fires on HTTP non-200.

- **B-2 (iter 2): RESOLVED** — All four sub-requirements satisfied:
  1. `apps/api/tests/conftest.py` is in the §2 files-to-change table (row 4, "Add autouse fixture `_patch_engine_begin`").
  2. §3 contains a complete subsection "Conftest autouse engine mock for unit-test isolation" with full fixture code.
  3. Fixture correctness confirmed:
     - Patch target is `patch.object(db_session.engine, "begin", fake_begin)` — `engine` is module-level at `db/session.py:13`. Correct.
     - `@asynccontextmanager` on `fake_begin` — satisfies `async with engine.begin() as conn:` contract. Correct.
     - `conn.execute = AsyncMock(return_value=None)` — satisfies `await conn.execute(text("SELECT 1"))` without raising. Correct.
     - `@pytest.fixture(autouse=True)` — all tests pick it up automatically. Correct.
  4. §6 R-5 is fully rewritten: the false claim ("existing test model requires the stack for the httpx fixture reason") is gone. The replacement accurately describes: (a) all 7 tests are fully network-independent, (b) `_patch_httpx_no_ssl` is an SSL workaround not a stack-dependency signal, (c) conftest `DATABASE_URL` resolves to `localhost:5432` which is not the compose Postgres (mapped to host port 15432, user `app`, db `platform`), (d) `_patch_engine_begin` is the mitigation and keeps production code unaffected.

---

## Calibration checks

- **CAL-1 (async session):** PASS — lifespan diff uses `async with engine.begin() as conn: await conn.execute(...)`. No `session.query()` or sync session in any proposed code.
- **CAL-2 (LLM gateway):** N/A — no LLM calls in this sprint.
- **CAL-3 (OpenAPI sync):** N/A — §5 confirms no new routes; `make codegen` explicitly not required.
- **CAL-4 (lineage):** N/A — no Commit creation.
- **CAL-5 (CAS path):** N/A — no blob storage operations.
- **CAL-6 (schema freeze):** N/A — no schema changes.
- **CAL-7 (Bronze faithfulness):** N/A — no adapter work.
- **CAL-8 (MVP scope):** PASS — no Celery, OAuth, Docker-in-Docker, or other deferred features.
- **CAL-9 (plugin isolation):** N/A — no plugin work.
- **CAL-10 (test coverage):** PASS — smoke layer has observable V1/V2/V3 verifications; `_patch_engine_begin` preserves existing 7-test backend layer; no production code added without corresponding test path.
- **CAL-11 (bias check):** Approving with concrete `file:line` evidence throughout. Not relying on implementer self-assessment.

---

## Scope discipline

- Three files in scope: `verify/checks.sh`, `apps/api/dataplat_api/main.py`, `apps/api/tests/conftest.py`. Confirmed §2 and §5.
- No new Python dependencies. All new imports (`asynccontextmanager`, `Iterator`, `AsyncMock`, `MagicMock`) are stdlib.
- No changes to `db/session.py`. Explicitly listed in §7 "Out of scope."
- §7 contains no stale text from prior iterations. The `_patch_engine_begin` scope note is present.

---

## New findings

None. No blockers, no new high-severity issues.

---

## Non-blocking notes for implementation

1. **Return type annotation:** Use `Iterator[None]` from `collections.abc` (not `Generator[None, None, None]`). The proposal already specifies this — confirm the import `from collections.abc import Iterator` is added to conftest.

2. **`# type: ignore[return]` on `fake_begin`:** Already shown in the proposal's fixture code. This is the correct idiom to suppress pyright's complaint about the `@asynccontextmanager`-wrapped generator having no explicit return annotation. Keep it.

3. **Import ordering in conftest.py:** The new imports should follow the existing pattern (stdlib before third-party). Suggested order: extend the existing `from unittest.mock import patch` line to `from unittest.mock import AsyncMock, MagicMock, patch`; add `import dataplat_api.db.session as db_session` after the third-party section; `from collections.abc import Iterator` and `from contextlib import asynccontextmanager` alongside other stdlib imports.

4. **`patch.object` on instance attribute vs. module-level name:** The fixture uses `patch.object(db_session.engine, "begin", fake_begin)` which replaces the bound method on the engine singleton. This is correct for the current codebase (engine is a module-level singleton). The alternative `patch("dataplat_api.db.session.engine.begin", ...)` would also work but `patch.object` is more explicit. Either form is acceptable; the proposal's choice is fine.

5. **Fixture independence from `_patch_httpx_no_ssl`:** Both autouse fixtures use separate `patch.object` targets and independent `with` blocks. No ordering dependency between them. Correct as proposed.

6. **V2 assertion note:** §4 V2 correctly notes that `grep -c ": OK"` is a substring match and the C2 line (`smoke C2 DB connection: OK (via FastAPI lifespan)`) contains `: OK` as a substring. The count of 4 is correct. No issue.
