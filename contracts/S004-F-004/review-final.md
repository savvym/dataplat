# Mode B Review — S004-F-004 DagsterGateway Abstraction + Admin Status Route

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Commit:** 34e8467
**Diff base:** 1fafc79..34e8467
**Contract:** contracts/S004-F-004/agreed.md

---

## DECISION: APPROVED

Two minor findings noted below. Neither is a blocker or major: the 503 status code, structure, and routing are all correct; all hard invariants are satisfied; CAL-10 tests are delivered. The verifier may proceed.

---

## Contract criteria

- **agreed.md §2 (13 files):** PASS — all 13 files present in diff. `dagster/__init__.py`, `dagster/gateway.py`, `dagster/dependencies.py`, `schemas/__init__.py`, `schemas/admin.py`, `routers/admin.py`, `main.py` (modified), `config.py` (modified), `pyproject.toml` (modified), `uv.lock` (regenerated), `docker-compose.dev.yml` (modified), `.env.example` (modified), `verify/checks.sh` (modified). Count confirmed.

- **agreed.md §3.1 (6 failure modes in `get_dagster_version`):** PASS — `gateway.py` lines 98–131 cover all six: `TimeoutException` (line 102), `ConnectError` (line 105), `HTTPError` catch-all (line 108), HTTP non-2xx via `response.is_success` (line 112), non-JSON body via `except Exception` wrapping `response.json()` (line 117), GraphQL `"errors"` key check (line 121), missing/absent `data["version"]` via `except (KeyError, TypeError)` (line 126), None/empty string check (line 131). Every `except` clause has an inline comment naming the exception and the resulting 503 outcome per impl note N-2. No `KeyError`, `ValueError`, or httpx exceptions escape the method.

- **agreed.md §3.2 (lifespan singleton, `app.state`, `asynccontextmanager`):** PASS — `main.py` uses `@asynccontextmanager async def lifespan(app: FastAPI) -> AsyncIterator[None]`. `app.state.dagster_gateway = gateway` set before `yield`. `await gateway.aclose()` called after `yield` for clean teardown. `app = FastAPI(..., lifespan=lifespan)` — no deprecated `@app.on_event`. Return type `AsyncIterator[None]` is correct (unlike conftest.py; see Finding 2).

- **agreed.md §3.3 (enforcement boundary docstring):** PASS — `gateway.py` module-level docstring lines 1–17 reproduce the §3.3 rule verbatim: "All FastAPI → Dagster GraphQL calls MUST go through `apps/api/dataplat_api/dagster/gateway.py`. No other module in `apps/api/` — and no plugin — may import `httpx` to call Dagster directly." Impl note N-3 satisfied.

- **agreed.md §4 (route shape — status codes):** PASS for status codes. The route returns 200 on success and 503 on `DagsterGatewayError`. The `DagsterStatusResponse` Pydantic model is `{"dagster_version": str}`. The `type: ignore[return-value]` comment on the `JSONResponse` return acknowledges the mypy annotation tension (agreed in §4). **See Finding 1 for a minor deviation in the 503 detail string.**

- **agreed.md §5 (config):** PASS — `config.py` adds `DAGSTER_GRAPHQL_URL: str = "http://dagster-webserver:3000/graphql"` with correct default and rename comment.

- **agreed.md §6 (`docker-compose.dev.yml` three changes):** PASS — (1) `DAGSTER_GRAPHQL` → `DAGSTER_GRAPHQL_URL` in environment block; (2) `dagster-webserver: condition: service_healthy` added to `depends_on`; (3) `.env.example` line 25 renamed with migration comment. Old stale comment "Does NOT depend on dagster-webserver this sprint" removed from compose.

- **agreed.md §7 (`dagster)` block in `checks.sh`):** PASS — V1 uses `curl ... | python3 -c "... json.load(sys.stdin)"` (no shell injection). Boundary grep placed between V1 and V2 with `|| true` + `[[ -n "$BAD_CALLS" ]]` pattern correct under `set -euo pipefail`. V2 restart loop bounded to 30 iterations with `READY` flag and clear failure message. `bash "$0" dagster` added to `all)` block after `bash "$0" buckets`. Block ends with `;;`.

