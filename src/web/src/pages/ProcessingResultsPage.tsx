import { useEffect, useRef, useState } from "react";
import {
  deleteProcessingResult,
  fetchProcessingResults,
  fetchProcessingSummary,
  type ProcessingResultDto,
  type ProcessingSummaryDto,
} from "../api/processingClient";
import { UploadInvoiceForm } from "../components/UploadInvoiceForm";

type LoadState = "loading" | "loaded" | "error";

/**
 * Read-only view over the validate -> match -> route -> payment-instruction pipeline
 * (api/routers/processing.py). Not the real ingestion pipeline — see that module's
 * docstring — but the routing decision and payment instruction shown here are computed by
 * the actual, tested production logic, not mocked data.
 *
 * No direct file upload here on purpose — a raw PDF handed straight to POST /extract
 * skips every preprocessing check (blank-page drop, DPI/grayscale, the content-hash
 * cache, the page-count gate). The Preprocessing tab is the only path from "a PDF" to
 * "a processed invoice" now; its "Send for extraction" button is what actually calls
 * this same pipeline. UploadInvoiceForm below is unrelated — no file, no OCR, just typed
 * field values for exercising a specific routing outcome.
 *
 * Only shows invoices still needing a human — accepted and rejected ones (straight-
 * through, human-approved, or human-rejected) move to the Accepted tab once decided;
 * rejected ones were never in this response to begin with (GET /results excludes them
 * server-side — see AcceptedPage for where they actually show up).
 */
interface ProcessingResultsPageProps {
  onReview: (invoiceId: string) => void;
}

