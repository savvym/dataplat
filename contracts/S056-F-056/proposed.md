# Sprint S056-F-056 — Proposed Contract
# Protected Routes: Frontend Auth Guard for /sources and /datasets

**Sprint ID:** S056-F-056
**Feature:** F-056 (category: web, P1)
**Author:** implementer
**Date:** 2026-06-08
**Revision:** 2
**Depends on:** F-055 ✓ (passes: true)

---

## §1 Goal

Ensure every frontend route except `/login` is protected by the existing `RequireAuth` guard,
and that `/sources` and `/datasets` in particular redirect to `/login` when no valid JWT is
present in `localStorage`. The current `App.tsx` only declares `/` and `/login`; the
catch-all `*` routes every unknown path back to `/` which then falls into `RequireAuth` —
passing verification by coincidence. This sprint makes the behavior explicit and durable by
adding stub page routes for `/sources`, `/datasets`, and other top-level protected paths that
upcoming features will flesh out, and fixing the catch-all to redirect directly to `/login`
rather than looping through the home guard.

---

## §2 Scope

### In scope
- `apps/web/src/App.tsx` — add explicit routes for `/sources` and `/datasets` (and `/runs`
  as a forward-looking stub); change catch-all `*` from `<Navigate to="/" />` to
  `<Navigate to="/login" />`; refactor to export `AppRoutes` as a named export wrapping just
  the `<Routes>` block, with the default export `App` providing the `<BrowserRouter>` wrapper.
- `apps/web/src/pages/SourcesPage.tsx` — new minimal stub component ("Sources — coming soon").
- `apps/web/src/pages/DatasetsPage.tsx` — new minimal stub component ("Datasets — coming soon").
- `apps/web/src/pages/RunsPage.tsx` — new minimal stub component ("Runs — coming soon");
  forward-looking stub for F-072.
- `apps/web/src/pages/ProtectedRoutes.test.tsx` — new Vitest + RTL tests V1–V7 (see §6).
- `verify/checks.sh` — no change needed; the `frontend` layer already runs
  `pnpm --filter web run test -- --run` which will pick up the new test file automatically.

### Out of scope
- Actual UI content for `/sources`, `/datasets`, `/runs` (those are F-057, F-061, F-072).
- Navigation sidebar or header (future sprint).
- Any `apps/api/` change.
- Any `packages/api-types/` or `make codegen` change (no API schema change).
- Password reset, MFA, OAuth, httpOnly cookies, token refresh (CLAUDE.md MVP boundaries).
- Playwright / browser-level E2E tests (deferred).

---

## §3 File Table

| File | Action | Purpose |
|---|---|---|
| `apps/web/src/App.tsx` | **MODIFY** | Add `/sources`, `/datasets`, `/runs` routes each wrapped in `RequireAuth`; change catch-all `*` to `<Navigate to="/login" />`; export `AppRoutes` (inner `<Routes>` block) as a named export; keep `App` as default export wrapping `<AppRoutes>` in `<BrowserRouter>` |
| `apps/web/src/pages/SourcesPage.tsx` | **NEW** | Minimal stub: `<h1>Sources</h1><p>Coming soon.</p>` — satisfies F-056 verification literal for `/sources` |
| `apps/web/src/pages/DatasetsPage.tsx` | **NEW** | Minimal stub: `<h1>Datasets</h1><p>Coming soon.</p>` — satisfies F-056 verification literal for `/datasets` |
| `apps/web/src/pages/RunsPage.tsx` | **NEW** | Minimal stub: `<h1>Runs</h1><p>Coming soon.</p>` — forward-looking stub for F-072; avoids catch-all redirect for a route upcoming sprints will implement |
| `apps/web/src/pages/ProtectedRoutes.test.tsx` | **NEW** | Vitest + RTL tests V1–V7: `/sources`/`/datasets`/`/runs` without token → `/login`; same routes with token → stub page; logout flow; catch-all with token → `HomePage`; all using `MemoryRouter` + `AppRoutes` |

