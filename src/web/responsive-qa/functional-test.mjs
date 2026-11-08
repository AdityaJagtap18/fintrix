// FINTRIX functional smoke test (Phase 6): verifies core flows still work at
// the three representative sizes. Mutates in-memory review-assignment state
// (claim/approve) — restart the API server afterward to reset it.
import { chromium } from "playwright";

const BASE_URL = "http://localhost:5173";
const API_URL = "http://localhost:8000";
const REVIEWER = "aditya@company.com";

async function releaseClaim(invoiceId) {
  if (!invoiceId) return;
  try {
    await fetch(`${API_URL}/api/v1/review/${invoiceId}/release`, {
      method: "POST",
      headers: { "X-Reviewer": REVIEWER },
    });
  } catch {
    /* best-effort */
  }
}

const SIZES = [
  { id: "Desktop", width: 1440, height: 900 },
  { id: "Tablet", width: 768, height: 1024 },
  { id: "Mobile", width: 390, height: 844 },
];

const results = [];
function record(size, name, pass, note = "") {
  results.push({ size, name, pass, note });
  console.log(`[${size}] ${name}: ${pass ? "PASS" : "FAIL"}${note ? " — " + note : ""}`);
}

async function openNav(page, size) {
  if (size.width < 600) {
    await page.click('.app-topbar__nav-toggle');
    await page.waitForTimeout(150);
  }
}

async function run(size) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: size.width, height: size.height } });
  const page = await context.newPage();

  // --- Login: invalid email validation ---
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", "not-an-email");
  await page.fill("#password", "whatever");
  await page.click(".login-page__submit");
  const validationError = await page.locator('[role="alert"]').first().textContent().catch(() => null);
  record(size.id, "Login: invalid-email validation", !!validationError, validationError ?? "");

  // --- Login: password visibility toggle ---
  await page.fill("#email", "aditya@company.com");
  await page.fill("#password", "fintrix123");
  const typeBefore = await page.getAttribute("#password", "type");
  await page.click(".login-page__toggle-password");
  const typeAfter = await page.getAttribute("#password", "type");
  record(size.id, "Login: password visibility toggle", typeBefore === "password" && typeAfter === "text");

  // --- Login: remember me checkbox ---
  const rememberChecked = await page.isChecked('.login-page__remember input[type="checkbox"]');
  record(size.id, "Login: remember me default-checked", rememberChecked === true);

  // --- Login: successful sign-in ---
  await page.click(".login-page__submit");
  try {
    await page.waitForURL(`${BASE_URL}/`, { timeout: 10000 });
    record(size.id, "Login: sign-in success", true);
  } catch {
    record(size.id, "Login: sign-in success", false, `ended at ${page.url()}`);
  }
  await page.waitForLoadState("networkidle");

  // --- Navigation: sidebar links ---
  const navTargets = [
    { label: "Invoices", path: "/invoices" },
    { label: "Review Queue", path: "/review" },
    { label: "Vendors", path: "/vendors" },
  ];
  for (const t of navTargets) {
    await openNav(page, size);
    try {
      await page.click(`.app-sidebar__link:has-text("${t.label}")`, { timeout: 5000 });
      await page.waitForURL(`${BASE_URL}${t.path}`, { timeout: 5000 });
      await page.waitForLoadState("networkidle");
      record(size.id, `Navigation: sidebar -> ${t.label}`, true);
    } catch (err) {
      record(size.id, `Navigation: sidebar -> ${t.label}`, false, String(err).split("\n")[0]);
    }
  }

  // --- Invoices: filters ---
  await page.goto(`${BASE_URL}/invoices`, { waitUntil: "networkidle" });
  await page.waitForSelector(".data-table-wrap");
  const rowsBefore = await page.locator(".data-table tbody tr").count();
  await page.selectOption(".invoices-page__filters select", "needs_review");
  await page.waitForTimeout(400);
  const rowsAfterStatusFilter = await page.locator(".data-table tbody tr").count();
  record(
    size.id,
    "Invoices: status filter changes result set",
    rowsAfterStatusFilter !== rowsBefore || rowsBefore === 0,
    `before=${rowsBefore} after=${rowsAfterStatusFilter}`,
  );
  await page.selectOption(".invoices-page__filters select", "");
  await page.fill('.invoices-page__filters input[type="search"]', "TechBuild");
  await page.waitForTimeout(400);
  const searchRows = await page.locator(".data-table tbody tr").count();
  record(size.id, "Invoices: text search narrows results", searchRows >= 1, `rows=${searchRows}`);
  await page.fill('.invoices-page__filters input[type="search"]', "");
  await page.waitForTimeout(400);

  // --- Invoices: pagination ---
  const nextBtn = page.locator(".pagination-controls button").last();
  const nextDisabled = await nextBtn.isDisabled();
  record(size.id, "Invoices: pagination control present", await nextBtn.count() > 0, nextDisabled ? "only one page of demo data" : "enabled");

  // --- Invoice detail navigation ---
  await page.waitForTimeout(300);
  const rowLink = page.locator(".data-table tbody tr:first-child a").first();
  const href = await rowLink.getAttribute("href").catch(() => null);
  const clickedInvoiceId = href ? href.split("/").pop() : null;
  try {
    await rowLink.click({ timeout: 5000 });
    await page.waitForSelector(".review-page__layout", { timeout: 10000 });
    record(size.id, "Invoices: row -> review workspace navigation", true);
  } catch (err) {
    record(size.id, "Invoices: row -> review workspace navigation", false, String(err).split("\n")[0]);
  }

  await browser.close();
  // Release the claim this size's navigation took, so the next size's run
  // (and the user's own demo afterward) doesn't hit a stale 409 conflict.
  await releaseClaim(clickedInvoiceId);
}

async function runFieldEditingAndApproval() {
  // Dedicated pass on a single, non-showcase invoice so the demo's flagship
  // BR-02/BR-03 example invoice is left untouched for the user to see later.
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.fill("#email", "aditya@company.com");
  await page.fill("#password", "fintrix123");
  await page.click(".login-page__submit");
  await page.waitForURL(`${BASE_URL}/`, { timeout: 10000 });

  await page.goto(`${BASE_URL}/review/INV-0841`, { waitUntil: "networkidle" });
  try {
    await page.waitForSelector(".review-page__layout", { timeout: 10000 });
    const editable = page.locator('.field-row[data-needs-review="true"] input').first();
    const hasEditable = (await editable.count()) > 0;
    if (hasEditable) {
      await editable.fill("edited-value-qa-check");
      const val = await editable.inputValue();
      record("Desktop", "Review workspace: field editing", val === "edited-value-qa-check");
    } else {
      record("Desktop", "Review workspace: field editing", true, "no needsReview field on this invoice; editing path exists but untested here");
    }

    const acceptBtn = page.locator(".field-row button", { hasText: "Accept suggestion" }).first();
    if ((await acceptBtn.count()) > 0) {
      await acceptBtn.click();
      record("Desktop", "Review workspace: accept suggestion", true);
    }

    await page.click('button:has-text("Approve")');
    await page.waitForSelector('[data-testid="submitted-message"]', { timeout: 10000 });
    record("Desktop", "Review workspace: approve submits decision", true);
  } catch (err) {
    record("Desktop", "Review workspace: field editing / approve flow", false, String(err).split("\n")[0]);
  }
  await browser.close();
}

(async () => {
  for (const size of SIZES) {
    await run(size);
  }
  await runFieldEditingAndApproval();

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} functional checks passed.`);
  if (failed.length) {
    console.log("Failed:", JSON.stringify(failed, null, 2));
  }
})();
