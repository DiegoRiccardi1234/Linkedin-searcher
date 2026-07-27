// What was actually sent for this job: when, with which CV, and how it ended.
// The kanban tracks the funnel STATE; this block records the facts about the
// application itself, which the state cannot express — an offer and eight weeks
// of silence look identical to a board.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

// Mirrors app.db.Database.OUTCOMES. "pending" is the empty value.
const OUTCOMES = ["pending", "no_response", "rejected", "offer", "accepted", "withdrawn"];

// Statuses past the point of applying: below them the block is noise.
const APPLIED_STATES = new Set(["applied", "interviewing", "rejected"]);

function _fmtDay(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso).slice(0, 10) : d.toLocaleDateString();
}

export function applicationBlockHtml(job) {
  if (!job) return "";
  const status = String(job.status || "");
  if (!APPLIED_STATES.has(status) && !job.applied_at) return "";
  const applied = job.applied_at
    ? escapeHtml(_fmtDay(job.applied_at))
    : t("application.dateUnknown");
  // A CV recorded but since deleted, and an application made before the app
  // recorded any of this, are different things and read differently.
  const cv = job.applied_profile_id
    ? escapeHtml(job.applied_profile_name || t("application.cvGone"))
    : t("application.cvUnknown");
  const current = String(job.outcome || "pending");
  const options = OUTCOMES.map(
    (o) =>
      `<option value="${o}"${o === current ? " selected" : ""}>${escapeHtml(
        t(`application.outcome.${o}`),
      )}</option>`,
  ).join("");
  return `
    <div class="mt-16 info-card application-card">
      <h4>${t("application.title")}</h4>
      <div class="application-facts">
        <span><strong>${t("application.sentOn")}:</strong> ${applied}</span>
        <span><strong>${t("application.cvUsed")}:</strong> ${cv}</span>
      </div>
      <label class="application-outcome">
        <span>${t("application.outcomeLabel")}</span>
        <select id="detailOutcome">${options}</select>
      </label>
    </div>`;
}

export function wireApplicationBlock(jobId, onChanged) {
  const select = document.getElementById("detailOutcome");
  if (!select) return;
  select.addEventListener("change", async () => {
    try {
      await api(`/api/jobs/${jobId}/outcome`, {
        method: "POST",
        body: JSON.stringify({ outcome: select.value }),
      });
      showToast(t("application.saved"), "info");
      if (typeof onChanged === "function") onChanged();
    } catch (err) {
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
    }
  });
}
