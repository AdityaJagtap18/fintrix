import { API_ORIGIN } from "./config";

export interface AuthUser {
  email: string;
  name: string;
  role: string;
}

export class InvalidCredentialsError extends Error {
  constructor() {
    super("Invalid email or password.");
    this.name = "InvalidCredentialsError";
  }
}

const API_BASE = `${API_ORIGIN}/api/v1/auth`;

export async function login(email: string, password: string): Promise<{ token: string; user: AuthUser }> {
  const response = await fetch(`${API_BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (response.status === 401) throw new InvalidCredentialsError();
  if (!response.ok) throw new Error(`login failed: ${response.status}`);
  return response.json();
}

export async function me(token: string): Promise<AuthUser> {
  const response = await fetch(`${API_BASE}/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("not authenticated");
  return response.json();
}
