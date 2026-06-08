# Sprint S055-F-055 — Review Final (Mode B)
**Commit reviewed:** `ff86981`
**Diff base:** `3f246a5`
**Agreed spec:** `contracts/S055-F-055/agreed.md` Rev 3 (frozen)
**Reviewer:** reviewer (Mode B)
**Date:** 2026-06-08

---

## §1 Form-Encoding (HIGH — V1/V2/V3 critical path)

**Verdict: CORRECT**

`apps/web/src/lib/api.ts` lines 24–31:

```ts
const body = new URLSearchParams()
body.append('username', email)
body.append('password', password)

const response = await fetch(`${API_BASE}/api/auth/token`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  body: body.toString(),
})
```

- `Content-Type: application/x-www-form-urlencoded` is set explicitly. ✓
- Body uses `URLSearchParams.toString()` — produces `username=…&password=…` format. ✓
- `username` field name used (matches `OAuth2PasswordRequestForm.username`). ✓
- T4 asserts `ct === 'application/x-www-form-urlencoded'` and `body.includes('username=')` and `body.includes('password=')` — the test is a concrete guard. ✓

No deviation from agreed.md §5 login submit flow steps 3–4.

---

## §2 "Invalid credentials" Exact String (HIGH)

**Verdict: CORRECT**

End-to-end trace:

1. `api.ts` line 34: `throw new Error('Invalid credentials')` on `response.status === 401`. ✓
2. `LoginPage.tsx` lines 36–39:
   ```ts
   const message =
     err instanceof Error ? err.message : 'Something went wrong, please try again'
   setError(message)
   ```
   `err.message === 'Invalid credentials'` → `setError('Invalid credentials')`. ✓
3. `LoginPage.tsx` line 54: `{error && <p role="alert">{error}</p>}` — rendered verbatim. ✓
4. T5 line 97: `expect(await screen.findByText('Invalid credentials')).toBeInTheDocument()` — exact string match, no regex. ✓

The agreed.md AD-3 requirement that the API's `"Incorrect username or password"` detail is never displayed is satisfied — the UI never reads `response.body` on 401; it throws a hardcoded error message before any `.json()` call.

---

## §3 CORSMiddleware Placement Order (HIGH — M2)

**Verdict: CORRECT**

`apps/api/dataplat_api/main.py` line numbers (verified by `grep -n`):

| Line | Statement |
|------|-----------|
| 58 | `app = FastAPI(title="Dataplat API", ...)` |
| 63–70 | `app.add_middleware(CORSMiddleware, ...)` |
| 71 | `app.include_router(health_router)` |
| 72–89 | remaining `app.include_router(...)` calls |

`add_middleware` is at line 63–70, immediately after `app = FastAPI(...)` at line 58, and all `include_router` calls follow at line 71+. M2 placement order requirement is satisfied. ✓

CORS config matches agreed.md §4 AD-8 verbatim:
- `allow_origins=settings.CORS_ORIGINS` (default `["http://localhost:15173"]`) ✓
- `allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"]` ✓
- `allow_headers=["Content-Type","Authorization"]` ✓
- `allow_credentials=False` ✓

`apps/api/dataplat_api/config.py` adds `CORS_ORIGINS: list[str] = ["http://localhost:15173"]` — matches agreed.md §3 and §4 AD-8. ✓

---

## §4 localStorage Key

**Verdict: CORRECT**

`apps/web/src/lib/storage.ts` line 6: `const TOKEN_KEY = 'dataplat.access_token'`

All three helpers (`getToken`, `setToken`, `clearToken`) use only this constant — no inline string literals, no alternate key spellings. ✓

Tests (T3, T5, T8) assert `localStorage.getItem('dataplat.access_token')` — exact key confirmed in test assertions. ✓

---

## §5 Test Count and Coverage (T1–T9)

**Verdict: ALL 9 TESTS PRESENT AND CORRECTLY ASSERTING**

