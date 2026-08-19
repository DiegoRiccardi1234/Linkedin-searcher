// Job detail drawer + recommendations. Extracted from app.js. The shared
// job-list utilities (scoreCell/normalizeJobStatus/fmtDate/loadJobs) are imported
// from job_list.js; pinJobToActiveSession (chat) is injected via initJobDetail to
// avoid importing app.js.
import { api, escapeHtml, setText, showToast } from "./helpers.js";
import { t } from "./i18n.js";
import { appState } from "./state.js";
import {
  scoreCell,
  normalizeJobStatus,
  fmtDate,
  loadJobs,
  flagBadgesHtml,
  freshnessHtml,
} from "./job_list.js";
import { applicationBlockHtml, wireApplicationBlock } from "./application.js";
import { reminderEditorHtml, wireReminderEditor } from "./reminders.js";
import { scoreFeedbackHtml, wireScoreFeedback } from "./score_feedback.js";
import { setupGenerationButton } from "./features.js";

let _deps = { pinJobToActiveSession: () => {} };

export function initJobDetail(deps) {
  _deps = { ..._deps, ...deps };
}

export function openJobDetail() {
  document.getElementById("jobDetailInline")?.classList.add("is-open");
  document.getElementById("jobDetailBackdrop")?.classList.remove("hidden");
}

export function closeJobDetail() {
  document.getElementById("jobDetailInline")?.classList.remove("is-open");
  document.getElementById("jobDetailBackdrop")?.classList.add("hidden");
}

let _matchRadarChart = null;

function renderMatchRadar(axes) {
  const canvas = document.getElementById("detailMatchRadar");
  if (!canvas || typeof window.Chart === "undefined") return;
  const data = axes && typeof axes === "object" ? axes : {};
  // An axis the backend couldn't compute arrives as null (typically salary: no
  // source publishes it). Plotting `Number(null) || 0` drew a confident zero, so
  // unknown axes are dropped from the chart instead of being invented.
  const axisSpecs = [
    ["skills_match", "offcanvas.axisSkills"],
    ["seniority_match", "offcanvas.axisSeniority"],
    ["remote_match", "offcanvas.axisRemote"],
    ["salary_match", "offcanvas.axisSalary"],
    ["contract_match", "offcanvas.axisContract"],
  ].filter(([key]) => data[key] !== null && data[key] !== undefined && data[key] !== "");
  const labels = axisSpecs.map(([, labelKey]) => t(labelKey));
  const values = axisSpecs.map(([key]) => Number(data[key]) || 0);
  if (_matchRadarChart) {
    _matchRadarChart.destroy();
    _matchRadarChart = null;
  }
  // No axis at all = nobody judged this offer (or it was blocked before anyone
  // did). An empty chart canvas reads as a broken widget; say it in words.
  const empty = document.getElementById("detailRadarEmpty");
  if (empty) empty.classList.toggle("hidden", axisSpecs.length > 0);
  canvas.classList.toggle("hidden", axisSpecs.length === 0);
  if (!axisSpecs.length) return;
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const gridColor = isDark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)";
  const textColor = isDark ? "#cbd5e1" : "#334155";
  _matchRadarChart = new window.Chart(canvas, {
    type: "radar",
    data: {
      labels,
      datasets: [
        {
          label: t("offcanvas.matchScore"),
          data: values,
          backgroundColor: "rgba(99,91,255,0.25)",
          borderColor: "#635bff",
          pointBackgroundColor: "#635bff",
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0,
          max: 10,
          ticks: { stepSize: 2, color: textColor, backdropColor: "transparent" },
          angleLines: { color: gridColor },
          grid: { color: gridColor },
          pointLabels: { color: textColor, font: { size: 11 } },
        },
      },
    },
  });
}

