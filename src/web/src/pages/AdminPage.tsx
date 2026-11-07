import { useEffect, useState } from "react";
import { acceptInvoice, downloadAcceptedInvoicesExport, fetchForwardedResults } from "../api/adminClient";
import { fetchPreprocessingMetrics, type MetricsDto } from "../api/preprocessingClient";
import {
  fetchProcessingResults,
  fetchRejectedResults,
  type ProcessingResultDto,
} from "../api/processingClient";
import { BarChart } from "../components/BarChart";
import { DonutChart } from "../components/DonutChart";

type LoadState = "loading" | "loaded" | "error";
type View = "dashboard" | "list";

function isAccepted(result: ProcessingResultDto): boolean {
  return result.decision === "straight_through" || result.review_outcome === "approved";
}

/**
 * The admin-only tab — App.tsx only ever renders this for a signed-in admin, so there's
 * no login gate here, unlike before. The other half of the AP -> Admin chain: AP forwards
 * an invoice it thinks should be accepted (ReviewPage), but only an admin's Accept here
 * actually finalizes it — that's the one place a payment instruction gets built for
 * anything whose original router decision wasn't straight-through
 * (api/routers/processing.py's accept_forwarded). Export produces the bank-payment .xlsx
 * of every accepted invoice, straight-through and admin-accepted alike.
 */