- **agreed.md §8 (OQ-7 tests):** PASS — `test_admin_dagster_status.py` delivers exactly two tests: `test_dagster_status_200` (happy path, asserts 200 + `{"dagster_version": "1.11.16"}`) and `test_dagster_status_503_on_gateway_error` (failure path, asserts 503 + `"detail"` key). `pytest>=9.0.1` and `pytest-asyncio>=1.3.0` added to `[dependency-groups] dev`. `httpx==0.28.1` added to `[project.dependencies]` and confirmed present in `uv.lock` (version 0.28.1).

- **agreed.md §10 invariant #5 (async):** PASS — Every method in `gateway.py` is `async def`. `httpx.AsyncClient` used exclusively (no `httpx.Client`). No `requests` imports anywhere in diff. `lifespan` is `async def` with `AsyncIterator[None]` return type. Route handler `dagster_status` is `async def`. No `session.query()` or sync DB code.

- **agreed.md §10 invariant #6 (OpenAPI sync deferred):** PASS — `packages/` directory still does not exist on disk. `checks.sh` `contract` layer exits 0 early. `DagsterStatusResponse` model includes a docstring noting when codegen pipeline will be wired.

---

## Calibration checks

- **CAL-1 (async session):** N/A — no DB code touched in this diff. Verified: no `session.query`, no `.commit()` without `await` in any changed file.

- **CAL-2 (LLM gateway):** N/A — no LLM calls. No `import anthropic`, `import openai`, or direct LLM SDK calls anywhere in diff.

- **CAL-3 (OpenAPI sync):** PASS — deferral valid. `packages/` does not exist. `checks.sh` `contract` layer (line 82 of existing file) exits 0 early with "no packages/api-types yet". No Makefile. `DagsterStatusResponse` in `schemas/admin.py` is confirmed as a Pydantic `BaseModel` (will auto-feed codegen when pipeline is wired).

- **CAL-4 (lineage completeness):** N/A — no Commit objects created or modified.

- **CAL-5 (CAS path discipline):** N/A — no blob storage.

- **CAL-6 (schema freeze):** N/A — no Silver/Gold schema changes.

- **CAL-7 (Bronze faithfulness):** N/A — no adapter/processor code.

- **CAL-8 (MVP scope discipline):** PASS — no Celery, DinD, auth flows, granular ACL, training frameworks, or Kafka. Admin route correctly marked `TODO(F-008)` for JWT wiring. No deferred features introduced.

- **CAL-9 (plugin isolation):** N/A — no plugin work. `gateway.py` does not import from any plugin; plugins are not referenced anywhere in the diff.

- **CAL-10 (test coverage — happy + failure):** PASS — `test_dagster_status_200` (happy path: mock returns `"1.11.16"` → assert 200, `{"dagster_version": "1.11.16"}`). `test_dagster_status_503_on_gateway_error` (failure path: mock raises `DagsterGatewayError("connection refused")` → assert 503, `"detail"` key present). Two test functions confirmed by `grep -c "^def test_"` = 2.

- **CAL-11 (bias check):** Applied. Each criterion above is backed by specific file or line-number evidence from the diff. No vague approval language used.

---

## Findings

### Finding 1 — MINOR: 503 response body uses `str(exc)` instead of the fixed string specified in agreed.md §4

**Location:** `apps/api/dataplat_api/routers/admin.py`, the `except DagsterGatewayError` branch.

**Contract says** (agreed.md §4 lines 183 and 219):
```
Response 503 Service Unavailable:
    {"detail": "Dagster unreachable"}
```
```python
content={"detail": "Dagster unreachable"},
```

**Implementation does:**
```python
content={"detail": str(exc)},
```

`str(exc)` produces implementation-internal messages such as `"Dagster request timed out"`, `"Cannot connect to Dagster"`, `"Dagster GraphQL error: ..."`, etc. — not `"Dagster unreachable"`.

**Impact:** Low. The 503 status code is correct. The `"detail"` key is present. The error messages are human-readable and arguably more useful for debugging. The admin endpoint has no frontend consumer yet. The feature_list.json F-004 verification criteria do not check the detail string.

**What's wrong:** (a) The contract was not followed precisely. (b) The 503 test asserts `"connection refused" in body["detail"]` — it tests the implementation detail (the mock's message) rather than the API contract (the fixed string). If a future change normalizes the message back to the fixed string, the test would fail on a correct implementation. (c) Leaking internal error message strings from an admin endpoint is a minor information-disclosure concern, though low-risk given this is an internal admin route that will eventually be JWT-protected.