// Why each axis reads the way it does — derived from data the app already has,
// so the radar stops being decorative without costing a single extra token.
function axisReasonsHtml(analysis) {
  const axes = (analysis && analysis.match_axes) || {};
  const skills = (analysis && analysis.skills_match) || {};
  const flags = new Set(analysis?.blocchi || []);
  // The blockers are listed under "missing" too, but they are not missing
  // SKILLS — showing "outside the EU: needs a visa" as a skill gap reads as if
  // the candidate could go and learn it. Each blocker gets its own row below.
  const blockerTexts = new Set(Object.values(analysis?.blocchi_dettaglio || {}));
  const missing = (Array.isArray(skills.mancano) ? skills.mancano : [])
    .filter((item) => !blockerTexts.has(item))
    .slice(0, 3);
  const rows = [];
  if (axes.skills_match !== undefined && missing.length) {
    rows.push([t("offcanvas.axisSkills"), missing.join(", ")]);
  }
  if (flags.has("geo_non_ue")) {
    rows.push([t("offcanvas.axisRemote"), t("jobs.flag.geoLong")]);
  }
  if (flags.has("voto_minimo")) {
    rows.push([
      t("offcanvas.axisSeniority"),
      analysis?.blocchi_dettaglio?.voto_minimo || t("jobs.flag.grade"),
    ]);
  }
  if (flags.has("ral_sotto_minima")) {
    rows.push([t("offcanvas.axisSalary"), t("jobs.flag.salaryLong")]);
  } else if (axes.salary_match === null || axes.salary_match === undefined) {
    rows.push([t("offcanvas.axisSalary"), t("offcanvas.axisSalaryUnknown")]);
  }
  if (flags.has("lavoro_a_task")) {
    rows.push([t("offcanvas.axisContract"), t("jobs.flag.gigLong")]);
  }
  if (!rows.length) return "";
  const items = rows
    .map(
      ([axis, why]) =>
        `<li><strong>${escapeHtml(axis)}:</strong> ${escapeHtml(String(why))}</li>`,
    )
    .join("");
  return `<ul class="axis-reasons micro">${items}</ul>`;
}

async function renderTimeline(jobId) {
  const el = document.getElementById("detailTimeline");
  if (!el) return;
  let actions = [];
  try {
    ({ actions } = await api(`/api/jobs/${jobId}/timeline`));
  } catch {
    actions = [];
  }
  if (!actions || !actions.length) {
    el.innerHTML = `<p class="micro">${t("timeline.empty")}</p>`;
    return;
  }
  el.innerHTML = actions
    .slice()
    .reverse()
    .map((a) => {
      const isNote = a.action === "note";
      const statusKey = a.action === "reopened" ? "open" : a.action;
      const label = isNote ? t("timeline.note") : t(`jobs.status.${statusKey}`) || a.action;
      const icon = isNote ? "sticky_note_2" : "flag";
      return `<div class="tl-item"><span class="material-symbols-outlined tl-icon">${icon}</span><div class="tl-body"><div class="tl-head"><strong>${escapeHtml(label)}</strong><span class="tl-date">${fmtDate(a.created_at)}</span></div>${a.notes ? `<div class="tl-note">${escapeHtml(a.notes)}</div>` : ""}</div></div>`;
    })
    .join("");
}

