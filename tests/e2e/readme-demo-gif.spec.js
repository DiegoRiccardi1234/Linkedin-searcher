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
const VIEWPORT = { width: 1920, height: 1080 };
const FPS = 12;
const GIF_WIDTH = 960;
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

function pythonPillowAvailable() {
  const probe = spawnSync(
    "python",
    ["-c", "import PIL; print(PIL.__version__)"],
    { encoding: "utf8" },
  );
  return probe.status === 0;
}

function buildGifFfmpeg() {
  const palette = path.join(FRAMES_DIR, "palette.png");
  const inputGlob = path.join(FRAMES_DIR, "frame_%04d.png");

  const palStep = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-framerate", String(FPS),
      "-i", inputGlob,
      "-vf", `scale=${GIF_WIDTH}:-1:flags=lanczos,palettegen`,
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
      "-lavfi", `scale=${GIF_WIDTH}:-1:flags=lanczos [x]; [x][1:v] paletteuse`,
      "-loop", "0",
      OUT_GIF,
    ],
    { stdio: "inherit" },
  );
  if (gifStep.status !== 0) {
    throw new Error("ffmpeg paletteuse step failed");
  }
}

function buildGifPillow() {
  // Pure-Python fallback: assemble the GIF via Pillow. Lower quality
  // than ffmpeg's palette flow but no external binary needed.
  const script = `
from pathlib import Path
from PIL import Image

src = Path(${JSON.stringify(FRAMES_DIR)})
out = Path(${JSON.stringify(OUT_GIF)})
out.parent.mkdir(parents=True, exist_ok=True)
frames = sorted(src.glob("frame_*.png"))
if not frames:
    raise SystemExit("no frames")
target_w = ${GIF_WIDTH}
imgs = []
for f in frames:
    im = Image.open(f).convert("RGB")
    w, h = im.size
    nh = int(h * target_w / w)
    im = im.resize((target_w, nh), Image.LANCZOS)
    imgs.append(im.convert("P", palette=Image.ADAPTIVE, colors=256))
duration_ms = int(1000 / ${FPS})
imgs[0].save(
    out,
    save_all=True,
    append_images=imgs[1:],
    duration=duration_ms,
    loop=0,
    optimize=True,
    disposal=2,
)
`;
  const step = spawnSync("python", ["-c", script], { stdio: "inherit" });
  if (step.status !== 0) {
    throw new Error("Pillow GIF assembly failed");
  }
}

function buildGif() {
  if (ffmpegAvailable()) {
    buildGifFfmpeg();
    return "ffmpeg";
  }
  if (pythonPillowAvailable()) {
    buildGifPillow();
    return "pillow";
  }
  throw new Error("Neither ffmpeg nor Python+Pillow available on PATH");
}

function cleanupFrames() {
  for (const f of fs.readdirSync(FRAMES_DIR)) {
    if (f.endsWith(".png")) fs.unlinkSync(path.join(FRAMES_DIR, f));
  }
}

