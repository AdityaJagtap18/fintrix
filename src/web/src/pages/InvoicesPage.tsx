import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listInvoices, type InvoiceSummary } from "../api/invoicesClient";
import { InvoiceStatusBadge } from "../components/invoices/InvoiceStatusBadge";
import { formatDate, formatINR } from "../lib/format";

const PAGE_SIZE = 6;
type SortKey = "invoiceDate" | "amount" | "vendorName";

export function InvoicesPage() {
  const [items, setItems] = useState<InvoiceSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [vendor, setVendor] = useState("");
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("invoiceDate");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listInvoices({ status: status || undefined, vendor: vendor || undefined, q: q || undefined, page, pageSize: PAGE_SIZE })
      .then((result) => {
        if (cancelled) return;
        setItems(result.items);
        setTotal(result.total);
        setError(null);
      })
      .catch(() => !cancelled && setError("Could not load invoices."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [status, vendor, q, page]);

  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) => {
      let av: string | number = a[sortKey] ?? "";
      let bv: string | number = b[sortKey] ?? "";
      if (sortKey === "amount") {
        av = Number(a.amount ?? 0);
        bv = Number(b.amount ?? 0);
      }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return copy;
  }, [items, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => (prev.size === sorted.length ? new Set() : new Set(sorted.map((i) => i.invoiceId))));
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="invoices-page">
      <div className="invoices-page__header">
        <div>
          <h1>Invoices</h1>
          <p>View and manage all processed invoices.</p>
        </div>
        <button
          type="button"
          className="btn btn--primary"
          disabled
          title="Upload intake runs via email/blob/API per the architecture (README §2) — not wired into this demo UI."
        >
          Upload Invoices
        </button>
      </div>

      <div className="invoices-page__filters">
        <input
          type="search"
          placeholder="Search by invoice ID, vendor, or amount…"
          value={q}
          onChange={(e) => {
            setPage(1);
            setQ(e.target.value);
          }}
        />
        <select
          value={status}
          onChange={(e) => {
            setPage(1);
            setStatus(e.target.value);
          }}
        >
          <option value="">All statuses</option>
          <option value="approved">Approved</option>
          <option value="needs_review">Needs Review</option>
          <option value="flagged">Flagged</option>
          <option value="rejected">Rejected</option>
          <option value="processed">Processed</option>
        </select>
        <input
          type="text"
          placeholder="Filter by vendor…"
          value={vendor}
          onChange={(e) => {
            setPage(1);
            setVendor(e.target.value);
          }}
        />
      </div>

      {error && <p role="alert">{error}</p>}
      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          <table className="data-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={selected.size > 0 && selected.size === sorted.length}
                    onChange={toggleSelectAll}
                    aria-label="Select all invoices"
                  />
                </th>
                <th>Invoice ID</th>
                <th className="sortable" onClick={() => toggleSort("vendorName")}>
                  Vendor {sortKey === "vendorName" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                </th>
                <th className="sortable" onClick={() => toggleSort("invoiceDate")}>
                  Invoice Date {sortKey === "invoiceDate" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                </th>
                <th className="sortable" onClick={() => toggleSort("amount")}>
                  Amount {sortKey === "amount" ? (sortDir === "asc" ? "↑" : "↓") : ""}
                </th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((inv) => (
                <tr key={inv.invoiceId}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(inv.invoiceId)}
                      onChange={() => toggleSelected(inv.invoiceId)}
                      aria-label={`Select ${inv.invoiceId}`}
                    />
                  </td>
                  <td>
                    <Link to={`/review/${inv.invoiceId}`}>{inv.invoiceId}</Link>
                  </td>
                  <td>{inv.vendorName}</td>
                  <td>{formatDate(inv.invoiceDate)}</td>
                  <td>{formatINR(inv.amount)}</td>
                  <td>
                    <InvoiceStatusBadge status={inv.status} />
                  </td>
                  <td>
                    <Link to={`/review/${inv.invoiceId}`} className="btn btn--small">
                      Review
                    </Link>
                  </td>
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={7}>No invoices match these filters.</td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="invoices-page__pagination">
            <span>
              Showing {Math.min((page - 1) * PAGE_SIZE + 1, total)}–{Math.min(page * PAGE_SIZE, total)} of {total}{" "}
              invoices
            </span>
            <div className="pagination-controls">
              <button type="button" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                ‹
              </button>
              <span>
                {page} / {totalPages}
              </span>
              <button type="button" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                ›
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
