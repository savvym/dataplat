/**
 * ProtectedRoutes tests V1–V7
 *
 * Tests the AppRoutes named export (inner <Routes> block) wrapped in MemoryRouter.
 * No vi.mock for react-router-dom — uses real MemoryRouter + Navigate.
 * localStorage is the real jsdom implementation; cleared in beforeEach.
 */
import { describe, test, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AppRoutes } from '../App'
import { setToken, clearToken } from '../lib/storage'

beforeEach(() => {
  localStorage.clear()
})

describe('ProtectedRoutes', () => {
  // V1 — /sources without token → /login
  test('V1: /sources without token redirects to /login', () => {
    render(
      <MemoryRouter initialEntries={['/sources']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    // RequireAuth redirects to /login; LoginPage renders its submit button
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  // V2 — /datasets without token → /login
  test('V2: /datasets without token redirects to /login', () => {
    render(
      <MemoryRouter initialEntries={['/datasets']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  // V3 — /sources WITH token → stays at /sources (not redirected)
  test('V3: /sources with token stays at /sources', () => {
    setToken('fake.jwt.token')
    render(
      <MemoryRouter initialEntries={['/sources']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    // SourcesPage renders its heading
    expect(screen.getByRole('heading', { name: /sources/i })).toBeInTheDocument()
    // LoginPage must NOT be rendered
    expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
  })

  // V4 — Logout flow: clearToken + navigate to /datasets → /login
  test('V4: after clearToken, /datasets redirects to /login', () => {
    setToken('existing-token')
    const { rerender } = render(
      <MemoryRouter initialEntries={['/datasets']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    // Confirm authenticated state renders DatasetsPage
    expect(screen.getByRole('heading', { name: /datasets/i })).toBeInTheDocument()

    // Simulate logout: clear token, then re-render at same route
    clearToken()
    rerender(
      <MemoryRouter initialEntries={['/datasets']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  // V5 — Authenticated user at unknown path → HomePage (catch-all round-trip)
  test('V5: authed user at /nonexistent lands on HomePage (catch-all → /login → inverse guard → /)', () => {
    setToken('fake.jwt.token')
    render(
      <MemoryRouter initialEntries={['/nonexistent']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    // Catch-all redirects to /login; LoginPage inverse guard fires; / renders HomePage
    expect(screen.getByRole('heading', { name: /dataplat/i })).toBeInTheDocument()
    // Login button must NOT be present
    expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
  })

  // V6 — /runs without token → /login
  test('V6: /runs without token redirects to /login', () => {
    render(
      <MemoryRouter initialEntries={['/runs']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  // V7 — /runs WITH token → stays at /runs (stub rendered)
  test('V7: /runs with token stays at /runs', () => {
    setToken('fake.jwt.token')
    render(
      <MemoryRouter initialEntries={['/runs']}>
        <AppRoutes />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: /runs/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /log in/i })).toBeNull()
  })
})
