import { useState, useEffect } from 'react';
import { apiClient } from '../../api/client';

export default function Header() {
  const [apiKey, setApiKey] = useState(apiClient.getApiKey());
  const [editing, setEditing] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const isDefault = apiKey === apiClient.getDefaultApiKey() && !localStorage.getItem('rag-api-key');

  useEffect(() => {
    if (!apiKey) return;
    const check = async () => {
      try {
        const res = await fetch('/api/retrieval/health');
        setConnected(res.ok);
      } catch {
        setConnected(false);
      }
    };
    check();
    const interval = setInterval(check, 30000);
    return () => clearInterval(interval);
  }, [apiKey]);

  const handleSave = () => {
    apiClient.setApiKey(apiKey);
    setEditing(false);
  };

  const handleReset = () => {
    localStorage.removeItem('rag-api-key');
    setApiKey(apiClient.getDefaultApiKey());
    setEditing(false);
  };

  return (
    <header className="flex h-14 items-center justify-between border-b border-gray-200 bg-white px-6">
      <h1 className="text-sm font-medium text-gray-500">Document Processing Pipeline</h1>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              connected === true ? 'bg-emerald-500' : connected === false ? 'bg-red-500' : 'bg-gray-300'
            }`}
          />
          <span className="text-xs text-gray-500">
            {connected === true ? 'Connected' : connected === false ? 'Disconnected' : 'Checking...'}
          </span>
        </div>
        {editing ? (
          <div className="flex items-center gap-2">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter API Key"
              className="rounded border border-gray-300 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleSave()}
            />
            <button
              onClick={handleSave}
              className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700"
            >
              Save
            </button>
            {!isDefault && (
              <button
                onClick={handleReset}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Reset
              </button>
            )}
          </div>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            {isDefault ? 'API Key: auto' : `API Key: ****${apiKey.slice(-4)}`}
          </button>
        )}
      </div>
    </header>
  );
}
