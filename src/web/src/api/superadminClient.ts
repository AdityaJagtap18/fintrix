// Mirrors src/api/routers/superadmin.py — user management only (see that router's own
// docstring for why superadmin is its own island, not "admin plus more"). Every function
// here requires a superadmin-role session; the backend enforces that independently
// (get_superadmin_session), these functions just supply the header via authHeader().
import { authHeader } from "./authClient";

export interface UserSummaryDto {
  username: string;
  role: string;
  doc_quota: number | null;
  docs_used: number;
  valid_until: string | null;
  created_at: string | null;
  status: "active" | "expired";
}

export interface CreateUserRequest {
  username: string;
  role: "ap" | "admin";
  // Omit or leave undefined to have the server generate one.
  password?: string;
  doc_quota?: number;
  valid_days?: number;
}

export interface CreateUserResponseDto {
  username: string;
  // Real plaintext, shown exactly once — the server never stores or returns it again.
  password: string;
  role: string;
  doc_quota: number | null;
  valid_until: string | null;
}

async function throwForErrorResponse(response: Response): Promise<never> {
  const body = await response.json().catch(() => ({ detail: "unknown error" }));
  throw new Error(
    typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
  );
}

export async function fetchUsers(): Promise<UserSummaryDto[]> {
  const response = await fetch("/api/v1/superadmin/users", { headers: authHeader() });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

export async function createUser(request: CreateUserRequest): Promise<CreateUserResponseDto> {
  const response = await fetch("/api/v1/superadmin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify(request),
  });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

export interface ChangePasswordResponseDto {
  username: string;
  // Real plaintext, shown exactly once — same as CreateUserResponseDto's.
  password: string;
}

// `password` omitted or empty generates one server-side, same "type it or leave it
// blank" convention as createUser.
export async function changePassword(
  username: string,
  password?: string,
): Promise<ChangePasswordResponseDto> {
  const response = await fetch(`/api/v1/superadmin/users/${encodeURIComponent(username)}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ password: password || undefined }),
  });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

export async function deleteUser(username: string): Promise<void> {
  const response = await fetch(`/api/v1/superadmin/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) return throwForErrorResponse(response);
}
