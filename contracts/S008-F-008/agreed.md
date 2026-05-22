# S008-F-008 — Proposed Contract

**Status:** PROPOSED (Iter 3)
**Date drafted:** 2026-05-22
**Author:** Implementer (Claude)
**Depends on:** S007-F-007 (passes: true)

---

## 1. Goal

F-008 ships JWT Bearer enforcement on all non-public FastAPI routes. Every request to a
protected endpoint must carry a valid `Authorization: Bearer <token>` header; requests
without a token, with a malformed token, with an expired token, or whose subject user no
longer exists in the database all receive HTTP 401 with a `WWW-Authenticate: Bearer` header.

The feature_list.json verification items require a live `GET /api/sources/collections`
endpoint. That real endpoint belongs to F-010 (list, paginated). This sprint ships a minimal
stub for that route so the verification can proceed; the stub body is owned by F-010 and
must be replaced without touching the auth plumbing.

This sprint also closes the carry-over cleanup item from S007: the `RUN_INTEGRATION_TESTS`
comment discrepancy in `pyproject.toml` and `test_auth.py`.

---

## 2. What ships

- `apps/api/dataplat_api/auth/` package (new): `__init__.py`, `dependencies.py`.
  Houses the OAuth2 scheme, the `get_current_user` FastAPI dependency, and JWT decode logic.
- `apps/api/dataplat_api/routers/sources.py` (new): stub `GET /api/sources/collections`
  protected by `get_current_user`, returning `{"items": [], "total": 0}`.
- `apps/api/dataplat_api/schemas/collections.py` (new): `CollectionListResponse` Pydantic
  schema (`items: list[Any]`, `total: int`) so the stub has a declared response model that
  F-010 can refine without breaking clients.
- `apps/api/dataplat_api/routers/admin.py` (modified): add `get_current_user` dependency.
- `apps/api/dataplat_api/routers/runs.py` (modified): add `get_current_user` dependency to
  both `admin_runs_router` and `runs_router` routes.
- `apps/api/dataplat_api/main.py` (modified): `app.include_router(sources_router)`.
- `apps/api/tests/test_auth.py` (modified): add F-008 unit tests; fix carry-over comment
  discrepancy.
- `verify/checks.sh` (modified): extend `auth)` layer with V4/V5/V6 checks; update `runs)`
  layer to mint a Bearer token and authenticate the POST /api/admin/runs/hello-world and
  GET /api/runs/{run_id} curl calls; update `dagster)` layer to mint a Bearer token and
  authenticate both GET /api/admin/dagster-status curl calls (lines 212 and 250), all of
  which are now protected routes.
- `packages/api-types/openapi.json` (modified): regenerate to include new stub route.

---

## 3. File-by-file change list

| Path | New / Modified | Purpose |
|---|---|---|
| `apps/api/dataplat_api/auth/__init__.py` | New | Empty package marker |
| `apps/api/dataplat_api/auth/dependencies.py` | New | `oauth2_scheme`, `get_current_user` dependency (~50 LOC) |
| `apps/api/dataplat_api/schemas/collections.py` | New | `CollectionListResponse` Pydantic model (~10 LOC) |
| `apps/api/dataplat_api/routers/sources.py` | New | Stub `GET /api/sources/collections` (~20 LOC) |
| `apps/api/dataplat_api/routers/admin.py` | Modified | Add `current_user: User = Depends(get_current_user)` to route signature |
| `apps/api/dataplat_api/routers/runs.py` | Modified | Add `current_user: User = Depends(get_current_user)` to both route signatures |
| `apps/api/dataplat_api/main.py` | Modified | `app.include_router(sources_router)` |
| `apps/api/tests/test_auth.py` | Modified | Add 6 new F-008 unit tests; fix carry-over comment |
| `verify/checks.sh` | Modified | Add V4/V5/V6 to `auth)` case; modify `runs)` to mint a Bearer token (`RUNS_TOKEN`) and pass `-H "Authorization: Bearer $RUNS_TOKEN"` to both the POST /api/admin/runs/hello-world and GET /api/runs/{run_id} curl calls; modify `dagster)` to mint a Bearer token (`DAGSTER_TOKEN`) and pass `-H "Authorization: Bearer $DAGSTER_TOKEN"` to both GET /api/admin/dagster-status curl calls (lines 212 and 250) |
| `packages/api-types/openapi.json` | Modified | Regenerate after new stub route added |