**Files NOT touched:**
- `apps/web/src/components/RequireAuth.tsx` — no change; already correct.
- `apps/web/src/lib/storage.ts` — no change.
- `apps/web/src/lib/api.ts` — no change.
- `apps/web/src/pages/LoginPage.tsx` — no change.
- `apps/web/src/pages/HomePage.tsx` — no change.
- `apps/web/src/pages/LoginPage.test.tsx` — no change; all T1–T9 must continue to pass.
- `apps/api/` — zero files touched.
- `verify/checks.sh` — no change.

---

## §4 Architecture Decisions

### AD-1 — Option A chosen: explicit stub routes + fixed catch-all

**Option A (chosen):** Add explicit routes for `/sources`, `/datasets`, and `/runs`, each
wrapped in `<RequireAuth>`. Change the catch-all `*` to `<Navigate to="/login" />`.

**Option B (rejected):** Keep catch-all → `/` and rely on the `RequireAuth` at `/`.

**Rationale for choosing A:**

1. **Literal verification.** The F-056 spec checks that typing `/sources` and `/datasets`
   into the URL bar (no token) lands on `/login`. Option A satisfies this with explicit
   routes and no coincidence. Option B satisfies it only because the unknown path hits `*` →
   `/` → `RequireAuth` → `/login` — two extra hops that are fragile to future router changes.

2. **Future-proof.** F-057 (sources page), F-061 (datasets page), and F-072 (runs page) will
   need explicit routes anyway. Adding stubs now means those sprints only need to replace the
   stub component with the real one; the route declaration and `RequireAuth` wrapper are
   already in place and passing tests.

3. **Catch-all semantics.** The current `*` → `/` means a deep-linked unknown path cycles
   through the home guard instead of giving the user a clean `/login` redirect. Changing it
   to `*` → `/login` is strictly more correct: any unrecognized path goes to login.

4. **Test clarity.** With Option A, `V3` ("stays at `/sources` with token") is a meaningful
   test — `RequireAuth` is passing the route through, not the catch-all. With Option B, the
   same test would be testing the catch-all + home guard, not the feature.

### AD-2 — Route list

The following routes will exist in `App.tsx` after this sprint:

| Path | Component | Guard |
|---|---|---|
| `/login` | `LoginPage` | none (public) |
| `/` | `HomePage` | `RequireAuth` |
| `/sources` | `SourcesPage` | `RequireAuth` |
| `/datasets` | `DatasetsPage` | `RequireAuth` |
| `/runs` | `RunsPage` | `RequireAuth` |
| `*` | `<Navigate to="/login" />` | none |

`/runs` is included as a forward-looking stub per reviewer ruling (NIT-1 resolved: include it).

### AD-3 — Catch-all behavior

Old: `<Route path="*" element={<Navigate to="/" />} />`
New: `<Route path="*" element={<Navigate to="/login" />} />`

Previously, a deep-linked unknown path (e.g. `/admin`) looped through `/` → `RequireAuth` →
`/login` if unauthenticated, or rendered `HomePage` if authenticated. With the fix, the same
path goes directly to `/login` (unauthenticated) or is unmatched by any explicit route and
lands on `/login` (authenticated — not ideal, but acceptable for MVP with a small route set).
An authenticated deep-link miss going to `/login` is a minor UX nuisance; the login page's
inverse guard (`getToken() → navigate("/")`) will redirect them back to `/` anyway. This
round-trip is explicitly tested by V5 (see §6).

### AD-4 — Test strategy: MemoryRouter wrapping AppRoutes (named export)

`App.tsx` will be refactored to separate the router configuration from the browser-router
wrapper. The inner `<Routes>` block becomes the named export `AppRoutes`; the default export
`App` wraps it in `<BrowserRouter>`:

