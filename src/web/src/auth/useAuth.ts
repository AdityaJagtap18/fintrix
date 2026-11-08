import { createContext, useContext } from "react";
import type { AuthUser } from "../api/authClient";

export interface AuthState {
  user: AuthUser | null;
  token: string | null;
  status: "checking" | "authenticated" | "anonymous";
  /** `remember` picks where the token is persisted: localStorage survives closing the
   * browser, sessionStorage clears when the tab does — a real difference, not cosmetic. */
  login: (email: string, password: string, remember: boolean) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
