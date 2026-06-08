# Sprint S055-F-055 — Proposed Contract
# Login Page: React/Vite SPA with JWT Auth

**Sprint ID:** S055-F-055
**Feature:** F-055 (category: web, P1)
**Author:** implementer
**Date:** 2026-06-08
**Revision:** 3
**Depends on:** F-007 ✓ (passes: true)

---

## §1 Goal

Replace the nginx static-HTML placeholder at `docker/web/` with a production-built
React/Vite SPA that renders a login form at `/login`, obtains a JWT on valid credentials,
and redirects to the home dashboard at `/`; invalid credentials display the message
"Invalid credentials" without navigating away.

---

## §2 Scope Summary

### In scope
- `apps/web/` — new Vite + React + TypeScript project (pnpm workspace package `"web"`).
- `docker/web/Dockerfile` — rewritten as a two-stage build (Node build → nginx serve); the
  existing `docker/web/index.html` and `docker/web/nginx.conf` are superseded but the
  Dockerfile file path is kept to avoid docker-compose changes.
- `docker/web/nginx.conf` — updated to serve the SPA with `try_files $uri /index.html`.
- `apps/api/dataplat_api/main.py` — add `CORSMiddleware` (starlette) allowing
  `http://localhost:15173` (the deployed host port) as an allowed origin for
  `POST /api/auth/token`. `app.add_middleware(CORSMiddleware, ...)` MUST be placed
  immediately after `app = FastAPI(...)` and BEFORE any `app.include_router(...)` call
  (see §4 AD-8 and §5 server-side init order).
- `pnpm-workspace.yaml` — created at repo root, declaring `packages: ["apps/web"]`.
- `Makefile` — created at repo root with a `codegen` target (initially a no-op stub that
  prints a visible NOTICE message and exits 0); this unblocks `verify/checks.sh contract`
  which gates on `[[ -f Makefile ]]`.
- `packages/api-types/` — **no new generated output this sprint**; the codegen deferral is
  documented explicitly (see §4 decision AD-6 and §9).
- Component tests: Vitest + React Testing Library covering LoginPage (T1–T9; V1–V3 plus
  T9 defense-in-depth inverse-guard test).
- Verifier-layer checks: `bash verify/checks.sh frontend` (lint + typecheck + unit tests).

### Out of scope (explicit deferrals)
- TS client generation from `packages/api-types/openapi.json` (deferred — see §4 AD-6).
- Registration, password reset, MFA, OAuth, social login (CLAUDE.md MVP boundaries §11.6).
- Repository-level ACL (CLAUDE.md MVP boundaries §11.6).
- Any page beyond `/login` and `/` (home placeholder); the home page is a minimal stub.
- Playwright / browser-level end-to-end tests (deferred; verifier uses curl + unit tests).
- Tailwind CSS, MUI, Redux, Zustand — not pulled in; plain CSS module or inline styles only.
- httpOnly cookie token storage (more secure but requires server-side cookie issuance; deferred).
- The `FRONTEND_HOST_PORT` env var in docker-compose — its default `15173` is left unchanged.
- Full ESLint configuration (deferred); `lint` script aliased to `tsc --noEmit` for this sprint
  (see §3).

---

## §3 File Table

