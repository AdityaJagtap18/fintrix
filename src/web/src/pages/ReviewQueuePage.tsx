import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listInvoices, type InvoiceSummary } from "../api/invoicesClient";
import { InvoiceStatusBadge } from "../components/invoices/InvoiceStatusBadge";
import { formatDate, formatINR } from "../lib/format";

export function ReviewQueuePage() {
  const [items, setItems] = useState<InvoiceSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listInvoices({ status: "needs_review", pageSize: 25 })
      .then((r) => !cancelled && setItems(r.items))
      .catch(() => !cancelled && setError("Could not load the review queue."));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="review-queue-page">
      <h1>Review Queue</h1>
      <p>Invoices currently waiting on a human decision.</p>
      {error && <p role="alert">{error}</p>}
      {!error && items.length === 0 && <p>Nothing is waiting for review right now.</p>}
      <ul className="review-queue-page__list">
        {items.map((inv) => (
          <li key={inv.invoiceId} className="card">
            <div>
              <strong>{inv.vendorName}</strong>
              <span className="review-queue-page__meta">
                {inv.invoiceId} · {formatDate(inv.invoiceDate)} · {formatINR(inv.amount)}
              </span>
            </div>
            <InvoiceStatusBadge status={inv.status} />
            <Link to={`/review/${inv.invoiceId}`} className="btn btn--primary">
              Review
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