No migration required. No new Python dependencies required (`PyJWT` is already installed
from S007, as is `python-multipart`).

---

## 4. Design decisions

### 4.1 Auth dep location: new `dataplat_api/auth/` package

The token-issuance route lives at `dataplat_api/routers/auth.py`. The JWT enforcement
dependency is conceptually different — it is infrastructure consumed by other routers, not
a route itself. Placing it in a dedicated `dataplat_api/auth/` package prevents circular
imports (`routers/auth.py` → `auth/dependencies.py` is impossible since `dependencies.py`
does not import the router) and gives F-010+ a clean import path
(`from dataplat_api.auth.dependencies import get_current_user`).

The scheme instance and the dependency function are both in `dependencies.py`. A separate
`scheme.py` would add a file with a single line for no benefit. The `oauth2_scheme` is
module-level in `dependencies.py`:

```python
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
```

`auto_error` is left at its default (`True`), which means `OAuth2PasswordBearer` raises
HTTP 401 with `WWW-Authenticate: Bearer` automatically when the `Authorization` header is
absent. Do not set `auto_error=False` — doing so would silently return `None` as the token
and bypass the 401 guarantee for the missing-token case.

`get_current_user` signature:

```python
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_session),
) -> User:
```

`Annotated` is used for the token parameter because FastAPI's docs recommend it for
dependency typing clarity and mypy is happy with it (over the older `= Depends(...)` style).
The `session` parameter uses the older `= Depends(get_session)` style for consistency with
the existing route handlers in `routers/auth.py` and elsewhere in the codebase.

### 4.2 Public-route allowlist

| Route | Status | Rationale |
|---|---|---|
| `GET /healthz` | PUBLIC | Smoke probe; breaking this breaks `checks.sh smoke`. |
| `POST /api/auth/token` | PUBLIC | You cannot authenticate before having a token. |
| `GET /docs` | PUBLIC | FastAPI built-in; no explicit route registered. Developer convenience. |
| `GET /openapi.json` | PUBLIC | FastAPI built-in; required for Swagger UI to function. |
| `GET /api/admin/dagster-status` | PROTECTED | Internal ops route — no reason to leave it open. |
| `POST /api/admin/runs/hello-world` | PROTECTED | Admin smoke trigger — must not be open. |
| `GET /api/runs/{run_id}` | PROTECTED | Run status contains internal Dagster run IDs. |
| `GET /api/sources/collections` | PROTECTED | New stub; the real F-010 route must be protected. |

Protection is applied per-route via the `Depends(get_current_user)` parameter on each
handler. A global FastAPI middleware alternative is explicitly rejected because:
1. It would require carefully carving out public paths via path-prefix matching, which is
   fragile and hard to audit.
2. FastAPI's dependency injection is already the conventional FastAPI pattern for this.
3. Per-route injection is self-documenting: reading any route handler you can immediately
   see whether it is protected.

The two existing protected routes (`admin`, `runs`) already have `TODO(F-008)` comments
calling for exactly this change.

### 4.3 `get_current_user` logic

Decoding steps in order:

1. `jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])` — if this
   raises `jwt.ExpiredSignatureError` or `jwt.InvalidTokenError` (or any subclass), raise
   HTTP 401 immediately.
2. Extract `sub` claim (user id string). If absent, raise HTTP 401.
3. `await session.execute(select(User).where(User.id == int(sub)))` — if the user is not
   found in DB (e.g., deleted after the token was issued), raise HTTP 401.
4. Return the `User` ORM object.

