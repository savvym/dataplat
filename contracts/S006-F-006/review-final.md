# S006-F-006 review-final (Mode B)

## Verdict

APPROVED

---

## Calibration checks (verify/reviewer-calibration.md)

- **CAL-1 (async session):** PASS — `main.py:32-33` uses `async with engine.begin() as conn: await conn.execute(text("SELECT 1"))`. No `session.query()`, no sync session, no `.commit()` without await anywhere in the diff. Hard invariant #5 satisfied.

- **CAL-2 (LLM gateway):** N/A — no LLM SDK imports or calls in this diff. Grep confirmed: no `anthropic`, `openai`, or `LLMGateway` additions.

- **CAL-3 (OpenAPI sync):** N/A — no changes to `apps/api/dataplat_api/routers/` or `schemas/`. No `make codegen` required. Confirmed by `git diff --name-only`: only `main.py`, `conftest.py`, `checks.sh`, `claude-progress.txt` changed.

- **CAL-4 (lineage completeness):** N/A — no Commit creation in this sprint.

- **CAL-5 (CAS path discipline):** N/A — no blob storage operations.

- **CAL-6 (schema freeze):** N/A — no schema changes.

- **CAL-7 (Bronze faithfulness):** N/A — no adapter work.

- **CAL-8 (MVP scope):** PASS — no Celery, OAuth, Docker-in-Docker, training framework, Kafka, or other deferred features. Three files changed, all within stated scope.

- **CAL-9 (plugin isolation):** N/A — no plugin work.

- **CAL-10 (test coverage on happy path + failure):** PASS — smoke layer has concrete V1/V2/V3 verifications covering success and failure modes per agreed.md §4. The `_patch_engine_begin` fixture preserves all 7 existing backend tests (verified: `pytest -q` outputs `7 passed in 0.31s`). The new lifespan probe itself is the production happy-path; the test isolation fixture covers the failure mode (engine.begin raises without DB → TestClient breaks).

- **CAL-11 (bias check):** This review contains concrete `file:line` evidence for every criterion. Not relying on implementer self-assessment. Pyright diagnostics independently categorized.

---

## Compliance against agreed.md

### File 1 — `verify/checks.sh`

- **PASS:** Old `pytest -k smoke || true` body fully removed (lines 61-90 replaced entirely).
- **PASS:** Port variables declared at case start: `FASTAPI_HOST_PORT` (18000), `DAGSTER_HOST_PORT` (13000), `MINIO_API_HOST_PORT` (19000) — `checks.sh:62-64`.
- **PASS:** C1 — `curl -fsS .../healthz | grep -q '"ok"' || { FAIL; exit 1; }` — `checks.sh:67-70`. Correct failure message text matches agreed.md verbatim.
- **PASS:** C2 — comment + unconditional echo only — `checks.sh:72-75`. No active shell test needed per contract justification.
- **PASS:** C3 — two-tier guard present — `checks.sh:78-83`. Assignment-level guard (`STATUS=$(...) \ || { FAIL_transport; exit 1; }`) at lines 78-80; value-level guard (`[[ "$STATUS" == "200" ]] \ || { FAIL_status; exit 1; }`) at lines 81-82. Both fire in the correct scenarios.
- **PASS:** C4 — `curl -fsS .../server_info | grep -q '"dagster_version"' || { FAIL; exit 1; }` — `checks.sh:86-89`. Uses `/server_info` (not `/dagster_version` which returns SPA HTML in Dagster 1.11+).
- **PASS:** Case terminated with `;;` at `checks.sh:90`.
- **PASS:** `all)` block now calls `bash "$0" smoke` first — `checks.sh:315-318`. Comment present.
- **PASS:** No `set +e` or `|| true` in the new smoke section. The two `|| true` instances at lines 223 and 311 are pre-existing in the `dagster)` and `runs)` layers respectively, unchanged by this diff.
- **PASS:** No changes to `infra)`, `migration)`, `buckets)`, `dagster)`, `runs)`, `backend)`, `frontend)`, `contract)` case bodies.

