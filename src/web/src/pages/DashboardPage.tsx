import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getDashboardMetrics, type DashboardMetrics } from "../api/dashboardClient";
import { ProcessingChart } from "../components/dashboard/ProcessingChart";
import { StatusChart } from "../components/dashboard/StatusChart";
import { InvoiceStatusBadge } from "../components/invoices/InvoiceStatusBadge";
import { useAuth } from "../auth/useAuth";
import { formatDate, formatINR, formatINRLakh } from "../lib/format";

export function DashboardPage() {
  const { user } = useAuth();
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboardMetrics()
      .then((m) => !cancelled && setMetrics(m))
      .catch(() => !cancelled && setError("Could not load dashboard metrics."));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!metrics) return <p>Loading…</p>;

  const firstName = user?.name?.split(" ")[0] ?? "there";

  return (
    <div className="dashboard-page">
      <div className="dashboard-page__heading">
        <div>
          <h1>Good morning, {firstName}</h1>
          <p>Here&rsquo;s what&rsquo;s happening with your invoices.</p>
        </div>
      </div>

      <div className="kpi-row">
        <div className="kpi-card">
          <span className="kpi-card__label">Total processed</span>
          <span className="kpi-card__value">{metrics.totalProcessed}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-card__label">Awaiting review</span>
          <span className="kpi-card__value">{metrics.awaitingReview}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-card__label">Approved this month</span>
          <span className="kpi-card__value">{formatINRLakh(metrics.approvedThisMonth)}</span>
        </div>
        <div className="kpi-card">
          <span className="kpi-card__label">Exceptions</span>
          <span className="kpi-card__value">{metrics.exceptionsPct}%</span>
        </div>
      </div>

      <div className="dashboard-page__charts">
        <div className="card">
          <h2>Processing volume</h2>
          <p className="card__sub">Invoices processed over time</p>
          <ProcessingChart points={metrics.processingVolume} />
        </div>
        <div className="card">
          <h2>Status breakdown</h2>
          <p className="card__sub">Share of processed invoices</p>
          <StatusChart breakdown={metrics.statusBreakdown} />
        </div>
      </div>

      <div className="dashboard-page__bottom">
        <div className="card">
          <div className="card__header-row">
            <div>
              <h2>Recent invoices</h2>
              <p className="card__sub">Latest processed invoices and their status</p>
            </div>
            <Link to="/invoices">View all invoices →</Link>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Invoice ID</th>
                <th>Date</th>
                <th>Amount</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {metrics.recentInvoices.map((inv) => (
                <tr key={inv.invoiceId}>
                  <td>{inv.vendorName}</td>
                  <td>
                    <Link to={`/review/${inv.invoiceId}`}>{inv.invoiceId}</Link>
                  </td>
                  <td>{formatDate(inv.invoiceDate)}</td>
                  <td>{formatINR(inv.amount)}</td>
                  <td>
                    <InvoiceStatusBadge status={inv.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2>Needs attention</h2>
          <p className="card__sub">Items that require your review</p>
          <ul className="attention-list">
            {metrics.needsAttention.length === 0 && <li>Nothing needs attention right now.</li>}
            {metrics.needsAttention.map((item) => (
              <li key={item.invoiceId} className={`attention-list__item attention-list__item--${item.kind}`}>
                <Link to={`/review/${item.invoiceId}`}>
                  <strong>{item.title}</strong>
                  <span>{item.description}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