All four failure modes (missing token, malformed/invalid signature, expired, user-not-found)
produce the same HTTP 401 response with `WWW-Authenticate: Bearer` header and body:

```json
{"detail": "Could not validate credentials"}
```

"Could not validate credentials" is the canonical FastAPI docs wording. It does not leak
which check failed, satisfying the anti-enumeration requirement. The message is distinct
from the token-issuance 401 (`"Incorrect username or password"`) so logs can distinguish
the two phases.

### 4.4 Stub route for `GET /api/sources/collections`

Approach A is used as recommended. The stub is a 1-line body:

```python
@router.get("/collections", response_model=CollectionListResponse)
async def list_collections(
    current_user: User = Depends(get_current_user),
) -> CollectionListResponse:
    """Stub — body owned by F-010. Auth enforcement is the F-008 deliverable."""
    return CollectionListResponse(items=[], total=0)
```

The `session` dependency is NOT injected into the stub (the stub does not need DB access).
F-010 will add `session: AsyncSession = Depends(get_session)` when it replaces the body.

`CollectionListResponse` is defined in `schemas/collections.py` as:

```python
class CollectionListResponse(BaseModel):
    items: list[Any]
    total: int
```

`items: list[Any]` is intentionally loose so F-010 can narrow it to
`list[SourceCollectionOut]` without a breaking change to the schema field name or the
`total` field. F-010 must update the `response_model` annotation and the `items` type to
a proper Pydantic schema. This is explicitly called out in the stub's docstring.

The `sources_router` uses `prefix="/api/sources"` and `tags=["sources"]`. The router
module-level docstring documents that the route body is owned by F-010.

### 4.5 Expired-token test strategy

The unit test mints an expired token directly using `jwt.encode()` with `exp` set to a
past timestamp. No monkey-patching of `JWT_TTL_SECONDS` is needed:

```python
import time, jwt
from dataplat_api.config import settings

expired_payload = {
    "sub": "1",
    "email": "test@example.com",
    "iat": int(time.time()) - 7200,
    "exp": int(time.time()) - 3600,  # expired 1 hour ago
}
expired_token = jwt.encode(expired_payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
```

This is a pure unit test (no live DB) — the token decode raises `jwt.ExpiredSignatureError`
before the DB lookup, so no session mock is needed. The test simply hits the stub route with
the crafted token and asserts 401.

For `test_collections_wrong_key_returns_401`, the token is signed with the literal string
`"definitely-not-the-real-secret"` (distinct from `settings.SECRET_KEY = "test-secret-key-not-for-production"`
in the test environment). This ensures the test fails for signature reasons
(`jwt.InvalidSignatureError`) and not because the token is expired or structurally malformed.

### 4.6 Carry-over comment cleanup (S007 item)

`apps/api/pyproject.toml` line 43 comment says "Skip integration tests by default; run with
`RUN_INTEGRATION_TESTS=1` to include them" but the `addopts` line is unconditional
(`addopts = "-m 'not integration'"`). The `RUN_INTEGRATION_TESTS` env var has no effect.

`apps/api/tests/test_auth.py` lines 11-12 repeat the same claim.

The fix: update both comments to accurately describe the actual behavior:
- `pyproject.toml`: change to "Integration tests are excluded by default; to include them,
  run `pytest -m integration` or remove the -m filter from addopts."
- `test_auth.py`: update the module docstring to remove the `RUN_INTEGRATION_TESTS=1`
  reference and replace with the accurate description.

No behavioral change — only comment text. The addopts remains `"-m 'not integration'"`.

---

## 5. Open questions

**OQ-1: Should `GET /api/runs/{run_id}` stay protected after F-008?**

RESOLVED: Option A (leader, 2026-05-22). `runs)` layer in checks.sh updated to mint a
Bearer token and apply it to both curl calls.

Both routes in `runs.py` carry `TODO(F-008)` markers and F-008's description says "ALL
non-public API routes". The implementer must:
- Add `get_current_user` dependency to both `POST /api/admin/runs/hello-world` and
  `GET /api/runs/{run_id}` route handlers.
