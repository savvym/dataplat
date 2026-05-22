# S008-F-008 — Mode A Review Feedback

**Reviewer:** Claude (independent)
**Iteration reviewed:** Iter 3 (updated after iter 1 CHANGES_REQUESTED)
**Date:** 2026-05-22
**Prior feedback file:** this file (overwrite of iter 1)

---

## Top-line verdict: APPROVED

All six required findings from iter 1 are correctly resolved. No new substantive issues
introduced by the iter-2/iter-3 changes. The contract is ready for implementation.

---

## Resolution of iter-1 findings

**L-1 (functional HIGH — dagster) checks.sh layer not updated):** RESOLVED.
§3 file table now lists `dagster)` as modified. §6 adds a dedicated `dagster)` layer
update section. Token mint block with `DAGSTER_TOKEN_BODY` / `DAGSTER_TOKEN_STATUS` /
`DAGSTER_TOKEN` variables inserted at the top of the `dagster)` case, mirroring the
`runs)` pattern exactly. Both existing curl calls (lines ~212 and ~250 of current script)
gain `-H "Authorization: Bearer $DAGSTER_TOKEN"`. Standalone-run failure caveat
documented. Variable name `DAGSTER_TOKEN` avoids namespace collision.

**H-1 (V5 curl pipe-into-grep reliability gap):** RESOLVED.
V5 now uses the established two-step pattern: `mktemp` for body file, `-w '\n%{http_code}'
-o "$TOKEN_BODY"`, `STATUS_CODE=$(echo "$RESP" | tail -n1)`, `test "$STATUS_CODE" = "200"
|| { ... rm -f "$TOKEN_BODY"; exit 1; }`, python3 extraction, `rm -f "$TOKEN_BODY"`.
Comment accurately notes that V2 does not export a `TOKEN` variable and V5 mints
independently. Pattern matches auth) V2 / runs) V1 exactly.

**H-2 (auto_error=True not stated explicitly):** RESOLVED.
§4.1 now contains: "`auto_error` is left at its default (`True`), which means
`OAuth2PasswordBearer` raises HTTP 401 with `WWW-Authenticate: Bearer` automatically when
the `Authorization` header is absent. Do not set `auto_error=False` — doing so would
silently return `None` as the token and bypass the 401 guarantee for the missing-token
case." Precise and actionable.

**M-1 (F-010 CAL-3 implication not documented in §8):** RESOLVED.
§8 non-goals now reads: "When F-010 replaces `list[Any]` with a typed schema,
`packages/api-types/openapi.json` MUST be regenerated in F-010's commit (CAL-3 applies)."
Correct and specific.

**M-2 (test table for user_not_found_returns_401 did not specify which dep to override):** RESOLVED.
Test table entry now reads: "Valid JWT, `get_session` overridden to return no row (NOT
`get_current_user`)". The prose below the table also explicitly warns against overriding
`get_current_user` for this test case, explaining why (`get_current_user` override would
bypass the JWT decode and DB lookup paths being tested).

**M-3 (spurious "401 → 422" description in runs) standalone failure):** RESOLVED.
§5 now correctly states: "if it does not, `POST /api/auth/token` returns 401 (wrong
credentials or no user — not 422, which is a form-decode error and not applicable here)
and the `test "$RUNS_TOKEN_STATUS" = "200"` guard immediately exits with the 'run bash $0
auth first' message."

---

## Nice-to-have resolutions (informational)

**L-2 (wrong-key test key unspecified):** RESOLVED. §4.5 now states the literal key
`"definitely-not-the-real-secret"` and explains it is distinct from
`settings.SECRET_KEY = "test-secret-key-not-for-production"` in the test environment,
ensuring the failure is due to signature mismatch, not expiry or malformation.

**N-1, N-2 (minor wording cleanups):** RESOLVED. §4.1 mixed-style justification is
accurate. V5 comment about re-using V2's TOKEN variable is removed/corrected.

**WWW-Authenticate header assertion (verification coverage gap):** ADDRESSED.
§6 adds "Recommended addition for `test_collections_no_token_returns_401`": assert
`response.headers["WWW-Authenticate"] == "Bearer"`. This is framed as recommended rather
than required — acceptable for a contract; the implementer should include it.

**N-3 (runs) curl pattern comment — intentionally not addressed):** ACCEPTED omission.
The `RUNS_TOKEN_STATUS=$(curl ... -w '%{http_code}' -o "$file")` pattern is correct
(stdout carries only the status code when `-o` redirects the body to a file). No
contract text needed.

---

## New findings introduced by iter-2/iter-3 changes

None. The `dagster)` layer update section is new and correct. The `auto_error=True` text
in §4.1 is new and correct. The `test_collections_user_not_found_returns_401` table entry
clarification is new and correct. No new gaps, no new scope creep, no new invariant risks.

---

## Verification coverage

| Bullet | Unit test | checks.sh | Covered? |
|---|---|---|---|
| V1: No token → 401 | `test_collections_no_token_returns_401` (+ recommended WWW-Authenticate header assertion) | auth) V4 | FULL |
| V2: Valid token → 200 | `test_collections_valid_token_returns_200` + `test_collections_jwt_decode_path` | auth) V5 | FULL |
| V3: Expired token → 401 | `test_collections_expired_token_returns_401` | auth) V6 | FULL |

All three feature_list.json verification bullets have at least one pure unit test and one
`checks.sh` integration check. No bullet relies solely on a skippable integration test.

