class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const STORAGE_KEY     = 'rag-api-key';
const BACKEND_URL_KEY = 'rag-backend-url';
const DEFAULT_API_KEY: string = __DEFAULT_API_KEY__;

class ApiClient {
  // ── API key ────────────────────────────────────────────────────────────────

  getApiKey(): string {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_API_KEY;
  }

  setApiKey(key: string) {
    localStorage.setItem(STORAGE_KEY, key);
  }

  getDefaultApiKey(): string {
    return DEFAULT_API_KEY;
  }

  // ── Backend base URL ───────────────────────────────────────────────────────

  getBackendUrl(): string {
    return localStorage.getItem(BACKEND_URL_KEY) || '';
  }

  setBackendUrl(url: string) {
    const trimmed = url.trim().replace(/\/$/, '');
    if (trimmed) {
      localStorage.setItem(BACKEND_URL_KEY, trimmed);
    } else {
      localStorage.removeItem(BACKEND_URL_KEY);
    }
  }

  /**
   * Translate a proxy-prefixed path to the correct fetch URL.
   *
   * When backendUrl is empty the Nginx reverse-proxy handles routing so paths
   * are returned unchanged:
   *   /api/auth/…      → proxied to backend:8000/api/v1/auth/…
   *   /api/ingest/…    → proxied to backend:8000/api/v1/…
   *   /api/retrieval/… → proxied to backend:8000/api/v1/…
   *
   * When backendUrl is set the proxy is bypassed and the correct backend path
   * is constructed directly:
   *   /api/auth/X      → {backendUrl}/api/v1/auth/X
   *   /api/ingest/X    → {backendUrl}/api/v1/X
   *   /api/retrieval/X → {backendUrl}/api/v1/X
   */
  resolveUrl(path: string): string {
    const base = this.getBackendUrl();
    if (!base) return path;

    let v1Path: string;
    if (path.startsWith('/api/auth/')) {
      v1Path = path.replace(/^\/api\/auth\//, '/api/v1/auth/');
    } else if (path.startsWith('/api/ingest/')) {
      v1Path = path.replace(/^\/api\/ingest\//, '/api/v1/');
    } else if (path.startsWith('/api/retrieval/')) {
      v1Path = path.replace(/^\/api\/retrieval\//, '/api/v1/');
    } else {
      v1Path = path;
    }

    return base + v1Path;
  }

  // ── HTTP helpers ───────────────────────────────────────────────────────────

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      'X-API-Key': this.getApiKey(),
    };
    if (body) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(this.resolveUrl(path), {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, text);
    }
    return res.json();
  }

  async uploadFile<T>(path: string, file: File): Promise<T> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(this.resolveUrl(path), {
      method: 'POST',
      headers: { 'X-API-Key': this.getApiKey() },
      body: formData,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, text);
    }
    return res.json();
  }
}

export const apiClient = new ApiClient();
export { ApiError };
