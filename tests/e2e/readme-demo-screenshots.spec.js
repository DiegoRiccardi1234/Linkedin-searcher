// Screenshot capture against a pre-seeded demo database.
//
// Prereq: run `python scripts/seed_demo.py --db data/demo.db --force` first,
// then launch the webapp with `SEARCHER_DB_PATH=data/demo.db` so it loads
// the seeded data instead of your real DB.
//
// Produces a varied set of README screenshots: hero, recommendations grid,
// job archive (table + kanban), settings, chat coach close-up, and dark mode.

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

async function shotViewport(page, file) {
  await page.screenshot({
    path: path.join(OUTPUT_DIR, file),
    fullPage: false,
  });
}

async function shotElement(page, selector, file) {
  const el = page.locator(selector).first();
  await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await el.screenshot({ path: path.join(OUTPUT_DIR, file) });
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(300);
}

async function scrollTo(page, selector) {
  await page.locator(selector).first().scrollIntoViewIfNeeded();
  await page.waitForTimeout(400);
}

test("demo screenshots (pre-seeded DB)", async ({ page }) => {
  test.setTimeout(180_000);
  ensureOutputDir();

  await page.setViewportSize(VIEWPORT);
  await page.addInitScript(() => {
    localStorage.setItem("tutorialSeen", "1");
  });
  await page.goto("/");
  await expect(page.locator(".brand")).toBeVisible();
  await page.evaluate(() => {
    document.querySelectorAll(".tutorial-overlay").forEach((el) => el.remove());
  });
  await page.waitForTimeout(300);

  // 1. Dashboard hero + analytics (top of page)
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shotViewport(page, "dashboard-recommendations-en.png");

  // 2. Recommendations grid close-up
  await scrollTo(page, ".recommendations-section");
  await shotElement(page, ".recommendations-section", "recommendations-grid-en.png");

  // 3. Job archive - table view (scroll + capture viewport to get filters + rows)
  await scrollTo(page, ".jobs-section");
  await shotViewport(page, "jobs-table-en.png");

  // 4. Job archive - kanban view
  await page.locator("#viewKanbanBtn").click();
  await page.waitForTimeout(500);
  await scrollTo(page, "#kanbanView");
  await shotViewport(page, "jobs-kanban-en.png");
  await page.locator("#viewTableBtn").click();
  await page.waitForTimeout(300);

  // 5. Chat coach close-up (the right rail with full conversation history)
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(300);
  await shotElement(page, "#view-dashboard .right-rail, #view-dashboard aside, #chatBox", "chat-coach-en.png").catch(async () => {
    // Fallback: crop right half of viewport
    await page.screenshot({
      path: path.join(OUTPUT_DIR, "chat-coach-en.png"),
      clip: { x: 900, y: 0, width: 540, height: 900 },
    });
  });

  // 6. Settings view
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shotViewport(page, "settings-en.png");

  // 7. Dark mode - dashboard
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await setTheme(page, "dark");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shotViewport(page, "dashboard-dark-en.png");

  // 8. Dark mode - jobs table
  await scrollTo(page, ".jobs-section");
  await shotViewport(page, "jobs-table-dark-en.png");

  await setTheme(page, "light");
});