export async function showJobDetail(jobId) {
  const payload = await api(`/api/jobs/${jobId}`);
  const job = payload.job || {};
  const analysis = job.analysis || {};
  appState.selectedJobId = job.id || null;

  setText("detailStatus", `${t("jobs.statusLabel")}: ${t("jobs.status." + normalizeJobStatus(job.status))}`);
  setText("detailTitle", job.titolo || t("jobs.titleUnavailable"));
  setText("detailCompany", job.azienda || t("jobs.companyUnavailable"));
  // Build the meta line from distinct parts: legacy data sometimes stored the
  // city in ``modalita`` too, which produced "Torino | Score | Torino".
  const sede = (job.sede || "").trim();
  const modalita = (job.modalita || "").trim();
  const detailScore = scoreCell(job.punteggio_ai);
  const metaParts = [sede || t("jobs.locationUnavailable"), `${t("jobs.score")}: ${detailScore.text}`];
  if (modalita && modalita.toLowerCase() !== sede.toLowerCase()) {
    metaParts.push(modalita);
  }
  setText("detailMeta", metaParts.join(" | "));

  // Empty for everything scored before v2.0.0: the column is filled from here
  // on, and guessing who wrote an old verdict would be a fact-shaped guess.
  setText(
    "detailScoredBy",
    job.analysis_model ? t("jobs.scoredBy", { model: job.analysis_model }) : "",
  );

  const detailLinkBtn = document.getElementById("detailLinkBtn");
  if (detailLinkBtn) {
    if (job.link) {
      detailLinkBtn.href = job.link;
      // Static markup, so the tag it is matched on has to be set here, next to
      // the href it belongs with (see apply_watch.js).
      detailLinkBtn.dataset.jobLink = String(job.id);
      detailLinkBtn.style.display = "flex";
    } else {
      detailLinkBtn.style.display = "none";
    }
  }

  const notAppliedBtn = document.getElementById("detailNotAppliedBtn");
  if (notAppliedBtn) {
    const pending = Boolean(job.link_opened_at) && (job.status || "open") === "open";
    notAppliedBtn.style.display = pending ? "" : "none";
    notAppliedBtn.dataset.jobId = String(job.id);
  }

  // Only for a marking the mailbox made, never a manual one — that is exactly
  // what the endpoint enforces, and the button follows the same rule. Without
  // it, a mail that picked the wrong offer could not be taken back at all.
  const undoBtn = document.getElementById("detailUndoMailBtn");
  if (undoBtn) {
    undoBtn.style.display = job.apply_confirmed_by === "email" ? "" : "none";
    undoBtn.dataset.jobId = String(job.id);
  }

  const genBtn = document.getElementById("generateCoverLetterBtn");
  const covBox = document.getElementById("coverLetterBox");
  if (genBtn && covBox) {
    genBtn.style.display = "inline-block";
    covBox.style.display = "none";
    document.getElementById("coverLetterOutput").textContent = "";
  }

  // Optional generation features: show the button only when enabled, and
  // re-render any previously generated artifact stored on the job.
  setupGenerationButton({
    btnId: "generateInterviewPrepBtn",
    boxId: "interviewPrepBox",
    outId: "interviewPrepOutput",
    enabled: appState.featureFlags.interview_prep !== false,
    saved: analysis.interview_prep,
  });
  setupGenerationButton({
    btnId: "generateTailoredResumeBtn",
    boxId: "tailoredResumeBox",
    outId: "tailoredResumeOutput",
    enabled: appState.featureFlags.resume_tailoring !== false,
    saved: analysis.tailored_resume,
  });
  setupGenerationButton({
    btnId: "generateRecruiterMsgBtn",
    boxId: "recruiterMsgBox",
    outId: "recruiterMsgOutput",
    enabled: true,
    saved: analysis.recruiter_outreach,
  });

  const recruiter = payload.recruiter || null;

  const renderList = (items, max = 5) => {
    if (!Array.isArray(items) || !items.length) return "";
    return `<ul class="bullet-list">${items.slice(0, max).map((x) => `<li>${escapeHtml(String(x))}</li>`).join("")}</ul>`;
  };

  const requisitiBlock = analysis && analysis.requisiti ? `
        <div class="info-card mt-16">
          <h4>${t("offcanvas.requirements") || "Requisiti chiave"}</h4>
          ${renderList(analysis.requisiti)}
        </div>` : "";
  const responsabilitaBlock = analysis && analysis.responsabilita ? `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.responsibilities") || "Responsabilità"}</h4>
          ${renderList(analysis.responsabilita)}
        </div>` : "";
  const benefitBlock = analysis && Array.isArray(analysis.benefit) && analysis.benefit.length ? `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.benefits") || "Benefit"}</h4>
          ${renderList(analysis.benefit)}
        </div>` : "";

  let skillsMatchBlock = "";
  if (analysis && analysis.skills_match) {
    const hai = Array.isArray(analysis.skills_match.hai) ? analysis.skills_match.hai : [];
    const mancano = Array.isArray(analysis.skills_match.mancano) ? analysis.skills_match.mancano : [];
    skillsMatchBlock = `
        <div class="info-card mt-8">
          <h4>${t("offcanvas.skillsMatch") || "Skills match"}</h4>
          <div class="skills-match">
            <div><strong class="pro-label">✅ ${t("offcanvas.skillsHave") || "Hai"}:</strong> ${hai.length ? hai.map((s) => `<span class="chip-mini">${escapeHtml(s)}</span>`).join("") : "—"}</div>
            <div class="mt-8"><strong class="con-label">❌ ${t("offcanvas.skillsMissing") || "Mancano"}:</strong> ${mancano.length ? mancano.map((s) => `<span class="chip-mini missing">${escapeHtml(s)}</span>`).join("") : "—"}</div>
          </div>
        </div>`;
  }

  let recruiterBlock = "";
  if (recruiter && (recruiter.name || recruiter.headline)) {
    recruiterBlock = `
        <div class="info-card mt-8 recruiter-card">
          <h4>${t("offcanvas.postedBy") || "Pubblicato da"}</h4>
          <div class="recruiter-name">${escapeHtml(recruiter.name || "—")}</div>
          ${recruiter.title ? `<div class="text-sm text-dim">${escapeHtml(recruiter.title)}</div>` : ""}
          ${recruiter.headline ? `<div class="text-sm">${escapeHtml(recruiter.headline)}</div>` : ""}
          ${recruiter.profile_url ? `<a class="ghost-btn small mt-8" target="_blank" rel="noopener" href="${escapeHtml(recruiter.profile_url)}">${t("offcanvas.viewProfile") || "View profile"}</a>` : ""}
        </div>`;
  }

  const pinBtnRow = `
        <div class="mt-16 detail-action-row">
          <button type="button" class="ghost-btn" id="detailPinBtn"><span class="material-symbols-outlined">push_pin</span> ${t("offcanvas.pinToChat") || "Pin to chat"}</button>
        </div>`;

  // "Also on" badge: the same role can be seen on more than one source
  // (LinkedIn + Indeed) — cross-source dedup keeps one card and records the rest.
  const sources = Array.isArray(job.sources) ? job.sources : [];
  let sourcesBadge = "";
  if (sources.length > 1) {
    const links = sources
      .map((s) => {
        const label = escapeHtml(s.fonte || "?");
        return s.link
          ? `<a target="_blank" rel="noopener" data-job-link="${job.id}" href="${escapeHtml(s.link)}">${label}</a>`
          : label;
      })
      .join(", ");
    sourcesBadge = `<div class="info-tag also-on"><strong>${t("jobs.alsoOn")}:</strong> ${links}</div>`;
  }

  const container = document.getElementById("jobDetailContainer");
  if (container) {
    const sc = scoreCell(job.punteggio_ai);
    let ralSpan = "";
    if (analysis && analysis.ral_dichiarata) {
      // What the ad itself prints, which is a stronger thing than an estimate.
      ralSpan = `<div class="info-tag"><strong>${escapeHtml(t("jobs.declaredPay"))}:</strong> ${escapeHtml(analysis.ral_dichiarata)}</div>`;
    } else if (analysis && analysis.ral_stimata && analysis.ral_stimata !== "Non stimabile") {
      ralSpan = `<div class="info-tag"><strong>RAL:</strong> ${escapeHtml(analysis.ral_stimata)}</div>`;
    }
    // The reasons a score is capped, as badges instead of a sentence glued to
    // the front of the weakness line.
    const badges = flagBadgesHtml(analysis?.blocchi) + freshnessHtml(job.last_seen_at);
    const flagsRow = badges ? `<div class="job-flags mt-8">${badges}</div>` : "";
    // Analyses stored before the rename still carry the old, name-bearing keys.
    const strengths = analysis ? analysis.punti_forza || analysis.punti_forza_per_diego : null;
    const weaknesses = analysis ? analysis.punti_deboli || analysis.punti_deboli_per_diego : null;
    // Platform task work vs employment: same list, very different decision.
    let engagementSpan = "";
    const engagement = analysis && analysis.tipo_ingaggio;
    if (engagement && engagement !== "Non specificato") {
      engagementSpan = `<div class="info-tag"><strong>${t("offcanvas.engagement")}:</strong> ${escapeHtml(engagement)}</div>`;
    }
    // The retry is offered on ANY offer, judged or not. Restricting it to
    // unscored ones meant a verdict could never be revisited: a score written by
    // a model that turned out to be too generous, or before a fix to the
    // blocking rules, was frozen for as long as the posting stayed in the
    // archive. The one exception stays: when the posting itself carries no
    // description, another call comes back just as empty.
    const unscored = job.punteggio_ai === null || job.punteggio_ai === undefined;
    const shortDesc = (analysis?.blocchi || []).includes("descrizione_breve");
    let rescoreRow = "";
    if (unscored && shortDesc) {
      rescoreRow = `<p class="micro mt-8 text-center">${t("offcanvas.shortDescHint")}</p>`;
    } else {
      rescoreRow =
        `<button type="button" id="detailReanalyzeBtn" data-id="${job.id}" class="secondary mt-8">` +
        `<span class="material-symbols-outlined">refresh</span> ${t("offcanvas.reanalyze")}</button>`;
    }

    container.innerHTML = `
      <div class="modern-detail">
        <div class="modern-detail-grid">
          <div class="info-card highlight" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
            <h4>${t("offcanvas.matchScore")}</h4>
            <div class="score-xl ${sc.cls}">${sc.text}</div>
            <div class="text-sm mt-8 text-center">${escapeHtml((analysis ? analysis.consiglio : null) || job.consiglio || "")}</div>
            <button type="button" data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary icon-btn detail-fav${job.is_favorite ? " is-active" : ""}" title="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}" aria-label="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}"><span class="material-symbols-outlined">${job.is_favorite ? "star" : "star_border"}</span></button>
            ${flagsRow}
            ${rescoreRow}
            ${unscored ? "" : scoreFeedbackHtml(payload.score_feedback)}
          </div>
          <div class="info-card">
            <h4>${t("offcanvas.positionDetails")}</h4>
            ${ralSpan}
            <div class="info-tag"><strong>${t("offcanvas.contract")}:</strong> ${escapeHtml((analysis ? analysis.contratto : null) || "N/A")}</div>
            ${engagementSpan}
            <div class="info-tag"><strong>${t("offcanvas.remoteWork")}:</strong> ${escapeHtml((analysis ? analysis.smart_working : null) || job.modalita || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.experience")}:</strong> ${escapeHtml((analysis ? analysis.anni_esperienza_richiesti : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.codingSkills")}:</strong> ${escapeHtml((analysis ? analysis.programmazione_richiesta : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.graduateFriendly")}:</strong> ${escapeHtml((analysis ? analysis.adatta_neolaureati : null) || "N/A")}</div>
          </div>
        </div>
        <div class="mt-16">
          <h4>${t("offcanvas.prosAndCons")}</h4>
          <ul class="pros-cons">
            <li class="pro">✅ ${escapeHtml(strengths || "N/A")}</li>
            <li class="con">❌ ${escapeHtml(weaknesses || "N/A")}</li>
          </ul>
        </div>
        <div class="info-card mt-8">
            <p class="text-sm">💡 <strong>${t("offcanvas.aiVerdict")}:</strong> ${escapeHtml((analysis ? analysis.riassunto : null) || "")}</p>
        </div>
        <div class="mt-16 info-card">
          <h4>${t("offcanvas.breakdown")}</h4>
          <canvas id="detailMatchRadar" height="220"></canvas>
          <p id="detailRadarEmpty" class="micro hidden">${t("offcanvas.noBreakdown")}</p>
          ${unscored ? "" : axisReasonsHtml(analysis)}
        </div>
        ${requisitiBlock}
        ${responsabilitaBlock}
        ${benefitBlock}
        ${skillsMatchBlock}
        ${recruiterBlock}
        ${pinBtnRow}
        <div class="mt-16">
          <h4>${t("offcanvas.listingMeta")}</h4>
          ${sourcesBadge}
          <p class="text-sm text-dim">${t("offcanvas.search")}: ${escapeHtml(job.ricerca_usata)} | ${t("jobs.source")}: ${escapeHtml(job.fonte || "App")} | ${t("offcanvas.found")}: ${fmtDate(job.first_seen_at)}</p>
        </div>
        ${applicationBlockHtml(job)}
        <div class="mt-16 info-card">
          <h4>${t("timeline.title")}</h4>
          <div id="detailTimeline" class="detail-timeline"></div>
          ${reminderEditorHtml(job)}
          <div class="note-add">
            <textarea id="detailNoteInput" rows="2" data-i18n-placeholder="timeline.notePlaceholder" placeholder="Add a note…"></textarea>
            <button type="button" id="detailNoteBtn" class="ghost-btn small">${t("timeline.addNote")}</button>
          </div>
        </div>
      </div>
    `;
    renderMatchRadar(analysis && analysis.match_axes);
    const pinBtn = document.getElementById("detailPinBtn");
    if (pinBtn) {
      pinBtn.addEventListener("click", () => {
        if (appState.selectedJobId && typeof _deps.pinJobToActiveSession === "function") {
          _deps.pinJobToActiveSession(appState.selectedJobId);
        }
      });
    }

    renderTimeline(job.id);
    wireReminderEditor(job.id);
    wireApplicationBlock(job.id, () => renderTimeline(job.id));
    wireScoreFeedback(job.id);
    container.querySelector("button.detail-fav")?.addEventListener("click", (event) => {
      const btn = event.currentTarget;
      toggleFavorite(btn.dataset.id, btn.dataset.favorite === "1");
    });
    const rescoreBtn = document.getElementById("detailReanalyzeBtn");
    if (rescoreBtn) {
      rescoreBtn.addEventListener("click", async () => {
        rescoreBtn.disabled = true;
        const original = rescoreBtn.innerHTML;
        rescoreBtn.textContent = t("offcanvas.reanalyzeRunning");
        try {
          const res = await api(`/api/jobs/${job.id}/analyze`, { method: "POST" });
          if (res.evaluated) {
            showToast(t("toast.reanalyzeDone"), "success");
            await showJobDetail(job.id);
            if (typeof _deps.loadJobs === "function") await _deps.loadJobs();
          } else {
            // The provider is still down. Not an app error, and not a score.
            showToast(t("toast.reanalyzeStillUnscored"), "info");
            rescoreBtn.disabled = false;
            rescoreBtn.innerHTML = original;
          }
        } catch (err) {
          showToast(`${t("toast.reanalyzeFailed")}: ${err.message}`, "error");
          rescoreBtn.disabled = false;
          rescoreBtn.innerHTML = original;
        }
      });
    }
    const noteBtn = document.getElementById("detailNoteBtn");
    const noteInput = document.getElementById("detailNoteInput");
    if (noteBtn && noteInput) {
      noteBtn.addEventListener("click", async () => {
        const notes = noteInput.value.trim();
        if (!notes) return;
        noteBtn.disabled = true;
        try {
          await api(`/api/jobs/${job.id}/note`, { method: "POST", body: JSON.stringify({ notes }) });
          noteInput.value = "";
          await renderTimeline(job.id);
        } catch (err) {
          showToast(`${t("timeline.noteError")}: ${err.message}`, "info");
        } finally {
          noteBtn.disabled = false;
        }
      });
    }
  }

  openJobDetail();
}

