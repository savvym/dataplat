# S007-F-007 Mode B Review

**Reviewer:** Claude (independent)
**Date:** 2026-05-22
**Commit under review:** 07c1c3c
**Parent baseline:** 3ed5846
**Contract:** contracts/S007-F-007/agreed.md
**Diff stats:** 19 files, +2095/-1 (confirmed via `git diff --stat`)

---

## Verdict: APPROVED

---

## Findings (numbered, by severity)

### BLOCKER

None.

### HIGH

None.

### MEDIUM

**M-1 — `RUN_INTEGRATION_TESTS=1` comment is misleading; the env var does nothing**
File: `apps/api/pyproject.toml:43-44` and `apps/api/tests/test_auth.py:12`

`pyproject.toml` says:
```
# Skip integration tests by default; run with RUN_INTEGRATION_TESTS=1 to include them.
addopts = "-m 'not integration'"
```

`test_auth.py` says (line 12): "skipped in backend) layer unless RUN_INTEGRATION_TESTS=1".

But no code reads `RUN_INTEGRATION_TESTS`. The `addopts` value is unconditional — pytest always deselects `@pytest.mark.integration` tests regardless of that env var. The only way to run `test_seed_admin_creates_one_row` via pytest is to explicitly override `addopts` on the command line (e.g., `pytest --override-ini=addopts= -m integration`). The comment creates a false expectation for future developers.

This is not a correctness bug (the `auth)` layer in checks.sh covers V1 via docker compose exec, not pytest), but it is misleading documentation. Should be fixed: either remove the `RUN_INTEGRATION_TESTS` reference from comments, or implement the override (e.g., conditional addopts in a `conftest.py` hook, or a `pytest_configure` plugin that reads the env var).

### LOW

**L-1 — `uv.lock` not listed in agreed.md §3 files-changed table**
File: `apps/api/uv.lock`

The lock file changed (90 lines added, new entries for `bcrypt 5.0.0`, `PyJWT`, `python-multipart`). This is an expected and correct consequence of adding three new deps to `pyproject.toml`. It is NOT a scope violation. The agreed.md §3 table simply omits it as an implicit side-effect. No action required; noting for completeness.

**L-2 — `Dockerfile` pre-installation list not updated with new deps**
File: `docker/api/Dockerfile`

The Dockerfile's pre-installation `pip install` list at lines 16-24 does not include `bcrypt`, `PyJWT`, or `python-multipart`. These ARE installed by the subsequent `pip install -e .` which reads `pyproject.toml`, so a fresh `docker compose build` correctly installs all deps. However, the unlisted pre-install block exists as an optimization layer-cache pattern — its value is reduced if it silently diverges from actual runtime deps. This is a maintainability concern, not a correctness bug (confirmed: `import bcrypt; import jwt; import multipart` succeeds in the running container).

### NIT

**N-1 — `_DUMMY_HASH` rounds consistency (carried from Mode A impl note 1)**
File: `apps/api/dataplat_api/routers/auth.py:41`

`bcrypt.gensalt(rounds=12)` is correctly used for `_DUMMY_HASH` (the impl followed the Mode A review suggestion). This is explicitly noted as "reviewer impl note 1" in a code comment. No action required; noting as resolved.

### INFO (no fix required)

**I-1 — `bcrypt 5.0.0` resolved (contract says `>=4.0.0`)**
The lock file resolves `bcrypt>=4.0.0` to `bcrypt 5.0.0`. The `bcrypt.hashpw / bcrypt.checkpw / bcrypt.gensalt` API is stable across 4.x and 5.x. The contract rationale (dropping passlib because `bcrypt.__about__` was removed in 4.0) applies equally to 5.0. No breakage.

**I-2 — `test_seed_admin_creates_one_row` never runs in any check layer**
The integration test is deselected by `addopts = "-m 'not integration'"` in `backend)`. The `auth)` layer covers V1 via `docker compose exec psql` (not pytest). There is no check layer that actually runs this test. This is by design (the auth) layer's docker compose exec approach is a superset of what the integration test does), but it means the pytest-based integration test is effectively dead code in the current workflow. Acceptable given the auth) layer coverage; note for F-008 when integration test patterns may be revisited.

---

## Deviation analysis

### Deviation 1: `python-multipart>=0.0.9` added to `pyproject.toml`

- **Self-reported claim:** "Real runtime requirement of `OAuth2PasswordRequestForm`; FastAPI raises at import time without it."
- **Verified by:** `apps/api/pyproject.toml:32-33`; FastAPI's form body parsing delegates to python-multipart for `application/x-www-form-urlencoded`; the import error occurs at route invocation time (not strictly at import time, but early enough that it surfaces immediately in testing). The dependency is genuinely required for the feature to function.
- **Verdict:** ACCEPT
- **Rationale:** This is a correct runtime dependency. It was not in agreed.md §3 because it was discovered during implementation. It does not constitute scope creep — it is a required dependency of `OAuth2PasswordRequestForm` which IS in scope.

