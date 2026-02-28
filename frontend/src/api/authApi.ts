import type { LoginResponse, AuthUser, AuthConfig } from '../types';

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }
  return res.json();
}

export async function getMe(token: string): Promise<AuthUser> {
  const res = await fetch('/api/auth/me', {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    throw new Error('Token invalid or expired');
  }
  return res.json();
}

export async function getAuthConfig(): Promise<AuthConfig> {
  const res = await fetch('/api/auth/config');
  if (!res.ok) {
    return { environment: 'DEV', auth_method: 'credentials' };
  }
  return res.json();
}
