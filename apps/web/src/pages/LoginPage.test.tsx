/**
 * LoginPage tests T1–T9
 *
 * vi.mock is hoisted by Vitest before module imports, so useNavigate returns
 * mockNavigate from the very first render across all tests.
 */
import { vi, describe, test, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Module-level mock (hoisted before imports) ────────────────────────────
const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

// ── Import components under test AFTER the mock declaration ───────────────
import LoginPage from './LoginPage'
import HomePage from './HomePage'

// ── Helpers ───────────────────────────────────────────────────────────────

beforeEach(() => {
  mockNavigate.mockReset()
  localStorage.clear()
})

/**
 * Build a minimal fetch mock that returns the given response shape.
 */
function mockFetch(opts: { status: number; ok: boolean; body?: unknown }) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    status: opts.status,
    ok: opts.ok,
    json: () => Promise.resolve(opts.body ?? {}),
  } as Response)
}

/**
 * Render LoginPage and return user-event helpers.
 */
async function renderLogin() {
  const user = userEvent.setup()
  render(<LoginPage />)
  return { user }
}

// ── Tests ─────────────────────────────────────────────────────────────────

describe('LoginPage', () => {
  // T1 — renders form with email + password inputs + submit button
  test('T1: renders email and password inputs', async () => {
    await renderLogin()

    const emailInput = screen.getByRole('textbox', { name: /email/i })
    expect(emailInput).toHaveAttribute('type', 'email')

    const passwordInput = screen.getByLabelText(/password/i)
    expect(passwordInput).toHaveAttribute('type', 'password')

    expect(screen.getByRole('button', { name: /log in/i })).toBeInTheDocument()
  })

  // T2 — no error message on initial render
  test('T2: renders no error on initial render', async () => {
    await renderLogin()

    expect(screen.queryByText('Invalid credentials')).toBeNull()
    expect(screen.queryByText(/something went wrong/i)).toBeNull()
  })

  // T3 — valid credentials store token and navigate to /
  test('T3: valid credentials store token and navigate to /', async () => {
    mockFetch({
      status: 200,
      ok: true,
      body: { access_token: 'test-jwt', token_type: 'bearer' },
    })

    const { user } = await renderLogin()
    await user.type(screen.getByLabelText(/email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(localStorage.getItem('dataplat.access_token')).toBe('test-jwt')
    expect(mockNavigate).toHaveBeenCalledWith('/')
  })

  // T4 — fetch called with form-encoded body to /api/auth/token
  test('T4: valid credentials call fetch with form-encoded body', async () => {
    const fetchSpy = mockFetch({
      status: 200,
      ok: true,
      body: { access_token: 'test-jwt', token_type: 'bearer' },
    })

    const { user } = await renderLogin()
    await user.type(screen.getByLabelText(/email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/auth\/token$/)
    expect(init.method).toBe('POST')
    const ct = (init.headers as Record<string, string>)['Content-Type']
    expect(ct).toBe('application/x-www-form-urlencoded')
    const body = init.body as string
    expect(body).toContain('username=')
    expect(body).toContain('password=')
  })

  // T5 — 401 response shows "Invalid credentials", no navigation
  test('T5: invalid credentials (401) show error without navigating', async () => {
    mockFetch({ status: 401, ok: false })

    const { user } = await renderLogin()
    await user.type(screen.getByLabelText(/email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByText('Invalid credentials')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(localStorage.getItem('dataplat.access_token')).toBeNull()
  })

  // T6 — non-401 error shows generic message, no navigation
  test('T6: non-401 error shows generic message without navigating', async () => {
    mockFetch({ status: 500, ok: false })

    const { user } = await renderLogin()
    await user.type(screen.getByLabelText(/email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(
      await screen.findByText('Something went wrong, please try again'),
    ).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  // T7 — submit button is disabled while request is in-flight
  test('T7: submit button is disabled while request is in flight', async () => {
    // Never-resolving promise keeps the component in loading state
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}))

    const { user } = await renderLogin()
    await user.type(screen.getByLabelText(/email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/password/i), 'secret')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(screen.getByRole('button', { name: /logging in/i })).toBeDisabled()
  })

  // T9 — already-logged-in: /login redirects to /
  test('T9: already-logged-in: /login redirects to /', async () => {
    localStorage.setItem('dataplat.access_token', 'fake.jwt.token')

    await renderLogin()

    expect(mockNavigate).toHaveBeenCalledWith('/')
  })
})

describe('HomePage', () => {
  // T8 — logout clears token and navigates to /login
  test('T8: logout on HomePage clears token and navigates to /login', async () => {
    localStorage.setItem('dataplat.access_token', 'existing')

    const user = userEvent.setup()
    render(<HomePage />)

    await user.click(screen.getByRole('button', { name: /logout/i }))

    expect(localStorage.getItem('dataplat.access_token')).toBeNull()
    expect(mockNavigate).toHaveBeenCalledWith('/login')
  })
})
