# S008-F-008 — Mode B Final Review

**Reviewer:** Claude (independent)
**Commit reviewed:** 91a2651
**Base commit:** 9d975fd
**Date:** 2026-05-22
**Contract:** contracts/S008-F-008/agreed.md (Mode A APPROVED at iter 2)

---

## Top-line verdict: APPROVED

The implementation satisfies every contract criterion. Two implementer-declared deviations
are both acceptable (see §1 below). No blocking or high-severity findings. The code is
correct, isolated, and consistent with the project's hard invariants.

---

## 1. Deviation decisions

### Deviation 1: test_admin_dagster_status.py and test_runs_hello_world.py modified

**Decision: ACCEPTED.**

The agreed.md §3 "Modified" list is non-exhaustive by construction — it cannot anticipate
every file touched by necessary implementation reality. Adding `Depends(get_current_user)`
to protected routes inevitably breaks pre-existing tests that hit those routes without a
token. The fix (override `get_current_user` in the `client` fixture via try/finally) is the
correct FastAPI test-isolation pattern and matches the S007 pattern already established in
the codebase. The override is properly scoped (fixture-level, not module-global), cleaned
up in a `finally` block, and documented in both files' module docstrings. No auth logic is
bypassed in `test_auth.py` itself, which is where auth enforcement is tested.

### Deviation 2: PyJWT InsecureKeyLengthWarning for dev SECRET_KEY

**Decision: ACCEPTED for dev environment; follow-up note required for production deploy.**

The 24-byte dev `SECRET_KEY` ("dev-secret-key-change-me") triggers a non-fatal HS256
recommendation warning. This is a warning, not an error, and does not affect runtime
correctness. The fix belongs in the production deploy checklist (generate a ≥32-byte
random key via `python3 -c "import secrets; print(secrets.token_hex(32))"`). It must NOT
be silenced with a `warnings.filterwarnings` suppress — the warning correctly identifies
the sub-optimal key length and should remain visible. A note is appended to
`claude-progress.txt` in the same commit to flag this for the production deploy owner.

---

## 2. Findings

### BLOCKER
None.

### HIGH
None.

### MEDIUM
None.

### LOW

**L-1: agreed.md §4.3 claims all four failure modes return the SAME detail message — technically imprecise for the missing-token case.**

`apps/api/dataplat_api/auth/dependencies.py:35` defines `_CREDENTIALS_EXCEPTION` with
`detail="Could not validate credentials"`. This fires for modes 2-4 (malformed, expired,
user-not-found). Mode 1 (missing Authorization header) is handled by FastAPI's
`OAuth2PasswordBearer` with `auto_error=True`, which raises with
`detail="Not authenticated"` — a FastAPI platform detail that cannot be overridden without
replacing the scheme itself.

Impact: an attacker can distinguish "I sent no token" from "I sent a token that failed" by
the detail string. However, this is not a useful enumeration vector — the attacker already
knows whether they included an Authorization header. The security anti-enumeration goal
(do not distinguish which of the 3 token-validation failure modes fired) is satisfied by
the constant `_CREDENTIALS_EXCEPTION` for modes 2-4.

The `test_collections_no_token_returns_401` test correctly does NOT assert on the detail
message, only on status_code (401) and `WWW-Authenticate: Bearer` header — the test is
written to match the actual FastAPI behavior.

Assessment: LOW (not MEDIUM) because this is a FastAPI platform constraint, the agreed.md
claim is slightly imprecise rather than the code being wrong, and there is no practical
security regression. No code change required; the distinction could be clarified in
agreed.md §4.3 as a future documentation update.

### NIT

**N-1: dagster) and runs) token-mint patterns diverge from the V5 pattern**

