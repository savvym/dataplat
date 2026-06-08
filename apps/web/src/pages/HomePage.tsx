import { useNavigate } from 'react-router-dom'
import { clearToken } from '../lib/storage'

/**
 * Minimal home page stub. Renders a heading and a logout button.
 */
function HomePage() {
  const navigate = useNavigate()

  function handleLogout() {
    clearToken()
    navigate('/login')
  }

  return (
    <div>
      <h1>Dataplat</h1>
      <button onClick={handleLogout}>Logout</button>
    </div>
  )
}

export default HomePage