| Test | Present | Key assertions verified |
|------|---------|------------------------|
| T1 | ✓ | `getByRole('textbox',{name:/email/i})` has `type="email"`; `getByLabelText(/password/i)` has `type="password"`; submit button present |
| T2 | ✓ | `queryByText('Invalid credentials')` is null; `queryByText(/something went wrong/i)` is null |
| T3 | ✓ | `localStorage.getItem('dataplat.access_token') === 'test-jwt'`; `mockNavigate.toHaveBeenCalledWith('/')` |
| T4 | ✓ | `fetch` called once; URL ends `/api/auth/token`; method `'POST'`; `Content-Type: application/x-www-form-urlencoded`; body contains `username=` and `password=` |
| T5 | ✓ | `findByText('Invalid credentials')` present (exact string); `mockNavigate.not.toHaveBeenCalled()` (concrete — reset in `beforeEach`); token is null |
| T6 | ✓ | `findByText('Something went wrong, please try again')` present; `mockNavigate.not.toHaveBeenCalled()` |
| T7 | ✓ | Never-resolving fetch; button with name `/logging in/i` is disabled |
| T8 | ✓ | (in `describe('HomePage')`) clears token; `mockNavigate.toHaveBeenCalledWith('/login')` |
| T9 | ✓ | Seeds localStorage token; `mockNavigate.toHaveBeenCalledWith('/')` |

**V3 specific (T5):** `expect(mockNavigate).not.toHaveBeenCalled()` is present AND `localStorage.getItem('dataplat.access_token')` is asserted null. The `mockNavigate` instance is reset in `beforeEach(() => { mockNavigate.mockReset(); localStorage.clear() })` — the `.not.toHaveBeenCalled()` assertion is non-vacuous. ✓

