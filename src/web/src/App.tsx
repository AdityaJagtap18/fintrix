import { ReviewPage } from "./pages/ReviewPage";

// A real router (queue list -> /review/:invoiceId) lands once GET /api/v1/review/queue
// exists (FR-060) — it needs a real filterable invoice query, which needs T-047's SQL
// repositories to be worth building against. Until then this is the one screen that
// matters for the demo: a reviewer resolving a single flagged invoice.
export function App() {
  return <ReviewPage />;
}