| File | Action | Purpose |
|---|---|---|
| `apps/web/package.json` | **NEW** | pnpm package `"web"`; deps: react, react-dom, react-router-dom; devDeps: vite, @vitejs/plugin-react, typescript, vitest, @testing-library/react, @testing-library/jest-dom, @testing-library/user-event, jsdom; scripts MUST include `"build": "vite build"`, `"test": "vitest"`, `"lint": "tsc --noEmit"`, `"typecheck": "tsc --noEmit"` — `lint` is aliased to `tsc --noEmit` (no separate ESLint config) so `pnpm --filter web lint` succeeds in `checks.sh frontend` |
| `apps/web/tsconfig.json` | **NEW** | TypeScript config for the web app (target ES2020, module ESNext, jsx react-jsx, strict) |
| `apps/web/vite.config.ts` | **NEW** | Vite config: React plugin, Vitest globals + jsdom environment, test setup file |
| `apps/web/index.html` | **NEW** | HTML entrypoint for Vite (single `<div id="root">` + script tag) |
| `apps/web/.gitignore` | **NEW** | Prevent accidental staging of build artifacts and deps; ignores `node_modules/`, `dist/`, `.env`, `*.local` — see contents below table |
| `apps/web/src/main.tsx` | **NEW** | React root mount: `ReactDOM.createRoot(root).render(<App/>)` |
| `apps/web/src/App.tsx` | **NEW** | Router setup with `BrowserRouter`; declares two routes: `/login` → `LoginPage`, `/` → `HomePage`; includes `RequireAuth` guard |
| `apps/web/src/pages/LoginPage.tsx` | **NEW** | Login form component (email + password inputs, submit handler, error state); calls `authApi.login()`; on success stores token + navigates to `/`; on 401 shows "Invalid credentials"; inverse guard: if already logged-in redirects to `/` immediately (tested by T9) |
| `apps/web/src/pages/HomePage.tsx` | **NEW** | Minimal home stub: heading "Dataplat" + logout button (clears token, redirects to `/login`) |
| `apps/web/src/lib/api.ts` | **NEW** | Auth API client — hand-rolled `login(email, password)` function; POSTs `application/x-www-form-urlencoded` to `VITE_API_BASE_URL/api/auth/token`; maps HTTP 401 → throws `"Invalid credentials"`; exports `getToken()`, `setToken()`, `clearToken()` (localStorage key `dataplat.access_token`) |
| `apps/web/src/components/RequireAuth.tsx` | **NEW** | Route guard component: reads token from localStorage; if absent redirects to `/login`; if present renders children |
| `apps/web/src/test/setup.ts` | **NEW** | Vitest setup file: `import '@testing-library/jest-dom'` |
| `apps/web/src/pages/LoginPage.test.tsx` | **NEW** | Vitest + RTL tests for LoginPage — T1–T9, with module-level `vi.mock` for `react-router-dom` / `useNavigate` (see §7 test harness setup) |
| `apps/web/.env.example` | **NEW** | Documents `VITE_API_BASE_URL=http://localhost:18000`; baked into build at Vite build time via `import.meta.env.VITE_API_BASE_URL` |
| `pnpm-workspace.yaml` | **NEW** | Declares `packages: ["apps/web"]` at repo root |
| `Makefile` | **NEW** | `codegen` target: prints NOTICE message (exact body in §4 AD-6), exits 0; unblocks `verify/checks.sh contract` |
| `docker/web/Dockerfile` | **MOD** | Rewrite as two-stage: stage 1 `node:22-alpine` builds `apps/web`; stage 2 `nginx:1.27-alpine` serves `dist/`; build context changes to repo root (see §4 AD-7) |
| `docker/web/nginx.conf` | **MOD** | Keep existing `try_files $uri $uri/ /index.html` rule (already present); no functional change needed — verified it already supports SPA routing |
| `docker/docker-compose.dev.yml` | **MOD** | Update `frontend.build.context` from `./web` to `..` (repo root) and add `dockerfile: docker/web/Dockerfile`; add `VITE_API_BASE_URL` build-arg to bake the API URL into the SPA at build time |
| `apps/api/dataplat_api/main.py` | **MOD** | Add `CORSMiddleware` from `starlette.middleware.cors`; call MUST appear immediately after `app = FastAPI(...)` and BEFORE all `app.include_router(...)` calls (M2 requirement); allowed origins `["http://localhost:15173"]` via settings; allowed methods `["GET","POST","PUT","PATCH","DELETE","OPTIONS"]` (broad, accepted for MVP — see AD-8); allowed headers `["Content-Type","Authorization"]`; `allow_credentials=False` |
| `apps/api/dataplat_api/config.py` | **MOD** | Add optional `CORS_ORIGINS: list[str]` setting with default `["http://localhost:15173"]`; used by `main.py` CORS setup |

**`apps/web/.gitignore` contents (4 lines):**

```
node_modules/
dist/
.env
*.local
```

---

## §4 Architecture Decisions

**AD-1 — Port convention (RESOLVED: use 15173, not 5173)**

The compose mapping `15173:80` is left unchanged. The feature spec's mention of
`http://localhost:5173/login` is treated as a stand-in for the project's deployed host port.
All verification in this sprint runs against `http://localhost:15173`. The `FRONTEND_HOST_PORT`
env var default in `docker/.env.example` stays `15173`.

