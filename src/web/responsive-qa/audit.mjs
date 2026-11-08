// FINTRIX responsive QA audit script.
// Launches Chromium via Playwright, visits every existing route at every
// target viewport, and records overflow/console/network defects plus
// screenshots as evidence. Run with: node responsive-qa/audit.mjs
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASE_URL = "http://localhost:5173";
const OUT_DIR = __dirname;
const AUTH_STATE = path.join(OUT_DIR, ".auth", "state.json");

const VIEWPORTS = [
  { id: "D1", bucket: "desktop", width: 1280, height: 720, label: "Small laptop" },
  { id: "D2", bucket: "desktop", width: 1366, height: 768, label: "Standard laptop" },
  { id: "D3", bucket: "desktop", width: 1440, height: 900, label: "Primary design reference" },
  { id: "D4", bucket: "desktop", width: 1512, height: 982, label: "Large laptop" },
  { id: "D5", bucket: "desktop", width: 1920, height: 1080, label: "Full HD desktop" },
  { id: "D6", bucket: "desktop", width: 2560, height: 1440, label: "Large desktop" },
  { id: "T1", bucket: "tablet", width: 768, height: 1024, label: "Tablet portrait" },
  { id: "T2", bucket: "tablet", width: 820, height: 1180, label: "Standard tablet" },
  { id: "T3", bucket: "tablet", width: 834, height: 1194, label: "Tablet Pro portrait" },
  { id: "T4", bucket: "tablet", width: 1024, height: 768, label: "Tablet landscape" },
  { id: "T5", bucket: "tablet", width: 1032, height: 1376, label: "Large tablet" },
  { id: "M1", bucket: "mobile", width: 360, height: 800, label: "Small Android" },
  { id: "M2", bucket: "mobile", width: 390, height: 844, label: "Standard iPhone" },
  { id: "M3", bucket: "mobile", width: 393, height: 873, label: "Large phone" },
  { id: "M4", bucket: "mobile", width: 402, height: 874, label: "iPhone Pro" },
  { id: "M5", bucket: "mobile", width: 412, height: 915, label: "Large Android" },
  { id: "M6", bucket: "mobile", width: 440, height: 956, label: "Large phone" },
];

// Only routes that actually exist in src/web/src/App.tsx.
const ROUTES = [
  { key: "login", path: "/login", auth: false },
  { key: "dashboard", path: "/", auth: true },
  { key: "invoices", path: "/invoices", auth: true },
  { key: "review-queue", path: "/review", auth: true },
  { key: "review-workspace", path: "/review/INV-2026-0842", auth: true },
  { key: "vendors", path: "/vendors", auth: true },
  { key: "analytics", path: "/analytics", auth: true },
  { key: "settings", path: "/settings", auth: true },
];

async function ensureAuthState(browser) {
  if (fs.existsSync(AUTH_STATE)) return;
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", "aditya@company.com");
  await page.fill("#password", "fintrix123");
  await page.click(".login-page__submit");
  await page.waitForURL(`${BASE_URL}/`, { timeout: 10000 });
  await page.waitForLoadState("networkidle");
  await context.storageState({ path: AUTH_STATE });
  await context.close();
}

function filenameBase(routeKey, vp) {
  return `${routeKey}-${vp.width}x${vp.height}`;
}

async function auditRoute(browser, route, vp, results) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    storageState: route.auth ? AUTH_STATE : undefined,
  });
  const page = await context.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("requestfailed", (req) => {
    failedRequests.push(`${req.method()} ${req.url()} :: ${req.failure()?.errorText ?? "unknown"}`);
  });

  let overflow = null;
  let screenshotViewport = null;
  let screenshotFull = null;
  let navError = null;

  try {
    await page.goto(`${BASE_URL}${route.path}`, { waitUntil: "networkidle", timeout: 20000 });
    await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
    await page.waitForTimeout(250);

    overflow = await page.evaluate(() => {
      const docEl = document.documentElement;
      const body = document.body;
      const scrollWidth = Math.max(docEl.scrollWidth, body.scrollWidth);
      const clientWidth = docEl.clientWidth;
      return {
        scrollWidth,
        clientWidth,
        horizontalOverflow: scrollWidth - clientWidth > 2,
        overflowPx: scrollWidth - clientWidth,
      };
    });

    const base = filenameBase(route.key, vp);
    const vpPath = path.join(OUT_DIR, "screenshots", vp.bucket, `${base}-viewport.png`);
    const fullPath = path.join(OUT_DIR, "screenshots", vp.bucket, `${base}-fullpage.png`);
    await page.screenshot({ path: vpPath });
    await page.screenshot({ path: fullPath, fullPage: true });
    screenshotViewport = path.relative(OUT_DIR, vpPath);
    screenshotFull = path.relative(OUT_DIR, fullPath);
  } catch (err) {
    navError = String(err);
  }

  await context.close();

  results.push({
    route: route.key,
    path: route.path,
    viewport: vp.id,
    bucket: vp.bucket,
    width: vp.width,
    height: vp.height,
    label: vp.label,
    overflow,
    consoleErrors,
    pageErrors,
    failedRequests,
    screenshotViewport,
    screenshotFull,
    navError,
  });
}

async function main() {
  const browser = await chromium.launch();
  await ensureAuthState(browser);

  const results = [];
  for (const route of ROUTES) {
    for (const vp of VIEWPORTS) {
      process.stdout.write(`Testing ${route.key} @ ${vp.id} (${vp.width}x${vp.height})... `);
      await auditRoute(browser, route, vp, results);
      const last = results[results.length - 1];
      const flags = [];
      if (last.navError) flags.push("NAV-ERROR");
      if (last.overflow?.horizontalOverflow) flags.push(`OVERFLOW(+${last.overflow.overflowPx}px)`);
      if (last.consoleErrors.length) flags.push(`CONSOLE(${last.consoleErrors.length})`);
      if (last.failedRequests.length) flags.push(`NET-FAIL(${last.failedRequests.length})`);
      console.log(flags.length ? flags.join(" ") : "ok");
    }
  }

  await browser.close();

  const reportPath = path.join(OUT_DIR, "reports", "overflow-report.json");
  fs.writeFileSync(reportPath, JSON.stringify(results, null, 2));
  console.log(`\nWrote ${results.length} results to ${reportPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
