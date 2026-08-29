import { useState } from "react";
import { ProcessingResultsPage } from "./pages/ProcessingResultsPage";
import { ReviewPage } from "./pages/ReviewPage";

// A real router (queue list -> /review/:invoiceId) lands once GET /api/v1/review/queue
// exists (FR-060) — it needs a real filterable invoice query, which needs T-047's SQL
// repositories to be worth building against. Until then this is plain state-based tab
// switching between the two screens that exist: a read-only view of what the routing
// pipeline decided for a small batch, and a reviewer resolving a single flagged invoice.
type Tab = "processing" | "review";

export function App() {
  const [tab, setTab] = useState<Tab>("processing");

  return (
    <div className="app">
      <nav className="app__tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "processing"}
          className={tab === "processing" ? "app__tab app__tab--active" : "app__tab"}
          onClick={() => setTab("processing")}
        >
          Processing Results
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
      {tab === "processing" ? <ProcessingResultsPage /> : <ReviewPage />}
    </div>
  );
}
