import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import type { AuthUser, AuthConfig } from '../types';
import { login as apiLogin, getMe, getAuthConfig } from '../api/authApi';

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  environment: string;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = 'rag-auth-token';
const USER_KEY = 'rag-auth-user';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [environment, setEnvironment] = useState('DEV');
  const [isLoading, setIsLoading] = useState(true);

  // On mount: check for existing session
  useEffect(() => {
    const init = async () => {
      // Fetch environment config
      try {
        const config: AuthConfig = await getAuthConfig();
        setEnvironment(config.environment);
      } catch {
        // Default to DEV if config endpoint unavailable
      }

      // Check for stored token
      const storedToken = localStorage.getItem(TOKEN_KEY);
      if (storedToken) {
        try {
          const me = await getMe(storedToken);
          setToken(storedToken);
          setUser(me);
        } catch {
          // Token expired or invalid — clear storage
          localStorage.removeItem(TOKEN_KEY);
          localStorage.removeItem(USER_KEY);
        }
      }
      setIsLoading(false);
    };
    init();
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiLogin(username, password);
    setToken(res.access_token);
    setUser(res.user);
    setEnvironment(res.environment);
    localStorage.setItem(TOKEN_KEY, res.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(res.user));
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        environment,
        isAuthenticated: !!token && !!user,
        isLoading,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
