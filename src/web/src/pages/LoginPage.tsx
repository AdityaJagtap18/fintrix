import { useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import illustrationUrl from "../assets/branding/fintrix-invoice-illustration.svg";
import { LogoMark } from "../components/common/LogoMark";
import { AlertIcon, AnalyticsIcon, ScanIcon, ShieldCheckIcon } from "../components/common/FeatureIcons";
import { InvalidCredentialsError } from "../api/authClient";
import { useAuth } from "../auth/useAuth";

const FEATURES: { icon: ReactNode; label: string }[] = [
  { icon: <ScanIcon />, label: "AI-powered data extraction" },
  { icon: <ShieldCheckIcon />, label: "Automated invoice validation" },
  { icon: <AlertIcon />, label: "Exception handling & review" },
  { icon: <AnalyticsIcon />, label: "Spend and vendor analytics" },
];

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function emailIsValid(value: string): boolean {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!email || !password) {
      setError("Email and password are required.");
      return;
    }
    if (!emailIsValid(email)) {
      setError("Enter a valid email address.");
      return;
    }

    setSubmitting(true);
    try {
      await login(email, password, remember);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof InvalidCredentialsError ? err.message : "Could not sign in. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDemoLogin() {
    setError(null);
    setSubmitting(true);
    try {
      await login("aditya@company.com", "fintrix123", remember);
      const from = (location.state as { from?: string } | null)?.from ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof InvalidCredentialsError ? err.message : "Could not sign in. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-page__panel">
        <div className="login-page__logo">
          <LogoMark width={240} />
        </div>

        <h1 className="login-page__headline">
          Intelligent Financial
          <br />
          Document Processing
        </h1>
        <p className="login-page__description">Extract, validate and streamline invoices with AI.</p>

        <ul className="login-page__features">
          {FEATURES.map((feature) => (
            <li key={feature.label}>
              <span className="login-page__feature-icon">{feature.icon}</span>
              <span>{feature.label}</span>
            </li>
          ))}
        </ul>

        <img
          className="login-page__illustration"
          src={illustrationUrl}
          alt="Invoice processing illustration"
        />
      </div>

      <div className="login-page__card-wrap">
        <form className="login-page__card" onSubmit={handleSubmit} noValidate>
          <h2>Welcome to FINTRIX</h2>
          <p className="login-page__card-sub">Sign in to your account</p>

          {error && (
            <p role="alert" className="login-page__error">
              {error}
            </p>
          )}

          <label htmlFor="email">Email address</label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />

          <label htmlFor="password">Password</label>
          <div className="login-page__password-field">
            <input
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="login-page__toggle-password"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>

          <div className="login-page__row">
            <label className="login-page__remember">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Remember me
            </label>
            <span
              className="login-page__forgot"
              title="Password reset isn't wired to a backend yet — there's no email-sending service behind this demo."
            >
              Forgot password?
            </span>
          </div>

          <button type="submit" className="login-page__submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign In"}
          </button>

          <button
            type="button"
            className="login-page__demo-login"
            onClick={handleDemoLogin}
            disabled={submitting}
          >
            {submitting ? "Signing in…" : "Demo Login"}
          </button>
        </form>
      </div>
    </div>
  );
}