export async function performJobAction(jobId, action) {
  try {
    await api(`/api/jobs/${jobId}/action`, {
      method: "POST",
      body: JSON.stringify({ action, notes: "" }),
    });
    await Promise.all([loadJobs(), loadRecommendations()]);
    const specific = t(`toast.job.${action}`);
    showToast(specific && specific !== `toast.job.${action}` ? specific : (t("toast.jobUpdated") || "Job updated"), "info");
  } catch (err) {
    showToast(`${t("toast.jobActionFailed") || "Job action failed"}: ${err.message}`, "error");
  }
}

export async function toggleFavorite(jobId, isFavorite) {
  // Paint the star first: the confirmation used to wait for a full reload of
  // the job list (a 250-row query plus a re-sort), so the click looked ignored
  // — and if that reload failed, the star never moved at all.
  paintFavorite(jobId, isFavorite);
  try {
    await api(`/api/jobs/${jobId}/favorite`, {
      method: "POST",
      body: JSON.stringify({ is_favorite: isFavorite }),
    });
    const key = isFavorite ? "toast.favoriteAdded" : "toast.favoriteRemoved";
    showToast(t(key), "info");
    await Promise.all([loadJobs(), loadRecommendations()]);
  } catch (err) {
    paintFavorite(jobId, !isFavorite); // the write failed: put the star back
    showToast(`${t("toast.jobActionFailed")}: ${err.message}`, "error");
  }
}

