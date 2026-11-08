import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { ComingSoonPage } from "./pages/ComingSoonPage";
import { DashboardPage } from "./pages/DashboardPage";
import { InvoicesPage } from "./pages/InvoicesPage";
import { LoginPage } from "./pages/LoginPage";
import { ReviewPage } from "./pages/ReviewPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { VendorsPage } from "./pages/VendorsPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell>
              <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/invoices" element={<InvoicesPage />} />
                <Route path="/review" element={<ReviewQueuePage />} />
                <Route path="/review/:invoiceId" element={<ReviewPage />} />
                <Route path="/vendors" element={<VendorsPage />} />
                <Route
                  path="/analytics"
                  element={
                    <ComingSoonPage
                      title="Analytics"
                      note="Spend and vendor analytics need a real event/audit log to report on (Phase 2, T-047+). Not wired to live data yet."
                    />
                  }
                />
                <Route
                  path="/settings"
                  element={
                    <ComingSoonPage
                      title="Settings"
                      note="Per-vendor thresholds, user management and integrations land once the SQL-backed vendor master (T-047) exists."
                    />
                  }
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
