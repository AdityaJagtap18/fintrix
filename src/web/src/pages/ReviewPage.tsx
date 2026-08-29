import { useEffect, useRef, useState } from "react";
import { AlreadyClaimedError, claimInvoice, releaseInvoice, submitReview } from "../api/reviewClient";
import { DocumentViewer } from "../components/DocumentViewer";
import { FieldEditor } from "../components/FieldEditor";
import { IssuePanel } from "../components/IssuePanel";
import { mockInvoice } from "../data/mockInvoice";
import type { ExtractedField, ReviewOutcome } from "../types";

// Placeholder for real auth (T-062) — mirrors the dev-mode placeholder in
// src/api/dependencies.py. Both need replacing together.
const CURRENT_REVIEWER = "priya";

type ClaimStatus = "claiming" | "claimed" | "conflict" | "error";

export function ReviewPage() {
  const invoice = mockInvoice; // placeholder pending GET /invoices/{id} — see mockInvoice.ts
  const [fields, setFields] = useState<ExtractedField[]>(invoice.fields);
  const [selectedFieldName, setSelectedFieldName] = useState<string | null>(null);
  const [claimStatus, setClaimStatus] = useState<ClaimStatus>("claiming");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submittedOutcome, setSubmittedOutcome] = useState<ReviewOutcome | null>(null);
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
        } else {
          setStatusMessage(err instanceof Error ? err.message : "Could not claim this invoice.");
        }
        setClaimStatus(err instanceof AlreadyClaimedError ? "conflict" : "error");
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
      setSubmittedOutcome(outcome);
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "Could not submit this review.");
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
  if (submittedOutcome) {
    return (
      <p data-testid="submitted-message">
        Review {submittedOutcome}. Thanks, {CURRENT_REVIEWER}.
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
            <button type="button" disabled={submitting} onClick={() => handleDecision("approved")}>
              Approve
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
