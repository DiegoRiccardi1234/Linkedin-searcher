// Optional, toggleable features: interview-prep / resume-tailoring generation,
// skill-gap panel, and the scheduled auto-scan controls + highlights banner.
// Pure helpers are exported for app.js (loadHealth / showJobDetail / bootstrap);
// initFeatures() wires the DOM event listeners (loadJobs injected to avoid a
// circular import).
import { api, escapeHtml, setText, showToast } from "./helpers.js";
import { t, getCurrentLang } from "./i18n.js";
import { appState } from "./state.js";

export function readFeatureFlags(prefs) {
  const off = (v) => v === "0" || v === "false" || v === "off";
  const on = (v) => v === "1" || v === "true" || v === "on";
  // Every flag defaults ON: a missing pref is undefined, off(undefined) is false,
  // so !off(...) is true. privacy_mode ships ON by design.
  return {
    interview_prep: !off(prefs.feature_interview_prep),
    resume_tailoring: !off(prefs.feature_resume_tailoring),
    skill_gap: !off(prefs.feature_skill_gap),
    cv_review: !off(prefs.feature_cv_review),
    privacy_mode: !off(prefs.feature_privacy_mode),
    // The exception, and read the other way round: a desktop notification
    // interrupts, so it is opted into rather than inherited. The server reads
    // the same preference with the same default, so the box and the behaviour
    // cannot disagree.
    reminder_notify: on(prefs.feature_reminder_notify),
  };
}

export async function loadSkillGap() {
  const section = document.getElementById("skillGapSection");
  if (!section) return;
  if (appState.featureFlags.skill_gap === false) {
    section.style.display = "none";
    return;
  }
  try {
    const data = await api("/api/skill-gap");
    renderSkillGap(data);
    section.style.display = "block";
  } catch (error) {
    section.style.display = "none";
  }
}

function renderSkillGap(data) {
  const bars = document.getElementById("skillGapBars");
  const empty = document.getElementById("skillGapEmpty");
  if (!bars) return;
  const gaps = (data && data.gaps) || [];
  bars.innerHTML = "";
  if (!gaps.length) {
    if (empty) empty.style.display = "block";
    return;
  }
  if (empty) empty.style.display = "none";
  const max = (data && data.max_count) || 1;
  for (const g of gaps) {
    const pct = Math.max(8, Math.round((g.count / max) * 100));
    const row = document.createElement("div");
    row.className = "skill-gap-row";
    row.innerHTML =
      `<span class="skill-gap-label">${escapeHtml(g.skill)}</span>` +
      `<span class="skill-gap-track"><span class="skill-gap-fill" style="width:${pct}%"></span></span>` +
      `<span class="skill-gap-count">${g.count}</span>`;
    bars.appendChild(row);
  }
}

