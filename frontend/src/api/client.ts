class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const STORAGE_KEY = 'rag-api-key';
const DEFAULT_API_KEY: string = __DEFAULT_API_KEY__;

class ApiClient {
  getApiKey(): string {
    return localStorage.getItem(STORAGE_KEY) || DEFAULT_API_KEY;
  }

  setApiKey(key: string) {
    localStorage.setItem(STORAGE_KEY, key);
  }

  getDefaultApiKey(): string {
    return DEFAULT_API_KEY;
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {
      'X-API-Key': this.getApiKey(),
    };
    if (body) {
      headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(path, {
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
    const res = await fetch(path, {
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
