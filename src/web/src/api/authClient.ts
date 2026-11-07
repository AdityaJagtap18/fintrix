// Mirrors src/api/routers/auth.py — one login for the whole app. AP and admin
// credentials both work here; the response's `role` says which, and that's what the UI
// uses to decide whether to show the Admin tab. The token is a bearer string the backend
// issues and independently validates on every gated request — nothing about how it's
// stored here is what actually enforces access; api/auth.py's get_session/
// get_admin_session do that server-side, on every request, regardless of what this page
// thinks the role is.

export type Role = "ap" | "admin" | "superadmin";

const TOKEN_KEY = "invoice-iq-token";
const ROLE_KEY = "invoice-iq-role";

function storeSession(token: string | null, role: Role | null): void {
  try {
    if (token && role) {
      sessionStorage.setItem(TOKEN_KEY, token);
      sessionStorage.setItem(ROLE_KEY, role);
    } else {
      sessionStorage.removeItem(TOKEN_KEY);
      sessionStorage.removeItem(ROLE_KEY);
    }
  } catch {
    // Nothing to fall back to — the token still works for this page load via the
    // in-memory state that called this; it just won't survive a refresh.
  }
}

export function getStoredToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // sessionStorage can throw in a locked-down/private context
  }
}

export function getStoredRole(): Role | null {
  try {
    return sessionStorage.getItem(ROLE_KEY) as Role | null;
  } catch {
    return null;
  }
}

// Every other api/*.ts client spreads this into its fetch headers instead of taking a
// token parameter — one place reads sessionStorage, not one call site per endpoint.
export function authHeader(): HeadersInit {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function login(username: string, password: string): Promise<{ token: string; role: Role }> {
  const response = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "unknown error" }));
    throw new Error(
      typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
    );
  }
  const data = (await response.json()) as { token: string; role: Role };
  storeSession(data.token, data.role);
  return data;
}

export async function logout(token: string): Promise<void> {
  try {
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } finally {
    // Clear local state regardless of whether the server call itself succeeded — an
    // already-expired token 401ing on logout shouldn't leave the UI thinking it's still
    // signed in.
    storeSession(null, null);
  }
}
