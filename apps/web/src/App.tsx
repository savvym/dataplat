import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import LoginPage from './pages/LoginPage'
import HomePage from './pages/HomePage'
import SourcesPage from './pages/SourcesPage'
import DatasetsPage from './pages/DatasetsPage'
import RunsPage from './pages/RunsPage'
import RequireAuth from './components/RequireAuth'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <HomePage />
          </RequireAuth>
        }
      />
      <Route
        path="/sources"
        element={
          <RequireAuth>
            <SourcesPage />
          </RequireAuth>
        }
      />
      <Route
        path="/datasets"
        element={
          <RequireAuth>
            <DatasetsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/runs"
        element={
          <RequireAuth>
            <RunsPage />
          </RequireAuth>
        }
      />
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