**This is not a blocker for approval**, but the implementer should either: (a) change `str(exc)` back to the fixed `"Dagster unreachable"` string as specified in the contract, or (b) amend the contract (via a follow-up note in `agreed.md` or in `claude-progress.txt`) acknowledging the deviation. The test assertion should be updated to match whichever behavior is chosen.

---

### Finding 2 — MINOR: `conftest.py` fixture has wrong return annotation and no TODO for future integration test scope

**Location:** `apps/api/tests/conftest.py`, `_patch_httpx_no_ssl` fixture definition.

**Wrong return annotation:**
```python
def _patch_httpx_no_ssl() -> pytest.FixtureRequest:
```
`pytest.FixtureRequest` is the type of the `request` parameter injected by pytest, not a fixture return type. A generator fixture that `yield`s `None` should be annotated `-> Iterator[None]` (or `-> Generator[None, None, None]`). The current annotation is functionally harmless (pytest ignores fixture return annotations; `mypy` only runs on `dataplat_api/`, not `tests/`), but it is incorrect and will confuse future readers.

**Missing TODO for integration test scope:**
The `autouse=True` fixture applies to every test function in the entire `apps/api/tests/` tree. Any future integration test that wants a real `httpx.AsyncClient` will silently receive a `MockTransport` that returns HTTP 500 unless it explicitly passes `transport=...` to `AsyncClient`. The fixture is correctly defensive (the 500 response fails loudly with `"test: unpatched httpx call"`), but there is no `# TODO` or comment warning future test authors. The agreed.md review noted this risk (Mode A iter-2 feedback N-2, N-3 adjacent discussion). A single comment explaining the scope would prevent confusion.

**Suggested fixes (non-blocking):**
1. Change `-> pytest.FixtureRequest` to `-> Iterator[None]` and add `from collections.abc import Iterator` import.
2. Add a comment: `# NOTE: autouse=True applies to all tests in this package. Future integration tests that need a real HTTP stack must pass transport= explicitly to httpx.AsyncClient, or move to a separate conftest.py that does not apply this patch.`

Neither issue is a blocker. The deviation from agreed.md is that the conftest was not described in the contract at all (it is explicitly called out as a "deviation to evaluate" in the review brief). The workaround is sound, the defensive behavior is correct, and the underlying SSL issue it solves is a real environment constraint.

---

## Deviation evaluation — `conftest.py` autouse httpx monkeypatch

**Is this an acceptable test-environment workaround?** Yes. The implementer's claim is verified: `test_admin_dagster_status.py` patches `get_dagster_version` at the method level via `AsyncMock` — the actual `httpx.AsyncClient.post()` is never called in any of these tests. The conftest patch is defensive (it catches accidental unpatched network calls) rather than load-bearing (the tests would pass without it if the SSL issue weren't present). The MockTransport returns HTTP 500 with a clear message, making any accidental real network call fail loudly rather than silently.

**Does it mask real httpx behavior?** No, for this sprint's tests. The gateway's `get_dagster_version()` implementation is tested at the integration level by `verify/checks.sh dagster` against a live Dagster instance — that is where real httpx behavior matters, and the conftest patch is not active there.

**Is the patch correctly scoped?** Functionally yes — it does not affect production containers. Architecturally, the `autouse=True` with no session/module scoping means it covers all future tests, which could surprise future integration test authors. See Finding 2.

**Is `unittest.mock.patch` exception-safe?** Yes. `patch.object` is a context manager from `unittest.mock`; the `with` block restores `httpx.AsyncClient.__init__` even if the test raises an exception.

---

## Verifier handoff notes

The verifier should run `bash verify/checks.sh dagster` from the repo root with the compose stack up. The expected sequence:
1. V1 hits `http://localhost:18000/api/admin/dagster-status` → 200 with `dagster_version` key non-empty.
2. Boundary grep: no output → gateway boundary check OK.
3. V2 restarts fastapi container, waits up to 30s for `/healthz`, then re-runs V1.
4. `bash verify/checks.sh backend` runs `uv run pytest -q` → 2 tests pass.

The implementer's 503 sanity check (stop dagster-webserver → 503; restart → 200) is consistent with the gateway's error handling but is not part of the formal `checks.sh dagster` script — the verifier does not need to reproduce it unless instructed.