### Deviation 2: `auth)` layer uses `python -m dataplat_api.cli` (not `uv run python -m ...`)

- **Self-reported claim:** "The fastapi container uses pip, not uv, inside `docker compose exec`."
- **Verified by:** `docker/api/Dockerfile` — FROM python:3.12-slim; installs via `pip install --no-cache-dir ... && pip install --no-cache-dir -e .`; no uv installed. `verify/checks.sh:329-331` uses `docker compose exec -T fastapi python -m dataplat_api.cli seed-admin ...`. The container Python environment has `dataplat_api` on `sys.path` via the editable install, so `python -m dataplat_api.cli` works correctly without uv.
- **Verdict:** ACCEPT
- **Rationale:** The Dockerfile confirms no uv is present in the container. `python -m dataplat_api.cli` is the correct invocation for the pip-based container environment. The agreed.md §4.3 "uv run python -m ..." refers to local development invocation outside the container.

### Deviation 3: `addopts = "-m 'not integration'"` added to `[tool.pytest.ini_options]`

- **Self-reported claim:** "Needed to skip the integration test in `backend)` layer; agreed.md mentioned the marker but not the skip mechanism. Acceptable if the `auth)` layer's behavior is unaffected."
- **Verified by:** `apps/api/pyproject.toml:39-44`; `verify/checks.sh:321-368` (auth layer uses docker compose exec, NOT pytest); `grep -rn "pytest.mark" apps/api/tests/test_admin_dagster_status.py test_runs_hello_world.py` → zero hits (no existing test carries a marker that addopts would accidentally deselect).
- **Verdict:** ACCEPT with the caveat that M-1 applies: the `RUN_INTEGRATION_TESTS=1` comment is misleading and should be corrected. The functional behavior is correct — existing tests are unaffected, integration test is deselected in `backend)`, and `auth)` layer is unaffected because it uses docker compose exec, not pytest.

---

## Contract-coverage map

All rows from agreed.md §3 verified:

| Contract §3 row | Addressed? | Evidence |
|---|---|---|
| `apps/api/pyproject.toml` — add bcrypt, PyJWT | YES | `pyproject.toml:27-33` |
| `apps/api/alembic/versions/0002_users_add_hashed_password.py` — new | YES | diff, 51 lines |
| `apps/api/dataplat_api/db/models.py` — add `hashed_password: Mapped[str]` | YES | `models.py:37-43` |
| `apps/api/dataplat_api/config.py` — SECRET_KEY, JWT_ALGORITHM, JWT_TTL_SECONDS | YES | `config.py:21-23` |
| `docker/docker-compose.dev.yml` — SECRET_KEY injection | YES | compose diff lines 226-229 |
| `docker/.env.example` — SECRET_KEY entry | YES | `.env.example:28-31` |
| `apps/api/dataplat_api/cli.py` — new | YES | diff, 83 lines |
| `apps/api/dataplat_api/schemas/auth.py` — new TokenResponse | YES | diff, 24 lines |
| `apps/api/dataplat_api/routers/auth.py` — new | YES | diff, 102 lines |
| `apps/api/dataplat_api/main.py` — include_router(auth_router) | YES | `main.py:47` |
| `apps/api/tests/conftest.py` — SECRET_KEY setdefault | YES | `conftest.py:34-36` |
| `apps/api/tests/test_auth.py` — new | YES | diff, 251 lines |
| `verify/checks.sh` — auth) case + all) insertion + contract) guard | YES | checks.sh diff |
| `packages/api-types/openapi.json` — new | YES | diff, 338 lines |

One unlisted file changed:
- `apps/api/uv.lock` — expected side-effect of adding deps; see L-1.

---

## F-007 verification-bullet coverage

The F-007 feature description has three verification bullets:

**V1: "Running the seed command creates exactly one row in the users table"**
- Unit: `test_seed_admin_logic` — mock session, no existing user → `session.add()` called once, bcrypt hash verified round-trip. (`apps/api/tests/test_auth.py:96-128`)
- Unit (idempotency): `test_seed_admin_idempotent` — existing user → `session.add()` never called. (`apps/api/tests/test_auth.py:131-150`)
- Integration: `auth)` V1 in `verify/checks.sh:328-337` — docker compose exec seed then psql COUNT = 1.
- **Coverage: FULL**