> **RESOLVED by reviewer (round 1):** Interpretation is correct — `15173` is the project's
> actual deployed host port; `5173` in the spec is the Vite-convention stand-in. Running
> verification against `http://localhost:15173` is accepted. No compose mapping change
> required. (See §8 OQ-1.)

---

**AD-2 — JWT storage: localStorage under key `dataplat.access_token` (CLOSED/ACKNOWLEDGED)**

Token is stored in and read from `localStorage` under the key `dataplat.access_token`. This
is MVP-acceptable: localStorage is readable by any JS on the same origin; XSS exposure is
the known trade-off. httpOnly cookie issuance (the more secure alternative) requires a
server-side `Set-Cookie` response from the API, which is out of scope for this sprint.

The `apps/web/src/lib/api.ts` module exposes three helpers so all storage access is
centralized: `getToken()`, `setToken(token: string)`, `clearToken()`.

> **CLOSED/ACKNOWLEDGED:** XSS trade-off accepted for MVP. httpOnly cookie with server-side
> `Set-Cookie` deferred to a future sprint. Reviewer confirmed in round 1. (See §8 OQ-4.)

---

**AD-3 — Error message mapping: always "Invalid credentials"**

The API returns `{"detail": "Incorrect username or password"}` on HTTP 401. The UI MUST NOT
display this string. Any HTTP 401 response from `POST /api/auth/token` is mapped to the
literal display string `"Invalid credentials"` in `LoginPage.tsx`. Network errors (fetch
failure, non-401 errors) display a generic "Something went wrong, please try again" message,
also without navigating away. Only "Invalid credentials" is explicitly required by the spec;
the network error fallback is a UX hygiene addition.

---

**AD-4 — Route guard**

`RequireAuth` is a thin wrapper component placed around the `/` route in `App.tsx`. It
reads `getToken()` from localStorage; if null or empty it imperatively redirects to
`/login` using `<Navigate to="/login" />`. The `/login` route contains an inverse
guard: if `getToken()` returns a non-empty string the page immediately redirects to `/`
via `<Navigate to="/" />` (prevents double-login). The inverse guard is covered by
test T9 (see §7).

---

**AD-5 — Home page: minimal stub**

`HomePage.tsx` renders an `<h1>Dataplat</h1>` heading and a "Logout" button that calls
`clearToken()` then `navigate("/login")`. No data fetching, no sidebar, no further layout.
The feature spec verification only requires that a redirect to `/` succeeds; a functional
placeholder is sufficient for this sprint.

---

**AD-6 — OpenAPI → TS codegen: hand-roll auth client, defer full codegen (RESOLVED: deferred to F-075)**

The auth endpoint uses `application/x-www-form-urlencoded` request body (OAuth2
PasswordRequestForm). OpenAPI generators typically emit `application/json` clients; wiring
form-encoded OAuth2 token exchange through a generator adds non-trivial scope and tooling
(e.g. `openapi-typescript-codegen` or `orval` configuration for form bodies) that is
disproportionate for a single endpoint.

**Decision: hand-roll `apps/web/src/lib/api.ts` for this sprint.** The `Makefile` is
introduced with a `codegen` stub (exits 0) so `verify/checks.sh contract` passes without
error. Full `openapi-typescript` or `orval` wiring is a follow-up task deferred to F-075.

The exact `codegen` Makefile target body is:

```makefile
codegen:
	@echo "NOTICE: codegen is a no-op stub; full openapi-typescript wiring deferred to F-075."
```

> **RESOLVED by reviewer (round 1):** Stub exits 0; no TS client generated this sprint.
> The stub MUST print the NOTICE message above verbatim so no future maintainer is confused.
> `packages/api-types/` has no diff; the `git diff --exit-code` check passes. (See §8 OQ-2.)

---

**AD-7 — Multi-stage Dockerfile and build context**

The existing `docker/web/Dockerfile` is rewritten in place as a two-stage build:

```
Stage 1 (builder): node:22-alpine
  WORKDIR /repo
  COPY pnpm-workspace.yaml package.json* ./
  COPY apps/web ./apps/web
  RUN corepack enable && pnpm install --frozen-lockfile
  RUN VITE_API_BASE_URL=$VITE_API_BASE_URL pnpm --filter web build

Stage 2 (runtime): nginx:1.27-alpine
  COPY --from=builder /repo/apps/web/dist /usr/share/nginx/html
  COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf
```

