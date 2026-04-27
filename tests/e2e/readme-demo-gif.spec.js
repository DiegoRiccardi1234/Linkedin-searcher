// Records a short animated demo for the README hero.
//
// Prereq:
//   1. python scripts/seed_demo.py --db data/demo.db --force
//   2. SEARCHER_DB_PATH=data/demo.db python run_webapp.py    (separate shell)
//   3. ffmpeg installed and on PATH
//
// Run:
//   npm run record-demo
//
// Output: screenshots/readme/demo.gif (~5-8 s, 720px wide, ~12 fps)

const { test, expect } = require("@playwright/test");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const FRAMES_DIR = path.join(process.cwd(), "tests", "e2e", "_demo_frames");
const OUT_GIF = path.join(process.cwd(), "screenshots", "readme", "demo.gif");
const VIEWPORT = { width: 1440, height: 900 };
const FPS = 12;
const FRAME_INTERVAL_MS = Math.round(1000 / FPS);

function ensureDirs() {
  fs.mkdirSync(FRAMES_DIR, { recursive: true });
  fs.mkdirSync(path.dirname(OUT_GIF), { recursive: true });
  // Wipe leftover frames from any previous run.
  for (const f of fs.readdirSync(FRAMES_DIR)) {
    if (f.startsWith("frame_") && f.endsWith(".png")) {
      fs.unlinkSync(path.join(FRAMES_DIR, f));
    }
  }
}

function frameName(idx) {
  return `frame_${String(idx).padStart(4, "0")}.png`;
}

async function captureFor(page, durationMs, startIdx) {
  const end = Date.now() + durationMs;
  let i = startIdx;
  while (Date.now() < end) {
    await page.screenshot({
      path: path.join(FRAMES_DIR, frameName(i)),
      fullPage: false,
    });
    i += 1;
    // Small wait between frames to honor FPS budget.
    await page.waitForTimeout(FRAME_INTERVAL_MS);
  }
  return i;
}

function ffmpegAvailable() {
  const probe = spawnSync("ffmpeg", ["-version"], { encoding: "utf8" });
  return probe.status === 0;
}

function buildGif() {
  const palette = path.join(FRAMES_DIR, "palette.png");
  const inputGlob = path.join(FRAMES_DIR, "frame_%04d.png");

  const palStep = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-framerate", String(FPS),
      "-i", inputGlob,
      "-vf", "scale=720:-1:flags=lanczos,palettegen",
      palette,
    ],
    { stdio: "inherit" },
  );
  if (palStep.status !== 0) {
    throw new Error("ffmpeg palettegen step failed");
  }

  const gifStep = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-framerate", String(FPS),
      "-i", inputGlob,
      "-i", palette,
      "-lavfi", "scale=720:-1:flags=lanczos [x]; [x][1:v] paletteuse",
      "-loop", "0",
      OUT_GIF,
    ],
    { stdio: "inherit" },
  );
  if (gifStep.status !== 0) {
    throw new Error("ffmpeg paletteuse step failed");
  }
}

function cleanupFrames() {
  for (const f of fs.readdirSync(FRAMES_DIR)) {
    if (f.endsWith(".png")) fs.unlinkSync(path.join(FRAMES_DIR, f));
  }
}

test("record README demo GIF", async ({ page }) => {
  test.setTimeout(180_000);
  ensureDirs();

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

  let idx = 0;

  // Beat 1 — Dashboard hero
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1500, idx);

  // Beat 2 — Open Job Search wizard
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(300);
  idx = await captureFor(page, 1200, idx);

  // Beat 3 — Analyze CV → role chips
  await page.locator("#wizardAnalyzeBtn").click();
  await page.waitForTimeout(700);
  idx = await captureFor(page, 1500, idx);

  // Beat 4 — Back to dashboard with chat panel populated
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => {
    const box = document.getElementById("chatBox");
    if (!box) return;
    box.innerHTML = "";
    const mk = (role, html) => {
      const div = document.createElement("div");
      div.className = `chat-item ${role}`;
      div.innerHTML = `<div class="role">${role === "user" ? "You" : "AI Coach"}</div><div class="bubble">${html}</div>`;
      box.appendChild(div);
    };
    mk("user", "Which roles best fit my CV?");
    mk(
      "assistant",
      "Based on your CV I'd target <b>Backend Engineer</b>, <b>Data Engineer</b>, <b>ML Engineer</b>.",
    );
    box.scrollTop = box.scrollHeight;
  });
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1800, idx);

  // Beat 5 — Scan progress overlay
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    const fill = document.getElementById("scanProgressFill");
    const text = document.getElementById("scanProgressText");
    if (!overlay || !fill || !text) return;
    overlay.style.display = "flex";
    fill.style.width = "62%";
    text.textContent = "Analyzed: Senior Data Engineer @ Satispay — Score 8/10";
  });
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1800, idx);

  expect(idx).toBeGreaterThan(50);

  if (!ffmpegAvailable()) {
    test.skip(true, "ffmpeg not available on PATH; skipping GIF assembly. Frames left in tests/e2e/_demo_frames/.");
    return;
  }

  buildGif();
  cleanupFrames();
  expect(fs.existsSync(OUT_GIF)).toBe(true);
});