**V2: "POST /api/auth/token with correct credentials returns 200 with {access_token, token_type}"**
- Unit: `test_token_correct_credentials_returns_200` — mocked session, bcrypt-hashed password, correct plaintext → 200, `access_token` present, `token_type == "bearer"`. (`apps/api/tests/test_auth.py:167-196`)
- Integration: `auth)` V2 in `verify/checks.sh:339-357` — real curl, python3 JSON assertions.
- **Coverage: FULL**

**V3: "POST /api/auth/token with wrong password returns 401"**
- Unit: `test_token_wrong_password_returns_401` — correct email, wrong password → 401, `detail == "Incorrect username or password"`. (`apps/api/tests/test_auth.py:199-218`)
- Unit (user not found): `test_token_user_not_found_returns_401` — email not in DB → 401, same message. (`apps/api/tests/test_auth.py:221-240`)
- Integration: `auth)` V3 in `verify/checks.sh:359-366` — real curl, asserts 401.
- **Coverage: FULL**

Additional tests from agreed.md §5 table:
- `test_token_missing_fields_returns_422`: PRESENT at `test_auth.py:243-249`. Asserts 422 on missing `username` field.

All 7 tests from the agreed.md §5 table are present. All 3 F-007 verification bullets are covered.

---

## Hard-invariant audit

**1. Lineage mandatory — N/A**
This sprint adds no Commit, Repository, or Dagster materialization. The `users` table is not a lineage-tracked entity. No lineage code touched. CHECKED.

**2. Storage separation + CAS — CHECKED: PASS**
`hashed_password` is a bcrypt credential hash stored in Postgres (correct). No blob bytes written to MinIO. No confusion with CAS sha256 content hashes — the column is named `hashed_password`, distinct from the `sha256` column in the `source` table (`db/models.py:37`). No MinIO writes in `cli.py`, `routers/auth.py`, or any file in the diff.

**3. Schema frozen post-publish — N/A**
Migration 0002 adds a column to `users`, not to any published Silver/Gold dataset schema. The `users` table is an operator-facing system table, not a repository schema subject to the freeze rule. CHECKED.

**4. LLM calls through gateway — N/A**
No LLM SDK imports anywhere in the diff. `grep -n "anthropic\|openai\|import jwt" routers/auth.py` — `jwt` is PyJWT (not an LLM SDK). CHECKED.

**5. Async SQLAlchemy — CHECKED: PASS**
- `apps/api/dataplat_api/cli.py:36-50`: `async with SessionLocal() as session:` → `await session.execute(select(User).where(...))` → `result.scalars().first()` → `session.add(user)` → `await session.commit()`. No `session.query()`, no sync session.
- `apps/api/dataplat_api/routers/auth.py:59-65`: `await session.execute(select(User).where(User.email == form_data.username))` → `result.scalars().first()`. Uses `get_session` async generator from `db/session.py`.
- Both code paths are fully async. Invariant satisfied.

**6. OpenAPI ↔ TS sync — CHECKED: PARTIAL COMPLIANCE (acknowledged deviation, mechanism correct)**
`packages/api-types/openapi.json` is committed in this same diff (338 lines). The file contains `/api/auth/token` path with `Body_login_api_auth_token_post` form schema and `TokenResponse` response schema. Confirmed present at `packages/api-types/openapi.json:162-212`.

Full `make codegen` is deferred (no Makefile, no pnpm). The `contract)` case guard at `verify/checks.sh:111` — `[[ -f Makefile ]] || { echo "no Makefile yet (codegen deferred to web sprint)"; exit 0; }` — prevents CI breakage. Guard placement: AFTER `exists packages/api-types` (line 105) and BEFORE `run "make codegen"` (line 112). Order is correct. The deferral is mechanism-gated (Makefile absence), not policy-skipped. Acknowledged deviation from hard invariant #6 per agreed.md §6.

---

## Scrutiny checklist (from review instructions)

1. **Constant-time dummy-hash flow** — `routers/auth.py:66-70`: `if user is None: bcrypt.checkpw(form_data.password.encode("utf-8"), _DUMMY_HASH); raise HTTPException(...)`. Does NOT short-circuit. PASS.

2. **Migration downgrade safety** — `0002_users_add_hashed_password.py:44-48`: upgrade uses `op.add_column("users", sa.Column("hashed_password", sa.Text, nullable=False, server_default=sa.text("''")))`. Downgrade uses `op.drop_column("users", "hashed_password")`. Both correct. PASS.

3. **Idempotent seed-admin** — `cli.py:38-43`: SELECT → if existing is not None: print skipping, return. INSERT path only reached when no existing user. Exit code 0 in both branches (no sys.exit() on skip path → implicit 0). PASS.

