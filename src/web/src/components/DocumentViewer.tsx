import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorkerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import { useEffect, useRef, useState } from "react";
import { polygonToOverlayRect } from "../lib/geometry";
import type { ExtractedField } from "../types";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl;

// Rendered at 1.5x so text is legible on a normal display — this is the scale
// `polygonToOverlayRect` needs to place overlays correctly; changing it means every
// overlay moves with it automatically (the math is scale-aware), nothing else to update.
const RENDER_SCALE = 1.5;

interface DocumentViewerProps {
  pdfUrl: string;
  fields: ExtractedField[];
  selectedFieldName: string | null;
}

/**
 * Renders the invoice PDF and draws each field's bounding region over it (FR-063,
 * AC-02.2: selecting a field scrolls to its page and highlights its polygon).
 */
export function DocumentViewer({ pdfUrl, fields, selectedFieldName }: DocumentViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const selectedField = fields.find((field) => field.name === selectedFieldName) ?? null;

  // AC-02.2: selecting a field jumps the viewer to that field's page.
  useEffect(() => {
    if (selectedField?.page) {
      setCurrentPage(selectedField.page);
    }
  }, [selectedField]);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      try {
        const doc = await pdfjsLib.getDocument(pdfUrl).promise;
        if (cancelled) return;
        setPageCount(doc.numPages);

        const page = await doc.getPage(currentPage);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: RENDER_SCALE });

        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const context = canvas.getContext("2d");
        if (!context) return;

        await page.render({ canvasContext: context, viewport }).promise;
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "failed to render document");
        }
      }
    }

    void render();
    return () => {
      cancelled = true;
    };
  }, [pdfUrl, currentPage]);

  if (error) {
    return (
      <div role="alert" className="document-viewer document-viewer--error">
        Could not render document: {error}
      </div>
    );
  }

  const pageFields = fields.filter((field) => field.page === currentPage && field.region);

  return (
    <div className="document-viewer">
      <div className="document-viewer__toolbar">
        <button type="button" disabled={currentPage <= 1} onClick={() => setCurrentPage((p) => p - 1)}>
          Previous page
        </button>
        <span>
          Page {currentPage} of {pageCount}
        </span>
        <button
          type="button"
          disabled={currentPage >= pageCount}
          onClick={() => setCurrentPage((p) => p + 1)}
        >
          Next page
        </button>
      </div>
      <div className="document-viewer__canvas-wrap" style={{ position: "relative" }}>
        <canvas ref={canvasRef} data-testid="pdf-canvas" />
        {pageFields.map((field) => {
          // field.region is guaranteed non-null by the filter above.
          const rect = polygonToOverlayRect(field.region!.polygon, RENDER_SCALE);
          const isSelected = field.name === selectedFieldName;
          return (
            <div
              key={field.name}
              data-testid={`overlay-${field.name}`}
              className={`field-overlay${isSelected ? " field-overlay--selected" : ""}${field.needsReview ? " field-overlay--needs-review" : ""}`}
              style={{
                position: "absolute",
                left: rect.left,
                top: rect.top,
                width: rect.width,
                height: rect.height,
                pointerEvents: "none",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
