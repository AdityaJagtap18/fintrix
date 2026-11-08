import type { ReviewInvoice } from "../types";

export interface InvoiceSummary {
  invoiceId: string;
  vendorName: string;
  invoiceDate: string | null;
  amount: string | null;
  status: string;
}

export interface InvoiceListResult {
  items: InvoiceSummary[];
  total: number;
  page: number;
  pageSize: number;
}

export interface InvoiceListParams {
  status?: string;
  vendor?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}

const API_BASE = "/api/v1/invoices";

export async function getInvoiceDetail(invoiceId: string): Promise<ReviewInvoice> {
  const response = await fetch(`${API_BASE}/${invoiceId}`);
  if (response.status === 404) throw new Error("invoice not found");
  if (!response.ok) throw new Error(`failed to load invoice: ${response.status}`);
  return response.json();
}

export async function listInvoices(params: InvoiceListParams = {}): Promise<InvoiceListResult> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.vendor) search.set("vendor", params.vendor);
  if (params.q) search.set("q", params.q);
  search.set("page", String(params.page ?? 1));
  search.set("page_size", String(params.pageSize ?? 10));

  const response = await fetch(`${API_BASE}?${search.toString()}`);
  if (!response.ok) throw new Error(`failed to load invoices: ${response.status}`);
  return response.json();
}
