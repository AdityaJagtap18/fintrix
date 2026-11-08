import { API_ORIGIN } from "./config";

export interface VendorSummary {
  vendorId: string;
  name: string;
  taxId: string | null;
  invoicesProcessed: number;
  isActive: boolean;
}

export async function listVendors(): Promise<VendorSummary[]> {
  const response = await fetch(`${API_ORIGIN}/api/v1/vendors`);
  if (!response.ok) throw new Error(`failed to load vendors: ${response.status}`);
  return response.json();
}