Because `apps/web/` is outside `docker/web/`, the build context must be the **repo root**
(`..` relative to `docker/`). The `docker-compose.dev.yml` `frontend` service is updated:

```yaml
frontend:
  build:
    context: ..               # repo root
    dockerfile: docker/web/Dockerfile
    args:
      VITE_API_BASE_URL: "${VITE_API_BASE_URL:-http://localhost:18000}"
```

`VITE_API_BASE_URL` is a Docker build-arg baked into the static bundle at build time via
Vite's `import.meta.env` mechanism. The default `http://localhost:18000` matches the
`fastapi` service's host port mapping.

---

**AD-8 — CORS middleware (placement order REQUIRED; broad methods RESOLVED for MVP)**

No `CORSMiddleware` exists anywhere in `apps/api/` today. This sprint adds it to
`apps/api/dataplat_api/main.py`. Configuration:

```python
from starlette.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,   # ["http://localhost:15173"] by default
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)
```

**PLACEMENT ORDER REQUIREMENT (M2):** The `app.add_middleware(CORSMiddleware, ...)` call
MUST be placed immediately after `app = FastAPI(...)` and BEFORE any `app.include_router(...)`
call. Starlette applies middleware in LIFO order relative to `add_middleware()` calls;
registering middleware after routers risks subtle ordering bugs if additional middleware is
added in future sprints. The canonical initialization sequence is shown in §5.

`Settings.CORS_ORIGINS` is added to `apps/api/dataplat_api/config.py` as:

```python
CORS_ORIGINS: list[str] = ["http://localhost:15173"]
```

This is the only `apps/api/` change. Invariant #5 (async SQLAlchemy) is not affected; no
session or query code is touched. Reviewer confirmed in round 1 that `CORSMiddleware` is
synchronous at the network layer and does not block the async event loop.

> **RESOLVED by reviewer (round 1, OQ-3):** Broad `allow_methods` (all HTTP verbs) is
> acceptable for MVP. CORS only restricts browser-side cross-origin requests; the real-world
> risk is minimal. No code change required relative to the broad config.
>
> **RESOLVED by reviewer (round 1, OQ-5):** `allow_credentials=False` is correct for the
> no-cookie MVP. Will change to `True` only when httpOnly cookies are introduced. (See §8
> OQ-3 and OQ-5.)

---

**AD-9 — Tooling: Vite + React + TypeScript, pnpm, no heavy UI library**

- **Build tool:** Vite 5.x (fastest cold start, native ESM).
- **Framework:** React 18 + react-router-dom v6 (declarative routing, `<Navigate>`,
  `useNavigate`).
- **Language:** TypeScript (strict mode).
- **Package manager:** pnpm (matches project tooling direction implied by
  `verify/checks.sh` which calls `pnpm --filter web`).
- **Testing:** Vitest (Vite-native, no Jest config overhead) + React Testing Library +
  `@testing-library/jest-dom` + `@testing-library/user-event`.
- **CSS:** plain CSS or inline styles only. No Tailwind, MUI, or CSS-in-JS runtime.
- **State management:** React built-in state (`useState`, `useEffect`). No Redux, Zustand,
  React Query.

---

## §5 Login Submit Flow

```
User opens http://localhost:15173/login
  → LoginPage renders
  → If getToken() is non-empty → <Navigate to="/" /> (already logged in; tested T9)
  → Else: render <form> with email (type="email") and password (type="password") inputs

User types credentials and clicks "Log in"
  → handleSubmit(e: FormEvent)
     1. e.preventDefault()
     2. setError(null); setLoading(true)
     3. Construct URLSearchParams:
           body = new URLSearchParams()
           body.append("username", email)   ← API field is "username", value is email
           body.append("password", password)
     4. fetch(`${import.meta.env.VITE_API_BASE_URL}/api/auth/token`, {
           method: "POST",
           headers: { "Content-Type": "application/x-www-form-urlencoded" },
           body: body.toString(),
        })
     5. if response.status === 401:
           setError("Invalid credentials")
           setLoading(false)
           return   ← NO navigation
     6. if !response.ok (other error):
           setError("Something went wrong, please try again")
           setLoading(false)
           return   ← NO navigation
     7. const data = await response.json()
        setToken(data.access_token)        ← localStorage.setItem("dataplat.access_token", ...)
        navigate("/")                          ← redirect to home
```