export function AdminPage() {
  const [forwarded, setForwarded] = useState<ProcessingResultDto[]>([]);
  const [allResults, setAllResults] = useState<ProcessingResultDto[]>([]);
  const [rejected, setRejected] = useState<ProcessingResultDto[]>([]);
  const [metrics, setMetrics] = useState<MetricsDto | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [acceptingId, setAcceptingId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [view, setView] = useState<View>("dashboard");

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    Promise.all([
      fetchForwardedResults(),
      fetchProcessingResults(),
      fetchRejectedResults(),
      fetchPreprocessingMetrics(),
    ])
      .then(([forwardedData, resultsData, rejectedData, metricsData]) => {
        if (cancelled) return;
        setForwarded(forwardedData);
        setAllResults(resultsData);
        setRejected(rejectedData);
        setMetrics(metricsData);
        setState("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMessage(err instanceof Error ? err.message : "Could not load the forwarded queue.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleAccept(invoiceId: string) {
    setAcceptingId(invoiceId);
    try {
      await acceptInvoice(invoiceId);
      setForwarded((prev) => prev.filter((r) => r.invoice_id !== invoiceId));
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Could not accept this invoice.");
    } finally {
      setAcceptingId(null);
    }
  }

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      await downloadAcceptedInvoicesExport();
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Could not export accepted invoices.");
    } finally {
      setExporting(false);
    }
  }

  const acceptedCount = allResults.filter(isAccepted).length;

  return (
    <div className="admin-page">
      <div className="admin-page__header">
        <div>
          <h1>{view === "dashboard" ? "Admin Dashboard" : "Forwarded for Acceptance"}</h1>
        </div>
        <div className="admin-page__header-actions">
          <button type="button" className="btn-primary" onClick={handleExport} disabled={exporting}>
            {exporting ? "Exporting…" : "Export accepted invoices (.xlsx)"}
          </button>
        </div>
      </div>

      {exportError && (
        <p role="alert" data-testid="admin-export-error">
          {exportError}
        </p>
      )}
      {errorMessage && state === "loaded" && (
        <p role="alert" data-testid="admin-action-error">
          {errorMessage}
        </p>
      )}

      {state === "loading" && <p>Loading…</p>}
      {state === "error" && errorMessage && (
        <p role="alert" data-testid="admin-load-error">
          {errorMessage}
        </p>
      )}

      {state === "loaded" && view === "dashboard" && (
        <>
          <div className="dash-panel">
            <h2 className="dash-panel__header">Overview</h2>
            <div className="kpi-row">
              <div className="kpi-row__item">
                <span className="kpi-row__value kpi-row__value--forwarded" data-testid="admin-stat-forwarded">
                  {forwarded.length}
                </span>
                <span className="kpi-row__label">Forwarded, awaiting acceptance</span>
              </div>
              <span className="kpi-row__divider" aria-hidden="true" />
              <div className="kpi-row__item">
                <span className="kpi-row__value kpi-row__value--accepted" data-testid="admin-stat-accepted">
                  {acceptedCount}
                </span>
                <span className="kpi-row__label">Accepted</span>
              </div>
              <span className="kpi-row__divider" aria-hidden="true" />
              <div className="kpi-row__item">
                <span className="kpi-row__value kpi-row__value--rejected" data-testid="admin-stat-rejected">
                  {rejected.length}
                </span>
                <span className="kpi-row__label">Rejected</span>
              </div>
            </div>
            <div className="chart-grid">
              <div className="chart-card">
                <h3>Invoices by outcome</h3>
                <DonutChart
                  data={[
                    { label: "Accepted", value: acceptedCount, color: "#2a9d8f" },
                    { label: "Forwarded", value: forwarded.length, color: "#e9a23b" },
                    { label: "Rejected", value: rejected.length, color: "#c0392b" },
                  ]}
                />
              </div>
              <div className="chart-card">
                <h3>Forwarded invoices by confidence</h3>
                <BarChart
                  data={forwarded.map((r) => ({
                    label: r.vendor_name ?? "?",
                    value: Math.round(r.overall_confidence * 100),
                    color: "#e9a23b",
                  }))}
                />
              </div>
            </div>
          </div>

          {metrics && (
            <div className="dash-panel">
              <h2 className="dash-panel__header">Observability</h2>
              <div className="kpi-row">
                <div className="kpi-row__item">
                  <span className="kpi-row__value" data-testid="admin-metric-files-checked">
                    {metrics.files_checked}
                  </span>
                  <span className="kpi-row__label">Files checked</span>
                </div>
                <span className="kpi-row__divider" aria-hidden="true" />
                <div className="kpi-row__item">
                  <span className="kpi-row__value" data-testid="admin-metric-cache-hit-rate">
                    {(metrics.cache_hit_rate * 100).toFixed(0)}%
                  </span>
                  <span className="kpi-row__label">Cache hit rate</span>
                </div>
                <span className="kpi-row__divider" aria-hidden="true" />
                <div className="kpi-row__item">
                  <span className="kpi-row__value" data-testid="admin-metric-avg-confidence">
                    {(metrics.avg_confidence_score * 100).toFixed(0)}%
                  </span>
                  <span className="kpi-row__label">Avg confidence</span>
                </div>
                <span className="kpi-row__divider" aria-hidden="true" />
                <div className="kpi-row__item">
                  <span className="kpi-row__value kpi-row__value--forwarded" data-testid="admin-metric-hitl-rate">
                    {(metrics.hitl_trigger_rate * 100).toFixed(0)}%
                  </span>
                  <span className="kpi-row__label">Sent to a human (HITL)</span>
                </div>
              </div>
              <div className="chart-grid">
                <div className="chart-card">
                  <h3>Document Intelligence calls</h3>
                  <DonutChart
                    data={[
                      { label: "Made", value: metrics.di_calls_made, color: "#0c5af7" },
                      { label: "Avoided (cache)", value: metrics.di_calls_avoided, color: "#2a9d8f" },
                    ]}
                  />
                </div>
                <div className="chart-card">
                  <h3>Pipeline outcomes</h3>
                  <DonutChart
                    data={[
                      { label: "Succeeded", value: metrics.pipeline_success_count, color: "#2a9d8f" },
                      { label: "Errored", value: metrics.pipeline_error_count, color: "#c0392b" },
                    ]}
                  />
                </div>
              </div>
            </div>
          )}

          <button type="button" className="btn-primary dashboard-cta" onClick={() => setView("list")}>
            View forwarded queue →
          </button>
        </>
      )}

      {state === "loaded" && view === "list" && (
        <>
          <button type="button" className="dashboard-back" onClick={() => setView("dashboard")}>
            ← Back to dashboard
          </button>
          {forwarded.length === 0 && <p data-testid="admin-empty">Nothing forwarded right now.</p>}
          {forwarded.length > 0 && (
            <div role="list" aria-label="Forwarded invoices" className="admin-page__list">
              {forwarded.map((result) => (
                <article key={result.invoice_id} role="listitem" className="admin-card">
                  <header className="admin-card__header">
                    <h2>
                      {result.vendor_name} — {result.invoice_number}
                    </h2>
                    <button
                      type="button"
                      onClick={() => handleAccept(result.invoice_id)}
                      disabled={acceptingId === result.invoice_id}
                    >
                      {acceptingId === result.invoice_id ? "Accepting…" : "Accept"}
                    </button>
                  </header>
                  <dl className="admin-card__meta">
                    <div>
                      <dt>Reason</dt>
                      <dd>{result.reason}</dd>
                    </div>
                    <div>
                      <dt>Confidence</dt>
                      <dd>{(result.overall_confidence * 100).toFixed(0)}%</dd>
                    </div>
                  </dl>
                  {result.issues.length > 0 && (
                    <div role="list" aria-label={`Issues for ${result.invoice_number}`} className="issue-panel">
                      {result.issues.map((issue) => (
                        <div key={issue.rule_id} role="listitem" className={`issue issue--${issue.severity}`}>
                          <strong>{issue.rule_id}</strong>
                          <p>{issue.message}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
