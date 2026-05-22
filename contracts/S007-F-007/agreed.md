# S007-F-007 — Proposed Contract

**Status:** PROPOSED (Iter 3 — addresses Mode A CHANGES_REQUESTED on Iter 2)
**Date drafted:** 2026-05-22
**Last revised:** 2026-05-22 (Iter 3)
**Author:** Implementer (Claude)
**Depends on:** S002-F-002 (passes: true)

---

## 1. Goal

F-007 ships the minimum viable auth foundation for the platform: a one-time admin-user seed
command and a JWT issuance endpoint. Specifically, "a single admin user record can be seeded
into the users table via a one-time setup command; the user can obtain a JWT token via
POST /api/auth/token."

This unblocks F-008 (per-route JWT enforcement), which cannot proceed without a user record
and a working token issuer. The users table already exists in the database (created by
migration 0001). What ships:

1. Two new Python dependencies: `bcrypt>=4.0.0` (password hashing) and `PyJWT>=2.9.0` (JWT
   issuance and decoding) added to `apps/api/pyproject.toml`.
2. A new `SECRET_KEY` setting in `apps/api/dataplat_api/config.py`, sourced from the
   `SECRET_KEY` environment variable. Missing at startup → fast fail (pydantic-settings
   ValidationError).
3. `docker/docker-compose.dev.yml` updated to inject `SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-me}`
   into the fastapi service environment block. `docker/.env.example` updated to add the
   `SECRET_KEY` entry with a production-change comment.
4. A migration `0002_users_add_hashed_password.py` that adds `hashed_password TEXT NOT NULL
   DEFAULT ''` to the `users` table (the baseline users table has no password column — see §3).
5. An updated `User` ORM model in `db/models.py` with the `hashed_password` field.
6. A seed CLI command: `uv run python -m dataplat_api.cli seed-admin --email X --password Y`.
7. A new router `apps/api/dataplat_api/routers/auth.py` with `POST /api/auth/token`. Module-
   level bcrypt constants (`_DUMMY_HASH`) and the hash/verify helpers are co-located here.
8. New Pydantic schemas in `apps/api/dataplat_api/schemas/auth.py`.
9. Unit tests in `apps/api/tests/test_auth.py`.
10. A new `auth)` layer in `verify/checks.sh`; `bash "$0" auth` added to the `all)` block
    after `migration` and before `buckets`.