```tsx
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireAuth><HomePage /></RequireAuth>} />
      <Route path="/sources" element={<RequireAuth><SourcesPage /></RequireAuth>} />
      <Route path="/datasets" element={<RequireAuth><DatasetsPage /></RequireAuth>} />
      <Route path="/runs" element={<RequireAuth><RunsPage /></RequireAuth>} />
      <Route path="*" element={<Navigate to="/login" />} />
    </Routes>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

export default App
```

Tests import `{ AppRoutes }` (named export) and wrap it in `MemoryRouter` themselves. This
avoids double-router issues and keeps route declarations in a single source of truth. `App`
remains the default export for `main.tsx` — no change to the application entry point.

---

## §5 Invariants Check

| # | Invariant | Applies? | Assessment |
|---|---|---|---|
| **#1** | Lineage mandatory (parents[] + processor identity + config hash + input refs) | **N/A** | Pure frontend routing change. No Commit, blob, or lineage record written. |
| **#2** | Storage separation + CAS (metadata in Postgres, content in MinIO) | **N/A** | No storage operations. |
| **#3** | Schema frozen post-publish | **N/A** | No Silver/Gold schema touched. |
| **#4** | LLM calls via gateway only | **N/A** | No LLM calls. |
| **#5** | Async SQLAlchemy (no `session.query()`, no sync sessions) | **N/A** | Zero `apps/api/` files touched. |
| **#6** | OpenAPI ↔ TS type sync (`make codegen` + committed diff) | **N/A** | No API schema change. No `apps/api/` files touched. `make codegen` need not run; `packages/api-types/` is unchanged. |

All six invariants are either N/A or confirmed unaffected. This is a frontend-only sprint.

---

## §6 Verification Plan

### V1 — `/sources` without token → `/login`

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

**Setup:**
```ts
beforeEach(() => { localStorage.clear() })
```

**Concrete implementation:**
```ts
// No token in localStorage (cleared by beforeEach)
render(
  <MemoryRouter initialEntries={['/sources']}>
    <AppRoutes />
  </MemoryRouter>
)
// RequireAuth redirects to /login; LoginPage renders its submit button
expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
```

**Key assertions:** `screen.getByRole('button', { name: /log in/i })` is present (LoginPage rendered after redirect).

---

### V2 — `/datasets` without token → `/login`

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

Same setup as V1; change `initialEntries` to `['/datasets']`.

**Concrete implementation:**
```ts
render(
  <MemoryRouter initialEntries={['/datasets']}>
    <AppRoutes />
  </MemoryRouter>
)
expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
```

---

### V3 — `/sources` WITH token → stays at `/sources` (not redirected)

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

**Setup:** Seed localStorage via `setToken` before render.

```ts
test('V3: /sources with token stays at /sources', () => {
  setToken('fake.jwt.token')
  render(
    <MemoryRouter initialEntries={['/sources']}>
      <AppRoutes />
    </MemoryRouter>
  )
  // SourcesPage renders its heading
  expect(screen.getByRole('heading', { name: /sources/i })).toBeInTheDocument()
  // LoginPage must NOT be rendered
  expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
})
```

**Key assertions:** `screen.getByRole('heading', { name: /sources/i })` present; login button absent.

---

### V4 — Logout flow: clearToken + navigate to `/datasets` → `/login`

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

```ts
test('V4: after clearToken, /datasets redirects to /login', () => {
  setToken('existing-token')
  const { rerender } = render(
    <MemoryRouter initialEntries={['/datasets']}>
      <AppRoutes />
    </MemoryRouter>
  )
  // Confirm authenticated state renders DatasetsPage
  expect(screen.getByRole('heading', { name: /datasets/i })).toBeInTheDocument()

  // Simulate logout: clear token, then re-render at same route
  clearToken()
  rerender(
    <MemoryRouter initialEntries={['/datasets']}>
      <AppRoutes />
    </MemoryRouter>
  )
  expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
})
```

**Key assertions:** After `clearToken()` + re-render at `/datasets`, `LoginPage` renders.

---

### V5 — Authenticated user at unknown path → `HomePage` (catch-all round-trip)

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

