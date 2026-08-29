import { useState } from "react";
import type { ProcessInvoiceRequest, ProcessingResultDto } from "../api/processingClient";
import { submitInvoice } from "../api/processingClient";
import { FAKE_INVOICE_PRESETS } from "../data/fakeInvoicePresets";

interface UploadInvoiceFormProps {
  onSubmitted: (result: ProcessingResultDto) => void;
}

const BLANK: ProcessInvoiceRequest = {
  vendor_name: "",
  vendor_tax_id: "",
  invoice_number: "",
  invoice_date: "",
  due_date: "",
  po_number: "",
  currency: "INR",
  subtotal: "",
  tax_total: "",
  total: "",
  amount_due: "",
  confidence: 0.97,
  vendor_requires_po: false,
  vendor_invoices_processed: 50,
  vendor_bank_account: "",
  source_filename: null,
};

function toRequest(form: ProcessInvoiceRequest): ProcessInvoiceRequest {
  // Empty strings on optional fields must become null, not "" — Pydantic's Decimal/date
  // fields reject an empty string outright rather than treating it as absent.
  const nullIfBlank = (v: string | null) => (v && v.trim() !== "" ? v : null);
  return {
    ...form,
    vendor_tax_id: nullIfBlank(form.vendor_tax_id),
    due_date: nullIfBlank(form.due_date),
    po_number: nullIfBlank(form.po_number),
    subtotal: nullIfBlank(form.subtotal),
    tax_total: nullIfBlank(form.tax_total),
    amount_due: nullIfBlank(form.amount_due),
    vendor_bank_account: nullIfBlank(form.vendor_bank_account),
  };
}

export function UploadInvoiceForm({ onSubmitted }: UploadInvoiceFormProps) {
  const [form, setForm] = useState<ProcessInvoiceRequest>(BLANK);
  const [fileName, setFileName] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof ProcessInvoiceRequest>(key: K, value: ProcessInvoiceRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function loadPreset(presetId: string) {
    const preset = FAKE_INVOICE_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setForm({ ...preset.request, source_filename: fileName });
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null;
    setFileName(file?.name ?? null);
    update("source_filename", file?.name ?? null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await submitInvoice(toRequest(form));
      onSubmitted(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not process this invoice.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <details className="upload-form" open>
      <summary>Upload an invoice</summary>
      <p className="upload-form__notice">
        There's no live OCR wired in yet (that needs an Azure Document Intelligence
        resource — see spec.md). The file below is attached for reference only and its
        content is never read; fill in the fields as if you already extracted them, or
        load a preset below to test a specific routing outcome.
      </p>

      <form onSubmit={handleSubmit}>
        <div className="upload-form__row">
          <label htmlFor="preset-select">Load a fake invoice</label>
          <select
            id="preset-select"
            onChange={(e) => loadPreset(e.target.value)}
            defaultValue=""
          >
            <option value="" disabled>
              Choose a scenario…
            </option>
            {FAKE_INVOICE_PRESETS.map((preset) => (
              <option key={preset.id} value={preset.id} title={preset.description}>
                {preset.label}
              </option>
            ))}
          </select>
        </div>

        <div className="upload-form__row">
          <label htmlFor="invoice-file">Invoice file (PDF)</label>
          <input id="invoice-file" type="file" accept="application/pdf" onChange={handleFileChange} />
        </div>

        <div className="upload-form__grid">
          <label>
            Vendor name
            <input
              required
              value={form.vendor_name}
              onChange={(e) => update("vendor_name", e.target.value)}
            />
          </label>
          <label>
            Vendor tax ID (GSTIN)
            <input
              value={form.vendor_tax_id ?? ""}
              onChange={(e) => update("vendor_tax_id", e.target.value)}
            />
          </label>
          <label>
            Invoice number
            <input
              required
              value={form.invoice_number}
              onChange={(e) => update("invoice_number", e.target.value)}
            />
          </label>
          <label>
            Invoice date
            <input
              required
              type="date"
              value={form.invoice_date}
              onChange={(e) => update("invoice_date", e.target.value)}
            />
          </label>
          <label>
            Due date
            <input
              type="date"
              value={form.due_date ?? ""}
              onChange={(e) => update("due_date", e.target.value)}
            />
          </label>
          <label>
            Currency
            <input value={form.currency} onChange={(e) => update("currency", e.target.value)} />
          </label>
          <label>
            Subtotal
            <input value={form.subtotal ?? ""} onChange={(e) => update("subtotal", e.target.value)} />
          </label>
          <label>
            Tax total
            <input
              value={form.tax_total ?? ""}
              onChange={(e) => update("tax_total", e.target.value)}
            />
          </label>
          <label>
            Total
            <input
              required
              value={form.total}
              onChange={(e) => update("total", e.target.value)}
            />
          </label>
          <label>
            Amount due
            <input
              value={form.amount_due ?? ""}
              onChange={(e) => update("amount_due", e.target.value)}
            />
          </label>
          <label>
            Vendor bank account
            <input
              value={form.vendor_bank_account ?? ""}
              onChange={(e) => update("vendor_bank_account", e.target.value)}
            />
          </label>
          <label>
            OCR confidence ({Math.round(form.confidence * 100)}%)
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={form.confidence}
              onChange={(e) => update("confidence", Number(e.target.value))}
            />
          </label>
          <label>
            Vendor's prior invoices processed
            <input
              type="number"
              min={0}
              value={form.vendor_invoices_processed}
              onChange={(e) => update("vendor_invoices_processed", Number(e.target.value))}
            />
          </label>
          <label className="upload-form__checkbox">
            <input
              type="checkbox"
              checked={form.vendor_requires_po}
              onChange={(e) => update("vendor_requires_po", e.target.checked)}
            />
            Vendor requires a PO
          </label>
        </div>

        {error && (
          <p role="alert" data-testid="upload-error">
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? "Processing…" : "Process invoice"}
        </button>
      </form>
    </details>
  );
}