`verify/checks.sh:210-218` (dagster) and `verify/checks.sh:282-290` (runs) use
`-w '%{http_code}' -o "$file"` to capture the status code directly into
`DAGSTER_TOKEN_STATUS` / `RUNS_TOKEN_STATUS`. The V5 auth block (line 406-411) uses
`-w '\n%{http_code}' -o "$file"` with `tail -n1`. Both patterns are functionally correct
(the `-o file` flag redirects the body; stdout contains only the format string). The
agreed.md Mode A feedback.md explicitly noted this as an implementation risk to watch
(N-3 intentional omission). No change needed.

**N-2: dagster V2 post-restart uses the same DAGSTER_TOKEN minted before the restart**

`verify/checks.sh:263` reuses `$DAGSTER_TOKEN` (minted at line 217) after a container
restart at line 251. This is safe: the token TTL is set by `JWT_TTL_SECONDS` (24 hours in
the default config), and the restart+readiness check completes in under 30 seconds. Not
a bug.

**N-3: `test_collections_no_token_returns_401` uses module-level `client` fixture (no get_current_user override)**

This is correct by design: the test specifically exercises the real `oauth2_scheme`
auto-rejection path. If the `client` fixture overrode `get_current_user`, the test would
not exercise the right code path. No change.

### INFO

**I-1: Feature_list.json F-008 `passes` is still `false`**

Expected — the sprint definition of done requires a verifier PASS before flipping
`passes: true`. This is outside the reviewer's scope.

---

## 3. Verification of Mode A resolutions

**L-1 (dagster) checks.sh layer not updated) — RESOLVED in code.**
`verify/checks.sh:208-218` mints `DAGSTER_TOKEN`. Lines 224 and 263 both pass
`-H "Authorization: Bearer $DAGSTER_TOKEN"`. Both curl calls confirmed in diff.

**H-1 (V5 curl pipe-into-grep fragility) — RESOLVED in code.**
`verify/checks.sh:405-415` uses `mktemp` + `-w '\n%{http_code}' -o "$TOKEN_BODY"` +
`STATUS_CODE=$(echo "$RESP" | tail -n1)` + cleanup. Matches the agreed.md V5 spec exactly.

**H-2 (auto_error=True not stated explicitly) — RESOLVED in code.**
`apps/api/dataplat_api/auth/dependencies.py:32` comment: "auto_error=True (default) — FastAPI
raises HTTP 401 with WWW-Authenticate: Bearer automatically... DO NOT set False." The
instance at line 32 has no `auto_error` parameter, confirming the default is used.

**M-1 (F-010 CAL-3 obligation not documented) — RESOLVED in code.**
`apps/api/dataplat_api/schemas/collections.py:6` states: "F-010 MUST update the
response_model annotation and items type to a proper Pydantic schema and regenerate
packages/api-types/openapi.json in the same commit (CAL-3)."

**M-2 (test table missing override-target for user_not_found test) — RESOLVED in code.**
`apps/api/tests/test_auth.py:371-394` overrides `get_session` (NOT `get_current_user`),
matching the agreed.md M-2 resolution exactly.

**M-3 (runs) standalone failure incorrectly described as 422) — RESOLVED in code.**
`verify/checks.sh:287-288` error message says "runs) could not mint auth token" with no
mention of 422.

**L-2 (wrong-key test key unspecified) — RESOLVED in code.**
`apps/api/tests/test_auth.py:334`: `_mint_token(key="definitely-not-the-real-secret")` —
literal key matches the agreed.md L-2 specification exactly.

**N-1, N-2 (wording cleanups) — RESOLVED in code.**
`verify/checks.sh:404-422` V5 comment does not claim to reuse V2's TOKEN variable.
`apps/api/dataplat_api/auth/dependencies.py:42-44` uses the `Annotated` style for `token`
and the older `= Depends(get_session)` style for `session`, consistent with the agreed
justification.

**WWW-Authenticate header assertion (recommended addition) — IMPLEMENTED.**
`apps/api/tests/test_auth.py:293`: `assert response.headers.get("WWW-Authenticate") == "Bearer"`.
The "recommended" addition from Mode A feedback was implemented. Good.

**N-3 (runs) curl pattern comment) — intentionally omitted, as agreed.** No issue.

---

