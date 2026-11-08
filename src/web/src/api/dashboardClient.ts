import { API_ORIGIN } from "./config";

export interface ProcessingVolumePoint {
  date: string;
  count: number;
}

export interface RecentInvoice {
  invoiceId: string;
  vendorName: string;
  invoiceDate: string | null;
  amount: string | null;
  status: string;
}

export interface NeedsAttentionItem {
  kind: string;
  invoiceId: string;
  title: string;
  description: string;
}

export interface DashboardMetrics {
  totalProcessed: number;
  awaitingReview: number;
  approvedThisMonth: string;
  exceptionsPct: number;
  processingVolume: ProcessingVolumePoint[];
  statusBreakdown: Record<string, number>;
  recentInvoices: RecentInvoice[];
  needsAttention: NeedsAttentionItem[];
}

export async function getDashboardMetrics(): Promise<DashboardMetrics> {
  const response = await fetch(`${API_ORIGIN}/api/v1/dashboard/metrics`);
  if (!response.ok) throw new Error(`failed to load dashboard metrics: ${response.status}`);
  return response.json();
}
