/**
 * Auth API client for the dataplat backend.
 *
 * Uses application/x-www-form-urlencoded to match the OAuth2PasswordRequestForm
 * on the FastAPI side — NOT JSON.
 */

export interface TokenResponse {
  access_token: string
  token_type: string
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:18000'

/**
 * Obtain a JWT by posting OAuth2 password-grant credentials.
 *
 * Throws "Invalid credentials" on HTTP 401.
 * Throws a generic message on any other error.
 */
export async function login(email: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams()
  body.append('username', email)
  body.append('password', password)

  const response = await fetch(`${API_BASE}/api/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  })

  if (response.status === 401) {
    throw new Error('Invalid credentials')
  }

  if (!response.ok) {
    throw new Error('Something went wrong, please try again')
  }

  return response.json() as Promise<TokenResponse>
}
