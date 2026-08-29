import { useState } from "react";
import type { ProcessingResultDto } from "../api/processingClient";
import { extractInvoice } from "../api/processingClient";

interface ExtractInvoiceFormProps {
  onExtracted: (result: ProcessingResultDto, fileUrl: string) => void;
}

type Status = "idle" | "extracting" | "error";

/** Real OCR: picks a PDF, sends its actual bytes to Azure Document Intelligence via
 * POST /api/v1/processing/extract, and shows whatever comes back. No field is typed in. */
export function ExtractInvoiceForm({ onExtracted }: ExtractInvoiceFormProps) {
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatus("extracting");
    setError(null);
    try {
      const result = await extractInvoice(file);
      onExtracted(result, URL.createObjectURL(file));
      setStatus("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not extract this invoice.");
      setStatus("error");
    } finally {
      e.target.value = ""; // let the same file be picked again
    }
  }

  return (
    <section className="extract-form">
      <h2>Upload an invoice</h2>
      <p className="extract-form__notice">
        Sends the file to Azure Document Intelligence for real OCR — every field below
        comes from the document itself, nothing is typed in.
      </p>
      <label htmlFor="extract-file">
        Choose a PDF
        <input
          id="extract-file"
          type="file"
          accept="application/pdf"
          onChange={handleFileChange}
          disabled={status === "extracting"}
        />
      </label>
      {status === "extracting" && (
        <p role="status" data-testid="extracting-status">
          Extracting via Azure Document Intelligence…
        </p>
      )}
      {status === "error" && (
        <p role="alert" data-testid="extract-error">
          {error}
        </p>
      )}
    </section>
  );
}