## 4. Calibration checks (CAL-1..CAL-11)

**CAL-1 (Async session enforcement):** PASS.
`apps/api/dataplat_api/auth/dependencies.py:75`: `result = await session.execute(...)`.
No `session.query()`, no `session.commit()` without `await`. Sources stub has no DB access.
Admin and runs routers have no new DB calls. No sync session patterns in any modified file.

**CAL-2 (LLM gateway enforcement):** N/A.
No `import anthropic`, `import openai`, or direct LLM httpx calls in any modified file.
`import jwt` is PyJWT (a crypto library, not an LLM SDK).

**CAL-3 (OpenAPI sync):** PASS.
`apps/api/dataplat_api/routers/sources.py` is a new router file under `dataplat_api/routers/`.
`packages/api-types/openapi.json` is modified in the same commit (91a2651). The diff shows
the new path `/api/sources/collections`, new schema `CollectionListResponse`, and security
scheme references on all 4 protected routes. The `[[ -f Makefile ]] || exit 0` guard
acknowledged in agreed.md §7 remains in place; the deferral treatment is consistent with
S007. The openapi.json change is in the same commit: CAL-3 satisfied.

**CAL-4 (Lineage completeness):** N/A.
No `Commit` objects, no Dagster materializations, no lineage-tracked entities created.

**CAL-5 (CAS path discipline):** N/A.
No blob storage operations. No MinIO interaction.

**CAL-6 (Schema freeze post-publish):** N/A.
`schemas/collections.py` is a new API response schema (Pydantic BaseModel). No Silver/Gold
dataset schema modified.

**CAL-7 (Bronze faithfulness):** N/A.
No adapter or Bronze processor code touched.

**CAL-8 (MVP scope discipline):** PASS.
No self-registration, MFA, OAuth, social login, RBAC, `is_admin` column, Celery,
Docker-in-Docker, or training framework code. `get_current_user` returns a bare `User`
object with no role or scope claims. No `visibility` ACL logic added.

**CAL-9 (Plugin isolation):** N/A.
No plugin code touched.

**CAL-10 (Test coverage — happy path + one failure):** PASS.
7 new unit tests: 2 success paths (test_collections_valid_token_returns_200,
test_collections_jwt_decode_path) and 5 failure modes (no-token, malformed, expired,
wrong-key, user-not-found). Far exceeds the CAL-10 minimum. Existing tests in
test_admin_dagster_status.py and test_runs_hello_world.py continue to cover the happy path
and one failure per module.

**CAL-11 (Bias check):** CHECKED. Each finding above cites specific `file:line` evidence
or explicitly states N/A with rationale. No vague sign-off. One LOW finding identified
(L-1, anti-enumeration imprecision in agreed.md — platform constraint, not a code bug).
Three NITs identified. No approval without concrete evidence.

---

## 5. Hard-invariant re-check

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit or Dagster materialization in scope. No lineage entities touched. |
| 2 | Storage separation + CAS | N/A — no blob writes, no MinIO interaction. |
| 3 | Schema frozen post-publish | N/A — no published dataset schema modified. CollectionListResponse is a new API response type, not a Silver/Gold schema. |
| 4 | LLM gateway | N/A — no LLM calls. `import jwt` is PyJWT. |
| 5 | Async SQLAlchemy | PASS — `apps/api/dataplat_api/auth/dependencies.py:75`: `await session.execute(select(User).where(User.id == user_id))`. No `session.query()`, no `.commit()` without await, no sync session anywhere in the diff. |
| 6 | OpenAPI ↔ TS sync | PASS (mechanism-level deferral, same treatment as S007) — `packages/api-types/openapi.json` modified in the same commit as new router. Security scheme block and CollectionListResponse schema both present. Makefile guard prevents CI check failure. Deferral documented. |

All six invariants confirmed.

---

## 6. Contract criteria (agreed.md)