test("record README demo GIF", async ({ page }) => {
  test.setTimeout(240_000);
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

  // Beat 1 — Dashboard hero (analytics, hero, chat panel)
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1600, idx);

  // Beat 2 — Settings (provider keys form, scan parameters)
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.evaluate(() => {
    // Defensive: blank any pre-filled API key inputs so the GIF never leaks them.
    document.querySelectorAll("input[type='password']").forEach((el) => {
      el.value = "";
    });
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1500, idx);

  // Beat 3 — Job Search wizard, step 1 with role chips populated
  await page.locator(".topnav .nav-link[data-view='job-search']").click();
  await page.waitForTimeout(300);
  await page.locator("#wizardAnalyzeBtn").click();
  await page.waitForTimeout(800);
  await page.evaluate(() => window.scrollTo(0, 0));
  idx = await captureFor(page, 1800, idx);

  // Beat 4 — Chat coach bubbles with role pills (dashboard right rail)
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
    mk("user", "Which roles best fit my CV?");
    mk(
      "assistant",
      "Strong <b>backend</b> and <b>data engineering</b> skills. I'd target these first:",
      `
      <div class="role-pill-row">
        <button class="role-pill">Backend Engineer (Python)</button>
        <button class="role-pill">Data Engineer</button>
        <button class="role-pill">ML Engineer</button>
        <button class="role-pill">Platform Engineer</button>
      </div>`,
    );
    mk("user", "Find jobs in Milan, remote ok.");
    mk(
      "assistant",
      "Searching <b>Backend Engineer</b> + <b>Data Engineer</b> in <b>Milan</b> + remote. Launching scan...",
    );
    box.scrollTop = box.scrollHeight;
  });
  await page.waitForTimeout(400);
  idx = await captureFor(page, 1800, idx);

  // Beat 5 — Scan progress overlay with animated progress + live feed
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    const fill = document.getElementById("scanProgressFill");
    const text = document.getElementById("scanProgressText");
    const feed = document.getElementById("scanFeed");
    if (!overlay || !feed) return;
    overlay.style.display = "flex";
    fill.style.width = "30%";
    text.textContent = "Searching LinkedIn for: Backend Engineer, Data Engineer";
    const rows = [
      { icon: "travel_explore", text: "Searching: <b>Backend Engineer</b>", chip: null },
      { icon: "manage_search", text: "Found 24 ads on <b>LinkedIn</b>", chip: null },
      { icon: "manage_search", text: "Found 18 ads on <b>Indeed</b>", chip: null },
      { icon: "check_circle", text: "<b>Backend Python Engineer</b> @ Nexi", chip: { label: "7/10", cls: "score-high" } },
      { icon: "check_circle", text: "<b>Platform Engineer</b> @ Bending Spoons", chip: { label: "6/10", cls: "score-mid" } },
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
  idx = await captureFor(page, 1500, idx);

  // Animate scan progress 30% → 90% with new feed entries scrolling in
  for (const step of [
    { pct: 50, txt: "Analyzed: Backend Python Engineer @ Nexi — Score 7/10", row: { icon: "check_circle", text: "<b>Senior Data Engineer</b> @ Satispay", chip: { label: "8/10", cls: "score-high" } } },
    { pct: 70, txt: "Analyzed: Senior Data Engineer @ Satispay — Score 8/10", row: { icon: "check_circle", text: "<b>Junior Developer</b> @ StartupX", chip: { label: "3/10", cls: "score-low" } } },
    { pct: 90, txt: "Analyzed: ML Engineer @ Prima — Score 7/10", row: { icon: "check_circle", text: "<b>ML Engineer</b> @ Prima", chip: { label: "7/10", cls: "score-high" } } },
  ]) {
    await page.evaluate((s) => {
      const fill = document.getElementById("scanProgressFill");
      const text = document.getElementById("scanProgressText");
      const feed = document.getElementById("scanFeed");
      if (fill) fill.style.width = `${s.pct}%`;
      if (text) text.textContent = s.txt;
      if (feed) {
        const li = document.createElement("li");
        const chipHtml = s.row.chip ? `<span class="feed-chip ${s.row.chip.cls}">${s.row.chip.label}</span>` : "";
        li.innerHTML = `<span class="material-symbols-outlined feed-icon">${s.row.icon}</span><span class="feed-text">${s.row.text}</span>${chipHtml}`;
        feed.appendChild(li);
        feed.scrollTop = feed.scrollHeight;
      }
    }, step);
    idx = await captureFor(page, 700, idx);
  }

  // Beat 6 — Close overlay, scroll down through results to reveal the jobs table
  await page.evaluate(() => {
    const overlay = document.getElementById("scanOverlay");
    if (overlay) overlay.style.display = "none";
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(400);
  idx = await captureFor(page, 600, idx);

  // Smooth-scroll down to the jobs section so the viewer sees the actual matches.
  const jobsTarget = await page.evaluate(() => {
    const sec = document.querySelector(".jobs-section");
    return sec ? sec.getBoundingClientRect().top + window.scrollY - 80 : 0;
  });
  const STEPS = 14;
  for (let s = 1; s <= STEPS; s++) {
    await page.evaluate((y) => window.scrollTo(0, y), Math.round((jobsTarget * s) / STEPS));
    idx = await captureFor(page, 90, idx);
  }
  // Hold on the jobs list so the matches are clearly readable in the GIF.
  idx = await captureFor(page, 1500, idx);

  expect(idx).toBeGreaterThan(60);

  if (!ffmpegAvailable() && !pythonPillowAvailable()) {
    test.skip(true, "Neither ffmpeg nor Python+Pillow on PATH; skipping assembly. Frames left in tests/e2e/_demo_frames/.");
    return;
  }

  const tool = buildGif();
  console.log(`[record-demo] GIF built with ${tool} from ${idx} frames`);
  cleanupFrames();
  expect(fs.existsSync(OUT_GIF)).toBe(true);
});
