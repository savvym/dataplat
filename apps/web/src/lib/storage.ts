/**
 * Storage helpers for the dataplat JWT access token.
 * All localStorage access is centralized here under the canonical key.
 */

const TOKEN_KEY = 'dataplat.access_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