- Update the `runs)` layer in `checks.sh` to mint a token at the top of the case block
  (same pattern as `auth)` V2 — POST to `/api/auth/token` with the seeded admin credentials,
  extract `access_token`) and pass `-H "Authorization: Bearer $TOKEN"` to both existing curl
  calls. No verification logic changes — only the Authorization header is added.
- The `all)` ordering already has `auth` before `runs`, so the seeded admin user from
  `auth)` V1 is guaranteed to exist when `runs)` runs.

**OQ-2: `GET /api/sources/collections` response shape for F-010 compatibility**

The stub uses `items: list[Any]`. F-010 will need to decide the per-item Pydantic schema
for `SourceCollection` objects. Nothing in this sprint hard-codes the item shape, so
F-010 has full freedom. This is noted as an OQ for completeness — not a blocker; the
leader does not need to resolve it before implementation.

---

## 6. Verification plan

### V1 (F-008): GET /api/sources/collections without Authorization header → 401

**Unit test — `test_collections_no_token_returns_401`** (backend layer):

Use `TestClient(app)`. Send `GET /api/sources/collections` with no `Authorization` header.
`oauth2_scheme` raises HTTP 401 automatically (FastAPI's `OAuth2PasswordBearer` raises 401
when the header is absent). Assert `response.status_code == 401`.

**checks.sh auth) V4:**

```bash
echo "--- auth V4: GET /api/sources/collections without token → 401 ---"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
test "$STATUS" = "401" \
  || { echo "FAIL: auth V4 returned $STATUS (expected 401)"; exit 1; }
echo "auth V4 no-token 401: OK"
```

---

### V2 (F-008): GET /api/sources/collections with a valid token → 200

**Unit test — `test_collections_valid_token_returns_200`** (backend layer):

Override `get_current_user` dependency via `app.dependency_overrides` to return a mock
`User`. Send `GET /api/sources/collections` with `Authorization: Bearer <valid_token>`.
Assert `response.status_code == 200` and body matches `{"items": [], "total": 0}`.

Note: the dependency override bypasses JWT decode, keeping the test a pure unit test.
A separate test (`test_collections_jwt_decode_path`) tests the actual JWT decode path
with a real token (see test table below).

**checks.sh auth) V5:**

```bash
echo "--- auth V5: GET /api/sources/collections with valid token → 200 ---"
# Mint a fresh token using the established two-step pattern (mirrors auth) V2 / runs) V1).
# Note: auth) V2 does not export a TOKEN variable — it removes the body file after assertion.
# V5 mints its own token independently so that the case remains self-contained.
TOKEN_BODY=$(mktemp)
RESP=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '\n%{http_code}' -o "$TOKEN_BODY")
STATUS_CODE=$(echo "$RESP" | tail -n1)
test "$STATUS_CODE" = "200" \
  || { echo "FAIL: auth V5 could not mint token (status $STATUS_CODE): $(cat "$TOKEN_BODY")"; rm -f "$TOKEN_BODY"; exit 1; }
TOKEN=$(python3 -c "import json; print(json.load(open('$TOKEN_BODY'))['access_token'])")
rm -f "$TOKEN_BODY"

STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
test "$STATUS" = "200" \
  || { echo "FAIL: auth V5 returned $STATUS (expected 200)"; exit 1; }
echo "auth V5 valid-token 200: OK"
```

---

### V3 (F-008): GET /api/sources/collections with an expired token → 401

**Unit test — `test_collections_expired_token_returns_401`** (backend layer):

Mint an expired token via `jwt.encode({..., "exp": int(time.time()) - 3600}, ...)`.
Override `get_session` to return a mock session (in case the request reaches the DB lookup
— it won't, but defense in depth). Send `GET /api/sources/collections` with
`Authorization: Bearer <expired_token>`. Assert `response.status_code == 401`.

**checks.sh auth) V6:**

