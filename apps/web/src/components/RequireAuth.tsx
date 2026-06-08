import { Navigate } from 'react-router-dom'
import { getToken } from '../lib/storage'

interface RequireAuthProps {
  children: React.ReactNode
}

/**
 * Route guard: redirects to /login if no token is present in localStorage.
 */
function RequireAuth({ children }: RequireAuthProps) {
  const token = getToken()
  if (!token) {
    return <Navigate to="/login" />
  }
  return <>{children}</>
}

export default RequireAuth
