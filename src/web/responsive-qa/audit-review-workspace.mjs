// Re-runs just the review-workspace route across all 17 viewports. The main
// audit.mjs run left every viewport after the first with a stale claim (the
// backend's claim is reviewer+invoice scoped and survives a closed browser
// context, since nothing ever calls /release), so every later viewport hit a
// 409 Conflict and only ever rendered the "already claimed" fallback, never
// the real review workspace layout. This releases the claim via the API
// before each navigation so the actual layout gets exercised everywhere.
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = "http://localhost:5173";
const API_URL = "http://localhost:8000";
const AUTH_STATE = path.join(__dirname, ".auth", "state.json");
const INVOICE_ID = "INV-2026-0842";
const REVIEWER = "aditya@company.com";

const VIEWPORTS = [
  { id: "D1", bucket: "desktop", width: 1280, height: 720 },
  { id: "D2", bucket: "desktop", width: 1366, height: 768 },
  { id: "D3", bucket: "desktop", width: 1440, height: 900 },
  { id: "D4", bucket: "desktop", width: 1512, height: 982 },
  { id: "D5", bucket: "desktop", width: 1920, height: 1080 },
  { id: "D6", bucket: "desktop", width: 2560, height: 1440 },
  { id: "T1", bucket: "tablet", width: 768, height: 1024 },
  { id: "T2", bucket: "tablet", width: 820, height: 1180 },
  { id: "T3", bucket: "tablet", width: 834, height: 1194 },
  { id: "T4", bucket: "tablet", width: 1024, height: 768 },
  { id: "T5", bucket: "tablet", width: 1032, height: 1376 },
  { id: "M1", bucket: "mobile", width: 360, height: 800 },
  { id: "M2", bucket: "mobile", width: 390, height: 844 },
  { id: "M3", bucket: "mobile", width: 393, height: 873 },
  { id: "M4", bucket: "mobile", width: 402, height: 874 },
  { id: "M5", bucket: "mobile", width: 412, height: 915 },
  { id: "M6", bucket: "mobile", width: 440, height: 956 },
];

async function releaseClaim() {
  try {
    await fetch(`${API_URL}/api/v1/review/${INVOICE_ID}/release`, {
      method: "POST",
      headers: { "X-Reviewer": REVIEWER },
    });
  } catch {
    /* best-effort: fine if it was never claimed */
  }
}

async function main() {
  const browser = await chromium.launch();
  const results = [];

  for (const vp of VIEWPORTS) {
    await releaseClaim();
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      storageState: AUTH_STATE,
    });
    const page = await context.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => pageErrors.push(String(err)));
    page.on("requestfailed", (req) =>
      failedRequests.push(`${req.method()} ${req.url()} :: ${req.failure()?.errorText ?? "unknown"}`),
    );

    let overflow = null;
    let navError = null;
    const base = `review-workspace-${vp.width}x${vp.height}`;
    const vpPath = path.join(__dirname, "screenshots", vp.bucket, `${base}-viewport.png`);
    const fullPath = path.join(__dirname, "screenshots", vp.bucket, `${base}-fullpage.png`);

    try {
      await page.goto(`${BASE_URL}/review/${INVOICE_ID}`, { waitUntil: "networkidle", timeout: 20000 });
      await page.waitForSelector(".review-page__layout", { timeout: 10000 });
      await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
      await page.waitForTimeout(300);
      overflow = await page.evaluate(() => {
        const docEl = document.documentElement;
        const body = document.body;
        const scrollWidth = Math.max(docEl.scrollWidth, body.scrollWidth);
        const clientWidth = docEl.clientWidth;
        return { scrollWidth, clientWidth, horizontalOverflow: scrollWidth - clientWidth > 2, overflowPx: scrollWidth - clientWidth };
      });
      await page.screenshot({ path: vpPath });
      await page.screenshot({ path: fullPath, fullPage: true });
    } catch (err) {
      navError = String(err);
    }

    await context.close();

    const flags = [];
    if (navError) flags.push("NAV-ERROR");
    if (overflow?.horizontalOverflow) flags.push(`OVERFLOW(+${overflow.overflowPx}px)`);
    if (consoleErrors.length) flags.push(`CONSOLE(${consoleErrors.length}): ${consoleErrors.join(" | ")}`);
    if (failedRequests.length) flags.push(`NET-FAIL(${failedRequests.length})`);
    console.log(`review-workspace @ ${vp.id} (${vp.width}x${vp.height})... ${flags.length ? flags.join(" ") : "ok"}`);

    results.push({
      route: "review-workspace",
      path: `/review/${INVOICE_ID}`,
      viewport: vp.id,
      bucket: vp.bucket,
      width: vp.width,
      height: vp.height,
      overflow,
      consoleErrors,
      pageErrors,
      failedRequests,
      screenshotViewport: path.relative(__dirname, vpPath),
      screenshotFull: path.relative(__dirname, fullPath),
      navError,
    });
  }

  await releaseClaim();
  await browser.close();

  // Merge into the main report, replacing the stale review-workspace entries.
  const reportPath = path.join(__dirname, "reports", "overflow-report.json");
  const existing = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  const filtered = existing.filter((r) => r.route !== "review-workspace");
  fs.writeFileSync(reportPath, JSON.stringify([...filtered, ...results], null, 2));
  console.log(`\nMerged ${results.length} corrected review-workspace results into ${reportPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
