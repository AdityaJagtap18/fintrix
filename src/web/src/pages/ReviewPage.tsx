import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getInvoiceDetail } from "../api/invoicesClient";
import { AlreadyClaimedError, claimInvoice, releaseInvoice, submitReview } from "../api/reviewClient";
import { DocumentViewer } from "../components/DocumentViewer";
import { FieldEditor } from "../components/FieldEditor";
import { IssuePanel } from "../components/IssuePanel";
import { useAuth } from "../auth/useAuth";
import type { ExtractedField, ReviewInvoice, ReviewOutcome } from "../types";

type ClaimStatus = "loading" | "claiming" | "claimed" | "conflict" | "error";

export function ReviewPage() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const { user } = useAuth();
  const reviewer = user?.email ?? "unknown";

  const [invoice, setInvoice] = useState<ReviewInvoice | null>(null);
  const [fields, setFields] = useState<ExtractedField[]>([]);
  const [selectedFieldName, setSelectedFieldName] = useState<string | null>(null);
  const [claimStatus, setClaimStatus] = useState<ClaimStatus>("loading");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submittedOutcome, setSubmittedOutcome] = useState<ReviewOutcome | null>(null);
  const claimedRef = useRef(false);

  useEffect(() => {
    if (!invoiceId) return;
    let cancelled = false;
    setClaimStatus("loading");

    getInvoiceDetail(invoiceId)
      .then((inv) => {
        if (cancelled) return;
        setInvoice(inv);
        setFields(inv.fields);
        setClaimStatus("claiming");
        return claimInvoice(inv.invoiceId, reviewer);
      })
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
        } else {
          setStatusMessage(err instanceof Error ? err.message : "Could not load this invoice.");
          setClaimStatus("error");
        }
      });

    return () => {
      cancelled = true;
      if (claimedRef.current) {
        claimedRef.current = false;
        void releaseInvoice(invoiceId, reviewer);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoiceId]);

  function handleSelectField(name: string) {
    setSelectedFieldName(name);
  }

  function handleChangeValue(name: string, value: string) {
    setFields((prev) => prev.map((field) => (field.name === name ? { ...field, value } : field)));
  }

  function handleAcceptSuggestion(name: string) {
    setFields((prev) =>
      prev.map((field) =>
        field.name === name && field.suggestedValue !== null
          ? { ...field, value: field.suggestedValue }
          : field,
      ),
    );
  }

  async function handleDecision(outcome: ReviewOutcome) {
    if (!invoice) return;
    setSubmitting(true);
    setStatusMessage(null);
    try {
      await submitReview(invoice.invoiceId, reviewer, outcome);
      claimedRef.current = false;
      setSubmittedOutcome(outcome);
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "Could not submit this review.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!invoiceId) {
    return <p role="alert">No invoice selected. Pick one from the Invoices list.</p>;
  }
  if (claimStatus === "loading" || claimStatus === "claiming") {
    return <p>Loading invoice…</p>;
  }
  if (claimStatus === "conflict" || claimStatus === "error") {
    return (
      <div>
        <p role="alert" data-testid="claim-status-message">
          {statusMessage}
        </p>
        <Link to="/invoices">← Back to invoices</Link>
      </div>
    );
  }
  if (submittedOutcome) {
    return (
      <div>
        <p data-testid="submitted-message">
          Review {submittedOutcome}. Thanks, {user?.name ?? reviewer}.
        </p>
        <Link to="/invoices">← Back to invoices</Link>
      </div>
    );
  }
  if (!invoice) return null;

  return (
    <div className="review-page">
      <nav className="review-page__breadcrumb">
        <Link to="/invoices">Invoices</Link> / Review Invoice
      </nav>
      <header className="review-page__header">
        <h1>
          {invoice.vendorName} — {invoice.invoiceNumber}
        </h1>
        {invoice.issues.some((i) => !i.passed) && <span className="badge badge--warning">Needs Attention</span>}
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
            <button type="button" disabled={submitting} onClick={() => handleDecision("approved")} className="btn btn--primary">
              Approve
            </button>
            <button type="button" disabled={submitting} onClick={() => handleDecision("rejected")} className="btn btn--danger">
              Reject
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
