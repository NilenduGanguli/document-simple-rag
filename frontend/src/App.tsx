import { BrowserRouter, useLocation, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import AppShell from './components/layout/AppShell';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import IngestPage from './pages/IngestPage';
import RetrievePage from './pages/RetrievePage';
import ExplorePage from './pages/ExplorePage';
import AdminPage from './pages/AdminPage';

const KNOWN_PATHS = ['/', '/ingest', '/retrieve', '/explore', '/admin'];

/**
 * Renders all pages simultaneously and shows/hides them with CSS so that
 * component state (query results, expanded chunks, scroll position, etc.)
 * is preserved across sidebar navigation.
 */
function Pages() {
  const { pathname } = useLocation();
  const vis = (path: string): React.CSSProperties | undefined =>
    pathname === path ? undefined : { display: 'none' };

  return (
    <>
      <div style={vis('/')}><DashboardPage /></div>
      <div style={vis('/ingest')}><IngestPage /></div>
      <div style={vis('/retrieve')}><RetrievePage /></div>
      {/* ExplorePage manages its own full-height layout so it needs display:contents
          when active to let its -m-6 / overflow-hidden styles reach the parent */}
      <div style={pathname === '/explore' ? { display: 'contents' } : { display: 'none' }}>
        <ExplorePage />
      </div>
      <div style={vis('/admin')}><AdminPage /></div>
      {!KNOWN_PATHS.includes(pathname) && <Navigate to="/" replace />}
    </>
  );
}

/**
 * Gates the entire application behind authentication.
 * Shows LoginPage when not authenticated, AppShell+Pages when authenticated.
 */
function AuthGate() {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-600 border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <AppShell>
      <Pages />
    </AppShell>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </BrowserRouter>
  );
}
