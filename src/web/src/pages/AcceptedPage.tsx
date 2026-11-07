import { useEffect, useState } from "react";
import { fetchProcessingResults, fetchRejectedResults, type ProcessingResultDto } from "../api/processingClient";

type LoadState = "loading" | "loaded" | "error";

/**
 * Read-only archive of everything settled — nothing here needs further action, which is
 * exactly why it's kept off the main Processing Results page: that list is for invoices
 * someone still has to look at. Two sections: Accepted (straight-through by the router, or
 * approved by a human) and Rejected (a human explicitly rejected it during review) — both
 * terminal states, just opposite outcomes. Rejected used to live on its own AP-only page;
 * now that signing in gates the whole app (any signed-in user reaches this), there's no
 * more reason to keep it separate.
 */
export function AcceptedPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [accepted, setAccepted] = useState<ProcessingResultDto[]>([]);
  const [rejected, setRejected] = useState<ProcessingResultDto[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchProcessingResults(), fetchRejectedResults()])
      .then(([results, rejectedResults]) => {
        if (cancelled) return;
        setAccepted(
          results.filter((r) => r.decision === "straight_through" || r.review_outcome === "approved"),
        );
        setRejected(rejectedResults);
        setState("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMessage(err instanceof Error ? err.message : "Could not load accepted invoices.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "loading") return <p>Loading accepted invoices…</p>;
  if (state === "error") {
    return (
      <p role="alert" data-testid="accepted-error">
        {errorMessage}
      </p>
    );
  }

  return (
    <div className="accepted-page">
      {accepted.length === 0 ? (
        <p data-testid="accepted-empty">No accepted invoices yet.</p>
      ) : (
        <div role="list" aria-label="Accepted invoices" className="accepted-page__list">
          {accepted.map((result) => (
            <article key={result.invoice_id} role="listitem" className="accepted-card">
              <header className="accepted-card__header">
                <h2>
                  {result.vendor_name} — {result.invoice_number}
                </h2>
                <span className={`decision-badge decision-badge--${result.decision}`}>
                  {result.decision === "straight_through" ? "straight through" : "approved"}
                </span>
              </header>
              <dl className="accepted-card__meta">
                <div>
                  <dt>Reason</dt>
                  <dd>{result.reason}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>{(result.overall_confidence * 100).toFixed(0)}%</dd>
                </div>
                {result.due_date && (
                  <div>
                    <dt>Due date</dt>
                    <dd>{result.due_date}</dd>
                  </div>
                )}
              </dl>
              {result.payment_instruction && (
                <p className="accepted-card__amount">
                  {result.payment_instruction.amount} {result.payment_instruction.currency} —
                  paid to {result.payment_instruction.vendor_bank_account}
                </p>
              )}
            </article>
          ))}
        </div>
      )}

      <h2>Rejected</h2>

      {rejected.length === 0 ? (
        <p data-testid="rejected-empty">No rejected invoices.</p>
      ) : (
        <div role="list" aria-label="Rejected invoices" className="accepted-page__list">
          {rejected.map((result) => (
            <article key={result.invoice_id} role="listitem" className="ap-card">
              <header className="ap-card__header">
                <h2>
                  {result.vendor_name} — {result.invoice_number}
                </h2>
                <span className="decision-badge decision-badge--rejected">rejected</span>
              </header>
              <dl className="ap-card__meta">
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
    </div>
  );
}
