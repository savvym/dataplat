import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../lib/api'
import { getToken, setToken } from '../lib/storage'

/**
 * Login page — renders the login form, handles JWT obtain, and navigates on success.
 *
 * Inverse guard: if the user is already logged in (token present in localStorage),
 * navigate to / immediately via useNavigate (defense-in-depth, tested by T9).
 * Using useEffect + navigate instead of <Navigate> so the mocked navigate in tests
 * captures the redirect call correctly.
 */
function LoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Inverse guard: already-logged-in users are sent to /.
  // Runs on every render so it also fires after a successful login before navigation.
  useEffect(() => {
    if (getToken()) {
      navigate('/')
    }
  }, [navigate])

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await login(email, password)
      setToken(data.access_token)
      navigate('/')
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'Something went wrong, please try again'
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <h1>Log in to Dataplat</h1>
      <form onSubmit={(e) => { void handleSubmit(e) }}>
        <div>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
          />
        </div>
        <div>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </div>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={loading}>
          {loading ? 'Logging in…' : 'Log in'}
        </button>
      </form>
    </div>
  )
}

export default LoginPage