```
User opens http://localhost:15173/
  → App.tsx routes to <RequireAuth><HomePage /></RequireAuth>
  → RequireAuth reads getToken()
  → If null/empty → <Navigate to="/login" />
  → If present → renders HomePage

User clicks "Logout" on HomePage
  → clearToken()   ← localStorage.removeItem("dataplat.access_token")
  → navigate("/login")
```

### Server-side initialization order for `apps/api/dataplat_api/main.py`

The following canonical initialization order MUST be followed in `main.py` (M2 requirement
from §4 AD-8):

```python
# main.py — canonical initialization order
app = FastAPI(...)                          # 1. create app instance
app.add_middleware(CORSMiddleware, ...)     # 2. add ALL middleware BEFORE any routers
                                            #    (Starlette LIFO ordering requires this)
app.include_router(auth_router, ...)        # 3. routers follow
# ... additional app.include_router() calls
```

This ensures Starlette processes CORS preflight `OPTIONS` requests at the middleware layer
before routing logic, and prevents subtle ordering bugs if additional middleware is added
in future sprints.

---

## §6 Verification Matrix

| Criterion | Spec text | Unit test (Vitest + RTL) | Verifier-layer check |
|---|---|---|---|
| **V1** | "renders a form with email and password fields" | `LoginPage.test.tsx` › `renders email and password inputs`: `screen.getByRole("textbox", {name:/email/i})` has `type="email"`; `screen.getByLabelText(/password/i)` has `type="password"` | `curl -s -o /dev/null -w "%{http_code}" http://localhost:15173/login` returns `200`; `curl -s http://localhost:15173/login \| grep -i "<form"` exits 0 |
| **V2** | "Submitting valid credentials redirects to /" | `LoginPage.test.tsx` › `valid credentials navigate to /`: mock `fetch` returns `{status:200, ok:true, json:()=>Promise.resolve({access_token:"tok",token_type:"bearer"})}`; submit form; assert `expect(mockNavigate).toHaveBeenCalledWith("/")` and `localStorage.getItem("dataplat.access_token") === "tok"` | (No Playwright for MVP) Smoke: `bash verify/checks.sh frontend` exits 0 |
| **V3** | "invalid credentials shows 'Invalid credentials' error message without navigating away" | `LoginPage.test.tsx` › `invalid credentials show error without navigating`: mock `fetch` returns `{status:401, ok:false}`; submit form; `await screen.findByText("Invalid credentials")`; assert `expect(mockNavigate).not.toHaveBeenCalled()` (concrete negative assertion; `mockNavigate` reset in `beforeEach` so any navigation call would be detected; same `mockNavigate` instance as V2) | Same `bash verify/checks.sh frontend` run |

---

## §7 Tests

All tests live in `apps/web/src/pages/LoginPage.test.tsx`. Run via:

```bash
pnpm --filter web test --run
```

or inside CI:

```bash
bash verify/checks.sh frontend
```

### Test harness setup (module-level mock for `useNavigate`)

The following declarations are placed at the **top of `LoginPage.test.tsx`**, outside any
`describe` or `test` block. Vitest hoists `vi.mock(...)` calls before module imports, which
guarantees `useNavigate` returns `mockNavigate` from the first render — the real hook is
never invoked regardless of import order.

```ts
const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})
```

A `beforeEach` hook resets the mock between tests to prevent cross-test contamination:

```ts
beforeEach(() => {
  mockNavigate.mockReset()
})
```

**All tests T1 through T9 reference the same `mockNavigate` instance.** Tests that assert
navigation (T3, T8, T9) use `expect(mockNavigate).toHaveBeenCalledWith(...)`. Tests that
assert no navigation (T5, T6) use `expect(mockNavigate).not.toHaveBeenCalled()`. Because
`mockNavigate` is reset in `beforeEach`, the `.not.toHaveBeenCalled()` assertion is
non-vacuous: any navigation call in the component under test would cause it to fail.

### Test table

