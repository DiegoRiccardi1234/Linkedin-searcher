// Self-update flow (dev git-pull + bundled Updater.exe), the version banner,
// the System settings card, and the post-scan summary modal. Self-contained:
// talks only to /api/version|update*|health|system and the DOM. app.js wires
// the entry points from its window-load handler and scan flow.
import { api, renderCoachMarkdown } from "./helpers.js";
import { t } from "./i18n.js";

async function _reconcileStuckUpdate() {
  // A detached updater that failed (PermissionError on a locked exe), or a page
  // killed mid-update, leaves localStorage.updateInProgress set. On the next
  // load that dead-locks the "Update now" button — its click guard early-returns
  // while the flag matches the latest version. Clear it when no updater is
  // actually running so the user can retry.
  if (!localStorage.getItem("updateInProgress")) return;
  const unstick = () => {
    localStorage.removeItem("updateInProgress");
    const runBtn = document.getElementById("updateBannerRun");
    if (runBtn) runBtn.disabled = false;
  };
  try {
    const pr = await fetch("/api/update/progress", { cache: "no-store" });
    const p = pr.ok ? await pr.json() : { step: "idle" };
    // "idle"/"error" = nothing live (or last run failed) → safe to unstick and
    // release the backend lock. download/verify/replace/restart = a real update
    // is in flight (e.g. another tab) → leave it alone.
    if (p.step === "idle" || p.step === "error") {
      unstick();
      fetch("/api/update/lock", { method: "DELETE" }).catch(() => {});
    }
  } catch {
    // Progress unreachable → assume nothing running; unstick so we don't strand
    // the button forever.
    unstick();
  }
}

export async function checkForUpdate(opts) {
  await _reconcileStuckUpdate();
  const forceRefresh = !!(opts && opts.forceRefresh);
  const banner = document.getElementById("updateBanner");
  const url = forceRefresh ? "/api/version?refresh=true" : "/api/version";
  const info = await api(url);
  if (!banner) return info;
  try {
    if (!info.update_available || !info.latest) {
      banner.classList.add("hidden");
      return info;
    }
    const dismissed = localStorage.getItem("updateDismissed");
    // Dismissed = hide the banner, NOT swallow the answer: returning undefined
    // here made the caller in wireSystemSettings render "up to date" forever,
    // with no way to un-dismiss from the UI.
    if (dismissed === info.latest) {
      banner.classList.add("hidden");
      return info;
    }

    document.getElementById("updateBannerVersions").textContent = `${info.current} → ${info.latest}`;
    const link = document.getElementById("updateBannerLink");
    if (info.release_url) {
      link.href = info.release_url;
      link.classList.remove("hidden");
    } else {
      link.classList.add("hidden");
    }
    const notesEl = document.getElementById("updateBannerNotes");
    const notesBody = document.getElementById("updateBannerNotesBody");
    if (notesEl && notesBody) {
      const raw = (info.release_notes || "").trim();
      if (raw) {
        notesBody.innerHTML = renderCoachMarkdown(raw);
        notesEl.classList.remove("hidden");
      } else {
        notesEl.classList.add("hidden");
      }
    }
    banner.classList.remove("hidden");

    document.getElementById("updateBannerClose").onclick = () => {
      localStorage.setItem("updateDismissed", info.latest);
      banner.classList.add("hidden");
    };
    const runBtn = document.getElementById("updateBannerRun");
    runBtn.onclick = () => {
      // Guard against double-click: a parallel updater spawn races on
      // JobFinder.exe file locks and produces PermissionError on copy.
      if (runBtn.disabled) return;
      const inProgressFor = localStorage.getItem("updateInProgress");
      if (inProgressFor === info.latest) return;
      runBtn.disabled = true;
      localStorage.setItem("updateInProgress", info.latest);
      runUpdate(info).catch((err) => {
        console.warn("Update failed:", err);
        runBtn.disabled = false;
        localStorage.removeItem("updateInProgress");
      });
    };
    return info;
  } catch (err) {
    console.warn("Update check failed:", err);
    return info;
  }
}

async function runUpdate(info) {
  const modal = document.getElementById("updateModal");
  const log = document.getElementById("updateModalLog");
  const closeBtn = document.getElementById("updateModalClose");
  modal.classList.remove("hidden");
  // Reset error-state hooks from any previous run so the open-logs link
  // is re-appendable on a fresh failure.
  if (log) {
    log.dataset.openLogsLink = "";
    log.onclick = null;
    log.style.cursor = "";
    log.title = "";
  }
  closeBtn.onclick = () => {
    modal.classList.add("hidden");
    // Clear stuck flag so user can retry if they close the modal manually
    // before the update finishes (e.g. it errored out and they want a redo).
    localStorage.removeItem("updateInProgress");
    const runBtn = document.getElementById("updateBannerRun");
    if (runBtn) runBtn.disabled = false;
    // Also force-clear backend lockfile so 409 doesn't block the next click.
    fetch("/api/update/lock", { method: "DELETE" }).catch(() => {
      /* best-effort */
    });
  };

  if (info && info.frozen) {
    await runBundleUpdate(log);
  } else {
    await runDevUpdate(log);
  }
}