// Every surface that shows the flag for this job, updated in place.
export function paintFavorite(jobId, isFavorite) {
  const id = String(jobId);
  document.querySelectorAll(`[data-favorite][data-id="${id}"]`).forEach((btn) => {
    btn.dataset.favorite = isFavorite ? "0" : "1";
    btn.classList.toggle("is-active", isFavorite);
    btn.title = isFavorite ? t("jobs.unfavorite") : t("jobs.favorite");
    btn.setAttribute("aria-label", btn.title);
    const icon = btn.querySelector(".material-symbols-outlined");
    if (icon) icon.textContent = isFavorite ? "star" : "star_border";
  });
  document
    .querySelectorAll(`.kanban-card[data-id="${id}"] .kanban-fav, .job-fav-mark[data-id="${id}"]`)
    .forEach((mark) => mark.classList.toggle("hidden", !isFavorite));
}

function recommendationCardHtml(job) {
  const sc = scoreCell(job.punteggio_ai);
  const consiglio = escapeHtml(job.consiglio || "Evaluate match");
  const title = escapeHtml(job.titolo || t("jobs.titleUnavailable"));
  const company = escapeHtml(job.azienda || t("jobs.companyUnavailable"));
  const newTag = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
  const favoriteText = job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite");
  const nextFavorite = job.is_favorite ? "0" : "1";
  const linkHtml = job.link ? `<div style="margin-top: 4px"><a href="${escapeHtml(job.link)}" target="_blank" rel="noopener" data-job-link="${job.id}">🔗 ${t("jobs.linkToOffer")}</a></div>` : "";

  return `
    <article class="rec-card" data-rec-id="${job.id}">
      <div class="rec-head">
        <div class="rec-title">${title}</div>
        <span class="rec-score ${sc.cls}">${sc.text}</span>
      </div>
      <div class="rec-company">${company} ${newTag}</div>
      <div>${consiglio}</div>
      ${linkHtml}
      <div class="rec-actions">
        <button class="secondary" data-rec-action="detail" data-id="${job.id}">${t("jobs.details")}</button>
        <button class="apply-btn" data-rec-action="applied" data-id="${job.id}">${t("jobs.apply")}</button>
        <button class="danger" data-rec-action="rejected" data-id="${job.id}">${t("jobs.skip")}</button>
        <button class="secondary" data-rec-favorite="${nextFavorite}" data-id="${job.id}">${favoriteText}</button>
      </div>
    </article>
  `;
}