Additional depth: 4 further unit tests cover malformed token, wrong-key, user-not-found,
and full JWT decode path. 7 tests total for F-008. This exceeds the CAL-10 minimum.

---

## Calibration checks (verify/reviewer-calibration.md)

**CAL-1 (Async session enforcement):** CHECKED — PASS. `get_current_user` uses
`await session.execute(select(User).where(User.id == int(sub)))`. No `session.query()`,
no sync sessions anywhere in proposed code. §7 invariant #5 row explicitly confirms
compliance. Stub route has no DB interaction.

**CAL-2 (LLM gateway enforcement):** CHECKED — N/A. No LLM SDK imports in any proposed
file. `import jwt` is PyJWT (a token library). No `anthropic`, `openai`, or direct httpx
calls to LLM endpoints.

**CAL-3 (OpenAPI sync):** CHECKED — PASS (mechanism-level deferral, same treatment as
S007). New route `GET /api/sources/collections` will change the OpenAPI spec. §3 lists
`packages/api-types/openapi.json` as modified. The existing `[[ -f Makefile ]] || exit 0`
guard in `checks.sh contract)` from S007 prevents CI breakage. §7 row #6 acknowledges
the deviation explicitly. F-010's CAL-3 obligation documented in §8. Consistent with
S007's approved treatment.

**CAL-4 (Lineage completeness):** CHECKED — N/A. No `Commit` objects, no Dagster
materializations, no lineage-tracked entities created or modified.

**CAL-5 (CAS path discipline):** CHECKED — N/A. No blob storage operations in any
proposed file. No MinIO interaction.

**CAL-6 (Schema freeze post-publish):** CHECKED — N/A. `schemas/collections.py` is a
new API response schema (Pydantic BaseModel). No Silver/Gold dataset schema is touched.

**CAL-7 (Bronze faithfulness):** CHECKED — N/A. No adapter or Bronze processor code
touched.

**CAL-8 (MVP scope discipline):** CHECKED — PASS. §10 scope-discipline audit is
thorough. No self-registration, MFA, OAuth, RBAC, `is_admin` column, Celery,
Docker-in-Docker, or training framework integration. `get_current_user` returns a `User`
object with no role or scope claims — sole access check is "is this a valid user in the
DB?" Non-goals in §8 enumerate all deferred items explicitly.

**CAL-9 (Plugin isolation):** CHECKED — N/A. No plugin code touched.

**CAL-10 (Test coverage — happy path + one failure):** CHECKED — PASS. 7 unit tests
proposed: 2 success paths (`test_collections_valid_token_returns_200`,
`test_collections_jwt_decode_path`) and 5 failure modes (no-token, malformed, expired,
wrong-key, user-not-found). Far exceeds the minimum one-success + one-failure requirement.

**CAL-11 (Bias check):** CHECKED. Each resolution above is verified against specific
sections of proposed.md (iter 3). Every CAL item above states concrete evidence or N/A
with rationale. No vague sign-off.

---

## Hard-invariant review

| # | Invariant | Status |
|---|---|---|
| 1 | Lineage mandatory | N/A — no Commit or Dagster materialization in scope |
| 2 | Storage separation + CAS | N/A — no blob writes, no MinIO interaction |
| 3 | Schema frozen post-publish | N/A — no published dataset schema modified |
| 4 | LLM gateway | N/A — no LLM calls |
| 5 | Async SQLAlchemy | APPLIES — PASS: `get_current_user` uses `await session.execute(select(User).where(...))`. Stub route has no DB interaction. No `session.query()`, no sync session. |
| 6 | OpenAPI ↔ TS sync | APPLIES — PASS (mechanism-level deferral acknowledged): `openapi.json` committed in same commit as new route; Makefile guard prevents CI breakage; deferral noted in `claude-progress.txt` |

All six invariants checked. No violations.

---

## Scope-discipline audit

§10 of proposed.md covers all deferred items from CLAUDE.md §"Scope discipline":
no self-registration, no password reset email, no MFA, no OAuth, no social login,
no repository-level granular ACL, no `role`/`is_admin` columns, no Celery, no
Docker-in-Docker sandbox, no training framework integration, no Kafka streams.
`get_current_user` returns a `User` and that is the only access check.

PASS — no deferred MVP boundary item is silently in scope.

---

## Implementation risks to watch (not blockers)

1. The `runs)` token-mint pattern uses `-w '%{http_code}' -o "$file"` (status code
   captured via subshell, body to file) rather than the `-w '\n%{http_code}'` + `tail -n1`
   pattern in V2. Both are correct in isolation; the `dagster)` block uses the same
   `-o "$file"` pattern. The implementer should verify that both patterns produce a clean
   string `"200"` (no trailing newline or body prefix) in the CI shell before shipping.

2. `test_collections_expired_token_returns_401` defends against the DB lookup with a
   `get_session` override "in case the request reaches the DB lookup". For the test to be
   a true unit test (no live DB), that override must be present even if the code path never
   reaches it. The implementer should not skip the override on the grounds that the
   `ExpiredSignatureError` fires first — the defense-in-depth is correct.

3. The `WWW-Authenticate` header assertion is marked "recommended" in §6 rather than
   required. The implementer should add it: it costs one line and provides a concrete
   regression guard for the `auto_error=True` invariant stated in §4.1.

---

APPROVED