This test verifies the full round-trip: `*` → `<Navigate to="/login" />` → `LoginPage` inverse
guard (`getToken() → navigate("/")`) → `HomePage`. Without this test, removing the inverse
guard from `LoginPage` would silently break authenticated users who mistype a URL.

```ts
test('V5: authed user at /nonexistent lands on HomePage (catch-all → /login → inverse guard → /)', () => {
  setToken('fake.jwt.token')
  render(
    <MemoryRouter initialEntries={['/nonexistent']}>
      <AppRoutes />
    </MemoryRouter>
  )
  // Catch-all redirects to /login; LoginPage inverse guard fires; / renders HomePage
  expect(screen.getByRole('heading', { name: /dataplat/i })).toBeInTheDocument()
  // Login button must NOT be present
  expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
})
```

**Key assertions:** `HomePage` heading present; login button absent.

---

### V6 — `/runs` without token → `/login`

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

```ts
render(
  <MemoryRouter initialEntries={['/runs']}>
    <AppRoutes />
  </MemoryRouter>
)
expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
```

**Key assertions:** `LoginPage` submit button present (redirect fired from `RequireAuth`).

---

### V7 — `/runs` WITH token → stays at `/runs` (stub rendered)

**File:** `apps/web/src/pages/ProtectedRoutes.test.tsx`

```ts
test('V7: /runs with token stays at /runs', () => {
  setToken('fake.jwt.token')
  render(
    <MemoryRouter initialEntries={['/runs']}>
      <AppRoutes />
    </MemoryRouter>
  )
  expect(screen.getByRole('heading', { name: /runs/i })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
})
```

**Key assertions:** `RunsPage` heading present; login button absent.

---

### V8 — `bash verify/checks.sh frontend` exits 0

Runs:
1. `pnpm --filter web lint` — `tsc --noEmit` (no type errors in new files).
2. `pnpm --filter web typecheck` — same.
3. `pnpm --filter web run test -- --run` — all Vitest tests pass (T1–T9 from S055 unchanged, V1–V7 new).

No changes to `verify/checks.sh` are required; the `frontend` layer already discovers all
`*.test.tsx` files through Vitest's default glob.

---

## §7 Test Harness Notes

### File location

All new tests live in:

```
apps/web/src/pages/ProtectedRoutes.test.tsx
```

### Imports

```ts
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AppRoutes } from '../App'
import { setToken, clearToken } from '../lib/storage'
```

`AppRoutes` is imported as a named export (not a default import). `setToken` / `clearToken`
are imported from the storage module so the test never embeds the raw key string
`'dataplat.access_token'` — if that key ever changes in `storage.ts`, the change propagates
automatically to all tests without a string-grep hunt.

### MemoryRouter setup

`App.tsx` uses `BrowserRouter` internally. Tests must **not** double-wrap. The refactor
in AD-4 exports `AppRoutes` (the inner `<Routes>` block) as a named export. Tests import
`{ AppRoutes }` and provide their own `MemoryRouter`:

```ts
import { AppRoutes } from '../App'   // named export — curly braces required
```

### vi.mock

No `vi.mock` is needed for `react-router-dom` in `ProtectedRoutes.test.tsx`.
`MemoryRouter` from `react-router-dom` is used directly (real implementation);
`Navigate` performs real in-memory redirects within `MemoryRouter`. No `useNavigate`
mock needed since these tests assert rendered output (which page rendered), not
imperative navigation calls.

The `localStorage` mock is the real `jsdom` implementation (already configured via
`vite.config.ts` `environment: 'jsdom'`). No extra mock needed.

### localStorage cleanup

```ts
beforeEach(() => {
  localStorage.clear()
})
```

`localStorage.clear()` is used in `beforeEach` to reset all browser storage state between
tests (it does not embed the token key, so it is safe). Tests that need a token call
`setToken('fake.jwt.token')` at the start of the test body. The `clearToken()` helper
(from `storage.ts`) is used inside test bodies when simulating logout (V4), not as a
substitute for `localStorage.clear()` in the cleanup hook.