| Test ID | Test name | What it asserts |
|---|---|---|
| **T1** | `renders email and password inputs` | `render(<LoginPage />)` (module-level `vi.mock` provides `mockNavigate`); `screen.getByRole("textbox", {name:/email/i})` present with `type="email"`; `screen.getByLabelText(/password/i)` present with `type="password"`; submit button present |
| **T2** | `renders no error on initial render` | `render(<LoginPage />)`; `screen.queryByText("Invalid credentials")` is `null`; `screen.queryByText("Something went wrong")` is `null` |
| **T3** | `valid credentials store token and navigate to /` | Mock `globalThis.fetch` → `{status:200, ok:true, json:()=>Promise.resolve({access_token:"test-jwt",token_type:"bearer"})}`; `userEvent.type` into email + password; `userEvent.click` submit button; assert `localStorage.getItem("dataplat.access_token") === "test-jwt"`; assert `expect(mockNavigate).toHaveBeenCalledWith("/")` |
| **T4** | `valid credentials call fetch with form-encoded body` | Same mock as T3; assert `fetch` called once with URL ending `/api/auth/token`, method `"POST"`, `Content-Type` header `"application/x-www-form-urlencoded"`, body containing `username=` and `password=` |
| **T5** | `invalid credentials (401) show error without navigating` | Mock `fetch` → `{status:401, ok:false}`; submit; `await screen.findByText("Invalid credentials")`; assert `expect(mockNavigate).not.toHaveBeenCalled()` (concrete: `mockNavigate` was reset in `beforeEach`; any navigation call would fail this assertion); assert `localStorage.getItem("dataplat.access_token")` is `null` |
| **T6** | `non-401 error shows generic message without navigating` | Mock `fetch` → `{status:500, ok:false}`; submit; `await screen.findByText("Something went wrong, please try again")`; assert `expect(mockNavigate).not.toHaveBeenCalled()` |
| **T7** | `submit button is disabled while request is in flight` | Mock `fetch` → never-resolving Promise; `userEvent.click` submit; assert submit button has `disabled` attribute while pending |
| **T8** | `logout on HomePage clears token and navigates to /login` | Seed `localStorage.setItem("dataplat.access_token","existing")`; render `<HomePage />`; `userEvent.click` logout button; assert `localStorage.getItem("dataplat.access_token")` is `null`; assert `expect(mockNavigate).toHaveBeenCalledWith("/login")` |
| **T9** | `already-logged-in: /login redirects to /` | Seed `localStorage.setItem("dataplat.access_token","fake.jwt.token")`; render `<LoginPage />`; assert `expect(mockNavigate).toHaveBeenCalledWith("/")` — verifies inverse guard redirects a logged-in user away from `/login` immediately (defense-in-depth; added per reviewer NIT-5 recommendation) |

---

## §8 Open Questions for Reviewer

**OQ-1 (port):** RESOLVED — see §4 AD-1. Reviewer accepted `15173` as the verification
target in round 1. `5173` in the spec is treated as Vite-convention intent. No compose
mapping change required.

**OQ-2 (codegen deferral):** RESOLVED — see §4 AD-6. Stub `Makefile` `codegen` target exits
0 and prints a visible NOTICE message. Full `openapi-typescript` wiring deferred to F-075.

**OQ-3 (CORS scope):** RESOLVED — see §4 AD-8. Broad `allow_methods` accepted for MVP by
reviewer in round 1. No code change required.

**OQ-4 (JWT storage):** RESOLVED — see §4 AD-2. localStorage accepted for MVP; httpOnly
cookie deferred. XSS trade-off CLOSED/ACKNOWLEDGED.

**OQ-5 (CORS allow_credentials):** RESOLVED — see §4 AD-8. `allow_credentials=False` is
correct for the no-cookie MVP. Will change to `True` when httpOnly cookies are introduced.

**OQ-6 (pnpm lockfile bootstrapping):** RESOLVED — pnpm + corepack is accepted and consistent
with project tooling direction. Reviewer confirmed in round 1.

---

## §9 Out-of-Scope Deferrals

Per CLAUDE.md MVP boundaries:

| Item | Rationale |
|---|---|
| Self-registration / sign-up page | CLAUDE.md §11.6 explicitly defers; F-055 spec says nothing about registration |
| Password reset / forgot-password flow | CLAUDE.md §11.6 |
| MFA / TOTP | CLAUDE.md §11.6 |
| OAuth2 / social login (Google, GitHub, etc.) | CLAUDE.md §11.6 |
| Repository-level granular ACL | CLAUDE.md §11.6 (MVP: visibility=private\|internal only) |
| Full OpenAPI → TS codegen wiring | Disproportionate scope for one form-encoded endpoint; tracked as AD-6 / F-075 |
| Playwright / browser E2E tests | Not required by spec verification; deferred to a later test-quality sprint |
| Dashboard pages (repositories, sources, runs, etc.) | F-055 spec only requires `/` (home stub) and `/login` |
| Token refresh / silent re-auth | Not in spec; deferred |
| Docker-in-Docker plugin sandbox | CLAUDE.md §11.2 |
| Celery / Dagster job submission from the UI | CLAUDE.md §11.2; not in F-055 scope |
| Full ESLint configuration | `lint` script aliased to `tsc --noEmit` for MVP; full ESLint flat config (`eslint.config.js`) deferred |