**T9 behavior with `useEffect` deviation (see §9, divergence #2):** `renderLogin()` calls `render()` which is wrapped by RTL's `act()`; React 18 flushes effects synchronously within `act()`. The `expect(mockNavigate).toHaveBeenCalledWith('/')` assertion immediately after `await renderLogin()` is sound — effects are flushed before the assertion runs. T9 passes correctly.

**Notable: T8 is in a separate `describe('HomePage')` block, not in `describe('LoginPage')`.** The test ID numbering in agreed.md is satisfied by total count; the grouping is a non-material organizational choice.

---

## §6 auth.py — Must Be Untouched

**Verdict: CONFIRMED UNTOUCHED**

`git diff 3f246a5..ff86981 -- apps/api/dataplat_api/routers/auth.py` returns empty. No changes to token claims, auth logic, or any router logic. ✓

---

## §7 No Scope Creep

**Verdict: CLEAN**

Files added are exactly those in the agreed.md §3 file table plus the disclosed additions (see §9). Checked:

- No register/signup page. ✓
- No password reset. ✓
- No MFA/OAuth/social login. ✓
- No Tailwind, MUI, Redux, Zustand. ✓
- `grep -r "anthropic\|openai" apps/web/` returns nothing. ✓
- No `packages/api-types/` diff. ✓
- `spec/feature_list.json` untouched. ✓ (Leader flips `passes` after verifier, not implementer.)

---

## §8 Docker Build

**Verdict: CORRECT**

`docker/web/Dockerfile`:
- Stage 1: `FROM node:22-alpine AS builder` — Node 22 matches agreed.md AD-7. ✓
- `WORKDIR /repo` ✓
- `corepack enable && corepack prepare pnpm@9.15.4 --activate` — see divergence #5 below. ✓
- Copies `pnpm-workspace.yaml`, `pnpm-lock.yaml`, `package.json*`, `apps/web/`. ✓
- `pnpm install --frozen-lockfile` ✓
- `pnpm --filter web build` ✓
- Stage 2: `FROM nginx:1.27-alpine` ✓
- `COPY --from=builder /repo/apps/web/dist /usr/share/nginx/html` ✓
- `COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf` ✓

`docker/docker-compose.dev.yml`:
- `context: ..` (repo root) ✓
- `dockerfile: docker/web/Dockerfile` ✓
- `args: VITE_API_BASE_URL: "${VITE_API_BASE_URL:-http://localhost:18000}"` ✓

`docker/web/index.html` deletion confirmed: `git show ff86981:docker/web/index.html` → `fatal: path does not exist in 'ff86981'`. ✓

---

## §9 Disposition of Implementer-Disclosed Divergences

### Divergence #1 — `verify/checks.sh` test invocation patched

**APPROVED**

Change: `pnpm --filter web test --run` → `pnpm --filter web run test -- --run`

Evidence:
1. Change is minimal — one line in `checks.sh` (`verify/checks.sh` line 126), with an explanatory comment. No other lines touched in the `frontend` case block. ✓
2. `apps/web/package.json` `"test"` script is `"vitest run"`. The `pnpm --filter web run test -- --run` invocation executes `vitest run --run`. The `--run` flag passed via `--` is recognized by Vitest as "disable watch mode" — it is a no-op when already using the `run` subcommand (which disables watch by default), but harmless and explicit. CI will not hang. ✓
3. This is the correct pnpm v9 workaround — pnpm v9 changed shorthand behavior so `pnpm test --run` no longer passes `--run` to the underlying script; `pnpm run test -- --run` is the canonical form. ✓

### Divergence #2 — `LoginPage.tsx` inverse guard: `useEffect + navigate` instead of `<Navigate>`

**APPROVED WITH FOLLOW-UP NOTE**

Behavioral difference acknowledged:
- `<Navigate to="/" />` (agreed.md AD-4, §5): renders a redirect component synchronously during the render phase — no flash of login form.
- `useEffect(() => { if (getToken()) navigate('/') }, [navigate])` (implemented): runs after the first paint — one-frame flash of login form before redirect.

This is a real UX regression vs. the agreed spec. However:
- For MVP running over localhost, sub-frame rendering is imperceptible.
- The implementer's reason (testability with mocked `useNavigate`) is real for the test setup pattern chosen.
- T9 is sound: RTL's `act()` wraps `render()` and flushes effects synchronously in jsdom, so `expect(mockNavigate).toHaveBeenCalledWith('/')` is a valid and passing assertion.

**APPROVED for MVP.** Follow-up: In a subsequent sprint, revert to `<Navigate to="/" />` in JSX and rewrite T9 to use `MemoryRouter` with pre-seeded `initialEntries` + location assertion instead of mocking `useNavigate`. This eliminates both the one-frame flash and the test coupling to the navigate mock.

### Divergence #3 — `apps/web/src/vite-env.d.ts` added

**APPROVED**

Standard Vite TypeScript project boilerplate (`/// <reference types="vite/client" />`). Required for `import.meta.env` typing to be recognized by TypeScript. Not in agreed.md file table as it was an implied implementation detail. Single line, zero risk.

### Divergence #4 — `apps/web/src/lib/storage.ts` split from `api.ts`

**APPROVED**

Agreed.md §3 describes `api.ts` as owning `getToken()`, `setToken()`, `clearToken()`. The implementer extracted these into `storage.ts` — a clean separation of concerns (token storage vs. HTTP client). Both files are in `apps/web/src/lib/`. The surface contract (exported names, localStorage key, behavior) is identical to the spec. This is a pure internal refactor with no behavioral difference.

### Divergence #5 — Dockerfile pnpm pinned to `9.15.4`

**APPROVED**

`corepack prepare pnpm@9.15.4 --activate` instead of bare `corepack enable`.

Agreed.md §4 AD-7 said "generic `corepack enable`". Pinning to `9.15.4` is strictly safer — it avoids pnpm v10/v11 stricter supply-chain policies rejecting packages in the existing lockfile (specifically `nwsapi@2.2.24`). The pin matches the lockfile version. No security concern in pinning to a specific known-good minor version. ✓

### Divergence #6 — `.npmrc` with `strict-peer-dependencies=false`

**APPROVED WITH VERIFICATION**

Contents verified: `.npmrc` contains exactly one line: `strict-peer-dependencies=false`. ✓

No hidden settings, no registry overrides, no authentication tokens, no other directives. The sole purpose is to allow `pnpm install` to succeed when transitive peer dependency conflicts exist (common with React Testing Library + React 18 ecosystem). This is a standard pnpm workaround for the MVP phase. Acceptable.

---

## §10 Invariants Matrix

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| #1 | Lineage mandatory (parents[] + processor identity + config hash + input refs) | **N/A** | No Commit, blob, or lineage record written. Pure UI + JWT. |
| #2 | Storage separation + CAS (metadata in Postgres, content in MinIO) | **N/A** | No blob or metadata writes. JWT is ephemeral and client-held. |
| #3 | Schema frozen post-publish | **N/A** | No Silver/Gold repo schemas touched. |
| #4 | LLM calls via gateway only | **PASS** | `grep -r "anthropic\|openai" apps/web/` → empty. No LLM calls in login flow. |
| #5 | Async SQLAlchemy (no sync sessions, no `session.query()`) | **PASS** | `apps/api/dataplat_api/main.py` modified only to add `CORSMiddleware`. No session or DB code touched. `routers/auth.py` unchanged (verified: empty diff). `CORSMiddleware` is network-layer synchronous middleware that does not access the DB or block the async event loop. |
| #6 | OpenAPI ↔ TS type sync (`make codegen` + committed diff) | **PASS** | No OpenAPI schema change in this sprint. `Makefile` `codegen` target exits 0 and prints the required NOTICE message (verified: `@echo "NOTICE: codegen is a no-op stub; full openapi-typescript wiring deferred to F-075."`). `git diff 3f246a5..ff86981 -- packages/` → empty. No `packages/api-types/` diff. |

---

## §11 Verification Criteria Mapping

| Criterion | Agreed.md Ref | Test/Check | LOC Evidence |
|-----------|--------------|------------|--------------|
| V1: form renders with email + password | §6 V1 | T1 in `LoginPage.test.tsx` | L68–77: `getByRole('textbox',{name:/email/i})` + `.toHaveAttribute('type','email')`; `getByLabelText(/password/i)` + `.toHaveAttribute('type','password')` |
| V2: valid credentials navigate to `/` | §6 V2 | T3 in `LoginPage.test.tsx` | L83–98: `localStorage.getItem('dataplat.access_token') === 'test-jwt'`; `mockNavigate.toHaveBeenCalledWith('/')` |
| V2: valid credentials use form encoding | §6 V2 | T4 in `LoginPage.test.tsx` | L101–122: `Content-Type: application/x-www-form-urlencoded`; body contains `username=`, `password=` |
| V3: 401 shows "Invalid credentials", no navigate | §6 V3 | T5 in `LoginPage.test.tsx` | L124–135: `findByText('Invalid credentials')`; `mockNavigate.not.toHaveBeenCalled()`; token is null |
| localStorage key `dataplat.access_token` | §4 AD-2 | `storage.ts` + T3/T5/T8 | `storage.ts:6`; tests L93, L131, L170 |
| CORSMiddleware before routers (M2) | §4 AD-8, §5 | `main.py` line ordering | Lines 58 (FastAPI), 63–70 (add_middleware), 71+ (include_router) |
| No scope creep | §9 | static analysis | `grep` for anthropic/openai, register/MFA pages: clean |
| Inverse guard T9 | §7 T9 | `LoginPage.test.tsx` | L155–162: seeds token, renders LoginPage, asserts `mockNavigate.toHaveBeenCalledWith('/')` |

---

## §12 Minor Observations (Non-blocking)

1. **`apps/web/tsconfig.node.json`** — added as a companion file for `vite.config.ts` (`"references": [{"path":"./tsconfig.node.json"}]`). Not in the agreed.md file table but is standard Vite+TS scaffolding. No functional impact.

2. **`package.json` at repo root** — minimal workspace root descriptor `{"name":"dataplat","version":"0.0.0","private":true}`. Not in agreed.md file table but required for pnpm workspaces to function. No concern.

3. **`pnpm-lock.yaml`** — 2053-line lockfile committed, not audited in detail. Trust that `pnpm install --frozen-lockfile` in the Dockerfile validates integrity.

4. **`App.tsx` catch-all route** — `<Route path="*" element={<Navigate to="/" />} />` present. Not in agreed.md file table but is sensible defensive routing. `RequireAuth` wraps `/` so unauthenticated users hitting unknown paths still reach `/login` via the guard. No issue.

5. **`useEffect` dependency array** — `[navigate]` is the only dependency. `navigate` is stable across renders (react-router-dom guarantees reference stability). The effect fires once on mount. Correct.

6. **`LoginPage.tsx` inverse guard comment (line 8–11)** — explicitly documents the `useEffect` vs. `<Navigate>` choice with the reason. Good documentation hygiene. Follow-up item tracked in §9 divergence #2 disposition.

---

## §13 Summary

The implementation is complete, well-structured, and faithful to the agreed spec (Rev 3) in all HIGH-priority dimensions:

- Form encoding: correct (`URLSearchParams`, `application/x-www-form-urlencoded`, `username=` field). ✓
- "Invalid credentials" exact string: correct (thrown in api.ts as Error message, caught and rendered verbatim). ✓
- CORSMiddleware placement (M2): correct (line 63–70 in main.py, before all 15 `include_router` calls). ✓
- localStorage key: exact (`dataplat.access_token`, centralized via `storage.ts`). ✓
- All 9 tests (T1–T9): present, correctly asserting, non-vacuous. ✓
- Invariants #1–#6: all N/A or PASS. ✓
- No scope creep. ✓
- Tooling (Makefile, pnpm-workspace, checks.sh): correct and unblocking. ✓

All 6 implementer-disclosed divergences are APPROVED (divergence #2 approved for MVP with follow-up note).

---

## VERDICT: APPROVED
