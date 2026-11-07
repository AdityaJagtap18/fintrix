// Mirrors src/api/routers/preprocessing.py's Pydantic response models exactly,
// snake_case included — same convention as processingClient.ts's DTOs.
import { authHeader } from "./authClient";
import type { ProcessingResultDto } from "./processingClient";

export interface PreprocessReportDto {
  filename: string;
  page_count: number;
  content_hash: string;
  blank_page_numbers: number[];
  exceeds_page_limit: boolean;
  // #4: true when this exact content has already been extracted and is still within
  // the cache window — sending it for extraction will skip Document Intelligence.
  would_reuse_cache: boolean;
  // #5: heuristic invoice-number boundaries, [start_page, end_page], 1-indexed inclusive.
  suggested_splits: [number, number][];
  original_size_bytes: number;
  // #2/#3 applied: blank pages dropped, scan-like pages downsampled + grayscaled.
  optimized_size_bytes: number;
  // Set only for a file inside a zip batch that couldn't be read/was encrypted.
  error: string | null;
}

export interface CheckResponseDto {
  report: PreprocessReportDto;
  batch_id: string;
}

export interface BatchPreprocessSummaryDto {
  total_files: number;
  unreadable: number;
  total_pages: number;
  total_blank_pages: number;
  exceeds_page_limit_count: number;
  cache_hit_count: number;
  total_original_size_bytes: number;
  total_optimized_size_bytes: number;
}

export interface BatchPreprocessResponseDto {
  summary: BatchPreprocessSummaryDto;
  reports: PreprocessReportDto[];
  batch_id: string | null;
}

// One file's outcome from a send — mirrors api/routers/processing.py's
// BatchFileResultDto, reused as-is by the streaming send endpoint below.
export interface BatchFileResultDto {
  filename: string;
  status: "success" | "error";
  result: ProcessingResultDto | null;
  error: string | null;
}

export interface SendProgressEvent {
  type: "progress";
  completed: number;
  total: number;
  filename: string;
  status: "success" | "error";
}

interface SendDoneEvent {
  type: "done";
  results: BatchFileResultDto[];
}

type SendStreamLine = SendProgressEvent | SendDoneEvent;

export interface MetricsDto {
  files_checked: number;
  blank_pages_dropped: number;
  oversized_rejected: number;
  total_original_bytes: number;
  total_optimized_bytes: number;
  di_calls_made: number;
  di_calls_avoided: number;
  di_pages_billed: number;
  di_pages_avoided: number;
  cache_hit_rate: number;
  cost_per_page: number | null;
  estimated_cost_billed: number | null;
  estimated_cost_avoided: number | null;
  // Pipeline-level (extract_and_process) telemetry — one run per file actually sent for
  // real extraction, success or failure.
  pipeline_runs: number;
  pipeline_success_count: number;
  pipeline_error_count: number;
  avg_pipeline_latency_seconds: number;
  avg_field_completion_rate: number;
  avg_confidence_score: number;
  // The real routing outcome (decision != straight_through) — not a fixed confidence
  // cutoff. See api/observability.py's record_pipeline_run docstring.
  hitl_triggered_count: number;
  hitl_trigger_rate: number;
}

async function throwForErrorResponse(response: Response): Promise<never> {
  const body = await response.json().catch(() => ({ detail: "unknown error" }));
  throw new Error(
    typeof body.detail === "string" ? body.detail : `unexpected response: ${response.status}`,
  );
}

export async function checkOnePdf(file: File): Promise<CheckResponseDto> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/v1/preprocessing/check", {
    method: "POST",
    headers: authHeader(),
    body: formData,
  });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

export async function checkZipOfPdfs(file: File): Promise<BatchPreprocessResponseDto> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/api/v1/preprocessing/check/zip", {
    method: "POST",
    headers: authHeader(),
    body: formData,
  });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}

// The bridge into the real pipeline: every file batch_id points at — already optimized
// by /check or /check/zip — goes through the same extract_and_process POST /extract
// uses. A batch can only be sent once; the server pops it on send.
//
// The response is newline-delimited JSON, not a single JSON array: each file is a real
// network round-trip to Document Intelligence, so a multi-file batch has genuine
// per-file completion events to report as they happen, not just a final result. Pass
// onProgress to get a real "N of M done" callback as each file actually finishes.
export async function sendBatchForExtraction(
  batchId: string,
  onProgress?: (event: SendProgressEvent) => void,
): Promise<BatchFileResultDto[]> {
  const response = await fetch(`/api/v1/preprocessing/batches/${batchId}/send`, {
    method: "POST",
    headers: authHeader(),
  });
  if (!response.ok) return throwForErrorResponse(response);
  if (!response.body) return [];

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let results: BatchFileResultDto[] = [];

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const parsed = JSON.parse(line) as SendStreamLine;
      if (parsed.type === "progress") {
        onProgress?.(parsed);
      } else {
        results = parsed.results;
      }
    }
  }
  return results;
}

export async function fetchPreprocessingMetrics(): Promise<MetricsDto> {
  const response = await fetch("/api/v1/preprocessing/metrics", { headers: authHeader() });
  if (!response.ok) return throwForErrorResponse(response);
  return response.json();
}
