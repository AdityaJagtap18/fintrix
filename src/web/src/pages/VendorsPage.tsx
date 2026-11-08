import { useEffect, useState } from "react";
import { listVendors, type VendorSummary } from "../api/vendorsClient";

export function VendorsPage() {
  const [vendors, setVendors] = useState<VendorSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listVendors()
      .then((v) => !cancelled && setVendors(v))
      .catch(() => !cancelled && setError("Could not load vendors."));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="vendors-page">
      <h1>Vendors</h1>
      <p>Vendor master records and processing history.</p>
      {error && <p role="alert">{error}</p>}
      <table className="data-table">
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Tax ID</th>
            <th>Invoices processed</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {vendors.map((v) => (
            <tr key={v.vendorId}>
              <td>{v.name}</td>
              <td>{v.taxId ?? "—"}</td>
              <td>{v.invoicesProcessed}</td>
              <td>{v.isActive ? "Active" : "Inactive"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