async function runDevUpdate(log) {
  log.textContent = t("update.status.devRunning") + "\n";
  try {
    const result = await api("/api/update", { method: "POST" });
    log.textContent = `${result.message}\n\n`;
    for (const step of result.steps || []) {
      log.textContent += `=== ${step.step} (exit ${step.code}) ===\n${step.output}\n\n`;
    }
    if (result.ok) {
      log.textContent += "\n→ " + t("update.status.devRestartHint");
    }
  } catch (err) {
    log.textContent += "\n" + t("update.error.generic", { message: err.message });
  }
}

function _appendOpenLogsLink(log) {
  if (!log || log.dataset.openLogsLink === "1") return;
  log.dataset.openLogsLink = "1";
  const linkText = t("update.modal.viewLogs") || "Open logs folder";
  log.textContent += `\n\n→ ${linkText}`;
  log.style.cursor = "pointer";
  log.title = linkText;
  log.onclick = () => {
    fetch("/api/system/open-logs", { method: "POST" }).catch(() => {});
  };
}

function setUpdateStep(stepName, state, percent) {
  const list = document.getElementById("updateStepList");
  if (state === "error") {
    _appendOpenLogsLink(document.getElementById("updateModalLog"));
  }
  if (!list) return;
  const order = ["download", "verify", "replace", "restart"];
  const targetIdx = order.indexOf(stepName);
  list.querySelectorAll(".update-step").forEach((el) => {
    const idx = order.indexOf(el.dataset.step);
    el.classList.remove("pending", "active", "done", "error");
    const pctEl = el.querySelector(".update-step-percent");
    if (pctEl) pctEl.textContent = "";
    if (state === "error" && idx === targetIdx) el.classList.add("error");
    else if (idx < targetIdx) el.classList.add("done");
    else if (idx === targetIdx) {
      el.classList.add(state === "done" ? "done" : "active");
      if (pctEl && typeof percent === "number" && state !== "done") {
        pctEl.textContent = ` · ${percent}%`;
      }
    } else el.classList.add("pending");
  });
}

async function runBundleUpdate(log) {
  setUpdateStep("download", "active");
  log.textContent = "";
  let targetVersion = "";
  try {
    const r = await fetch("/api/update/start", { method: "POST" });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      const detail = err.detail;
      let msg;
      if (detail && typeof detail === "object" && detail.code === "update_already_in_progress") {
        msg = t("update.error.alreadyInProgress", { sec: detail.lock_age_s });
      } else {
        msg = typeof detail === "string" ? detail : JSON.stringify(detail || r.status);
      }
      log.textContent = t("update.error.failedStart", { message: msg });
      setUpdateStep("download", "error");
      localStorage.removeItem("updateInProgress");
      return;
    }
    const data = await r.json();
    targetVersion = data.next_version || "";
    log.textContent =
      t("update.status.spawned", { from: data.from_version, to: data.next_version }) + "\n";
  } catch (err) {
    log.textContent = t("update.error.generic", { message: err.message });
    setUpdateStep("download", "error");
    return;
  }

  // Poll /api/update/progress while the parent process is still alive
  // (~0–1 s window). Once the parent exits, /api/health takes over.
  const progressDeadline = Date.now() + 30_000;
  let progressLastEvent = "";
  while (Date.now() < progressDeadline) {
    await new Promise((r) => setTimeout(r, 600));
    try {
      const pr = await fetch("/api/update/progress", { cache: "no-store" });
      if (!pr.ok) break;
      const p = await pr.json();
      if (p.event && p.event !== progressLastEvent) {
        progressLastEvent = p.event;
        log.textContent += `${p.event}\n`;
      }
      if (p.step === "error") {
        setUpdateStep("download", "error");
        log.textContent +=
          "\n" + t("update.error.updaterError", { message: (p.details && p.details.message) || "unknown" });
        return;
      }
      if (p.step) setUpdateStep(p.step, "active", p.percent);
    } catch {
      break; // parent died, switch to /api/health polling
    }
  }
  setUpdateStep("restart", "active", 95);

  // Poll /api/health until the new process is up. Expect a window where the
  // request fails because the old app exited and the new one isn't serving yet
  // (download + extract + sync + relaunch). We can't tell "still downloading"
  // from "relaunch crashed" while nothing serves HTTP, so the deadline stays
  // generous (5 min covers a slow ~175 MB download) but on expiry we surface an
  // honest error state + the open-logs link instead of hanging forever.
  const startedAt = Date.now();
  const deadline = startedAt + 300_000;
  let outageObserved = false;
  let elapsedLine = "";
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    const elapsedSec = Math.floor((Date.now() - startedAt) / 1000);
    if (elapsedLine) {
      log.textContent = log.textContent.slice(0, -elapsedLine.length);
    }
    elapsedLine = t("update.status.elapsed", { sec: elapsedSec });
    log.textContent += elapsedLine;
    try {
      const h = await fetch("/api/health", { cache: "no-store" });
      if (!h.ok) throw new Error(String(h.status));
      const json = await h.json();
      const ver = (json.version_info && json.version_info.current) || (json.version || "");
      // Reload when the app is back after an observed outage, OR when it already
      // reports the target version — a fast machine can finish the swap between
      // two polls without a single failed request (outageObserved stays false).
      const reachedTarget =
        ver && targetVersion && normalizeVer(ver) === normalizeVer(targetVersion);
      if (outageObserved || reachedTarget) {
        setUpdateStep("restart", "done", 100);
        log.textContent = log.textContent.slice(0, -elapsedLine.length);
        log.textContent += t("update.status.reachable", { version: ver });
        localStorage.removeItem("updateInProgress");
        setTimeout(() => window.location.reload(), 1500);
        return;
      }
      // No outage yet — keep waiting; updater hasn't taken JobFinder.exe down.
    } catch {
      outageObserved = true;
    }
  }
  // Timed out with no server back. Flip to an explicit error (this also surfaces
  // the open-logs link via setUpdateStep) and hide the "will auto-reload" hint
  // so we stop telling the user to keep waiting.
  setUpdateStep("restart", "error");
  const hint = document.getElementById("updateModalHint");
  if (hint) hint.classList.add("hidden");
  log.textContent += "\n\n" + t("update.error.didNotStart");
  localStorage.removeItem("updateInProgress");
}

