# Sprint S056-F-056 — Reviewer Feedback (Mode A)
**Reviewer:** reviewer  
**Date:** 2026-06-08  
**Sprint:** S056-F-056 — Protected Routes / Redirect to /login when no JWT  
**Verdict:** APPROVED (with 2 mandatory items + 2 NITs)

---

## Summary

The contract is well-structured, technically sound, and stays fully within MVP scope. The
core approach (Option A: explicit stub routes + fixed catch-all) is the right call and the
rationale is convincing. The `AppRoutes` export refactor is clean and avoids double-router
issues. All six hard invariants are correctly assessed as N/A. No API changes, no codegen
needed. The two mandatory items below must be addressed before implementation begins; the
NITs are non-blocking.

---

## Findings

### M1 — Catch-all and authenticated deep-link miss (MUST FIX before implementation)

**§4 AD-3** acknowledges that `*` → `/login` sends an authenticated user who mistyped a URL
directly to the login page. The login page has an inverse guard (`getToken() → navigate("/")`),
so the authenticated user is bounced back to `/` — correct eventual behaviour. **However**,
the contract does not include a test for this case, and the behavior depends on `LoginPage`'s
inverse guard (defined in S055). The test suite must include:

> **V5 (mandatory):** An authenticated user navigating to `/foo` (unknown route) lands on
> `/` (not stuck on `/login`) — i.e., the catch-all → `/login` → inverse guard → `/` chain
> works end-to-end in `MemoryRouter`.

Concretely: seed `localStorage` with a token, render `AppRoutes` in `MemoryRouter` with
`initialEntries={['/nonexistent']}`, assert that `<h1>Dataplat</h1>` (or the `HomePage`
heading) is rendered and the login button is absent.

Without this test, the catch-all behavior is underspecified and fragile: a future change to
`LoginPage` (e.g., removing the inverse guard) would silently break authenticated users
without a failing test.

**Action required:** Add V5 to §6 (Verification Plan) and §3 (ProtectedRoutes.test.tsx
purpose description).

---

### M2 — Token key constant: test uses a string literal instead of `storage.ts` export (MUST FIX)

**§6 V3** seeds `localStorage.setItem('dataplat.access_token', 'fake.jwt.token')` directly.
**§7** repeats the same inline string literal `'dataplat.access_token'` twice more (V3 and
V4 setup).

The canonical key is `TOKEN_KEY = 'dataplat.access_token'` in `apps/web/src/lib/storage.ts`.
If this key ever changes, the tests will silently pass while the production code uses a
different key.

**Action required:** The test file should import and use the `setToken`/`getToken` helpers
from `../lib/storage` (or at minimum import the key constant) rather than embedding the raw
string. The storage module is already in scope — `RequireAuth` and the stub pages both
indirectly depend on it. Alternatively, document explicitly in §7 that the test uses a
hardcoded string and that any key change must update both `storage.ts` and the test.

Either fix is acceptable; the implementation must pick one and state it in the contract.

---

### NIT-1 — OQ-1: `/runs` stub — reviewer ruling (non-blocking)

The contract asks for reviewer guidance on whether to include `/runs`. **Ruling: include it.**
F-072 (Activity/Runs page) is already in `feature_list.json` as a `web` category P1 feature
with `depends_on: [F-049, F-055]`. Adding the stub now means F-072 only replaces the
component, not the route. The cost is two lines of code and zero test lines; the benefit is
route-table completeness. Drop it if and only if a downstream reviewer explicitly objects.

---

### NIT-2 — `AppRoutes` import path in §7 is slightly wrong (non-blocking)

The comment in §7 reads:

```ts
import AppRoutes from '../App'   // named export AppRoutes
```

This should be:

```ts
import { AppRoutes } from '../App'
```

Named exports are not imported with the default-import syntax. The prose correctly says
"named export", so this is just a code-comment inconsistency that would cause a TypeScript
error if copied verbatim. Fix in implementation.

---

## Pressure-test checklist

1. **Verification exercises `/sources` and `/datasets` literally** — ✅ V1 and V2 are route-specific, not generic catch-all tests.
2. **Catch-all behavior** — ✅ Specified (`*` → `/login`). Authed user deep-link miss is handled by `LoginPage` inverse guard; M1 adds the required test coverage.
3. **localStorage hygiene** — ✅ `beforeEach(() => { localStorage.clear() })` present; `MemoryRouter` + `initialEntries` used correctly; no double-router; each auth-requiring test seeds the token after the `beforeEach` wipe.
4. **Scope creep** — ✅ Strictly frontend-only. Zero `apps/api/` files. Zero new npm deps beyond already-installed `react-router-dom`. No new backend routes.
5. **Invariant #6** — ✅ Correctly asserted N/A; no API schema change; `make codegen` not needed; `packages/api-types/` unchanged.
6. **MVP scope** — ✅ No httpOnly cookies, no refresh tokens, no MFA. Token validity check (expiry) explicitly deferred and noted.
7. **File table completeness** — ✅ Every file mentioned in §6 (`ProtectedRoutes.test.tsx`) appears in §3. After M1 is addressed, the V5 test is in the same file — no new file needed.
8. **Future-feature compatibility** — ✅ Route structure leaves clean room: `/sources/:id` (F-059), `/datasets/:id` (F-070), `/sources/new`, etc. can all be added as sibling `<Route>` declarations inside `AppRoutes` without rework. The `/runs` stub similarly does not conflict with F-072's `/runs/:id` pattern since React Router matches most-specific first.
9. **Option A vs. Option B** — ✅ Option A is the right choice. The rationale is thorough and all four points are valid. Option B's fragility under future router changes is a real risk; Option A makes the protection explicit and testable.

---

## Verdict

**APPROVED** pending resolution of M1 and M2. These are small additions (one test, one import
fix). NITs are editorial and do not block implementation.

---

## Round 2 review

**Reviewer:** reviewer
**Date:** 2026-06-08
**Against revision:** 2

### Item-by-item check

| Item | Status | Notes |
|---|---|---|
| **M1** — V5 test for authed deep-link catch-all round-trip | ✅ RESOLVED | V5 added to §6 (lines 291–305) with exactly the prescribed setup: seed token, render `AppRoutes` in `MemoryRouter initialEntries={['/nonexistent']}`, assert `HomePage` heading present + login button absent. §3 file-table row for `ProtectedRoutes.test.tsx` updated to reference V1–V7. |
| **M2** — Use `setToken`/`clearToken` from `storage.ts`, not raw key string | ✅ RESOLVED | §7 Imports now imports `{ setToken, clearToken }` from `'../lib/storage'`. All test bodies in V3/V4 use helpers, not the embedded string. Rationale stated. |
| **NIT-1** — Include `/runs` stub | ✅ RESOLVED | `RunsPage.tsx` in §3 file table; route in §4 AD-2 route table; V6 + V7 tests in §6; OQ-1 in §8 marked RESOLVED. |
| **NIT-2** — Named export import syntax | ✅ RESOLVED | §7 Imports now shows `import { AppRoutes } from '../App'` with curly braces throughout. |

All four issues are cleanly addressed. No new concerns introduced.

### Verdict

**APPROVED** — implementation may proceed per this contract.
