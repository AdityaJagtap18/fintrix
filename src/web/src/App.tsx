import { useState } from "react";
import { getStoredRole, getStoredToken, login, logout, type Role } from "./api/authClient";
import { RoleLoginForm } from "./components/RoleLoginForm";
import { AcceptedPage } from "./pages/AcceptedPage";
import { AdminPage } from "./pages/AdminPage";
import { PreprocessPage } from "./pages/PreprocessPage";
import { ProcessingResultsPage } from "./pages/ProcessingResultsPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SuperAdminPage } from "./pages/SuperAdminPage";

// A real router (queue list -> /review/:invoiceId) lands once GET /api/v1/review/queue
// exists (FR-060) — it needs a real filterable invoice query, which needs T-047's SQL
// repositories to be worth building against. Until then this is plain state-based tab
// switching between the screens that exist: a read-only view of what the routing
// pipeline decided for a small batch, an AP reviewer resolving a single flagged invoice
// (reject, or forward for acceptance), a standalone pre-extraction inspection tool that
// never calls the pipeline at all, a read-only archive of settled invoices (accepted and
// rejected), and the admin-only forwarded queue + accepted-invoices export.
type Tab = "processing" | "preprocess" | "accepted" | "review" | "admin";

/**
 * Signing in is the first thing anyone does here — every tab below requires it (see
 * api/auth.py: get_session gates the whole backend now, not just AP/Admin). This
 * component is the one place that state lives; individual pages no longer manage their
 * own login gate.
 *
 * Two roles get something extra on top of the normal tab set: Admin gets one additional
 * tab (forwarded queue, accept, export). Superadmin doesn't get an extra tab — it
 * replaces the whole invoice-processing UI with its own single dashboard
 * (SuperAdminPage), since a superadmin manages accounts, not invoices (see
 * api/routers/superadmin.py's own docstring for why that's deliberate). Either way,
 * hiding UI is a nicety, not the real enforcement — api/routers/admin.py's and
 * superadmin.py's own router-level session dependencies are what actually keep the wrong
 * role out, regardless of what this nav shows.
 */
export function App() {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [role, setRole] = useState<Role | null>(() => getStoredRole());
  const [tab, setTab] = useState<Tab>("processing");
  const [reviewInvoiceId, setReviewInvoiceId] = useState<string | null>(null);

  function goReview(invoiceId: string) {
    setReviewInvoiceId(invoiceId);
    setTab("review");
  }

  async function handleSignOut() {
    if (token) await logout(token);
    setToken(null);
    setRole(null);
    setTab("processing");
  }

  if (!token || !role) {
    return (
      <RoleLoginForm
        subtitle="Sign in to use MitraEdge Invoice IQ."
        login={login}
        onSignedIn={(newToken, newRole) => {
          setToken(newToken);
          setRole(newRole);
        }}
      />
    );
  }

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__header-inner">
          <div className="app__brand" aria-hidden="true">
            <span className="app__brand-mark">
              <svg viewBox="0 0 100 100">
                <rect x="10" y="10" width="80" height="80" rx="20" fill="#0c5af7" />
                <text x="50" y="65" fontFamily="Questrial, sans-serif" fontWeight="800" fontSize="40" fill="#ffffff" textAnchor="middle">ME</text>
              </svg>
            </span>
            <span className="app__brand-name">MitraEdge Invoice IQ</span>
          </div>
          {role !== "superadmin" && (
            <nav className="app__tabs" role="tablist">
              {role === "admin" && (
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === "admin"}
                  className={tab === "admin" ? "app__tab app__tab--active" : "app__tab"}
                  onClick={() => setTab("admin")}
                >
                  Admin Panel
                </button>
              )}
              <button
                type="button"
                role="tab"
                aria-selected={tab === "processing"}
                className={tab === "processing" ? "app__tab app__tab--active" : "app__tab"}
                onClick={() => setTab("processing")}
              >
                Processed Invoices
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "preprocess"}
                className={tab === "preprocess" ? "app__tab app__tab--active" : "app__tab"}
                onClick={() => setTab("preprocess")}
              >
                Preprocessing
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "accepted"}
                className={tab === "accepted" ? "app__tab app__tab--active" : "app__tab"}
                onClick={() => setTab("accepted")}
              >
                Accepted
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "review"}
                className={tab === "review" ? "app__tab app__tab--active" : "app__tab"}
                onClick={() => setTab("review")}
              >
                Review
              </button>
            </nav>
          )}
          <button type="button" className="app__signout" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>
      {role === "superadmin" ? (
        <SuperAdminPage />
      ) : (
        <>
          {tab === "admin" && role === "admin" && <AdminPage />}
          {tab === "processing" && <ProcessingResultsPage onReview={goReview} />}
          {tab === "preprocess" && <PreprocessPage />}
          {tab === "accepted" && <AcceptedPage />}
          {tab === "review" && (
            <ReviewPage invoiceId={reviewInvoiceId} onSubmitted={() => setTab("processing")} />
          )}
        </>
      )}
    </div>
  );
}
