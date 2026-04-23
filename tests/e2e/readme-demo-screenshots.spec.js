// Screenshot capture against a pre-seeded demo database.
//
// Prereq: run `python scripts/seed_demo.py --db data/demo.db --force` first,
// then launch the webapp with `SEARCHER_DB_PATH=data/demo.db` so it loads
// the seeded data instead of your real DB.
//
// This spec skips CV upload + manual job insertion (already seeded) and
// focuses purely on navigation + screenshot capture, so results are
// deterministic and do not depend on any LLM API key.

const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const OUTPUT_DIR = path.join(process.cwd(), "screenshots", "readme");
const VIEWPORT = { width: 1440, height: 900 };

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function shot(page, file) {
  await page.screenshot({
    path: path.join(OUTPUT_DIR, file),
    fullPage: false,
  });
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(300);
}

test("demo dashboard screenshots (pre-seeded DB)", async ({ page }) => {
  test.setTimeout(120_000);
  ensureOutputDir();

  await page.setViewportSize(VIEWPORT);
  await page.goto("/");
  await expect(page.getByText("Job Finder")).toBeVisible();

  // Dashboard with populated recommendations + analytics
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await expect(page.locator("#view-dashboard")).toHaveClass(/is-active/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(600);
  await shot(page, "dashboard-recommendations-en.png");

  // Chat coach with pre-seeded conversation history
  await page.waitForTimeout(300);
  await shot(page, "chat-coach-en.png");

  // Dark mode snapshot
  await setTheme(page, "dark");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shot(page, "dashboard-dark-en.png");
  await setTheme(page, "light");
});