11. `packages/api-types/openapi.json` committed as a new file in the same commit as the
    router (exported OpenAPI JSON spec; full TS codegen deferred to the web sprint — see
    §6 invariant #6 for the explicit acknowledged-deviation statement).

---

## 2. Scope

### In-scope

- Migration `0002`: add `hashed_password TEXT NOT NULL DEFAULT ''` column to `users` table.
  The `DEFAULT ''` is a migration-time-only default; application code never inserts an empty
  string. bcrypt never produces an empty string, so `hashed_password = ''` is an unambiguous
  sentinel for "no password set."
- ORM model update: add `hashed_password: Mapped[str]` to `User` in `db/models.py`.
- `dataplat_api/config.py`: add `SECRET_KEY: str` (no default; fail fast), `JWT_ALGORITHM:
  str = "HS256"`, `JWT_TTL_SECONDS: int = 3600`.
- `docker/docker-compose.dev.yml`: add `SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-me}`
  to the fastapi service environment block.
- `docker/.env.example`: add `SECRET_KEY=dev-secret-key-change-me  # Change in production`.
- `dataplat_api/cli.py` (new file): `seed-admin` command using argparse + asyncio.run +
  AsyncSession.
- `dataplat_api/routers/auth.py` (new file): `POST /api/auth/token` with module-level bcrypt
  constants co-located here.
- `dataplat_api/schemas/auth.py` (new file): `TokenResponse` Pydantic model.
- `apps/api/tests/conftest.py`: add `SECRET_KEY` to `os.environ.setdefault` block.
- `apps/api/tests/test_auth.py` (new file): unit tests for the seed command and the endpoint.
- `verify/checks.sh`: new `auth)` case; `bash "$0" auth` added to `all)` block after
  `migration` and before `buckets`.
- `packages/api-types/openapi.json` (new file): exported OpenAPI JSON, committed in the same
  commit as the router. TS generation deferred to web sprint.

### Explicitly out-of-scope (protects against scope creep)

- Bearer-token enforcement on existing or new routes (that is F-008; all existing routes
  remain unauthenticated this sprint).
- Refresh tokens, token revocation, token rotation, logout endpoint.
- Self-registration, password reset email, MFA, OAuth, social login (CLAUDE.md §Scope
  discipline, design doc §12.4).
- Any RBAC, scopes, per-route ACL, or `is_admin` / `role` column (not in the users table).
- Multiple user management (list, update, delete users).
- Key rotation or secret management beyond a single env-var `SECRET_KEY`.
- Changes to existing routers, gateway, or Dagster logic.
- Any frontend changes.
- Full TypeScript codegen from openapi.json (deferred to web sprint; acknowledged deviation
  from hard invariant #6 — see §6).

---

## 3. Files changed

| Path | New / Modified | Purpose |
|---|---|---|
| `apps/api/pyproject.toml` | Modified | Add `bcrypt>=4.0.0` and `PyJWT>=2.9.0` to `[project].dependencies` |
| `apps/api/alembic/versions/0002_users_add_hashed_password.py` | New | Migration: add `hashed_password TEXT NOT NULL DEFAULT ''` to `users`; `downgrade()` drops the column |
| `apps/api/dataplat_api/db/models.py` | Modified | Add `hashed_password: Mapped[str]` to `User` model |
| `apps/api/dataplat_api/config.py` | Modified | Add `SECRET_KEY: str` (no default, fail fast), `JWT_ALGORITHM: str = "HS256"`, `JWT_TTL_SECONDS: int = 3600` |
| `docker/docker-compose.dev.yml` | Modified | Add `SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-me}` to the fastapi service environment block (same pattern as `DAGSTER_GRAPHQL_URL`) |
| `docker/.env.example` | Modified | Add `SECRET_KEY=dev-secret-key-change-me  # Change in production` |
| `apps/api/dataplat_api/cli.py` | New | `seed-admin` CLI command (argparse, asyncio.run + AsyncSession) |
| `apps/api/dataplat_api/schemas/auth.py` | New | `TokenResponse` Pydantic model: `access_token: str`, `token_type: str` |
| `apps/api/dataplat_api/routers/auth.py` | New | `POST /api/auth/token`; module-level `_DUMMY_HASH` and bcrypt helpers co-located here |
| `apps/api/dataplat_api/main.py` | Modified | `app.include_router(auth_router)` |
| `apps/api/tests/conftest.py` | Modified | Add `os.environ.setdefault("SECRET_KEY", "test-secret-key")` before any `dataplat_api` import |
| `apps/api/tests/test_auth.py` | New | Unit + integration tests for seed command and token endpoint (see §5) |
| `verify/checks.sh` | Modified | Add `auth)` case with V1/V2/V3 checks; insert `bash "$0" auth` into `all)` block between `migration` and `buckets`; add `Makefile`-existence guard to `contract)` case to keep it inert until codegen is wired in a future web sprint |
| `packages/api-types/openapi.json` | New | Exported OpenAPI JSON spec; generated via `uv run python -c "import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))"` committed in the same commit as the router; full TS codegen deferred to web sprint |

**Migration justification:** The baseline migration `0001_baseline_schema.py` creates the
`users` table with only four columns: `id`, `email`, `name`, `created_at`. There is no
`hashed_password` or `password_hash` column in the migration or in the `User` ORM model
(confirmed by reading both files). Auth cannot work without storing a hashed password. A new
migration is required. This is not scope creep — it is the minimal structural change needed
to satisfy the F-007 verification items.

---

## 4. Design decisions

### 4.1 Password hashing

**Library:** `bcrypt>=4.0.0` (direct bcrypt, no passlib wrapper).

**Rationale for dropping passlib:** `passlib` 1.7.4 (the final release; effectively
unmaintained since 2020) accesses `bcrypt.__about__` at call time to detect the installed
bcrypt version. This attribute was removed in `bcrypt` 4.0 (released 2023; current default
installed by `uv`'s resolver). Using passlib with bcrypt 4.x raises
`AttributeError: module 'bcrypt' has no attribute '__about__'` at the first `hash()` or
`verify()` call. Pinning `bcrypt<4.0.0` would force a dependency on an unmaintained version.
The simpler fix is to drop passlib and call bcrypt directly. The bcrypt API is stable and
requires exactly two operations:

- **Hash:** `bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))`
  returns `bytes`; store as `hashed_password = hash_bytes.decode("utf-8")`.
- **Verify:** `bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))`
  returns `bool`. This function is constant-time by design.

**Cost factor:** rounds=12 (appropriate for interactive auth; sub-300ms on modern hardware).

**Column name:** `hashed_password TEXT NOT NULL`. The name `hashed_password` is chosen to
avoid confusion with CAS sha256 content hashes used elsewhere in the codebase (hard
invariant #2 — those are addressed by the `sha256` column in the `source` table).

**Co-location:** The module-level constant `_DUMMY_HASH` and all bcrypt imports live in
`dataplat_api/routers/auth.py`. The CLI (`dataplat_api/cli.py`) imports `bcrypt` directly
and independently. No shared `dataplat_api/auth/` package, no `dataplat_api/security.py`
flat file. Only files listed in §3 are created.

**Module-level dummy hash** (in `routers/auth.py`):
```python
import bcrypt
_DUMMY_HASH: bytes = bcrypt.hashpw(b"dummy", bcrypt.gensalt())
```
Computed once at import time; used in the "user not found" branch to perform a
constant-time verify that prevents timing-based user enumeration (see §4.4).

### 4.2 JWT

**Library:** `PyJWT>=2.9.0` (not `python-jose`).

**Rationale:** `python-jose` has known CVEs in its ECDSA implementation and is effectively
unmaintained. `PyJWT` is actively maintained with a clean API (`jwt.encode()` / `jwt.decode()`).

**Algorithm:** HS256 (HMAC-SHA256). Sufficient for single-tenant MVP where only the server
issues and verifies tokens.

**Claim set:**
- `sub`: user id as string (e.g., `"1"`) — standard JWT subject claim
- `email`: user email (convenience claim; avoids a DB lookup per request in F-008)
- `iat`: issued-at timestamp (set by PyJWT automatically)
- `exp`: expiry timestamp, computed as `iat + JWT_TTL_SECONDS`

No `iss` or `aud` claims this sprint (useful for multi-service setups; not needed for MVP).

**TTL:** `JWT_TTL_SECONDS` env var, default 3600 (1 hour). Stored in `Settings`.

**Secret key sourcing:** `SECRET_KEY` environment variable, read by `pydantic-settings` in
`config.py`. No default value: if absent, `pydantic-settings` raises `ValidationError` at
import time — fast fail. The `fastapi` service in `docker-compose.dev.yml` injects
`SECRET_KEY: ${SECRET_KEY:-dev-secret-key-change-me}` so the container starts with a
non-empty value even if `SECRET_KEY` is absent from the host environment. `docker/.env.example`
documents the variable with a production-change comment.

### 4.3 Seed command

**How invoked** (from `apps/api/` directory):
```
uv run python -m dataplat_api.cli seed-admin --email admin@example.com --password <pw>
```
This matches the existing `uv run` convention used in `checks.sh` backend commands.

**Where it lives:** `apps/api/dataplat_api/cli.py` — a new flat file, consistent with
the project's existing module layout.

**CLI framework:** stdlib `argparse`. Single command with two required flags; argparse is
sufficient and adds no dependency. A `# TODO: migrate to typer if CLI grows beyond 2
commands` comment is added in the file.

**Async session:** The entry point calls `asyncio.run(seed_admin(email, password))`. Inside
`seed_admin`, a `SessionLocal()` context manager (`async_sessionmaker` from `db/session.py`)
yields an `AsyncSession`. All DB interaction uses `await session.execute(select(...))`,
`session.add(user)`, `await session.commit()`. Hard invariant #5 is satisfied — no sync
session, no `session.query()`.

**Idempotency:** If a user with the given `--email` already exists (SELECT returns a row),
the command prints `Admin user <email> already exists. Skipping.` and exits with code 0.
No automatic overwrite. A `--force` flag is deferred to a future sprint.

**Credentials source:** `--email` and `--password` are required CLI flags. No env-var
sourcing, no interactive prompt.

### 4.4 Endpoint shape

**Path:** `POST /api/auth/token`

**Request format:** `application/x-www-form-urlencoded` with fields `username` (treated as
email) and `password`, using FastAPI's `OAuth2PasswordRequestForm`. This makes Swagger UI
"Try it out" work out of the box and is plug-and-play with `OAuth2PasswordBearer` in F-008.

**Response 200:**
```json
{"access_token": "<jwt_string>", "token_type": "bearer"}
```
Exactly matches the F-007 verification requirement verbatim.

**Response 401 (wrong password or user not found):**
```json
{"detail": "Incorrect username or password"}
```
HTTP 401 with `WWW-Authenticate: Bearer` header. Identical error message whether the user
is not found or the password is wrong — prevents user-enumeration attacks.

**Constant-time comparison:** `bcrypt.checkpw()` is constant-time. When the user is NOT
found in the database, the endpoint calls `bcrypt.checkpw(plain_password.encode(), _DUMMY_HASH)`
with the module-level dummy hash before returning 401. This prevents a timing attack that
would reveal whether a given email is registered. This anti-enumeration decision is
documented in a code comment in `routers/auth.py`.

**Response 422:** FastAPI's default validation error shape. `OAuth2PasswordRequestForm`
validates that `username` and `password` are non-empty strings.

---

## 5. Verification plan

### V1: "Running the seed command creates exactly one row in the users table"

**Unit test — `test_seed_admin_logic`** (runs in `backend)` layer, no live DB required):

Mocks `SessionLocal` to return an `AsyncMock` session whose `execute()` returns an empty
result (no existing user). Calls the seed command's core async function directly (extracted
from the argparse wiring). Asserts `session.add()` was called exactly once with a `User`
object whose `hashed_password` is non-empty and does not equal the plaintext password.

**Unit test — `test_seed_admin_idempotent`** (runs in `backend)` layer, no live DB required):

Mocks `SessionLocal` to return an `AsyncMock` session whose `execute()` result contains a
mock `User` row (simulating an already-existing user). Asserts that `session.add()` is
**never called** (INSERT path is skipped) and the function returns without error.

This is a pure mock unit test. No subprocess, no `@pytest.mark.integration` marker. Runs in
the `backend)` layer safely.

**Integration test — `test_seed_admin_creates_one_row`** (marked `@pytest.mark.integration`,
skipped in `backend)` layer unless `RUN_INTEGRATION_TESTS=1`):

Invokes the CLI via `subprocess.run(["uv", "run", "python", "-m", "dataplat_api.cli",
"seed-admin", "--email", "...", "--password", "..."])` against the real compose DB. Then
queries the DB (via a real `AsyncSession`) to assert `COUNT(*) = 1`.

**`_patch_engine_begin` interaction (explicit for reviewer):**

The subprocess-based integration test spawns a separate Python process. The `_patch_engine_begin`
autouse fixture patches `AsyncEngine.begin` class-wide only in the pytest process. The
subprocess inherits the process environment but not the monkeypatch — it connects to the
real Postgres. No interaction issue.

The unit tests `test_seed_admin_logic` and `test_seed_admin_idempotent` mock `SessionLocal`
entirely and never reach `engine.begin`. No interaction issue.

The endpoint unit tests (`test_token_*`) use `TestClient(app)`, which triggers the lifespan
and calls `engine.begin()`. The `_patch_engine_begin` autouse fixture mocks this to a no-op
for all tests — same behaviour as the existing test suite. No interaction issue.

**Checks.sh `auth)` V1:**
```bash
FASTAPI_HOST_PORT="${FASTAPI_HOST_PORT:-18000}"

echo "--- auth V1: seed creates exactly one row ---"
docker compose -f docker/docker-compose.dev.yml exec -T fastapi \
  uv run python -m dataplat_api.cli seed-admin \
  --email admin@example.com --password testpassword123
docker compose -f docker/docker-compose.dev.yml exec -T postgres \
  psql -U "${POSTGRES_USER:-app}" -d "${POSTGRES_DB:-platform}" -tAc \
    "SELECT COUNT(*) FROM users WHERE email='admin@example.com'" \
  | grep -q '^1$' \
  || { echo "FAIL: auth V1 seed did not create exactly one row"; exit 1; }
echo "auth V1 seed: OK"
```

---

### V2: "POST /api/auth/token with correct credentials returns 200 with {access_token, token_type}"

**Unit test — `test_token_correct_credentials_returns_200`** (runs in `backend)` layer):

Overrides the `get_session` FastAPI dependency via `app.dependency_overrides` to return an
`AsyncMock` session whose `execute()` result contains a mock `User` with a pre-computed
bcrypt hash for the test password. Posts to `/api/auth/token` with `username` and `password`
form data. Asserts `response.status_code == 200`, `"access_token" in body`,
`body["token_type"] == "bearer"`.

**Checks.sh `auth)` V2:**
```bash
AUTH_TOKEN_BODY=$(mktemp)
RESP=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '\n%{http_code}' -o "$AUTH_TOKEN_BODY")
STATUS_CODE=$(echo "$RESP" | tail -n1)
test "$STATUS_CODE" = "200" \
  || { echo "FAIL: auth V2 token returned $STATUS_CODE: $(cat "$AUTH_TOKEN_BODY")"; rm -f "$AUTH_TOKEN_BODY"; exit 1; }
python3 -c "
import json, sys
body = json.load(open('$AUTH_TOKEN_BODY'))
assert 'access_token' in body, f'missing access_token: {body}'
assert body.get('token_type') == 'bearer', f'wrong token_type: {body}'
print('  V2 OK: access_token present, token_type=bearer')
" || { echo "FAIL: auth V2 response shape incorrect"; rm -f "$AUTH_TOKEN_BODY"; exit 1; }
rm -f "$AUTH_TOKEN_BODY"
echo "auth V2 correct credentials: OK"
```

---

### V3: "POST /api/auth/token with wrong password returns 401"

**Unit test — `test_token_wrong_password_returns_401`** (runs in `backend)` layer):

Overrides `get_session` to return a `User` with a real bcrypt hash. Posts the correct email
but a wrong password. Asserts `response.status_code == 401`, `"detail" in body`.

**Checks.sh `auth)` V3:**
```bash
STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=WRONG_PASSWORD_XYZ" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -o /dev/null -w '%{http_code}')
test "$STATUS" = "401" \
  || { echo "FAIL: auth V3 wrong password returned $STATUS (expected 401)"; exit 1; }
echo "auth V3 wrong password: OK"
```

---

### Additional unit tests summary

| Test name | Layer | What it covers |
|---|---|---|
| `test_seed_admin_logic` | `backend)` | Mock session, no pre-existing user → `session.add()` called once, hashed_password non-empty |
| `test_seed_admin_idempotent` | `backend)` | Mock session returns existing User → `session.add()` never called (INSERT skipped) |
| `test_seed_admin_creates_one_row` | `auth)` (integration, `@pytest.mark.integration`) | Subprocess seed against real DB + psql COUNT == 1 |
| `test_token_correct_credentials_returns_200` | `backend)` | Mocked session + correct password → 200 + `{access_token, token_type: bearer}` |
| `test_token_wrong_password_returns_401` | `backend)` | Mocked session + wrong password → 401 |
| `test_token_user_not_found_returns_401` | `backend)` | Mocked session returns no user → 401 (same message; timing-safe via `_DUMMY_HASH`) |
| `test_token_missing_fields_returns_422` | `backend)` | Missing `username` form field → 422 |

---

### `contract)` case guard patch

Committing `packages/api-types/openapi.json` this sprint causes the second guard in the
existing `contract)` case (`exists packages/api-types || ... exit 0`) to no longer trip.
Without a further guard, `run "make codegen"` fires next — but there is no `Makefile` at
the repo root, so `contract)` (and `all)` by extension) exits 1, breaking the baseline.

The fix is a one-line Makefile-existence guard inserted between the `packages/api-types`
check and the `make codegen` call:

```bash
  contract)
    exists apps/api || { echo "no apps/api yet"; exit 0; }
    exists packages/api-types || { echo "no packages/api-types yet"; exit 0; }
    [[ -f Makefile ]] || { echo "no Makefile yet (codegen deferred to web sprint)"; exit 0; }
    run "make codegen"
    run "git diff --exit-code packages/api-types/"
    ;;
```

Once the web sprint scaffolds the `Makefile` and `pnpm` workspace, the guard trips back to
running `make codegen`, and the `openapi.json` committed this sprint becomes the input to
full TS generation. The deferral is mechanism-level (no `Makefile` yet), not policy-level
(hard invariant #6 still applies and is tracked via the `claude-progress.txt` note).

---

### Full updated `all)` block

```bash
all)
  # smoke first: cheapest check, fails fast if stack is not up at all.
  # apps/api confirmed present since F-001 passes:true.
  bash "$0" smoke
  bash "$0" infra
  bash "$0" backend
  bash "$0" frontend
  bash "$0" contract
  bash "$0" migration
  bash "$0" auth
  bash "$0" buckets
  bash "$0" dagster
  bash "$0" runs
  ;;
```

`auth` is placed after `migration` (auth depends on the `users` table and the `hashed_password`
column from migration 0002) and before `buckets` (auth has no dependency on MinIO bucket
state).

---

## 6. Hard invariant audit

| # | Invariant | APPLIES / N/A | Compliance |
|---|---|---|---|
| 1 | Lineage is mandatory | N/A | This sprint adds no Commit, Repository, or Dagster materialization. The `users` table is not a lineage-tracked entity. |
| 2 | Storage separation + CAS | N/A | No blob bytes written. `hashed_password` is a bcrypt hash of a credential — metadata — stored in Postgres where it belongs. No MinIO writes. No confusion with CAS sha256 content hashes: the column is named `hashed_password`, distinct from the `sha256` column in the `source` table. |
| 3 | Schema frozen post-publish | N/A | Migration 0002 adds a column to `users`, not to any published Silver/Gold dataset schema. |
| 4 | LLM calls go through the gateway | N/A | No LLM SDK imports in scope. |
| 5 | Async SQLAlchemy from day one | APPLIES | The seed CLI uses `asyncio.run()` + `SessionLocal()` (confirmed `async_sessionmaker` in `db/session.py`). All DB interaction: `await session.execute(select(...))`, `session.add()`, `await session.commit()`. No `session.query()`, no sync session. The `POST /api/auth/token` endpoint uses the `get_session` dependency (async generator at `db/session.py` line 26). Invariant satisfied in both the CLI and the endpoint. |
| 6 | OpenAPI ↔ TS type sync | APPLIES — partial compliance, acknowledged deviation | The new `POST /api/auth/token` endpoint and `TokenResponse` model change the OpenAPI spec. Full `make codegen` (which requires `packages/api-types/` monorepo scaffolding and `pnpm`) cannot run this sprint: no `Makefile` and no `packages/` directory exist in the repository (confirmed). **Binding partial deliverable:** the implementer must generate `packages/api-types/openapi.json` via `uv run python -c "import json; from dataplat_api.main import app; print(json.dumps(app.openapi(), indent=2))"` and commit it in the same commit as the router (see §3). A one-line `[[ -f Makefile ]] || { echo "no Makefile yet (codegen deferred to web sprint)"; exit 0; }` guard is added to the `contract)` case in `checks.sh` (see §5) so that the now-present `packages/api-types/` directory does not cause `make codegen` to fire against a missing `Makefile`, which would break `all)`. Once the web sprint scaffolds the `Makefile` and `pnpm` workspace, the guard trips back to running `make codegen` and the `openapi.json` committed this sprint becomes the input to full TS generation. The deferral is mechanism-level (no `Makefile` yet), not policy-level. A `claude-progress.txt` entry must note this deferral explicitly. |

---

## 7. Scope-discipline audit

Confirming that none of the deferred features listed in CLAUDE.md §"Scope discipline" are
touched:

- **Self-registration / password reset email / MFA / OAuth / social login:** Not implemented.
  The seed command is an operator-level CLI tool, not user-facing registration. No registration
  endpoint is added.
- **Repository-level granular ACL:** Not implemented. No `role`, `is_admin`, or permission
  column is added to the `users` table.
- **Celery / Dagster:** Not used. The seed CLI uses `asyncio.run()` directly.
- **Docker-in-Docker plugin sandbox:** Not used.
- **Training frameworks, experiment tracking, Kafka streams:** Not used.

---

## 8. Open questions (OQs)

**OQ-1: CLI framework — argparse vs typer**

ACCEPTED: use `argparse` this sprint. Add `# TODO: migrate to typer if CLI grows beyond 2
commands` comment in `cli.py`. Zero new dependency, adequate for a single command. No
further review needed on this question.

---

## 9. Rollback plan

**Alembic migration:** Migration `0002_users_add_hashed_password.py` implements:
```python
def downgrade() -> None:
    op.drop_column("users", "hashed_password")
```
Running `alembic downgrade 0001` restores the `users` table to its pre-F-007 state. Safe
to downgrade before F-008 ships — no production code depends on `hashed_password` until
F-008 wires JWT enforcement.

**Code rollback:** `git revert` of the implementation commit(s) removes all new files and
reverts modified files (`main.py`, `config.py`, `models.py`, `pyproject.toml`, `checks.sh`,
`conftest.py`, `docker-compose.dev.yml`, `.env.example`). Alembic downgrade must be run to
keep DB schema and code in sync.

No data loss risk: the `users` table existed before this sprint. Downgrading drops only the
`hashed_password` column; `email`, `name`, `id`, `created_at` are unaffected. Any seeded
admin user row remains in the table after downgrade (minus the password column).