| Criterion | Status | Evidence |
|---|---|---|
| New `dataplat_api/auth/` package | PASS | `apps/api/dataplat_api/auth/__init__.py` and `dependencies.py` present in diff |
| `oauth2_scheme` with `auto_error=True` (H-2) | PASS | `dependencies.py:32` — no `auto_error` param, default True; comment confirms. |
| `get_current_user`: decode → sub → DB lookup order | PASS | `dependencies.py:55-93` — exact order matches §4.3 spec |
| All 4 failure modes → 401 "Could not validate credentials" + WWW-Authenticate | PASS (modes 2-4) / INFO (mode 1 — see L-1) | `dependencies.py:35-51`; mode 1 is FastAPI platform behavior |
| ValueError (non-numeric sub) → 401 not 500 | PASS | `dependencies.py:83-86`: `except (ValueError, TypeError): raise _CREDENTIALS_EXCEPTION` |
| `GET /api/sources/collections` stub with `get_current_user` dep (per-route) | PASS | `routers/sources.py:24-25` — `current_user: User = Depends(get_current_user)` on handler |
| `admin.py` get_current_user per-route Depends | PASS | `routers/admin.py:21` — on handler signature, not router `dependencies=` |
| `runs.py` both handlers get_current_user per-route Depends | PASS | `routers/runs.py:61, 96` — both handler signatures |
| All TODO(F-008) markers removed | PASS | `git grep 'TODO(F-008)'` returns zero hits in `apps/api/` |
| V5 mint uses `mktemp + -w '\n%{http_code}' + tail -n1` pattern (H-1) | PASS | `checks.sh:405-415` |
| `runs)` mints `RUNS_TOKEN` and applies Bearer to both curl calls | PASS | `checks.sh:280-300, 318-322` |
| `dagster)` mints `DAGSTER_TOKEN` and applies Bearer to both curl calls (L-1) | PASS | `checks.sh:208-224, 263` |
| `RUNS_TOKEN` and `DAGSTER_TOKEN` don't collide with each other or `TOKEN` | PASS | Three distinct variable names confirmed in checks.sh grep output |
| 7 unit tests in test_auth.py as per §6 test table | PASS | All 7 `def test_collections_*` functions present |
| `test_collections_no_token_returns_401` asserts WWW-Authenticate header | PASS | `test_auth.py:293` |
| `test_collections_wrong_key_returns_401` uses literal `"definitely-not-the-real-secret"` (L-2) | PASS | `test_auth.py:334` |
| `test_collections_user_not_found_returns_401` overrides `get_session` not `get_current_user` (M-2) | PASS | `test_auth.py:384` — `app.dependency_overrides[get_session]` |
| All `app.dependency_overrides` uses have try/finally cleanup | PASS | `test_auth.py:313-320, 357-364, 384-391, 408-415`; `test_admin_dagster_status.py:42-47`; `test_runs_hello_world.py:55-60` |
| `pyproject.toml:43` carry-over comment fixed | PASS | `pyproject.toml:43-44` — correct description, no RUN_INTEGRATION_TESTS reference |
| `test_auth.py` module docstring carry-over comment fixed | PASS | `test_auth.py:11` — corrected to `pytest -m integration` description |
| `packages/api-types/openapi.json` regenerated in same commit | PASS | diff shows new path, schema, and securitySchemes block in same commit |
| auth) ordering before dagster/runs in `all)` | PASS | `checks.sh:444-456` — `auth` at line 453, `dagster` at 455, `runs` at 456 |

---

## 7. Recommendation

**APPROVED — dispatch verifier.**

The implementation is correct, complete, and consistent with the agreed contract. The one
LOW finding (L-1: missing-token returns "Not authenticated" rather than "Could not validate
credentials") is a FastAPI platform constraint that cannot be resolved in application code
without replacing `OAuth2PasswordBearer`. It is not a security regression. The test is
correctly written to not assert on the detail message for that case.

Production deploy note (from Deviation 2): the `SECRET_KEY` environment variable MUST be
set to a ≥32-byte random value in production. Generate with:
`python3 -c "import secrets; print(secrets.token_hex(32))"`
