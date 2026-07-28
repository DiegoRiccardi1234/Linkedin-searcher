// Job scan: the scan-form submit handler (SSE progress overlay + live feed) plus
// the snapshot/restore of the filter state reused by saved searches. Extracted
// from app.js. Tag-input access and provider gating are injected via initScan so
// this module doesn't import app.js.
import { showToast } from "./helpers.js";
import { t } from "./i18n.js";
import { showPostScanModal } from "./update.js";
import { loadJobs } from "./job_list.js";
import { loadRecommendations } from "./job_detail.js";

const _noopTags = { getTags: () => [], addMultiple: () => false, clear: () => {} };
let _deps = {
  getKeywords: _noopTags,
  getLocations: _noopTags,
  ensureProviderConfigured: null,
};

export function initScan(deps) {
  _deps = { ..._deps, ...deps };
  const form = document.getElementById("scanForm");
  if (form) form.addEventListener("submit", _onScanSubmit);
  void _initCountrySelect();
}

// Map a browser locale region to a jobspy country value, for the first-run default.
function _detectCountry(countries) {
  const region = (navigator.language || "").toLowerCase().split("-")[1];
  const map = {
    it: "italy", us: "usa", gb: "uk", de: "germany", fr: "france", es: "spain",
    pt: "portugal", nl: "netherlands", br: "brazil", in: "india", ca: "canada",
    au: "australia", mx: "mexico", ch: "switzerland", at: "austria", be: "belgium",
    ie: "ireland", pl: "poland", se: "sweden",
  };
  const guess = map[region];
  return guess && countries.some((c) => c.value === guess) ? guess : "";
}

async function _initCountrySelect() {
  const sel = document.getElementById("scanCountry");
  if (!sel) return;
  try {
    const res = await fetch("/api/countries").then((r) => r.json());
    const countries = Array.isArray(res.countries) ? res.countries : [];
    if (!countries.length) return;
    sel.innerHTML = countries
      .map((c) => `<option value="${c.value}">${c.label}</option>`)
      .join("");
    // Default: last used → backend default → browser-locale guess → first.
    const initial =
      localStorage.getItem("scanCountry") ||
      res.default ||
      _detectCountry(countries) ||
      countries[0].value;
    if ([...sel.options].some((o) => o.value === initial)) sel.value = initial;
    sel.addEventListener("change", () => localStorage.setItem("scanCountry", sel.value));
  } catch (_) {
    /* leave empty; scan falls back to the settings default country */
  }
}

// Snapshot / restore the Job Search filter state — shared by saved searches (F7).
export function readScanConfig() {
  const getKeywords = _deps.getKeywords;
  const getLocations = _deps.getLocations;
  const checked = (name) =>
    Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map((cb) => cb.value);
  const salaryRaw = document.getElementById("scanMinSalary")?.value.trim();
  return {
    terms: getKeywords.getTags().slice(),
    location: getLocations.getTags().slice(),
    country: document.getElementById("scanCountry")?.value || "",
    is_remote: document.getElementById("remoteToggle")?.checked || false,
    sites: checked("scanSites"),
    experience_levels: checked("scanExperience"),
    job_types: checked("scanJobType"),
    work_types: checked("scanWorkType"),
    min_salary: salaryRaw ? parseInt(salaryRaw, 10) || 0 : 0,
  };
}

export function applyScanConfig(cfg) {
  const getKeywords = _deps.getKeywords;
  const getLocations = _deps.getLocations;
  cfg = cfg || {};
  getKeywords.clear();
  getLocations.clear();
  if (Array.isArray(cfg.terms)) getKeywords.addMultiple(cfg.terms);
  if (Array.isArray(cfg.location)) getLocations.addMultiple(cfg.location);
  const cs = document.getElementById("scanCountry");
  if (cs && cfg.country) cs.value = cfg.country;
  const remote = document.getElementById("remoteToggle");
  if (remote) remote.checked = !!cfg.is_remote;
  const setChecks = (name, values) => {
    const set = new Set(values || []);
    document
      .querySelectorAll(`input[name="${name}"]`)
      .forEach((cb) => {
        cb.checked = set.has(cb.value);
      });
  };
  setChecks("scanSites", cfg.sites);
  setChecks("scanExperience", cfg.experience_levels);
  setChecks("scanJobType", cfg.job_types);
  setChecks("scanWorkType", cfg.work_types);
  const sal = document.getElementById("scanMinSalary");
  if (sal) sal.value = cfg.min_salary ? String(cfg.min_salary) : "";
}

// Role words that match anything on a job board. "AI Specialist" alone returned
// 27 of 47 postings on a real scan and nearly all of its noise, because
// "Specialist" also names payroll, sales and partnership roles. The scan runs
// anyway — this is a lesson, not a gate, and it belongs where the mistake is made.
const _VAGUE_TERMS = new Set([
  "specialist", "consultant", "consulente", "analyst", "analista", "engineer",
  "ingegnere", "developer", "sviluppatore", "manager", "coordinator", "coordinatore",
  "operatore", "operator", "assistant", "assistente", "junior", "senior", "stage",
  "tirocinio", "internship",
]);