export function populateSystemInfo(info) {
  if (!info) return;
  const versionEl = document.getElementById("systemCurrentVersion");
  if (versionEl && info.current) versionEl.textContent = `v${info.current}`;
  const chip = document.getElementById("topbarVersion");
  if (chip && info.current) chip.textContent = `v${info.current}`;
}

export function wireSystemSettings() {
  const checkBtn = document.getElementById("systemCheckUpdate");
  const lastEl = document.getElementById("systemLastCheck");
  if (checkBtn && lastEl) {
    checkBtn.onclick = async () => {
      checkBtn.disabled = true;
      lastEl.textContent = t("settings.system.checking");
      try {
        // An explicit check overrides an earlier "dismiss": otherwise the banner
        // stays hidden and this button is the only way to learn about the
        // update it is being asked about.
        localStorage.removeItem("updateDismissed");
        const info = await checkForUpdate({ forceRefresh: true });
        populateSystemInfo(info);
        const ts = new Date().toLocaleTimeString();
        if (info && info.update_available) {
          lastEl.textContent = `${ts} · v${info.latest} ${t("update.title")}`;
        } else {
          lastEl.textContent = `${ts} · ${t("settings.system.upToDate")}`;
        }
      } catch (err) {
        lastEl.textContent = `${new Date().toLocaleTimeString()} · ${err.message}`;
      } finally {
        checkBtn.disabled = false;
      }
    };
  }
  const logsBtn = document.getElementById("systemOpenLogs");
  if (logsBtn) {
    logsBtn.onclick = () => {
      fetch("/api/system/open-logs", { method: "POST" }).catch(() => {
        /* best-effort; silent if browser-only or 501 */
      });
    };
  }
}

export function wirePostScanModal() {
  const modal = document.getElementById("postScanModal");
  if (!modal) return;
  modal.querySelectorAll("[data-close-postscan]").forEach((btn) => {
    btn.addEventListener("click", () => modal.classList.add("hidden"));
  });
}

export function showPostScanModal(summary, topJobs) {
  const modal = document.getElementById("postScanModal");
  if (!modal) return;
  const dur = (summary?.duration_ms || 0) / 1000;
  const setText = (id, val) => {
    const el = modal.querySelector(`#${id}`);
    if (el) el.textContent = String(val);
  };
  setText("psFound", summary?.totale_trovati ?? 0);
  setText("psNew", summary?.totale_nuovi ?? 0);
  setText("psAnalyzed", summary?.totale_analizzati ?? 0);
  setText("psSkipped", summary?.totale_scartati ?? 0);
  setText("psArchived", summary?.archiviati ?? 0);
  setText("psDuration", `${dur.toFixed(1)}s`);
  const list = modal.querySelector("#psTopJobs");
  if (list) {
    if (!topJobs || !topJobs.length) {
      list.innerHTML = `<li class="micro">${t("postScan.noTop") || "—"}</li>`;
    } else {
      list.innerHTML = topJobs
        .slice(0, 3)
        .map((j) => {
          const score = Number(j.score || 0);
          const cls = score >= 7 ? "score-high" : score >= 4 ? "score-mid" : "score-low";
          return `<li><span class="ps-title">${escapeHtmlSafe(j.titolo || "?")}</span><span class="ps-co">${escapeHtmlSafe(j.azienda || "?")}</span><span class="ps-score ${cls}">${score}/10</span></li>`;
        })
        .join("");
    }
  }
  modal.classList.remove("hidden");
}

function normalizeVer(s) {
  // "v1.5.0" and "1.5.0" compare equal; trims stray whitespace.
  return String(s || "")
    .replace(/^v/i, "")
    .trim();
}

function escapeHtmlSafe(s) {
  return String(s || "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
