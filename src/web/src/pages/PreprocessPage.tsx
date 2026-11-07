import { useState } from "react";
import type {
  BatchFileResultDto,
  BatchPreprocessResponseDto,
  PreprocessReportDto,
  SendProgressEvent,
} from "../api/preprocessingClient";
import { checkOnePdf, checkZipOfPdfs, sendBatchForExtraction } from "../api/preprocessingClient";

type LoadStatus = "idle" | "loading" | "error";
type SendStatus = "idle" | "sending" | "sent" | "error";

/**
 * The six things worth doing to a PDF before it's billed to Document Intelligence:
 * blank-page detection, DPI downsampling + grayscale (scan-like pages only — a
 * born-digital page is left untouched), a content-hash cache check, a heuristic
 * multi-invoice split suggestion, and a page-count sanity gate. Checking never calls
 * Document Intelligence; "Send for extraction" is the explicit action that hands the
 * *optimized* result to the real pipeline (POST /extract's own logic, reused as-is).
 *
 * The observability counters this page used to show live here now live on the Admin
 * Dashboard instead — one place for that, not two.
 */
export function PreprocessPage() {
  return (
    <div className="preprocess-page">
      <SingleCheckForm />
      <BatchCheckForm />
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function SizeDelta({ original, optimized }: { original: number; optimized: number }) {
  const saved = original - optimized;
  if (saved <= 0) {
    return (
      <span>
        {formatBytes(original)} → {formatBytes(optimized)} (nothing to shrink — no scan-like
        pages)
      </span>
    );
  }
  const pct = Math.round((saved / original) * 100);
  return (
    <span>
      {formatBytes(original)} → {formatBytes(optimized)} ({pct}% smaller)
    </span>
  );
}

function ExtractionResults({ results }: { results: BatchFileResultDto[] }) {
  return (
    <div role="list" aria-label="Extraction results" className="preprocess-page__list">
      {results.map((r) =>
        r.status === "error" || !r.result ? (
          <article
            key={r.filename}
            role="listitem"
            className="preprocess-card preprocess-card--reject"
          >
            <header className="preprocess-card__header">
              <h4>{r.filename}</h4>
              <span className="decision-badge decision-badge--exception">failed</span>
            </header>
            <p role="alert">{r.error}</p>
          </article>
        ) : (
          <article key={r.filename} role="listitem" className="preprocess-card">
            <header className="preprocess-card__header">
              <h4>
                {r.result.vendor_name ?? "Unknown vendor"} — {r.result.invoice_number ?? "no number"}
              </h4>
              <span className={`decision-badge decision-badge--${r.result.decision}`}>
                {r.result.decision.replace(/_/g, " ")}
              </span>
            </header>
            <p>
              {r.result.extraction_method === "cache_reuse"
                ? "Reused a prior extraction — Document Intelligence was not called."
                : "Extracted fresh via Document Intelligence."}
            </p>
          </article>
        ),
      )}
    </div>
  );
}

function SendButton({ batchId }: { batchId: string }) {
  const [status, setStatus] = useState<SendStatus>("idle");
  const [progress, setProgress] = useState<{ completed: number; total: number } | null>(null);
  const [results, setResults] = useState<BatchFileResultDto[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleSend() {
    setStatus("sending");
    setError(null);
    setProgress(null);
    try {
      const sent = await sendBatchForExtraction(batchId, (event: SendProgressEvent) => {
        setProgress({ completed: event.completed, total: event.total });
      });
      setResults(sent);
      setStatus("sent");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send this batch.");
      setStatus("error");
    }
  }

  if (status === "sent") {
    return (
      <div data-testid="send-results">
        <p>Sent — {results.length} invoice(s) went through the real pipeline.</p>
        <ExtractionResults results={results} />
      </div>
    );
  }

  return (
    <div className="preprocess-send">
      <button type="button" className="btn-primary" onClick={handleSend} disabled={status === "sending"}>
        {status === "sending" ? "Sending…" : "Send for extraction"}
      </button>
      {status === "sending" && progress && (
        <p role="status" data-testid="send-progress">
          {progress.completed} of {progress.total} done
        </p>
      )}
      {error && (
        <p role="alert" data-testid="send-error">
          {error}
        </p>
      )}
    </div>
  );
}

function SingleCheckForm() {
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [report, setReport] = useState<PreprocessReportDto | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatus("loading");
    setError(null);
    setReport(null);
    setBatchId(null);
    try {
      const { report: result, batch_id } = await checkOnePdf(file);
      setReport(result);
      setBatchId(batch_id);
      setStatus("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check this file.");
      setStatus("error");
    } finally {
      e.target.value = "";
    }
  }

  return (
    <section className="extract-form">
      <h2>Check a single PDF</h2>
      <label htmlFor="preprocess-single-file">
        Choose a PDF
        <input
          id="preprocess-single-file"
          type="file"
          accept="application/pdf"
          onChange={handleFileChange}
          disabled={status === "loading"}
        />
      </label>
      {status === "loading" && (
        <p role="status" data-testid="single-check-status">
          Checking…
        </p>
      )}
      {error && (
        <p role="alert" data-testid="single-check-error">
          {error}
        </p>
      )}
      {report && (
        <>
          <p role="status" data-testid="single-check-complete">
            Preprocessing complete.
          </p>
          <ReportCard report={report} />
          {batchId && <SendButton batchId={batchId} />}
        </>
      )}
    </section>
  );
}

function BatchCheckForm() {
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [batch, setBatch] = useState<BatchPreprocessResponseDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setStatus("loading");
    setError(null);
    setBatch(null);
    try {
      const result = await checkZipOfPdfs(file);
      setBatch(result);
      setStatus("idle");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check this zip.");
      setStatus("error");
    } finally {
      e.target.value = "";
    }
  }

  return (
    <section className="extract-form">
      <h2>Check a zip of PDFs</h2>
      <p className="extract-form__notice">
        Every PDF inside the zip gets its own check — one unreadable file doesn't block
        the rest.
      </p>
      <label htmlFor="preprocess-zip-file">
        Choose a .zip
        <input
          id="preprocess-zip-file"
          type="file"
          accept=".zip,application/zip"
          onChange={handleFileChange}
          disabled={status === "loading"}
        />
      </label>
      {status === "loading" && (
        <p role="status" data-testid="batch-check-status">
          Checking every file in the zip…
        </p>
      )}
      {error && (
        <p role="alert" data-testid="batch-check-error">
          {error}
        </p>
      )}
      {batch && <BatchTracker batch={batch} />}
    </section>
  );
}

function BatchTracker({ batch }: { batch: BatchPreprocessResponseDto }) {
  const { summary, reports, batch_id } = batch;

  if (summary.total_files === 0) {
    return (
      <p data-testid="batch-tracker-empty">No PDFs found in this zip — nothing to check.</p>
    );
  }

  return (
    <div data-testid="batch-tracker">
      <p role="status" data-testid="batch-check-complete">
        Preprocessing complete — checked {summary.total_files} file(s).
      </p>
      <dl className="preprocess-tracker">
        <div>
          <dt>Total files</dt>
          <dd>{summary.total_files}</dd>
        </div>
        <div className="preprocess-tracker__stat--reject">
          <dt>Unreadable</dt>
          <dd>{summary.unreadable}</dd>
        </div>
        <div className="preprocess-tracker__stat--flagged">
          <dt>Blank pages found</dt>
          <dd>{summary.total_blank_pages}</dd>
        </div>
        <div className="preprocess-tracker__stat--flagged">
          <dt>Over the page limit</dt>
          <dd>{summary.exceeds_page_limit_count}</dd>
        </div>
        <div className="preprocess-tracker__stat--clean">
          <dt>Cache hits</dt>
          <dd>{summary.cache_hit_count}</dd>
        </div>
      </dl>

      <p className="preprocess-tracker__size">
        <SizeDelta
          original={summary.total_original_size_bytes}
          optimized={summary.total_optimized_size_bytes}
        />
      </p>

      {batch_id && <SendButton batchId={batch_id} />}

      <div role="list" aria-label="Per-file preprocessing reports" className="preprocess-page__list">
        {reports.map((report) => (
          <ReportCard key={report.filename} report={report} />
        ))}
      </div>
    </div>
  );
}

function ReportCard({ report }: { report: PreprocessReportDto }) {
  if (report.error) {
    return (
      <article
        role="listitem"
        className="preprocess-card preprocess-card--reject"
        data-testid={`report-${report.filename}`}
      >
        <header className="preprocess-card__header">
          <h3>{report.filename}</h3>
          <span className="decision-badge decision-badge--exception">unreadable</span>
        </header>
        <p role="alert">{report.error}</p>
      </article>
    );
  }

  return (
    <article
      role="listitem"
      className="preprocess-card"
      data-testid={`report-${report.filename}`}
    >
      <header className="preprocess-card__header">
        <h3>{report.filename}</h3>
        {report.would_reuse_cache && (
          <span className="decision-badge decision-badge--approved">cache hit</span>
        )}
      </header>

      <p className="preprocess-card__page-count">{report.page_count} page(s) checked</p>

      <ul className="preprocess-checklist" aria-label={`Checks for ${report.filename}`}>
        <CheckItem
          passed={!report.exceeds_page_limit}
          passLabel="Within the page limit"
          failLabel="Exceeds the page-count limit for a single invoice"
        />
        <CheckItem
          passed={report.blank_page_numbers.length === 0}
          passLabel="No blank pages"
          failLabel={`Blank page(s), dropped before sending: ${report.blank_page_numbers.join(", ")}`}
        />
      </ul>

      <p className="preprocess-card__size">
        <SizeDelta original={report.original_size_bytes} optimized={report.optimized_size_bytes} />
      </p>

      {report.suggested_splits.length > 1 && (
        <p data-testid={`splits-${report.filename}`}>
          Looks like {report.suggested_splits.length} separate invoices in this file:{" "}
          {report.suggested_splits.map(([start, end]) => `pages ${start}-${end}`).join(", ")}
        </p>
      )}
    </article>
  );
}

function CheckItem({
  passed,
  passLabel,
  failLabel,
}: {
  passed: boolean;
  passLabel: string;
  failLabel: string;
}) {
  return (
    <li className={passed ? "preprocess-check preprocess-check--pass" : "preprocess-check preprocess-check--fail"}>
      <span aria-hidden="true">{passed ? "✓" : "⚠"}</span> {passed ? passLabel : failLabel}
    </li>
  );
}
