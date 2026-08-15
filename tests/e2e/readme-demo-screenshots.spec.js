// Screenshot capture against a pre-seeded demo database.
//
// Prereq, and it matters: seed with `python scripts/seed_demo.py --force`
// (which now defaults to data/demo.db, not the real archive), then start the app
// yourself on PORT 8123 with SEARCHER_DB_PATH=data/demo.db. Playwright's config
// reuses an existing server on that port; if you let it start its own, these
// shots are taken against the development database instead of the demo one.
//
// Produces every image the README embeds. The v2.0.0 surfaces — the archive
// buckets, the Mail tab and its review queue, the provider panels, the readiness
// strips — were added because the README was still showing v1.7 with no way to
// regenerate half of what it embedded.

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

test("demo screenshots (pre-seeded DB)", async ({ page }) => {
  test.setTimeout(120_000);
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

  // 1. Dashboard (light)
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await shot(page, "dashboard-en.png");

  // 2. Job Search (flat layout): profile-derived role chips + scan form.
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(700);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "job-search-en.png");

  // 3. Chat view — inject a conversation so bubbles are visible
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => {
    const box = document.getElementById("chatBox");
    if (!box) return;
    box.innerHTML = "";
    const mk = (role, html, extra = "") => {
      const div = document.createElement("div");
      div.className = `chat-item ${role}`;
      div.innerHTML = `<div class="role">${role === "user" ? "You" : "AI Coach"}</div><div class="bubble">${html}</div>${extra}`;
      box.appendChild(div);
    };
    mk("user", "I'm a Python developer with 3 years of experience. What roles should I target?");
    mk("assistant", "Based on your CV I see strong <b>backend</b> and <b>data engineering</b> skills. I'd target these roles first:", `
      <div class="role-pill-row">
        <button class="role-pill">Backend Engineer (Python)</button>
        <button class="role-pill">Data Engineer</button>
        <button class="role-pill">ML Engineer</button>
        <button class="role-pill">Platform Engineer</button>
      </div>`);
    mk("user", "Focus on Milan, remote ok. Find jobs matching my profile.");
    mk("assistant", "Great — I'll search for <b>Backend Engineer</b> and <b>Data Engineer</b> in <b>Milan</b> plus remote. Launching scan now... you'll see live progress in the overlay.");
    box.scrollTop = box.scrollHeight;
  });
  await page.waitForTimeout(400);
  await shot(page, "chat-view-en.png");

  // 4. Scan progress feed — show the overlay + feed mid-analysis
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    const fill = document.getElementById("scanProgressFill");
    const text = document.getElementById("scanProgressText");
    const feed = document.getElementById("scanFeed");
    if (!overlay || !feed) return;
    overlay.style.display = "flex";
    fill.style.width = "62%";
    text.textContent = "Analyzed: Senior Data Engineer @ Satispay — Score 8/10";
    const rows = [
      { icon: "travel_explore", text: "Searching for: <b>Backend Engineer, Data Engineer</b>", chip: null },
      { icon: "manage_search", text: "Found 24 ads on <b>LinkedIn</b>", chip: null },
      { icon: "manage_search", text: "Found 18 ads on <b>Indeed</b>", chip: null },
      { icon: "check_circle", text: "<b>Backend Python Engineer</b> @ Nexi", chip: { label: "7/10", cls: "score-high" } },
      { icon: "check_circle", text: "<b>Platform Engineer</b> @ Bending Spoons", chip: { label: "6/10", cls: "score-mid" } },
      { icon: "check_circle", text: "<b>Senior Data Engineer</b> @ Satispay", chip: { label: "8/10", cls: "score-high" } },
      { icon: "check_circle", text: "<b>Junior Developer</b> @ StartupX", chip: { label: "3/10", cls: "score-low" } },
      { icon: "check_circle", text: "<b>ML Engineer</b> @ Prima", chip: { label: "7/10", cls: "score-high" } },
    ];
    feed.innerHTML = "";
    rows.forEach((r) => {
      const li = document.createElement("li");
      const chipHtml = r.chip ? `<span class="feed-chip ${r.chip.cls}">${r.chip.label}</span>` : "";
      li.innerHTML = `<span class="material-symbols-outlined feed-icon">${r.icon}</span><span class="feed-text">${r.text}</span>${chipHtml}`;
      feed.appendChild(li);
    });
    feed.scrollTop = feed.scrollHeight;
  });
  await page.waitForTimeout(400);
  await shot(page, "scan-progress-en.png");
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    if (overlay) overlay.style.display = "none";
  });

  // 5. AI Usage panel close-up (v1.5.4)
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.waitForTimeout(600);
  const usageCard = page.locator("#usageCard");
  if (await usageCard.count()) {
    await usageCard.scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    await usageCard.screenshot({ path: path.join(OUTPUT_DIR, "usage-panel-en.png") });
  }

  // 6. Manual "Add job" modal (v1.5.4) — the modal is a fixed overlay; fill it
  // via evaluate (robust for a screenshot; the click flow is covered elsewhere).
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(600);
  await page.evaluate(() => {
    document.getElementById("mjTitolo").value = "Senior Platform Engineer";
    document.getElementById("mjAzienda").value = "Vercel";
    document.getElementById("mjSede").value = "Remote - EU";
    document.getElementById("manualJobModal").classList.remove("hidden");
  });
  await page.waitForTimeout(300);
  await shot(page, "manual-add-en.png");
  await page.evaluate(() => document.getElementById("manualJobModal").classList.add("hidden"));
  await page.waitForTimeout(200);

  // 7. Job detail with timeline + notes — opened as the shared side drawer
  // (v1.6.0) from the dashboard recommendations.
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.waitForTimeout(700);
  await page.evaluate(() => {
    const btn = document.querySelector('#recommendationsGrid button[data-rec-action="detail"]');
    if (btn) btn.click();
  });
  await page.waitForTimeout(1300);
  await page.evaluate(() => {
    const tl = document.getElementById("detailTimeline");
    if (tl) tl.scrollIntoView({ block: "center" });
  });
  await page.waitForTimeout(400);
  await shot(page, "job-timeline-en.png");
  // Close the drawer — its backdrop would otherwise intercept the next click.
  await page.evaluate(() => {
    document.getElementById("jobDetailInline")?.classList.remove("is-open");
    document.getElementById("jobDetailBackdrop")?.classList.add("hidden");
  });
  await page.waitForTimeout(250);

  // 8. The archive (v2.0.0): five buckets with counts, and "showing X of Y".
  // Nothing used to visit this view, so the README showed a table that no
  // longer exists.
  await page.locator(".topnav .nav-link[data-view='jobs']").click();
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "archive-buckets-en.png");

  // 9. Mail (v1.8.x–v2.0.0): the tab, its badge, and the review queue — the
  // whole feature was invisible in the README.
  await page.locator(".topnav .nav-link[data-view='mail']").click();
  await page.waitForTimeout(1400);
  // The queue is the point of this shot, and it sits below the connection form:
  // scrolling to the top framed the settings and cut off the three proposals.
  await page.evaluate(() => {
    const box =
      document.getElementById("mailReviewBox") ||
      document.querySelector("#view-mail .card-block:last-of-type");
    if (box) box.scrollIntoView({ block: "center" });
  });
  await page.waitForTimeout(500);
  await shot(page, "mail-review-en.png");

  // 10. Settings → AI: providers, their track record, and which one to use.
  // Also the file the README embeds and no spec produced (settings-providers).
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.waitForTimeout(1500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "settings-providers-en.png");

  // 11. Settings → AI, further down: the rate-limit panel (v2.0.0).
  const limitsCard = page.locator("#rateLimitsCard");
  if (await limitsCard.count()) {
    await limitsCard.scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    await shot(page, "provider-limits-en.png");
  }

  // 12. Profile: the readiness strips that say what the app still needs.
  await page.locator(".topnav .nav-link[data-view='profile']").click();
  await page.waitForTimeout(1000);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shot(page, "profile-readiness-en.png");

  // 13. Dark-mode dashboard (v1.5.3 theme overhaul)
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await setTheme(page, "dark");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);
  await shot(page, "dashboard-dark.png");
  await setTheme(page, "light");
});
