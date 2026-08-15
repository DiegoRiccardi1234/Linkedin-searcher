const { test, expect } = require("@playwright/test");

// Smoke suite kept resilient to UI copy/redesign: it asserts structural
// behavior (shell loads, every nav tab activates its view, key API contracts
// exist, chat input does not crash) rather than brittle visible-text matches.

const VIEWS = ["dashboard", "job-search", "jobs", "mail", "profile", "settings", "info"];

test("shell loads and every nav tab activates its view", async ({ page }) => {
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(String(err)));

  await page.goto("/");
  await expect(page.locator("header.topbar .brand")).toBeVisible();
  await expect(page.locator(".nav-link[data-view='dashboard']")).toHaveClass(/is-active/);

  for (const view of VIEWS) {
    await page.locator(`.topnav .nav-link[data-view='${view}']`).click();
    await expect(page.locator(`#view-${view}`)).toHaveClass(/is-active/);
  }

  // The AI Career Coach right rail shows on the dashboard view.
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await expect(page.locator("#chatForm")).toBeVisible();

  expect(consoleErrors, `console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
});

test("settings exposes provider cards and key-status contract", async ({ page }) => {
  await page.goto("/");
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);
  await expect(page.locator("#providerCards")).toHaveCount(1);

  const keysStatus = await page.evaluate(async () => {
    const response = await fetch("/api/providers/keys/status");
    const payload = await response.json();
    return payload.keys || {};
  });

  for (const flag of [
    "cerebras_configured",
    "groq_configured",
    "openai_configured",
    "anthropic_configured",
    "google_configured",
    "openrouter_configured",
  ]) {
    expect(Object.prototype.hasOwnProperty.call(keysStatus, flag)).toBeTruthy();
  }
});

test("chat input appends the user message without crashing", async ({ page }) => {
  await page.goto("/");
  const input = page.locator("#chatInput");
  await input.fill("consigliami i top lavori oggi");
  await page.locator("#chatForm button[type='submit']").click();

  // Regardless of whether an LLM key is configured (assistant reply vs.
  // graceful fallback/error), the user's own message must render in the box.
  await expect(page.locator("#chatBox")).toContainText("consigliami i top lavori oggi");
});
