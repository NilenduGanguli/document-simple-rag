import { useState, useEffect } from 'react';
import { apiClient } from '../api/client';

interface ConnectionStatus {
  state: 'idle' | 'testing' | 'ok' | 'error';
  message: string;
}

// Derive the display URL for a given proxy-prefix path using a candidate base URL.
function previewUrl(base: string, path: string): string {
  if (!base) return path + '  (via Nginx proxy)';
  return apiClient.resolveUrl(path).replace(apiClient.getBackendUrl(), base.replace(/\/$/, ''));
}

export default function SettingsPage() {
  const [backendUrl, setBackendUrl]   = useState(apiClient.getBackendUrl());
  const [apiKey,     setApiKey]       = useState(apiClient.getApiKey());
  const [showKey,    setShowKey]      = useState(false);
  const [saved,      setSaved]        = useState(false);
  const [connStatus, setConnStatus]   = useState<ConnectionStatus>({ state: 'idle', message: '' });

  // Keep preview in sync with live typing without saving
  const effectiveBase = backendUrl.trim().replace(/\/$/, '');

  useEffect(() => {
    if (saved) {
      const t = setTimeout(() => setSaved(false), 2500);
      return () => clearTimeout(t);
    }
  }, [saved]);

  function handleSave() {
    apiClient.setBackendUrl(backendUrl);
    apiClient.setApiKey(apiKey.trim() || apiClient.getDefaultApiKey());
    // Refresh local state from storage so it normalises trailing-slash stripping etc.
    setBackendUrl(apiClient.getBackendUrl());
    setApiKey(apiClient.getApiKey());
    setSaved(true);
    setConnStatus({ state: 'idle', message: '' });
  }

  function handleReset() {
    apiClient.setBackendUrl('');
    apiClient.setApiKey(apiClient.getDefaultApiKey());
    setBackendUrl('');
    setApiKey(apiClient.getDefaultApiKey());
    setSaved(true);
    setConnStatus({ state: 'idle', message: '' });
  }

  async function handleTestConnection() {
    // Save current inputs first so resolveUrl uses the new base
    apiClient.setBackendUrl(backendUrl);
    apiClient.setApiKey(apiKey.trim() || apiClient.getDefaultApiKey());

    setConnStatus({ state: 'testing', message: 'Connecting…' });
    try {
      const url = apiClient.resolveUrl('/api/retrieval/health');
      const res = await fetch(url, {
        headers: { 'X-API-Key': apiClient.getApiKey() },
        signal: AbortSignal.timeout(8000),
      });
      if (res.ok) {
        const json = await res.json().catch(() => ({}));
        const status = (json as { status?: string }).status ?? 'ok';
        setConnStatus({ state: 'ok', message: `Connected — status: ${status}` });
      } else {
        setConnStatus({ state: 'error', message: `HTTP ${res.status} ${res.statusText}` });
      }
    } catch (err) {
      setConnStatus({ state: 'error', message: err instanceof Error ? err.message : String(err) });
    }
  }

  // URL preview rows shown in the "Resolved endpoints" table
  const endpointRows = [
    { label: 'Health',    path: '/api/retrieval/health' },
    { label: 'Auth',      path: '/api/auth/login' },
    { label: 'Documents', path: '/api/ingest/documents/ingest' },
    { label: 'Retrieve',  path: '/api/retrieval/retrieve' },
    { label: 'Stats',     path: '/api/retrieval/stats' },
  ];

  return (
    <div className="mx-auto max-w-2xl space-y-8 p-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>
        <p className="mt-1 text-sm text-gray-500">
          Configure how the frontend connects to the RAG backend. Changes are stored in
          the browser's <code className="rounded bg-gray-100 px-1 text-xs">localStorage</code> and
          take effect immediately without a page reload.
        </p>
      </div>

      {/* ── Backend connection ───────────────────────────────────────────── */}
      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-700">
            Backend Connection
          </h2>
        </div>
        <div className="space-y-5 px-5 py-5">
          {/* Base URL */}
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="backendUrl">
              Base URL
            </label>
            <p className="mb-1.5 text-xs text-gray-500">
              Common prefix for all backend services (e.g.{' '}
              <code className="rounded bg-gray-100 px-1">http://localhost:18000</code>).
              Leave empty to route through the Nginx reverse-proxy (default for Docker Compose).
            </p>
            <input
              id="backendUrl"
              type="url"
              value={backendUrl}
              onChange={(e) => { setBackendUrl(e.target.value); setConnStatus({ state: 'idle', message: '' }); }}
              placeholder="http://localhost:18000"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              spellCheck={false}
              autoComplete="off"
            />
          </div>

          {/* Resolved endpoint preview */}
          <div>
            <p className="mb-2 text-xs font-medium text-gray-600">Resolved endpoints</p>
            <div className="overflow-hidden rounded-md border border-gray-200 text-xs">
              <table className="w-full">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Service</th>
                    <th className="px-3 py-2 text-left font-medium">Effective URL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 font-mono text-gray-700">
                  {endpointRows.map(({ label, path }) => (
                    <tr key={label} className="hover:bg-gray-50">
                      <td className="px-3 py-2 font-sans font-medium text-gray-600">{label}</td>
                      <td className="px-3 py-2 break-all">
                        {effectiveBase
                          ? <span>{effectiveBase}{path.replace(/^\/api\/(auth|ingest|retrieval)/, (_, p) => p === 'auth' ? '/api/v1/auth' : '/api/v1')}</span>
                          : <span className="text-gray-400">{path}<span className="ml-1 font-sans italic text-gray-400">(Nginx proxy)</span></span>
                        }
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Test connection */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={connStatus.state === 'testing'}
              className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
            >
              {connStatus.state === 'testing' && (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
              )}
              Test Connection
            </button>
            {connStatus.state !== 'idle' && connStatus.state !== 'testing' && (
              <span className={`flex items-center gap-1.5 text-sm font-medium ${connStatus.state === 'ok' ? 'text-green-600' : 'text-red-600'}`}>
                {connStatus.state === 'ok'
                  ? <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" /></svg>
                  : <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" /></svg>
                }
                {connStatus.message}
              </span>
            )}
          </div>
        </div>
      </section>

      {/* ── API Authentication ───────────────────────────────────────────── */}
      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-5 py-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-gray-700">
            API Authentication
          </h2>
        </div>
        <div className="px-5 py-5">
          <label className="block text-sm font-medium text-gray-700" htmlFor="apiKey">
            API Key
          </label>
          <p className="mb-1.5 text-xs text-gray-500">
            Sent as the <code className="rounded bg-gray-100 px-1">X-API-Key</code> header on every request.
            Default: <code className="rounded bg-gray-100 px-1">dev-api-key-1</code> (set by{' '}
            <code className="rounded bg-gray-100 px-1">API_KEYS</code> in docker-compose).
          </p>
          <div className="flex gap-2">
            <input
              id="apiKey"
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="flex-1 rounded-md border border-gray-300 px-3 py-2 font-mono text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              onClick={() => setShowKey((s) => !s)}
              className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
              title={showKey ? 'Hide key' : 'Show key'}
            >
              {showKey
                ? <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l18 18" /></svg>
                : <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
              }
            </button>
          </div>
        </div>
      </section>

      {/* ── Save / Reset ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <button
          onClick={handleReset}
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50"
        >
          Reset to defaults
        </button>
        <div className="flex items-center gap-3">
          {saved && (
            <span className="flex items-center gap-1.5 text-sm font-medium text-green-600">
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
              Saved
            </span>
          )}
          <button
            onClick={handleSave}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            Save settings
          </button>
        </div>
      </div>
    </div>
  );
}
