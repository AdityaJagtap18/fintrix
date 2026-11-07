import { useEffect, useRef, useState } from "react";
import { fetchInvoiceDetail } from "../api/processingClient";
import { AlreadyClaimedError, claimInvoice, releaseInvoice, submitReview } from "../api/reviewClient";
import { DocumentViewer } from "../components/DocumentViewer";
import { FieldEditor } from "../components/FieldEditor";
import { IssuePanel } from "../components/IssuePanel";
import type { ExtractedField, ReviewInvoice, ReviewOutcome } from "../types";

// Placeholder for real per-person identity (T-062) — App.tsx's login is what actually
// gates this page now; this name is just attribution on the claim, same as
// api/routers/admin.py's fixed "admin" reviewer name for admin accepts.
const CURRENT_REVIEWER = "priya";

type LoadStatus = "loading" | "loaded" | "error";
type ClaimStatus = "claiming" | "claimed" | "conflict" | "error";

interface ReviewPageProps {
  invoiceId: string | null;
  onSubmitted: () => void;
}

/**
 * App.tsx only renders this once someone's signed in, so there's no login gate here —
 * unlike before, when this page managed its own AP session independently of the rest of
 * the app.
 */
export function ReviewPage({ invoiceId, onSubmitted }: ReviewPageProps) {
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [invoice, setInvoice] = useState<ReviewInvoice | null>(null);

  useEffect(() => {
    if (!invoiceId) return;
    let cancelled = false;
    setLoadStatus("loading");
    fetchInvoiceDetail(invoiceId)
      .then((result) => {
        if (cancelled) return;
        setInvoice(result);
        setLoadStatus("loaded");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Could not load this invoice.");
        setLoadStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [invoiceId]);

  if (!invoiceId) {
    return <p>Pick an invoice from Processing Results to review it.</p>;
  }
  if (loadStatus === "loading") {
    return <p>Loading invoice…</p>;
  }
  if (loadStatus === "error" || !invoice) {
    return (
      <p role="alert" data-testid="review-load-error">
        {loadError}
      </p>
    );
  }

  return <ReviewWorkspace invoice={invoice} onSubmitted={onSubmitted} />;
}

function ReviewWorkspace({
  invoice,
  onSubmitted,
}: {
  invoice: ReviewInvoice;
  onSubmitted: () => void;
}) {
  const [fields, setFields] = useState<ExtractedField[]>(invoice.fields);
  const [selectedFieldName, setSelectedFieldName] = useState<string | null>(null);
  const [claimStatus, setClaimStatus] = useState<ClaimStatus>("claiming");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const claimedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    claimInvoice(invoice.invoiceId, CURRENT_REVIEWER)
      .then(() => {
        if (cancelled) return;
        claimedRef.current = true;
        setClaimStatus("claimed");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof AlreadyClaimedError) {
          setStatusMessage(`This invoice is already claimed by ${err.heldBy}.`);
          setClaimStatus("conflict");
        } else if (err instanceof Error && err.name === "UnauthorizedError") {
          setStatusMessage("Your session has expired — sign out and sign in again.");
          setClaimStatus("error");
        } else {
          setStatusMessage(err instanceof Error ? err.message : "Could not claim this invoice.");
          setClaimStatus("error");
        }
      });

    return () => {
      cancelled = true;
      // AC-03: don't hold the lock past the review screen being open.
      if (claimedRef.current) {
        claimedRef.current = false;
        void releaseInvoice(invoice.invoiceId, CURRENT_REVIEWER);
      }
    };
  }, [invoice.invoiceId]);

  useEffect(() => {
    // A real tab close/refresh tears the page down before this component's own unmount
    // cleanup (above) can run, so that path alone leaves the claim held for the full
    // lock window (up to DEFAULT_LOCK_DURATION_MINUTES). `pagehide` fires reliably in
    // that case; `keepalive` lets the release request survive past unload.
    function handlePageHide() {
      if (claimedRef.current) {
        claimedRef.current = false;
        void releaseInvoice(invoice.invoiceId, CURRENT_REVIEWER, { keepalive: true });
      }
    }
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [invoice.invoiceId]);

  function handleSelectField(name: string) {
    setSelectedFieldName(name);
  }

  function handleChangeValue(name: string, value: string) {
    setFields((prev) => prev.map((field) => (field.name === name ? { ...field, value } : field)));
  }

  function handleAcceptSuggestion(name: string) {
    // AC-02.3: one click, no retyping the suggested value.
    setFields((prev) =>
      prev.map((field) =>
        field.name === name && field.suggestedValue !== null
          ? { ...field, value: field.suggestedValue }
          : field,
      ),
    );
  }

  async function handleDecision(outcome: ReviewOutcome) {
    setSubmitting(true);
    setStatusMessage(null);
    try {
      await submitReview(invoice.invoiceId, CURRENT_REVIEWER, outcome);
      claimedRef.current = false; // the backend releases the claim on submit
      onSubmitted();
    } catch (err) {
      if (err instanceof Error && err.name === "UnauthorizedError") {
        setStatusMessage("Your session has expired — sign out and sign in again.");
      } else {
        setStatusMessage(err instanceof Error ? err.message : "Could not submit this review.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (claimStatus === "claiming") {
    return <p>Claiming invoice…</p>;
  }
  if (claimStatus === "conflict" || claimStatus === "error") {
    return (
      <p role="alert" data-testid="claim-status-message">
        {statusMessage}
      </p>
    );
  }
  return (
    <div className="review-page">
      <header>
        <h1>
          {invoice.vendorName} — {invoice.invoiceNumber}
        </h1>
      </header>

      {statusMessage && (
        <p role="alert" data-testid="submit-error">
          {statusMessage}
        </p>
      )}

      <div className="review-page__layout">
        <DocumentViewer pdfUrl={invoice.pdfUrl} fields={fields} selectedFieldName={selectedFieldName} />

        <aside>
          <FieldEditor
            fields={fields}
            selectedFieldName={selectedFieldName}
            onSelectField={handleSelectField}
            onChangeValue={handleChangeValue}
            onAcceptSuggestion={handleAcceptSuggestion}
          />
          <IssuePanel issues={invoice.issues} />
          <div className="review-page__actions">
            <button type="button" disabled={submitting} onClick={() => handleDecision("forwarded")}>
              Forward for acceptance
            </button>
            <button type="button" disabled={submitting} onClick={() => handleDecision("rejected")}>
              Reject
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
