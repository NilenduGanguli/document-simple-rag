import { BrowserRouter, useLocation, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import DashboardPage from './pages/DashboardPage';
import IngestPage from './pages/IngestPage';
import RetrievePage from './pages/RetrievePage';
import ExplorePage from './pages/ExplorePage';

const KNOWN_PATHS = ['/', '/ingest', '/retrieve', '/explore'];

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
      {!KNOWN_PATHS.includes(pathname) && <Navigate to="/" replace />}
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Pages />
      </AppShell>
    </BrowserRouter>
  );
}