### File 2 — `apps/api/dataplat_api/main.py`

- **PASS:** `from sqlalchemy import text` imported at `main.py:11`.
- **PASS:** `from dataplat_api.db.session import engine` imported at `main.py:15`. Import path matches `db/session.py:13` (`engine = create_async_engine(...)`).
- **PASS:** DB probe runs before `DagsterGateway()` constructor — `main.py:32-33` probe, `main.py:34` gateway construction. Order matches agreed.md.
- **PASS:** `async with engine.begin() as conn: await conn.execute(text("SELECT 1"))` — fully async, no sync API, no `session.query()`.
- **PASS:** If Postgres is unreachable, `engine.begin()` raises, lifespan never reaches `yield`, uvicorn shuts down — `/healthz` unreachable. This is the C2 guarantee.
- **PASS:** No new routes, no new Pydantic models, no OpenAPI surface change.
- **PASS:** `mypy dataplat_api` exits 0 (`Success: no issues found in 16 source files`).
- **PASS:** `ruff check .` exits 0 (`All checks passed!`).

### File 3 — `apps/api/tests/conftest.py`

- **PASS:** `@pytest.fixture(autouse=True)` decorator present — `conftest.py:62`.
- **PASS:** `@asynccontextmanager` decorator on `fake_begin` — `conftest.py:82`.
- **PASS:** `conn.execute = AsyncMock(return_value=None)` — `conftest.py:85`. Satisfies `await conn.execute(text("SELECT 1"))` without raising.
- **PASS:** `fake_begin` signature `(self: object = None)` — `conftest.py:83`. Absorbs implicit instance argument from class-level dispatch.
- **PASS:** `with patch.object(AsyncEngine, "begin", fake_begin): yield` — `conftest.py:88-89`. Class-level patch, correct target.
- **PASS:** New imports: `AsyncGenerator, Iterator` from `collections.abc`; `asynccontextmanager` from `contextlib`; `AsyncMock, MagicMock` added to `unittest.mock` import; `AsyncEngine` from `sqlalchemy.ext.asyncio` — all stdlib or already-in-lockfile sqlalchemy.
- **NOTE:** `import dataplat_api.db.session as db_session` (specified in agreed.md) is absent — this is a direct consequence of the class-level patch deviation and is correct: with class-level patching the `db_session` alias is no longer needed. The fixture patches `AsyncEngine` directly instead.
- **PASS:** `AsyncEngine` import placed after `os.environ.setdefault` lines with `# noqa: E402` — `conftest.py:35`. This is cosmetically imperfect (sqlalchemy does not read environment variables at import time so the E402 suppression is unnecessary), but harmless. Functionally correct.
- **PASS:** `from collections.abc import AsyncGenerator, Iterator` — both used: `Iterator[None]` as fixture return type (`conftest.py:63`), `AsyncGenerator[MagicMock, None]` as `fake_begin` return annotation (`conftest.py:83`).
- **PASS:** All 7 tests pass with the new fixture active: `pytest -q` outputs `7 passed in 0.31s`.

---

## Deviation analysis: class-level vs instance-level patch

**Agreed.md specification:** `patch.object(db_session.engine, "begin", fake_begin)` — instance-level patch.

**Implementation:** `patch.object(AsyncEngine, "begin", fake_begin)` — class-level patch.

**Reason the instance-level patch is impossible:** Verified empirically using the project's own venv (`uv run python`). `AsyncEngine` defines `__slots__ = ('sync_engine', '_proxied')` and all classes in its MRO (`ProxyComparable`, `ReversibleProxy`, `AsyncConnectable`) also define `__slots__` without a `__dict__` entry. Consequently, `AsyncEngine` instances have no `__dict__` and cannot receive arbitrary attribute assignments. Attempting `engine.begin = fn` or `patch.object(engine_instance, "begin", fn)` raises `AttributeError: 'AsyncEngine' object attribute 'begin' is read-only`. This is a genuine CPython behavior, not an environment quirk.