4. **JWT claim set** — `routers/auth.py:81-87`: `sub`, `email`, `iat`, `exp` all present. `iat` is `datetime.now(tz=timezone.utc)` passed to `jwt.encode()` which serializes to Unix int. PASS.

5. **SECRET_KEY fail-fast** — `config.py:21`: `SECRET_KEY: str` (no default). `conftest.py:36`: `os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")`. `docker-compose.dev.yml:229`: `SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-me}`. `.env.example:28-31`: entry present. PASS.

6. **`packages/api-types/openapi.json` content** — `/api/auth/token` path present at `openapi.json:162`. `TokenResponse` schema at `openapi.json:280-302`. `Body_login_api_auth_token_post` at `openapi.json:211-258`. Not stale. PASS.

7. **Makefile guard placement in `contract)` case** — `checks.sh:105`: `exists packages/api-types` guard. `checks.sh:111`: `[[ -f Makefile ]] || ...` guard. `checks.sh:112`: `run "make codegen"`. Order: directory check → Makefile check → codegen. Correct. PASS.

8. **`addopts = "-m 'not integration'"` side-effects** — `grep -rn "pytest.mark" test_admin_dagster_status.py test_runs_hello_world.py` → zero hits. No existing test inadvertently deselected. PASS. (Note M-1: the `RUN_INTEGRATION_TESTS=1` mechanism is a documentation mismatch, not a functional side-effect.)

9. **Test for missing-fields/422** — `test_auth.py:243-249`: `test_token_missing_fields_returns_422` posts `{"password": "somepassword"}` (no username) → asserts 422. PASS.

10. **Bcrypt encoding consistency** — Hash: `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")` stored as TEXT (`cli.py:42-46`). Verify: `bcrypt.checkpw(form_data.password.encode("utf-8"), user.hashed_password.encode("utf-8"))` (`routers/auth.py:74-77`). str → encode("utf-8") → bytes → checkpw. Consistent. PASS.

11. **OAuth2PasswordRequestForm `username` field maps to `email`** — `routers/auth.py:62`: `select(User).where(User.email == form_data.username)`. PASS.

12. **No new Pyright/ruff blockers** — `routers/auth.py` uses `from __future__ import annotations`, correct type annotations (`User | None`, `TokenResponse`). `bcrypt` and `jwt` are real packages with type stubs. The `# type: ignore[misc]` in `test_auth.py:144` suppresses an expected variance issue with async generator typing. No production code has type: ignore. PASS.

---

## Calibration checks (from verify/reviewer-calibration.md)

- **CAL-1 (async session):** PASS — `cli.py:36` uses `async with SessionLocal() as session:` + `await session.execute(select(...))` + `await session.commit()`. `routers/auth.py:59` uses `await session.execute(select(...))`. No `session.query()`, no sync session. No `.commit()` without await.

- **CAL-2 (LLM gateway):** N/A — no LLM SDK imports in any file in the diff. `import jwt` is PyJWT (a token library, not an LLM SDK). PASS.

- **CAL-3 (OpenAPI sync):** PASS — diff touches `routers/auth.py` and `schemas/auth.py`. `packages/api-types/openapi.json` is committed in the same diff (line 1 of the openapi.json diff). The json contains the `/api/auth/token` path. Partial compliance acknowledged with Makefile guard.

- **CAL-4 (lineage completeness):** N/A — no Commit objects created. `users` is not a lineage-tracked entity.

- **CAL-5 (CAS path discipline):** N/A — no blob storage operations in this diff.

- **CAL-6 (schema freeze post-publish):** N/A — migration 0002 modifies `users`, not a Silver/Gold published schema.

- **CAL-7 (Bronze faithfulness):** N/A — no adapter/processor code touched.

- **CAL-8 (MVP scope discipline):** PASS — no self-registration, no password reset, no MFA/OAuth/social login, no RBAC, no `is_admin` column, no Celery, no Docker-in-Docker, no training frameworks. The seed command is an operator CLI tool, explicitly in scope for F-007.

- **CAL-9 (plugin isolation):** N/A — no plugin code touched.

- **CAL-10 (test coverage):** PASS — 6 unit tests + 1 integration test. Success path: `test_token_correct_credentials_returns_200`. Failure paths: `test_token_wrong_password_returns_401`, `test_token_user_not_found_returns_401`, `test_token_missing_fields_returns_422`, `test_seed_admin_idempotent`. Exceeds the minimum one-success + one-failure requirement.

- **CAL-11 (bias check):** Approval is grounded in file:line evidence above. Each scrutiny item was read directly from the diff. No vague sign-off.

---

APPROVED
