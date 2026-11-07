import { useState } from "react";
import type { Role } from "../api/authClient";

/**
 * The one login gate for the whole app — a modal popup (see .role-login in index.css,
 * matching MitraEdge's own .modal/.modal-backdrop/.modal-panel pattern from the
 * marketing site), not a full-page takeover. Any valid user signs in here, AP or admin;
 * `login`'s response says which role they got, and the caller (App.tsx) decides what
 * that unlocks — this component itself doesn't know or care about roles. Username +
 * password only — no social login, nothing here is backed by an OAuth provider.
 */
export function RoleLoginForm({
  subtitle,
  login,
  onSignedIn,
}: {
  subtitle: string;
  login: (username: string, password: string) => Promise<{ token: string; role: Role }>;
  onSignedIn: (token: string, role: Role) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const { token, role } = await login(username, password);
      onSignedIn(token, role);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
      setSubmitting(false);
    }
  }

  return (
    <div className="role-login">
      <div className="role-login__backdrop" aria-hidden="true" />
      <div className="role-login__panel" role="dialog" aria-modal="true" aria-label="Sign in">
        <div className="role-login__brand">
          <span className="role-login__brand-mark" aria-hidden="true">
            <svg viewBox="0 0 100 100">
              <rect x="10" y="10" width="80" height="80" rx="20" fill="#0c5af7" />
              <text x="50" y="65" fontFamily="Questrial, sans-serif" fontWeight="800" fontSize="40" fill="#ffffff" textAnchor="middle">ME</text>
            </svg>
          </span>
          <div>
            <p className="role-login__brand-name">MitraEdge Invoice IQ</p>
            <p className="role-login__brand-tag">Demo</p>
          </div>
        </div>

        <h1>Welcome back</h1>
        <p className="role-login__subtitle">{subtitle}</p>

        <form onSubmit={handleSubmit} className="role-login__form">
          <label htmlFor="app-login-username">
            Username
            <input
              id="app-login-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="Username"
            />
          </label>
          <label htmlFor="app-login-password">
            Password
            <span className="role-login__password-field">
              <input
                id="app-login-password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="Password"
              />
              <button
                type="button"
                className="role-login__toggle-password"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </span>
          </label>
          {error && (
            <p role="alert" data-testid="app-login-error">
              {error}
            </p>
          )}
          <button type="submit" className="role-login__submit btn-primary" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