function warnAboutVagueTerms(tags) {
  const vague = (tags || []).filter((tag) => {
    const words = String(tag).trim().toLowerCase().split(/\s+/).filter(Boolean);
    return words.length === 1 && _VAGUE_TERMS.has(words[0]);
  });
  if (vague.length) showToast(t("scan.vagueTermWarning", { terms: vague.join(", ") }), "info");
}

async function _onScanSubmit(event) {
  event.preventDefault();

  if (typeof _deps.ensureProviderConfigured === "function") {
    const ok = await _deps.ensureProviderConfigured();
    if (!ok) return;
  }

  const getKeywords = _deps.getKeywords;
  const getLocations = _deps.getLocations;

  // Add any pending typed text that wasn't entered
  const kwInputRaw = document.getElementById("keywordsInput").value.trim();
  // (see warnAboutVagueTerms below — a bare role word is where the noise starts)
  if (kwInputRaw) {
    getKeywords.addMultiple([kwInputRaw]);
    document.getElementById("keywordsInput").value = '';
  }
  const locInputRaw = document.getElementById("locationsInput").value.trim();
  if (locInputRaw) {
    getLocations.addMultiple([locInputRaw]);
    document.getElementById("locationsInput").value = '';
  }

  const termsText = getKeywords.getTags().join(", ");
  warnAboutVagueTerms(getKeywords.getTags());
  const siteCheckboxes = document.querySelectorAll('input[name="scanSites"]:checked');
  const selectedSites = Array.from(siteCheckboxes).map(cb => cb.value);
  const isRemote = document.getElementById("remoteToggle")?.checked || false;
  const locationTags = getLocations.getTags();

  const expLevels = Array.from(document.querySelectorAll('input[name="scanExperience"]:checked')).map(cb => cb.value);
  const jobTypes = Array.from(document.querySelectorAll('input[name="scanJobType"]:checked')).map(cb => cb.value);
  const workTypes = Array.from(document.querySelectorAll('input[name="scanWorkType"]:checked')).map(cb => cb.value);

  const params = new URLSearchParams();
  if (termsText) params.set("search_terms", termsText);
  // Locations are pipe-separated (a location like "Torino, Italy" contains commas),
  // so the backend scans each one instead of joining them into a single bad query.
  if (locationTags.length) params.set("locations", locationTags.join("|"));
  const country = document.getElementById("scanCountry")?.value || "";
  if (country) params.set("country", country);
  params.set("is_remote", isRemote);

  // Multi-location warning: terms x locations x ~20 jobs each can be a long scan.
  const nLoc = Math.max(1, locationTags.length);
  const nTerms = Math.max(1, getKeywords.getTags().length);
  if (nLoc * nTerms > 6) {
    showToast(t("scan.volumeWarning", { n: nLoc * nTerms }) || `Large scan (${nLoc * nTerms} searches) — this may take a while.`, "info");
  }
  params.set("sites", (selectedSites.length > 0 ? selectedSites : ["linkedin", "indeed"]).join(","));
  if (expLevels.length) params.set("experience_levels", expLevels.join(","));
  if (jobTypes.length) params.set("job_types", jobTypes.join(","));
  if (workTypes.length) params.set("work_types", workTypes.join(","));

  const minSalaryRaw = document.getElementById("scanMinSalary")?.value.trim();
  const minSalary = minSalaryRaw ? parseInt(minSalaryRaw, 10) : 0;
  if (minSalary > 0) params.set("min_salary", String(minSalary));

  // Show scan overlay
  const overlay = document.getElementById("scanOverlay");
  const progressText = document.getElementById("scanProgressText");
  const progressFill = document.getElementById("scanProgressFill");
  const feedEl = document.getElementById("scanFeed");
  overlay.style.display = "flex";
  overlay.classList.remove("minimized");
  progressFill.style.width = "5%";
  progressText.textContent = t("scan.connecting");
  if (feedEl) feedEl.innerHTML = "";

  const appendFeed = (iconName, textHtml, chip) => {
    if (!feedEl) return;
    const li = document.createElement("li");
    const chipHtml = chip ? `<span class="feed-chip ${chip.cls || ""}">${chip.label}</span>` : "";
    li.innerHTML = `<span class="material-symbols-outlined feed-icon">${iconName}</span><span class="feed-text">${textHtml}</span>${chipHtml}`;
    feedEl.appendChild(li);
    while (feedEl.children.length > 50) feedEl.removeChild(feedEl.firstChild);
    feedEl.scrollTop = feedEl.scrollHeight;
  };
  const escHtml = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let analysisCount = 0;
  let totalFound = 0;
  const lastTopJobs = [];

  const fmtEta = (ms) => {
    if (!ms || ms < 0) return "";
    const sec = Math.round(ms / 1000);
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${String(s).padStart(2, "0")}s`;
  };

  const setProgress = (pct, label) => {
    progressFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    if (label) progressText.textContent = label;
  };

  const evtSource = new EventSource(`/api/scan/stream?${params.toString()}`);
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.status === "started") {
        const terms = (data.terms || []).join(", ");
        setProgress(2, `${t("scan.searching")}: ${terms}`);
        appendFeed("travel_explore", t("scan.feedStarted", { terms: escHtml(terms) }));
      } else if (data.status === "progress") {
        const pct = Number(data.percent || 0);
        const eta = fmtEta(data.eta_ms);
        const stepKey = data.step === "analyzing" ? "scan.progress.analyzing" : "scan.progress.scraping";
        const stepLabel = t(stepKey) || (data.step === "analyzing" ? "Analyzing" : "Scraping");
        const line = `${stepLabel} ${data.current || 0}/${data.total || "?"}` + (eta ? ` · ${t("scan.progress.eta") || "ETA"} ${eta}` : "");
        setProgress(pct, line);
      } else if (data.status === "scraped") {
        totalFound += data.found || 0;
        appendFeed("manage_search", t("scan.feedScraped", { found: data.found || 0, portal: escHtml(data.portal || data.site || "") }));
      } else if (data.status === "analyzed") {
        analysisCount++;
        const j = data.job || {};
        // null score = nobody judged it. `Number(null || 0)` used to render that
        // as a confident 0/10 and let it into the top-5 of the summary.
        const unscored = j.score === null || j.score === undefined;
        const score = unscored ? null : Number(j.score);
        if (!unscored) {
          lastTopJobs.push({ titolo: j.titolo, azienda: j.azienda, score });
          lastTopJobs.sort((a, b) => b.score - a.score);
          if (lastTopJobs.length > 5) lastTopJobs.length = 5;
        }
        const cls = unscored ? "score-none" : score >= 7 ? "score-high" : score >= 4 ? "score-mid" : "score-low";
        const label = unscored ? t("jobs.notScored") : `${score}/10`;
        const pct = Number(data.percent || Math.min(30 + analysisCount * 3, 95));
        const eta = fmtEta(data.eta_ms);
        const line = t("scan.analyzed", { title: j.titolo || "?", company: j.azienda || "?", score: label });
        setProgress(pct, eta ? `${line} · ${t("scan.progress.eta") || "ETA"} ${eta}` : line);
        appendFeed(unscored ? "help" : "check_circle", t("scan.feedAnalyzed", { title: escHtml(j.titolo || "?"), company: escHtml(j.azienda || "?") }), { label, cls });
      } else if (data.status === "complete") {
        setProgress(100, t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 }));
        appendFeed("task_alt", t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 }));
        evtSource.close();
        // Close the scan overlay first so the post-scan modal isn't covered
        // by it. Stash the data on closure since the SSE event won't repeat.
        const completeData = data;
        const completeTops = lastTopJobs.slice();
        setTimeout(() => {
          overlay.style.display = "none";
          overlay.classList.remove("minimized");
          try {
            showPostScanModal(completeData, completeTops);
          } catch (err) {
            console.error("post-scan modal failed:", err);
            showToast(t("scan.postScanRenderFailed"), "info");
          }
        }, 600);
        Promise.all([loadJobs(), loadRecommendations()]);
      } else if (data.error) {
        // The daily request budget is a known, explainable stop — not a crash.
        const quotaStop = String(data.error).startsWith("daily_limit_");
        const message = quotaStop ? t("scan.quotaReached") : `${t("scan.error")}: ${data.error}`;
        progressText.textContent = message;
        appendFeed("error", escHtml(message));
        showToast(message, "error");
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; overlay.classList.remove("minimized"); }, 3000);
      }
    } catch (_) {}
  };
  evtSource.onerror = () => {
    evtSource.close();
    progressText.textContent = t("scan.connectionLost");
    showToast(t("scan.connectionLost") || "Scan connection lost", "error");
    setTimeout(() => { overlay.style.display = "none"; }, 2000);
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  document.getElementById("cancelScanBtn").onclick = () => {
    evtSource.close();
    // Tell the server to stop too — closing the EventSource alone leaves the
    // scan churning (and spending LLM quota) in the background.
    fetch("/api/scan/cancel", { method: "POST" }).catch(() => {});
    overlay.style.display = "none";
    overlay.classList.remove("minimized");
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  const minimizeBtn = document.getElementById("minimizeScanBtn");
  if (minimizeBtn) {
    minimizeBtn.onclick = () => overlay.classList.toggle("minimized");
  }
}