```bash
echo "--- auth V6: GET /api/sources/collections with expired token → 401 ---"
# Craft an expired token inside the fastapi container (PyJWT is installed there).
EXPIRED_TOKEN=$(docker compose -f "$COMPOSE" exec -T fastapi \
  python -c "
import jwt, time, os
payload = {
    'sub': '1',
    'email': 'admin@example.com',
    'iat': int(time.time()) - 7200,
    'exp': int(time.time()) - 3600,
}
token = jwt.encode(payload, os.environ['SECRET_KEY'], algorithm='HS256')
print(token, end='')
")
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $EXPIRED_TOKEN" \
  "http://localhost:${FASTAPI_HOST_PORT}/api/sources/collections")
test "$STATUS" = "401" \
  || { echo "FAIL: auth V6 expired token returned $STATUS (expected 401)"; exit 1; }
echo "auth V6 expired-token 401: OK"
```

---

### `runs)` layer update (Option A, OQ-1 resolution)

The `runs)` layer in `verify/checks.sh` exercises two now-protected routes. It must mint
a token before making any protected call. The pattern is identical to `auth)` V2.

The seeded admin user (`admin@example.com` / `testpassword123`) is already guaranteed to
exist by the time `runs)` runs: the `all)` block executes layers in order — `auth` (which
runs seed-admin in V1) precedes `runs`. Running `runs)` standalone requires the seeded
admin to exist; if it does not, `POST /api/auth/token` returns 401 (wrong credentials or
no user — not 422, which is a form-decode error and not applicable here) and the
`test "$RUNS_TOKEN_STATUS" = "200"` guard immediately exits with the
"run `bash $0 auth` first" message. This is the correct fail-fast behavior.

The token-minting block is inserted at the top of the `runs)` case, before the first
existing curl call (line 273, `POST /api/admin/runs/hello-world`):

```bash
echo "--- runs: mint Bearer token for protected routes ---"
RUNS_TOKEN_BODY=$(mktemp)
RUNS_TOKEN_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '%{http_code}' -o "$RUNS_TOKEN_BODY")
test "$RUNS_TOKEN_STATUS" = "200" \
  || { echo "FAIL: runs) could not mint auth token (status $RUNS_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$RUNS_TOKEN_BODY"; exit 1; }
RUNS_TOKEN=$(python3 -c "import json; print(json.load(open('$RUNS_TOKEN_BODY'))['access_token'])")
rm -f "$RUNS_TOKEN_BODY"
```

The existing `POST /api/admin/runs/hello-world` curl call then gains
`-H "Authorization: Bearer $RUNS_TOKEN"`, and the existing `GET /api/runs/${RUN_ID}` poll
loop curl call gains the same header. The variable name `RUNS_TOKEN` (not `TOKEN`) avoids
collision if `runs)` is ever called after `auth)` in the same shell process where `TOKEN`
was already set.

No other changes to `runs)` verification logic. The expected status codes (201 for POST,
200 for GET poll) and all success/failure assertions remain identical.

---

### `dagster)` layer update (L-1 resolution)

The `dagster)` layer in `verify/checks.sh` makes two curl calls to `GET /api/admin/dagster-status`
(lines 212 and 250), both without a Bearer token. Since that route is now PROTECTED, both
calls will return 401 after F-008 ships, causing `bash verify/checks.sh dagster` and
`bash verify/checks.sh all` to fail.

The fix mirrors the `runs)` update exactly. A token-minting block is inserted at the top
of the `dagster)` case, before the V1 curl call at line 212:

```bash
echo "--- dagster: mint Bearer token for protected routes ---"
DAGSTER_TOKEN_BODY=$(mktemp)
DAGSTER_TOKEN_STATUS=$(curl -sS -X POST \
  "http://localhost:${FASTAPI_HOST_PORT}/api/auth/token" \
  -d "username=admin@example.com&password=testpassword123" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -w '%{http_code}' -o "$DAGSTER_TOKEN_BODY")
test "$DAGSTER_TOKEN_STATUS" = "200" \
  || { echo "FAIL: dagster) could not mint auth token (status $DAGSTER_TOKEN_STATUS) — run 'bash $0 auth' first"; rm -f "$DAGSTER_TOKEN_BODY"; exit 1; }
DAGSTER_TOKEN=$(python3 -c "import json; print(json.load(open('$DAGSTER_TOKEN_BODY'))['access_token'])")
rm -f "$DAGSTER_TOKEN_BODY"
```

