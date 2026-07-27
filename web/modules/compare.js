// Side-by-side comparison of 2-3 offers.
//
// The archive could tell you a job was a 6; it could not tell you how that 6
// differed from the 6 next to it. Deciding "which of these do I actually apply
// to" meant opening the drawer, reading, closing, opening the next one and
// holding both in your head. This puts the axes, the blockers and the missing
// skills of up to three offers in one view.
//
// Selection lives here (not in job_list) so the table stays a renderer; the
// detail renderers are reused rather than duplicated.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";
import { flagBadgesHtml, scoreCell } from "./job_list.js";

const MAX_COMPARE = 3;
const _selected = new Set();

export function isSelected(jobId) {
  return _selected.has(String(jobId));
}

export function toggleCompare(jobId) {
  const key = String(jobId);
  if (_selected.has(key)) {
    _selected.delete(key);
  } else {
    if (_selected.size >= MAX_COMPARE) {
      showToast(t("compare.maxReached").replace("{n}", String(MAX_COMPARE)), "info");
      return false;
    }
    _selected.add(key);
  }
  _paintBar();
  return true;
}

export function clearCompare() {
  _selected.clear();
  _paintBar();
  document
    .querySelectorAll("input.compare-check")
    .forEach((el) => {
      el.checked = false;
    });
}

function _paintBar() {
  const bar = document.getElementById("compareBar");
  const count = document.getElementById("compareCount");
  if (!bar || !count) return;
  bar.classList.toggle("hidden", _selected.size === 0);
  count.textContent = t("compare.selected").replace("{n}", String(_selected.size));
}

const AXES = [
  ["skills_match", "offcanvas.axisSkills"],
  ["seniority_match", "offcanvas.axisSeniority"],
  ["remote_match", "offcanvas.axisRemote"],
  ["salary_match", "offcanvas.axisSalary"],
  ["contract_match", "offcanvas.axisContract"],
];

function _column(job, analysis) {
  const sc = scoreCell(job.punteggio_ai);
  const axes = analysis.match_axes || {};
  const skills = analysis.skills_match || {};
  const missing = Array.isArray(skills.mancano) ? skills.mancano.slice(0, 4) : [];
  const have = Array.isArray(skills.hai) ? skills.hai.slice(0, 4) : [];
  const axisRows = AXES.map(([key, labelKey]) => {
    const raw = axes[key];
    // An axis nobody could compute stays "N/D" here too: inventing a number in
    // a comparison view is how you talk yourself into the wrong job.
    const value = raw === null || raw === undefined || raw === "" ? "—" : `${raw}/10`;
    return `<tr><td>${escapeHtml(t(labelKey))}</td><td class="compare-num">${value}</td></tr>`;
  }).join("");
  const list = (items, cls) =>
    items.length
      ? `<ul class="compare-list ${cls}">${items.map((s) => `<li>${escapeHtml(String(s))}</li>`).join("")}</ul>`
      : `<p class="micro">—</p>`;
  return `
    <div class="compare-col">
      <div class="compare-head">
        <div class="score-xl ${sc.cls}">${sc.text}</div>
        <strong>${escapeHtml(job.titolo || "")}</strong>
        <div class="micro">${escapeHtml(job.azienda || "")} · ${escapeHtml(job.sede || "")}</div>
        <div class="job-flags">${flagBadgesHtml(analysis.blocchi)}</div>
      </div>
      <table class="compare-axes"><tbody>${axisRows}</tbody></table>
      <div class="micro compare-label">${escapeHtml(t("offcanvas.skillsHave"))}</div>
      ${list(have, "have")}
      <div class="micro compare-label">${escapeHtml(t("offcanvas.skillsMissing"))}</div>
      ${list(missing, "missing")}
      <div class="micro compare-label">RAL</div>
      <p class="micro">${escapeHtml(analysis.ral_stimata || "—")}</p>
      ${job.link ? `<a href="${escapeHtml(job.link)}" target="_blank" rel="noopener" class="micro">${escapeHtml(t("jobs.openPosting"))}</a>` : ""}
    </div>`;
}

export async function openCompare() {
  const body = document.getElementById("compareBody");
  const modal = document.getElementById("compareModal");
  if (!body || !modal || _selected.size < 2) {
    showToast(t("compare.needTwo"), "info");
    return;
  }
  modal.classList.remove("hidden");
  body.innerHTML = `<p class="micro">${escapeHtml(t("compare.loading"))}</p>`;
  try {
    const payloads = await Promise.all(
      [..._selected].map((id) => api(`/api/jobs/${encodeURIComponent(id)}`)),
    );
    body.innerHTML = payloads
      .map((p) => _column(p.job || {}, (p.job && p.job.analysis) || {}))
      .join("");
  } catch (err) {
    body.innerHTML = `<p class="micro">${escapeHtml(t("compare.error"))}: ${escapeHtml(err.message)}</p>`;
  }
}

export function initCompare() {
  document.getElementById("compareOpenBtn")?.addEventListener("click", openCompare);
  document.getElementById("compareClearBtn")?.addEventListener("click", clearCompare);
  document.querySelector("[data-close-compare]")?.addEventListener("click", () => {
    document.getElementById("compareModal")?.classList.add("hidden");
  });
  _paintBar();
}
