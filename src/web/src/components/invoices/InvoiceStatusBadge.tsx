const LABELS: Record<string, string> = {
  approved: "Approved",
  processed: "Processed",
  needs_review: "Needs Review",
  flagged: "Flagged",
  rejected: "Rejected",
};

export function InvoiceStatusBadge({ status }: { status: string }) {
  return <span className={`status-badge status-badge--${status}`}>{LABELS[status] ?? status}</span>;
}