export function ProcessingResultsPage({ onReview }: ProcessingResultsPageProps) {
  const [state, setState] = useState<LoadState>("loading");
  const [results, setResults] = useState<ProcessingResultDto[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Object URLs for files picked in the upload form, keyed by invoice_id — browser-local
  // only, never sent to the server (see UploadInvoiceForm's onSubmitted comment).
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});

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

  // Cleanup must see the latest map at unmount time without re-running on every upload —
  // a ref sidesteps needing fileUrls in the effect's deps (which would revoke and
  // re-attach on every single upload instead of only when the page itself goes away).
  const fileUrlsRef = useRef(fileUrls);
  fileUrlsRef.current = fileUrls;

  useEffect(() => {
    return () => {
      Object.values(fileUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  async function handleDelete(invoiceId: string) {
    await deleteProcessingResult(invoiceId);
    setResults((prev) => prev.filter((r) => r.invoice_id !== invoiceId));
    setFileUrls((prev) => {
      const { [invoiceId]: removed, ...rest } = prev;
      if (removed) URL.revokeObjectURL(removed);
      return rest;
    });
  }

  function handleNewResult(result: ProcessingResultDto, fileUrl: string | null = null) {
    setResults((prev) => [...prev, result]);
    if (fileUrl) {
      setFileUrls((prev) => ({ ...prev, [result.invoice_id]: fileUrl }));
    }
  }

  if (state === "loading") {
    return <p>Loading processed invoices…</p>;
  }
  if (state === "error") {
    return (
      <p role="alert" data-testid="processing-error">
        {errorMessage}
      </p>
    );
  }

  const pending = results.filter((r) => r.decision !== "straight_through" && !r.review_outcome);

  return (
    <div className="processing-page">
      <Tracker />

      <p className="processing-page__upload-notice" data-testid="upload-moved-notice">
        To process a new invoice, use the <strong>Preprocessing</strong> tab — every file
        is checked and optimized there before it's ever sent for extraction.
      </p>

      <UploadInvoiceForm onSubmitted={handleNewResult} />

      <div role="list" aria-label="Processing results" className="processing-page__list">
        {pending.map((result) => (
          <ResultCard
            key={result.invoice_id}
            result={result}
            fileUrl={fileUrls[result.invoice_id]}
            onReview={onReview}
            onDelete={handleDelete}
          />
        ))}
      </div>
    </div>
  );
}

function ResultCard({
  result,
  fileUrl,
  onReview,
  onDelete,
}: {
  result: ProcessingResultDto;
  fileUrl?: string;
  onReview: (invoiceId: string) => void;
  onDelete: (invoiceId: string) => void;
}) {
  const isStraightThrough = result.decision === "straight_through";
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function handleDeleteClick() {
    if (!window.confirm(`Delete ${result.vendor_name} — ${result.invoice_number}?`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDelete(result.invoice_id);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Could not delete this invoice.");
      setDeleting(false);
    }
  }

  return (
    <article role="listitem" className={`result-card result-card--${result.decision}`}>
      <header className="result-card__header">
        <h2>
          {result.vendor_name} — {result.invoice_number}
        </h2>
        <div className="result-card__header-actions">
          <span className={`decision-badge decision-badge--${result.decision}`}>
            {result.decision.replace(/_/g, " ")}
          </span>
          {result.review_outcome && (
            <span className={`decision-badge decision-badge--${result.review_outcome}`}>
              Reviewed: {result.review_outcome}
            </span>
          )}
          <button
            type="button"
            className="result-card__delete"
            onClick={handleDeleteClick}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </header>

      {deleteError && (
        <p role="alert" data-testid={`delete-error-${result.invoice_id}`}>
          {deleteError}
        </p>
      )}

      <span className={`source-badge source-badge--${result.extraction_method}`}>
        {extractionMethodLabel(result.extraction_method)}
      </span>

      {result.source_filename && (
        <p className="result-card__source">
          Uploaded: {result.source_filename}
          {fileUrl && (
            <>
              {" — "}
              <a href={fileUrl} target="_blank" rel="noopener noreferrer">
                view file
              </a>
            </>
          )}
        </p>
      )}

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
      ) : result.review_outcome ? (
        <p className="processing-page__no-payment">
          No payment instruction — a human has already reviewed this invoice and{" "}
          {result.review_outcome} it.
        </p>
      ) : (
        <div className="processing-page__no-payment">
          <p>No payment instruction — this invoice needs a human in the Review queue.</p>
          <button type="button" onClick={() => onReview(result.invoice_id)}>
            Review this invoice
          </button>
        </div>
      )}
    </article>
  );
}

function extractionMethodLabel(method: string): string {
  if (method === "azure_document_intelligence") return "Real OCR (Azure Document Intelligence)";
  // T-044: fields came from a prior real OCR run on byte-identical content, not this
  // call — genuinely real data, just not a fresh Document Intelligence call.
  if (method === "cache_reuse") return "Cached (previously extracted)";
  return "Manual test data";
}

function Tracker() {
  const [summary, setSummary] = useState<ProcessingSummaryDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchProcessingSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load the tracker.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <p role="alert" data-testid="tracker-error">
        {error}
      </p>
    );
  }
  if (!summary) return null;

  return (
    <div className="tracker" data-testid="tracker">
      <dl className="tracker__grid">
        <div className="tracker__box">
          <dt>Total received</dt>
          <dd data-testid="tracker-total">{summary.total}</dd>
        </div>
        <div className="tracker__box tracker__box--urgent">
          <dt>Urgent</dt>
          <dd data-testid="tracker-urgent">{summary.urgent_count}</dd>
        </div>
        <div className="tracker__box tracker__box--due-soon">
          <dt>Due within 3 days</dt>
          <dd data-testid="tracker-due-soon">{summary.due_soon_count}</dd>
        </div>
        <div className="tracker__box tracker__box--accepted">
          <dt>Accepted</dt>
          <dd data-testid="tracker-accepted">{summary.accepted_count}</dd>
        </div>
        <div className="tracker__box tracker__box--rejected">
          <dt>Rejected</dt>
          <dd data-testid="tracker-rejected">{summary.rejected_count}</dd>
        </div>
        <div className="tracker__box">
          <dt>Pending</dt>
          <dd data-testid="tracker-pending">{summary.pending_count}</dd>
        </div>
      </dl>
    </div>
  );
}