export async function loadRecommendations() {
  const container = document.getElementById("recommendationsGrid");
  if (!container) return;

  container.innerHTML = "";
  try {
    const payload = await api("/api/recommendations?limit=5");
    const jobs = payload.jobs || [];

    if (!jobs.length) {
      container.innerHTML = `<article class="rec-card"><div class="rec-title">${t("recommendations.noRecs")}</div><div>${t("recommendations.noRecsSub")}</div></article>`;
      return;
    }

    container.innerHTML = jobs.map((job) => recommendationCardHtml(job)).join("");

    container.querySelectorAll("button[data-rec-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const action = btn.dataset.recAction;
        try {
          if (action === "detail") {
            await showJobDetail(id);
            return;
          }
          await performJobAction(id, action);
        } catch (error) {
          showToast(`${t("toast.quickActionError")}: ${error.message}`, "info");
        }
      });
    });

    container.querySelectorAll("button[data-rec-favorite]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const fav = btn.dataset.recFavorite === "1";
        try {
          await toggleFavorite(id, fav);
        } catch (error) {
          showToast(`${t("toast.favoriteError")}: ${error.message}`, "info");
        }
      });
    });
  } catch (error) {
    container.innerHTML = `<article class="rec-card"><div class="rec-title">${t("recommendations.loadError")}</div><div>${escapeHtml(error.message)}</div></article>`;
  }
}