**Is the class-level patch functionally equivalent?** Yes. Tracing the call: when the lifespan executes `async with engine.begin() as conn:`, Python's attribute lookup on `engine` (the instance) finds no `begin` in the instance (no `__dict__`), then searches the class `AsyncEngine.__dict__`. Under `patch.object(AsyncEngine, "begin", fake_begin)`, `AsyncEngine.__dict__["begin"]` is now `fake_begin`. Python's descriptor protocol then calls `fake_begin.__get__(engine, AsyncEngine)`, which returns `fake_begin` bound to `engine` as `self`. The call `engine.begin()` therefore invokes `fake_begin(self=engine)`. The `@asynccontextmanager` shim wraps the async generator and returns a context manager. `async with engine.begin() as conn:` receives the `MagicMock` yielded by `fake_begin`. `await conn.execute(text("SELECT 1"))` calls the `AsyncMock`, returns `None`. Lifespan proceeds normally. This was confirmed by a direct runtime test: `fake_begin` receives the engine instance as `self`, and the full `async with engine.begin() as conn: await conn.execute(...)` call completes without error.

**Verdict: acceptable deviation.** The agreed.md approach was impossible at runtime; the implementation uses the only viable alternative. The functional contract — lifespan does not attempt a real DB connection during unit tests — is identically preserved. The docstring in `conftest.py:72-79` accurately explains the reason for the class-level approach.

**One non-blocking concern:** The class-level patch affects ALL `AsyncEngine` instances during each test, not only the module-level `engine` singleton from `db/session.py`. Any future integration test that creates its own `AsyncEngine` and expects a real `engine.begin()` call will silently get the mock instead, potentially masking bugs. This is a future risk only (no such tests exist today) and is an inherent trade-off of the instance-level approach being unavailable. Noted as INFO below.

---

## Findings

No blockers, no highs, no mediums. One low-severity cosmetic note:

1. [LOW] `apps/api/tests/conftest.py:35` — `from sqlalchemy.ext.asyncio import AsyncEngine` is placed after the `os.environ.setdefault` lines with a `# noqa: E402`. Sqlalchemy does not read environment variables at import time, so this import does not require the setdefaults to precede it. It could be moved to the top-of-file third-party block without any functional change. This is cosmetically inconsistent with the comment at lines 29-31 which explains why `dataplat_api.*` imports must come late. Not a bug; future cleanup can relocate it.

---

## INFO (non-blocking)

- **Pyright env-level imports** (`fastapi`, `fastapi.testclient` unresolvable on host Pyright): expected. Identical to S004/S005 precedent. Container has fastapi installed; host Pyright does not.

- **`main.py:33` Pyright `Cannot access attribute "execute" for class "None"`:** Pyright cannot narrow the `AsyncConnection | None` yield type of `engine.begin()`. At runtime it always yields `AsyncConnection` (or raises). Not a runtime bug.

- **`conftest.py` `Iterator[None]` / `FixtureRequest` mismatch:** Pre-existing pattern from S004. Cosmetic; noted for future cleanup.

- **`conftest.py:83` `self` parameter unused warning:** Intentional. `fake_begin(self=None)` exists solely to absorb the bound engine instance passed by Python's descriptor protocol when the class-level patch dispatches `engine.begin()`. The `self` variable is structurally required but not operationally needed inside the function body.

- **Autouse fixture masks future integration tests:** Class-level `patch.object(AsyncEngine, "begin", fake_begin)` is active for all tests. Any future test that creates a new `AsyncEngine` instance and expects the real `begin()` method will silently receive the mock. Future integration tests requiring a real DB connection must explicitly disable the autouse fixture (e.g., via a session-scoped override or a `_patch_engine_begin` fixture that yields without patching when a `--live-db` flag is present). Non-blocking for current scope.

- **`AsyncEngine` import placement (`conftest.py:35`):** Placed after `setdefault` lines with `E402 noqa`. Functionally correct. The low-severity finding above covers this.

- **`all)` block ordering:** `smoke` now runs first per agreed.md. Remaining order (`infra → backend → frontend → contract → migration → buckets → dagster → runs`) unchanged. Correct.
