import { useEffect, useState } from "react";
import { fetchProcessingResults, type ProcessingResultDto } from "../api/processingClient";

type LoadState = "loading" | "loaded" | "error";

/**
 * Read-only view over the validate -> match -> route -> payment-instruction pipeline
 * (api/routers/processing.py). Not the real ingestion pipeline — see that module's
 * docstring — but the routing decision and payment instruction shown here are computed by
 * the actual, tested production logic, not mocked data.
 */
export function ProcessingResultsPage() {
  const [state, setState] = useState<LoadState>("loading");
  const [results, setResults] = useState<ProcessingResultDto[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchProcessingResults()
      .then((data) => {
        if (cancelled) return;
        setResults(data);
        setState("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setErrorMessage(err instanceof Error ? err.message : "Could not load processing results.");
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "loading") {
    return <p>Loading processing results…</p>;
  }
  if (state === "error") {
    return (
      <p role="alert" data-testid="processing-error">
        {errorMessage}
      </p>
    );
  }

  return (
    <div className="processing-page">
      <h1>Processing Results</h1>
      <p className="processing-page__subtitle">
        Each invoice below was run through validation, 3-way matching, and confidence
        routing. A straight-through decision produces a payment instruction; anything else
        needs a human in the Review queue.
      </p>
      <div role="list" aria-label="Processing results" className="processing-page__list">
        {results.map((result) => (
          <ResultCard key={result.invoice_id} result={result} />
        ))}
      </div>
    </div>
  );
}

function ResultCard({ result }: { result: ProcessingResultDto }) {
  const isStraightThrough = result.decision === "straight_through";

  return (
    <article role="listitem" className={`result-card result-card--${result.decision}`}>
      <header className="result-card__header">
        <h2>
          {result.vendor_name} — {result.invoice_number}
        </h2>
        <span className={`decision-badge decision-badge--${result.decision}`}>
          {result.decision.replace(/_/g, " ")}
        </span>
      </header>

      <dl className="result-card__meta">
        <div>
          <dt>Reason</dt>
          <dd>{result.reason}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{(result.overall_confidence * 100).toFixed(0)}%</dd>
        </div>
        <div>
          <dt>3-way match</dt>
          <dd>{result.match_status.replace(/_/g, " ")}</dd>
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

      {isStraightThrough && result.payment_instruction ? (
        <div className="payment-instruction" data-testid={`payment-instruction-${result.invoice_id}`}>
          <h3>Payment instruction handed off</h3>
          <dl>
            <div>
              <dt>Vendor bank account (from vendor master)</dt>
              <dd>{result.payment_instruction.vendor_bank_account}</dd>
            </div>
            <div>
              <dt>Amount</dt>
              <dd>
                {result.payment_instruction.amount} {result.payment_instruction.currency}
              </dd>
            </div>
            <div>
              <dt>Due date</dt>
              <dd>{result.payment_instruction.due_date}</dd>
            </div>
            <div>
              <dt>Approval evidence</dt>
              <dd>{result.payment_instruction.approval_evidence}</dd>
            </div>
          </dl>
          <p className="payment-instruction__disclaimer">
            Mock hand-off only — no transfer is initiated. This system never executes
            payments (spec.md §5).
          </p>
        </div>
      ) : (
        <p className="processing-page__no-payment">
          No payment instruction — this invoice needs a human in the Review queue.
        </p>
      )}
    </article>
  );
}