Both existing `curl -fsS "http://localhost:${FASTAPI_HOST_PORT}/api/admin/dagster-status"`
calls (lines 212 and 250) then gain `-H "Authorization: Bearer $DAGSTER_TOKEN"`. The
variable name `DAGSTER_TOKEN` (not `TOKEN` or `RUNS_TOKEN`) avoids namespace collision if
layers are invoked in the same shell process.

Note: `curl -fsS` (used in `dagster)`) differs from `curl -sS` (used in `runs)` and
`auth)`). The `-f` flag causes curl to exit with a non-zero code on HTTP error responses.
After F-008, a 401 from the unprotected call would have caused curl to exit non-zero via
`-f` and fail the pipeline — but the `-f` exit only fires at the HTTP level after the
TCP connection succeeds. The existing `-fsS ... | python3 -c "..."` pipe pattern in
`dagster)` relies on the pipe receiving valid JSON; a 401 body (`{"detail": "..."}`) would
cause the python3 assertion to fail rather than curl's `-f`. Adding the Authorization
header restores the route to 200 and the existing pipe pattern continues to work unchanged.

Standalone-run caveat: same as `runs)` — `POST /api/auth/token` returns 401 if the admin
user has not been seeded, and the `test "$DAGSTER_TOKEN_STATUS" = "200"` guard exits 1
with the "run `bash $0 auth` first" message.

No other changes to `dagster)` verification logic. The V1 and V2 assertions (checking
`dagster_version` in the JSON body, the container restart test) remain identical.

---

### Full test table for `test_auth.py` additions

| Test name | Layer | What it covers |
|---|---|---|
| `test_collections_no_token_returns_401` | `backend)` | No `Authorization` header → 401 (oauth2_scheme raises) |
| `test_collections_malformed_token_returns_401` | `backend)` | `Authorization: Bearer notajwt` → 401 (jwt.InvalidTokenError) |
| `test_collections_expired_token_returns_401` | `backend)` | Manually crafted expired token → 401 (jwt.ExpiredSignatureError) |
| `test_collections_wrong_key_returns_401` | `backend)` | Token signed with a different key → 401 (jwt.InvalidSignatureError) |
| `test_collections_valid_token_returns_200` | `backend)` | Valid token, dep override returns User → 200 `{"items":[],"total":0}` |
| `test_collections_user_not_found_returns_401` | `backend)` | Valid JWT, `get_session` overridden to return no row (NOT `get_current_user`) → 401; exercises actual JWT decode + DB lookup path |
| `test_collections_jwt_decode_path` | `backend)` | Real jwt.encode + real `get_session` mock → 200 (exercises full decode path) |

All seven are pure unit tests using `TestClient(app)` with `_patch_engine_begin` already
active (no live Postgres required). The expired-token, wrong-key, and malformed tests do
not need a session mock — `get_current_user` raises before the DB lookup.

For `test_collections_user_not_found_returns_401`: override `get_session` (NOT
`get_current_user`) to return a mock session whose `execute()` returns no row (same
`_make_session_dependency` pattern used in F-007 tests). Craft a structurally valid,
non-expired JWT with `sub: "9999"`, signed with `settings.SECRET_KEY`. The dependency
decodes the JWT successfully, then fails the DB lookup (no user row) and raises 401.
Overriding `get_current_user` directly would bypass the JWT decode and DB lookup paths
being tested — do not do this for this test case.

**Recommended addition for `test_collections_no_token_returns_401`**: add
`assert response.headers["WWW-Authenticate"] == "Bearer"` alongside the status code
assertion. This locks in the `auto_error=True` guarantee from §4.1 — if `auto_error` is
ever accidentally set to `False`, the 401 response would still occur (from the handler's
own check) but without the header, and the test would catch the regression.

