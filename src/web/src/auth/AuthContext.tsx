import { useEffect, useMemo, useState, type ReactNode } from "react";
import { login as apiLogin, me as apiMe, type AuthUser } from "../api/authClient";
import { AuthContext, type AuthState } from "./useAuth";

const STORAGE_KEY = "fintrix.auth.token";

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function clearStoredToken(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("checking");

  useEffect(() => {
    const stored = readStoredToken();
    if (!stored) {
      setStatus("anonymous");
      return;
    }
    apiMe(stored)
      .then((u) => {
        setToken(stored);
        setUser(u);
        setStatus("authenticated");
      })
      .catch(() => {
        clearStoredToken();
        setStatus("anonymous");
      });
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      token,
      status,
      async login(email: string, password: string, remember: boolean) {
        const result = await apiLogin(email, password);
        setToken(result.token);
        setUser(result.user);
        setStatus("authenticated");
        try {
          (remember ? localStorage : sessionStorage).setItem(STORAGE_KEY, result.token);
        } catch {
          /* per-viewer convenience only; app works without it */
        }
      },
      logout() {
        setToken(null);
        setUser(null);
        setStatus("anonymous");
        clearStoredToken();
      },
    }),
    [user, token, status],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
