export interface VendorSummary {
  vendorId: string;
  name: string;
  taxId: string | null;
  invoicesProcessed: number;
  isActive: boolean;
}

export async function listVendors(): Promise<VendorSummary[]> {
  const response = await fetch("/api/v1/vendors");
  if (!response.ok) throw new Error(`failed to load vendors: ${response.status}`);
  return response.json();
}