---

## 7. Hard invariants checklist

| # | Invariant | APPLIES / N/A | Compliance |
|---|---|---|---|
| 1 | Lineage is mandatory | N/A | No Commit, Repository, or Dagster materialization is created. |
| 2 | Storage separation + CAS | N/A | No blob bytes written anywhere in this sprint. No MinIO interaction. |
| 3 | Schema frozen post-publish | N/A | No Silver/Gold dataset schema is touched. |
| 4 | LLM calls go through the gateway | N/A | No LLM SDK imported anywhere in this sprint. `get_current_user` uses PyJWT and SQLAlchemy only. |
| 5 | Async SQLAlchemy from day one | APPLIES | `get_current_user` uses `await session.execute(select(User).where(...))`. No `session.query()`, no sync session. Stub route does not touch the DB at all. Fully compliant. |
| 6 | OpenAPI ↔ TS type sync | APPLIES — partial compliance, same acknowledged deviation as S007 | The new stub route changes the OpenAPI spec. `packages/api-types/openapi.json` must be regenerated and committed in the same commit as the route. The `[[ -f Makefile ]]` guard already in `checks.sh contract)` from S007 remains in place — no `Makefile` exists yet, so `make codegen` does not fire. The deferral remains mechanism-level. A `claude-progress.txt` note must record the regeneration. |

---

## 8. Non-goals (explicit out-of-scope)

- **Real list endpoint body (F-010).** The stub returns `{"items": [], "total": 0}` and
  nothing more. Pagination, filtering, DB query, owner scoping — all F-010. When F-010
  replaces `list[Any]` with a typed schema, `packages/api-types/openapi.json` MUST be
  regenerated in F-010's commit (CAL-3 applies).
- **POST /api/sources/collections (F-009).** Not shipped this sprint.
- **Pagination parameters** on the stub (`limit`, `offset`, `cursor`). F-010 owns these.
- **Repository-level granular ACL.** MVP uses `visibility = private|internal` only.
  No `role`, `is_admin`, or `scope` claim added to the JWT or to the `User` model
  (CLAUDE.md §Scope discipline, design doc §11.6).
- **Role-based access control.** No roles exist in MVP. `get_current_user` returns a
  `User` and that is the only access check.
- **Token refresh, revocation, rotation, logout endpoint.** Deferred.
- **Self-registration, password reset, MFA, OAuth, social login.** Deferred
  (CLAUDE.md §Scope discipline, design doc §11.6).
- **WebSocket auth (F-051).** Separate sprint.
- **Any frontend changes.** F-055/F-056 (web sprint).
- **Global FastAPI middleware for auth.** Per-route Depends is the chosen approach
  (see §4.2); middleware would be a different approach and is not in scope.

---

## 9. Rollback plan

No migration is involved. Rollback is `git revert` of the implementation commit(s):
- Removes `dataplat_api/auth/` package.
- Reverts `routers/admin.py`, `routers/runs.py`, `routers/sources.py`, `main.py`.
- Reverts `schemas/collections.py`.
- Reverts test additions in `test_auth.py`.
- Reverts `verify/checks.sh` `auth)`, `runs)`, and `dagster)` layer changes.
- Reverts `packages/api-types/openapi.json` to S007 version.

All pre-F-008 routes return to unauthenticated behavior. No data is lost.

---

## 10. Scope-discipline audit

Confirming none of the deferred features listed in CLAUDE.md §"Scope discipline" are
touched:

- **Self-registration / password reset / MFA / OAuth / social login:** Not implemented.
- **Repository-level granular ACL:** Not implemented. No `role` column, no `is_admin`,
  no per-resource permission check.
- **Celery / Dagster:** Not used in this sprint's new code.
- **Docker-in-Docker plugin sandbox:** Not used.
- **Training frameworks, experiment tracking, Kafka streams:** Not used.