export async function loadSkillGapLearning() {
  const out = document.getElementById("skillGapLearning");
  const btn = document.getElementById("skillGapLearnBtn");
  if (!out) return;
  out.innerHTML = `<p class="micro">${escapeHtml(t("toast.generating"))}</p>`;
  if (btn) btn.disabled = true;
  try {
    const data = await api(`/api/skill-gap/learning?lang=${encodeURIComponent(getCurrentLang())}`);
    renderSkillGapLearning(data);
  } catch (error) {
    out.innerHTML = `<p class="micro">${escapeHtml(t("skillGap.learn.error"))}: ${escapeHtml(error.message)}</p>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderSkillGapLearning(data) {
  const out = document.getElementById("skillGapLearning");
  if (!out) return;
  const suggestions = (data && data.suggestions) || {};
  const skills = Object.keys(suggestions);
  if (!skills.length) {
    out.innerHTML = `<p class="micro">${escapeHtml(t("skillGap.learn.empty"))}</p>`;
    return;
  }
  out.innerHTML = skills
    .map((skill) => {
      const lis = (suggestions[skill] || [])
        .map((it) => {
          const typeTag = it.type ? `<span class="learn-type">${escapeHtml(it.type)}</span>` : "";
          return `<li><strong>${escapeHtml(it.title || "")}</strong> ${typeTag}<span class="learn-why">${escapeHtml(it.why || "")}</span></li>`;
        })
        .join("");
      return `<div class="learn-block"><h5>${escapeHtml(skill)}</h5><ul class="learn-list">${lis}</ul></div>`;
    })
    .join("");
}

//: Features that stay off until asked for, rather than on until refused.
const OPT_IN_FEATURES = new Set(["reminder_notify"]);

export function syncFeatureToggles() {
  // Global selector: toggles live in both the Settings card and the Profile card.
  document.querySelectorAll("input[data-feature]").forEach((cb) => {
    const key = cb.dataset.feature;
    if (key in appState.featureFlags) cb.checked = appState.featureFlags[key] !== false;
    // Most features are on unless switched off, so "not stored yet" means on.
    // Notifications are the exception: an interruption is opted into, never
    // inherited, so an unset flag must render — and behave — as off, the same
    // way the server reads it.
    else if (OPT_IN_FEATURES.has(key)) cb.checked = false;
  });
  // The whole AI CV tools panel (Review + Improve) is gated by the cv_review flag.
  const cvTools = document.querySelector(".profile-cv-tools");
  if (cvTools) {
    cvTools.style.display = appState.featureFlags.cv_review === false ? "none" : "";
  }
}

export function setupGenerationButton({ btnId, boxId, outId, enabled, saved }) {
  const btn = document.getElementById(btnId);
  const box = document.getElementById(boxId);
  const out = document.getElementById(outId);
  if (!btn || !box || !out) return;
  btn.style.display = enabled ? "inline-block" : "none";
  if (saved) {
    out.textContent = saved;
    box.style.display = "block";
  } else {
    out.textContent = "";
    box.style.display = "none";
  }
}

async function runGeneration({ btn, box, out, endpoint, field, query = "" }) {
  if (!appState.selectedJobId) return;
  box.style.display = "block";
  out.textContent = t("toast.generating");
  btn.disabled = true;
  const originalLabel = btn.innerHTML;
  btn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.generating")}`;
  try {
    const payload = await api(`/api/jobs/${appState.selectedJobId}/${endpoint}${query}`, {
      method: "POST",
    });
    out.textContent = payload[field] || t("toast.noResult");
  } catch (error) {
    out.textContent = `${t("toast.genError")}: ${error.message}`;
    showToast(`${t("toast.genError")}: ${error.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalLabel;
  }
}

export async function loadSchedulerStatus() {
  try {
    const st = await api("/api/scheduler/status");
    const en = document.getElementById("autoscanEnabled");
    const iv = document.getElementById("autoscanInterval");
    const th = document.getElementById("autoscanThreshold");
    if (en) en.checked = !!st.enabled;
    if (iv && st.interval_hours) iv.value = st.interval_hours;
    if (th && typeof st.threshold === "number") th.value = st.threshold;
    renderAutoscanBanner(st.pending);
  } catch (error) {
    /* scheduler is optional; ignore when unavailable */
  }
}

function renderAutoscanBanner(pending) {
  const banner = document.getElementById("autoscanBanner");
  if (!banner) return;
  if (pending && pending.count > 0) {
    setText(
      "autoscanBannerText",
      t("autoscan.bannerText", { count: pending.count, threshold: pending.threshold }),
    );
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

async function saveSchedulerConfig(patch) {
  try {
    await api("/api/scheduler/config", { method: "POST", body: JSON.stringify(patch) });
  } catch (error) {
    showToast(`${t("toast.actionError")}: ${error.message}`, "error");
  }
}

export function initFeatures({ loadJobs }) {
  const ipBtn = document.getElementById("generateInterviewPrepBtn");
  if (ipBtn) {
    ipBtn.addEventListener("click", () =>
      runGeneration({
        btn: ipBtn,
        box: document.getElementById("interviewPrepBox"),
        out: document.getElementById("interviewPrepOutput"),
        endpoint: "interview-prep",
        field: "interview_prep",
      }),
    );
  }

  const trBtn = document.getElementById("generateTailoredResumeBtn");
  if (trBtn) {
    trBtn.addEventListener("click", () =>
      runGeneration({
        btn: trBtn,
        box: document.getElementById("tailoredResumeBox"),
        out: document.getElementById("tailoredResumeOutput"),
        endpoint: "tailored-resume",
        field: "tailored_resume",
      }),
    );
  }

  const rmBtn = document.getElementById("generateRecruiterMsgBtn");
  if (rmBtn) {
    rmBtn.addEventListener("click", () =>
      runGeneration({
        btn: rmBtn,
        box: document.getElementById("recruiterMsgBox"),
        out: document.getElementById("recruiterMsgOutput"),
        endpoint: "recruiter-outreach",
        field: "recruiter_outreach",
        query: `?lang=${encodeURIComponent(getCurrentLang())}`,
      }),
    );
  }

  const copyRmBtn = document.getElementById("copyRecruiterMsgBtn");
  if (copyRmBtn) {
    copyRmBtn.addEventListener("click", async () => {
      const text = document.getElementById("recruiterMsgOutput").textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        setText("rmCopyStatus", t("offcanvas.copied") || "Copied");
        setTimeout(() => setText("rmCopyStatus", ""), 1500);
      } catch (error) {
        showToast(String(error), "error");
      }
    });
  }

  const copyTrBtn = document.getElementById("copyTailoredResumeBtn");
  if (copyTrBtn) {
    copyTrBtn.addEventListener("click", async () => {
      const text = document.getElementById("tailoredResumeOutput").textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        setText("trCopyStatus", t("offcanvas.copied") || "Copied");
        setTimeout(() => setText("trCopyStatus", ""), 1500);
      } catch (error) {
        showToast(String(error), "error");
      }
    });
  }

  // Shared toggle handler, attached to both the Settings and Profile feature
  // lists so a toggle saves regardless of which card it lives in.
  const onFeatureToggle = async (event) => {
    const cb = event.target.closest("input[data-feature]");
    if (!cb) return;
    const key = cb.dataset.feature;
    appState.featureFlags[key] = cb.checked;
    try {
      await api("/api/preferences", {
        method: "POST",
        body: JSON.stringify({ key: `feature_${key}`, value: cb.checked ? "1" : "0" }),
      });
      showToast(t("settings.features.saved") || "Saved", "info");
      if (key === "skill_gap") loadSkillGap();
      if (key === "cv_review") {
        const b = document.getElementById("cvReviewBtn");
        if (b) b.style.display = cb.checked ? "inline-block" : "none";
      }
    } catch (error) {
      cb.checked = !cb.checked;
      appState.featureFlags[key] = cb.checked;
      showToast(`${t("toast.actionError")}: ${error.message}`, "error");
    }
  };
  const featureToggleList = document.getElementById("profileFeatureToggleList");
  if (featureToggleList) featureToggleList.addEventListener("change", onFeatureToggle);

  const refreshSkillGapBtn = document.getElementById("refreshSkillGapBtn");
  if (refreshSkillGapBtn) {
    refreshSkillGapBtn.addEventListener("click", loadSkillGap);
  }
  const skillGapLearnBtn = document.getElementById("skillGapLearnBtn");
  if (skillGapLearnBtn) {
    skillGapLearnBtn.addEventListener("click", loadSkillGapLearning);
  }

  const autoscanEnabledEl = document.getElementById("autoscanEnabled");
  if (autoscanEnabledEl) {
    autoscanEnabledEl.addEventListener("change", () =>
      saveSchedulerConfig({ enabled: autoscanEnabledEl.checked }),
    );
  }
  const autoscanIntervalEl = document.getElementById("autoscanInterval");
  if (autoscanIntervalEl) {
    autoscanIntervalEl.addEventListener("change", () =>
      saveSchedulerConfig({ interval_hours: parseInt(autoscanIntervalEl.value, 10) || 12 }),
    );
  }
  // Read by list_reminders and, until now, settable nowhere at all: not in the
  // UI, not through the preferences endpoint, only by editing the table.
  const staleDaysEl = document.getElementById("reminderStaleDays");
  if (staleDaysEl && !staleDaysEl.dataset.wired) {
    staleDaysEl.dataset.wired = "1";
    staleDaysEl.addEventListener("change", async () => {
      const value = String(Math.max(1, Math.min(90, Number(staleDaysEl.value) || 7)));
      staleDaysEl.value = value;
      try {
        await api("/api/preferences", {
          method: "POST",
          body: JSON.stringify({ key: "reminder_stale_days", value }),
        });
        showToast(t("settings.features.saved") || "Saved", "info");
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  }

  const autoscanThresholdEl = document.getElementById("autoscanThreshold");
  if (autoscanThresholdEl) {
    autoscanThresholdEl.addEventListener("change", () =>
      saveSchedulerConfig({ threshold: parseInt(autoscanThresholdEl.value, 10) || 0 }),
    );
  }
  const autoscanRunNowEl = document.getElementById("autoscanRunNow");
  if (autoscanRunNowEl) {
    autoscanRunNowEl.addEventListener("click", async () => {
      try {
        await api("/api/scheduler/run-now", { method: "POST" });
        showToast(t("settings.features.autoscanStarted") || "Auto-scan started", "info");
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "error");
      }
    });
  }
  const autoscanBannerDismissEl = document.getElementById("autoscanBannerDismiss");
  if (autoscanBannerDismissEl) {
    autoscanBannerDismissEl.addEventListener("click", async () => {
      document.getElementById("autoscanBanner").classList.add("hidden");
      try {
        await api("/api/scheduler/dismiss", { method: "POST" });
      } catch (error) {
        /* best-effort dismiss */
      }
    });
  }
  const autoscanBannerViewEl = document.getElementById("autoscanBannerView");
  if (autoscanBannerViewEl) {
    autoscanBannerViewEl.addEventListener("click", () => {
      const onlyNew = document.getElementById("onlyNew");
      if (onlyNew) onlyNew.checked = true;
      // The job archive now lives in its own tab; the nav link switches view
      // and (re)loads jobs with the onlyNew filter applied.
      document.querySelector('[data-view="jobs"]')?.click();
    });
  }

  const autoscanNotifyEl = document.getElementById("autoscanNotify");
  if (autoscanNotifyEl) {
    autoscanNotifyEl.checked = localStorage.getItem("autoscanNotify") === "1";
    autoscanNotifyEl.addEventListener("change", () => {
      // Persist to the DB (the scheduler reads it to fire a native tray toast);
      // mirror in localStorage so the checkbox restores instantly on reload.
      const on = autoscanNotifyEl.checked;
      localStorage.setItem("autoscanNotify", on ? "1" : "0");
      fetch("/api/preferences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "autoscan_notify", value: on ? "1" : "0" }),
      }).catch(() => {});
    });
  }

  // Re-poll the scheduler periodically so new auto-scan results surface (and
  // notify) while the app stays open — no manual refresh needed.
  if (!window._autoscanPoll) {
    window._autoscanPoll = setInterval(() => {
      loadSchedulerStatus().catch(() => {});
    }, 120000);
  }
}