### Existing tests (LoginPage.test.tsx)

`LoginPage.test.tsx` uses `vi.mock('react-router-dom', ...)` to stub `useNavigate`.
That mock is file-scoped and does not affect `ProtectedRoutes.test.tsx`, which uses
`MemoryRouter` (real). No interference.

### AppRoutes export pattern

`apps/web/src/App.tsx` is refactored from:

```tsx
function App() {
  return (
    <BrowserRouter>
      <Routes>
        ...
      </Routes>
    </BrowserRouter>
  )
}
export default App
```

to:

```tsx
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireAuth><HomePage /></RequireAuth>} />
      <Route path="/sources" element={<RequireAuth><SourcesPage /></RequireAuth>} />
      <Route path="/datasets" element={<RequireAuth><DatasetsPage /></RequireAuth>} />
      <Route path="/runs" element={<RequireAuth><RunsPage /></RequireAuth>} />
      <Route path="*" element={<Navigate to="/login" />} />
    </Routes>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

export default App
```

`App` remains the default export (unchanged for `main.tsx`). `AppRoutes` is a named export
used only in tests. No existing code outside tests imports from `App.tsx`, so this is a
non-breaking change.

---

## §8 Risks / Open Questions

**OQ-1 — `/runs` stub inclusion (RESOLVED)**

Reviewer ruling (NIT-1): include `/runs`. F-072 is in `feature_list.json` as a `web` P1
feature; adding the stub now means F-072 only replaces the component, not the route. Stub
is included.

**OQ-2 — V4 implementation via `rerender` vs. separate test**

The V4 logout-flow test uses `rerender` with a fresh `MemoryRouter`. The `rerender` path
is sound (`MemoryRouter` with new `initialEntries` resets internal history state) but slightly
unusual. An alternative is to implement V4 as a second assertion within the same test after
clearing the token and forcing a re-render. Both are functionally equivalent.

**OQ-3 — Heading text for stub pages**

V3 asserts `screen.getByRole('heading', { name: /sources/i })` and V4 asserts
`screen.getByRole('heading', { name: /datasets/i })`. The stub pages must render `<h1>` tags
containing the words "Sources" and "Datasets" respectively. Similarly V7 requires "Runs" in
`<h1>`. This is a contract between the component and the test — implementer must not use a
heading like "Coming soon" that omits the route name.

**Risk — LoginPage.test.tsx mock scope**

`LoginPage.test.tsx` uses a module-level `vi.mock('react-router-dom', ...)`. If Vitest
runs both test files in the same worker, the mock could theoretically leak. In practice,
Vitest isolates modules per file. Confirmed safe: `ProtectedRoutes.test.tsx` does not
call `vi.mock` and uses `MemoryRouter` (real). If any interference is observed, add
`vi.unmock('react-router-dom')` at the top of `ProtectedRoutes.test.tsx`.

---

## §9 Out-of-Scope Deferrals

| Item | Rationale |
|---|---|
| Actual `/sources` page content (data table, filters, etc.) | F-057 |
| Actual `/datasets` page content | F-061 |
| Actual `/runs` page content | F-072 |
| Navigation sidebar / header with links to protected routes | Future sprint |
| Token expiry guard (expired JWT in localStorage → redirect to login) | Future sprint; `RequireAuth` currently only checks for token presence, not validity |
| Redirect-after-login (returning user to originally requested URL) | Future sprint; for MVP, login always redirects to `/` |
| Granular ACL per route | CLAUDE.md §11.6 (MVP: `visibility = private|internal` only) |
| Self-registration, password reset, MFA, OAuth | CLAUDE.md §11.6 |
| httpOnly cookie / token refresh | CLAUDE.md MVP boundaries; deferred |
| Playwright E2E tests | Deferred; verifier uses unit tests only |
| `make codegen` / `packages/api-types/` update | N/A — no API schema change this sprint |
