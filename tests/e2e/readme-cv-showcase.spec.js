const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const OUTPUT_DIR = path.join(process.cwd(), "screenshots", "readme");
const CV_DIR = path.join(process.cwd(), "Test-Mio-CV");
const VIEWPORT = { width: 1440, height: 900 };

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function shot(page, file) {
  // Viewport-only (no fullPage) so images stay readable and small
  await page.screenshot({
    path: path.join(OUTPUT_DIR, file),
    fullPage: false,
  });
}

async function uploadCv(page, filePath) {
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);

  await page.setInputFiles("#cvFile", filePath);
  await page.locator("#cvForm button[type='submit']").click();
  await expect(page.locator("#cvSummary")).toContainText("profile_id", { timeout: 120000 });
}

async function addManualJobs(page) {
  const jobs = [
    {
      titolo: "Senior Backend Python Engineer",
      azienda: "Alpine Data Labs",
      sede: "Remote - Europe",
      link: "https://example.com/jobs/backend-python",
      descrizione:
        "Build FastAPI services, integrate multiple LLM providers, optimize SQLite/Postgres data pipelines.",
    },
    {
      titolo: "AI Product Engineer",
      azienda: "NextWave Talent",
      sede: "Milan, Italy",
      link: "https://example.com/jobs/ai-product",
      descrizione:
        "Ship user-facing AI features, evaluate prompts, monitor output quality and drive UX improvements.",
    },
    {
      titolo: "Data & Automation Specialist",
      azienda: "Nordic Operations",
      sede: "Hybrid - Turin",
      link: "https://example.com/jobs/data-automation",
      descrizione:
        "Automate workflows, scrape and ingest listings, score applications and deliver operational reporting.",
    },
  ];

  for (const payload of jobs) {
    const result = await page.evaluate(async (jobPayload) => {
      const response = await fetch("/api/jobs/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(jobPayload),
      });
      return { ok: response.ok, text: await response.text() };
    }, payload);

    if (!result.ok) {
      throw new Error(`Manual job insert failed: ${result.text}`);
    }
  }
}

async function captureDashboard(page, label) {
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await expect(page.locator("#view-dashboard")).toHaveClass(/is-active/);

  const recPayload = await page.evaluate(async () => {
    const r = await fetch("/api/recommendations?limit=5");
    return await r.json();
  });
  const jobs = recPayload.jobs || [];
  if (!jobs.length) {
    throw new Error("No recommendations available after profile setup.");
  }

  fs.writeFileSync(
    path.join(OUTPUT_DIR, `recommended-${label}.json`),
    JSON.stringify(jobs, null, 2),
    "utf-8",
  );

  // Dashboard hero with recommendations grid
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shot(page, `dashboard-recommendations-${label}.png`);
}

async function captureChat(page, label, prompt) {
  // Chat lives in the dashboard right rail; send a seed message so the screenshot
  // shows a real conversation rather than an empty box.
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await expect(page.locator("#view-dashboard")).toHaveClass(/is-active/);

  await page.locator("#chatInput").fill(prompt);
  await page.locator("#chatForm button[type='submit']").click();
  // Wait until at least one assistant bubble appears
  await expect(page.locator("#chatBox .chat-item.assistant").first()).toBeVisible({ timeout: 60000 });
  await page.waitForTimeout(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, `chat-coach-${label}.png`);
}

async function setTheme(page, theme) {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(300);
}

async function captureDarkMode(page, label) {
  await setTheme(page, "dark");
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  await shot(page, `dashboard-dark-${label}.png`);
  await setTheme(page, "light");
}

test("portfolio screenshots for README (IT + EN)", async ({ page }) => {
  test.setTimeout(240000);
  ensureOutputDir();

  const cvIt = path.join(CV_DIR, "CV_Diego_Riccardi_IT.pdf");
  const cvEn = path.join(CV_DIR, "CV_Diego_Riccardi_EN.pdf");

  if (!fs.existsSync(cvIt) || !fs.existsSync(cvEn)) {
    throw new Error("Missing CVs in Test-Mio-CV folder.");
  }

  await page.setViewportSize(VIEWPORT);
  await page.goto("/");
  await expect(page.getByText("Job Finder")).toBeVisible();

  await uploadCv(page, cvEn);
  await addManualJobs(page);
  await captureDashboard(page, "en");
  await captureChat(page, "en", "Which roles best fit my CV?");
  await captureDarkMode(page, "en");
});