---

## §10 Hard Invariants Matrix

| # | Invariant | Applies? | Evidence / Notes |
|---|---|---|---|
| **#1** | Lineage mandatory (parents[] + processor identity + config hash + input refs) | **N/A** | No Commit, blob, or lineage record is written by the login page. Pure UI + JWT issuance. |
| **#2** | Storage separation + CAS (metadata in Postgres, content in MinIO) | **N/A** | No blob or metadata writes. The JWT is ephemeral and client-held. |
| **#3** | Schema frozen post-publish | **N/A** | No Silver/Gold repo schemas touched. |
| **#4** | LLM calls via gateway only | **N/A** | No LLM calls in the login flow. |
| **#5** | Async SQLAlchemy (no `session.query()`, no sync sessions) | **Applies (partially)** | `apps/api/dataplat_api/main.py` is modified to add `CORSMiddleware`. No session or DB code is touched. The auth router (`routers/auth.py`) already uses async SQLAlchemy correctly (unchanged). Reviewer confirmed in round 1 that `CORSMiddleware` is synchronous middleware at the network layer and does not introduce DB access or block the async event loop. |
| **#6** | OpenAPI ↔ TS type sync (`make codegen` + committed diff) | **Applies** | No OpenAPI schema change is made in this sprint (`CORSMiddleware` is runtime behavior, not schema). The `Makefile` `codegen` stub exits 0 and prints a NOTICE message; `verify/checks.sh contract` passes because `packages/api-types/` has no diff. Full codegen wiring deferred to F-075 (AD-6). Reviewer confirmed stub approach satisfies the guard. |

---

## §11 DoD Checklist

- [ ] `contracts/S055-F-055/agreed.md` exists and every item addressed.
- [ ] `apps/web/` created; `pnpm --filter web build` exits 0.
- [ ] `apps/web/src/pages/LoginPage.test.tsx` T1–T9 pass via `pnpm --filter web test --run`.
- [ ] `bash verify/checks.sh frontend` exits 0 (lint + typecheck + unit tests).
- [ ] `bash verify/checks.sh contract` exits 0 (Makefile present; `make codegen` exits 0 with NOTICE message; no `packages/api-types/` diff).
- [ ] `bash verify/checks.sh smoke` exits 0 (no regression).
- [ ] `bash verify/checks.sh backend` exits 0 (no ruff/mypy regression in `apps/api/`).
- [ ] `docker compose -f docker/docker-compose.dev.yml build frontend` succeeds (multi-stage build completes).
- [ ] `docker compose -f docker/docker-compose.dev.yml up -d frontend` brings service healthy.
- [ ] `curl -s -o /dev/null -w "%{http_code}" http://localhost:15173/login` → `200`.
- [ ] `curl -s http://localhost:15173/login | grep -i "<form"` exits 0.
- [ ] `curl -s http://localhost:15173/` → HTML response (does NOT return 404).
- [ ] CORS preflight `OPTIONS http://localhost:18000/api/auth/token` from origin `http://localhost:15173` returns `Access-Control-Allow-Origin: http://localhost:15173`.
- [ ] `feature_list.json` F-055 `passes` flipped to `true`.
- [ ] `claude-progress.txt` closing entry appended.
- [ ] Git commit(s) pushed with descriptive message referencing S055-F-055.

---

## Rev 2 — Change Log

Changes applied in response to Reviewer Mode A round-1 feedback (`contracts/S055-F-055/feedback.md`, verdict: CHANGES_REQUESTED).

| Finding | Change applied |
|---|---|
| **M1** | §7: Added "Test harness setup" sub-section specifying module-level `vi.mock('react-router-dom', async (importOriginal) => { const actual = await importOriginal<...>(); return { ...actual, useNavigate: () => mockNavigate } })` and `beforeEach(() => { mockNavigate.mockReset() })`; stated all T1–T9 reference same `mockNavigate` instance; updated T5 assertion to concrete `expect(mockNavigate).not.toHaveBeenCalled()`; updated §6 V2 and V3 to use `expect(mockNavigate).*` forms throughout |
| **M2** | §4 AD-8: Added explicit "PLACEMENT ORDER REQUIREMENT" paragraph stating `app.add_middleware(CORSMiddleware, ...)` MUST appear immediately after `app = FastAPI(...)` and BEFORE any `app.include_router(...)` call; §5: Added "Server-side initialization order" sub-section with canonical `main.py` init sequence showing middleware before routers; §3: Updated `apps/api/dataplat_api/main.py` Purpose cell to reference placement-order requirement |
| **L1** | §4 AD-6: Added exact `codegen` Makefile target body (`@echo "NOTICE: codegen is a no-op stub; full openapi-typescript wiring deferred to F-075."`); §3 Makefile and §11 DoD updated to reference "NOTICE message" explicitly |
| **NIT-1** | §3: Added `apps/web/.gitignore` as NEW file with one-line purpose; added 4-line contents block immediately after the file table (`node_modules/`, `dist/`, `.env`, `*.local`) |
| **NIT-2** | §4 AD-1: Replaced "Flag for reviewer" warning block with "RESOLVED by reviewer (round 1)" ruling; §8 OQ-1: Reduced to one-line RESOLVED cross-reference pointing to AD-1 |
| **NIT-3** | §4 AD-8: Added "RESOLVED by reviewer (round 1, OQ-3)" ruling for broad `allow_methods`; §8 OQ-3: Reduced to one-line RESOLVED cross-reference pointing to AD-8 |
| **NIT-4** | §4 AD-2: Added explicit `(CLOSED/ACKNOWLEDGED)` in section heading and a "CLOSED/ACKNOWLEDGED" note block confirming reviewer accepted the XSS trade-off in round 1 |
| **NIT-5** | §7: Added T9 `already-logged-in: /login redirects to /` (seeds `localStorage` token, asserts `expect(mockNavigate).toHaveBeenCalledWith("/")`) per reviewer recommendation for defense-in-depth; §4 AD-4: noted T9 covers inverse guard; §6: T9 visible in test count reference |
| **OQ-2** | §4 AD-6: Added "RESOLVED by reviewer (round 1)" ruling; §8 OQ-2: Reduced to one-line RESOLVED reference |
| **OQ-4** | §4 AD-2: Added RESOLVED note; §8 OQ-4: Reduced to one-line RESOLVED reference |
| **OQ-5** | §4 AD-8 and §8 OQ-5: Marked RESOLVED |
| **OQ-6** | §8 OQ-6: Marked RESOLVED |
| **Lint-fix** | §3 `apps/web/package.json` Purpose cell: Added explicit `"lint": "tsc --noEmit"` script requirement (no separate ESLint config needed) so `pnpm --filter web lint` succeeds in `checks.sh frontend`; §2 Out-of-scope and §9 Deferrals: noted full ESLint config as deferred |

---

## Rev 3 — Change Log

Changes applied in response to Reviewer Mode A round-2 feedback (`contracts/S055-F-055/feedback.md`, finding NEW-L1, verdict: CHANGES_REQUESTED). Option A chosen.

| Finding | Change applied |
|---|---|
| **NEW-L1** | Dropped `{ replace: true }` / `replace` prop everywhere in the design to align source-code call shape with existing single-arg test assertions. Specific edits: (1) §1 header: Revision 2 → 3. (2) §4 AD-4: `<Navigate to="/login" replace />` → `<Navigate to="/login" />`; `<Navigate to="/" replace />` → `<Navigate to="/" />` — two occurrences. (3) §5 flow block 1: `<Navigate to="/" replace />` → `<Navigate to="/" />`. (4) §5 flow block 1: `navigate("/", { replace: true })` → `navigate("/")`. (5) §5 flow block 2: `<Navigate to="/login" replace />` → `<Navigate to="/login" />`. (6) §5 flow block 2: `navigate("/login", { replace: true })` → `navigate("/login")`. No changes to §6 or §7 test assertions — they already used the single-arg form (`toHaveBeenCalledWith("/")`, `toHaveBeenCalledWith("/login")`), confirming source and tests now match exactly. |
