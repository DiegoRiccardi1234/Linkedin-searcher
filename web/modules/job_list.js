// Job archive: table + kanban rendering, filters and kanban drag-and-drop, plus
// the small shared score/status/date formatters. Extracted from app.js. Refs to
// the detail drawer and job-status actions are injected via initJobList so this
// module never has to import app.js (which would be circular).
import { api, escapeHtml, setText, showToast, truncate } from "./helpers.js";
import { t, getCurrentLang } from "./i18n.js";

let _deps = {
  showJobDetail: async () => {},
  performJobAction: async () => {},
  toggleFavorite: async () => {},
};

export function initJobList(deps) {
  _deps = { ..._deps, ...deps };
}

export function scoreClass(score) {
  const n = Number(score);
  return n >= 7 ? "score-high" : n >= 4 ? "score-mid" : "score-low";
}

// AI score cell: null/undefined/"" => "not scored" (unscored is NOT a 0/10 match).
export function scoreCell(score) {
  if (score === null || score === undefined || score === "") {
    return { text: t("jobs.notScored") || "—", cls: "score-none" };
  }
  return { text: `${score}/10`, cls: scoreClass(score) };
}

export function normalizeJobStatus(status) {
  const normalized = String(status || "open").trim().toLowerCase();
  if (normalized === "interview") return "interviewing";
  return normalized;
}

export function statusPillHtml(status) {
  const s = normalizeJobStatus(status);
  const label = t(`jobs.status.${s}`) || s;
  return `<span class="status-pill status-${s}">${escapeHtml(label)}</span>`;
}

export function fmtDate(s) {
  if (!s) return "";
  const d = new Date(s);
  return isNaN(d.getTime()) ? escapeHtml(s) : d.toLocaleDateString(getCurrentLang());
}

export async function loadJobs() {
  const onlyNew = document.getElementById("onlyNew").checked;
  const onlyFavorites = document.getElementById("onlyFavorites").checked;
  const remoteOnly = document.getElementById("remoteOnly").checked;
  const searchText = document.getElementById("searchText").value.trim();
  const status = document.getElementById("statusFilter").value;
  const minScoreRaw = document.getElementById("minScore").value.trim();
  const maxAgeRaw = document.getElementById("maxAgeDays").value.trim();

  const query = new URLSearchParams({
    only_new: onlyNew ? "true" : "false",
    only_favorites: onlyFavorites ? "true" : "false",
    limit: "250",
  });
  if (remoteOnly) query.set("remote_only", "true");
  if (searchText) query.set("search_text", searchText);
  if (status) query.set("status", status);
  if (minScoreRaw) query.set("min_score", minScoreRaw);
  if (maxAgeRaw) query.set("max_age_days", maxAgeRaw);

  const COLS = 9;
  const body = document.getElementById("jobsTableBody");
  const fullRow = (cls, msg) => `<tr><td colspan="${COLS}" class="${cls}">${msg}</td></tr>`;
  body.innerHTML = fullRow("table-empty", "…");

  let jobs;
  try {
    ({ jobs } = await api(`/api/jobs?${query.toString()}`));
  } catch (err) {
    console.error("loadJobs failed", err);
    body.innerHTML = fullRow("table-empty table-error", t("jobs.loadError") || "Couldn't load jobs.");
    return;
  }

  body.innerHTML = "";
  if (!jobs.length) {
    const filtered =
      onlyNew || onlyFavorites || remoteOnly || searchText || status || minScoreRaw || maxAgeRaw;
    body.innerHTML = fullRow(
      "table-empty",
      filtered ? t("jobs.emptyFiltered") || "No jobs match these filters."
               : t("jobs.emptyNoJobs") || "No jobs yet — run your first scan.",
    );
    renderKanban(jobs);
    return;
  }

  for (const job of jobs) {
    const newBadge = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
    const sc = scoreCell(job.punteggio_ai);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="${sc.cls}">${sc.text}</span> ${newBadge}</td>
      <td>${statusPillHtml(job.status)}</td>
      <td>${escapeHtml(truncate(job.titolo || ""))}</td>
      <td>${escapeHtml(truncate(job.azienda || ""))}</td>
      <td>${escapeHtml(truncate(job.sede || ""))}</td>
      <td>${escapeHtml(truncate(job.fonte || ""))}</td>
      <td>${escapeHtml(truncate(job.consiglio || ""))}</td>
      <td>
        <button data-detail-id="${job.id}" class="secondary">${t("jobs.details")}</button>
        ${job.link ? `<a href="${escapeHtml(job.link)}" target="_blank" rel="noopener" style="margin-left: 8px;" title="${t("jobs.openPosting")}" aria-label="${t("jobs.openPosting")}">🔗</a>` : ""}
      </td>
      <td>
        <div class="mini">
          <button class="apply-btn" data-action="applied" data-id="${job.id}">${t("jobs.apply")}</button>
          <button data-action="rejected" data-id="${job.id}" class="danger">${t("jobs.skip")}</button>
          <button data-action="reopened" data-id="${job.id}" class="secondary icon-btn" title="${t("jobs.reopen")}" aria-label="${t("jobs.reopen")}"><span class="material-symbols-outlined">restart_alt</span></button>
          <button data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary icon-btn${job.is_favorite ? " is-active" : ""}" title="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}" aria-label="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}"><span class="material-symbols-outlined">${job.is_favorite ? "favorite" : "favorite_border"}</span></button>
          <button data-delete-id="${job.id}" class="danger icon-btn" title="${t("jobs.delete")}" aria-label="${t("jobs.delete")}"><span class="material-symbols-outlined">delete</span></button>
        </div>
      </td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      try {
        await _deps.performJobAction(id, action);
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-favorite]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const fav = btn.dataset.favorite === "1";
      try {
        await _deps.toggleFavorite(id, fav);
      } catch (error) {
        showToast(`${t("toast.favoriteError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.detailId;
      try {
        await _deps.showJobDetail(id);
      } catch (error) {
        showToast(`${t("toast.detailError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-delete-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.deleteId;
      if (!confirm(t("jobs.deleteConfirm"))) return;
      try {
        await api(`/api/jobs/${id}`, { method: "DELETE" });
        showToast(t("jobs.deleted"), "info");
        await loadJobs();
      } catch (error) {
        showToast(`${t("toast.deleteError")}: ${error.message}`, "info");
      }
    });
  });

  renderKanban(jobs);
}

// Kanban status change (via drag-drop or the per-card select). Maps the target
// column to a JobAction the backend understands, then re-renders.
let _kanbanDragId = null;
const _KANBAN_ACTION = {
  open: "reopened",
  applied: "applied",
  interviewing: "interviewing",
  rejected: "rejected",
  archived: "archived", // dropdown-only: no board column, the card just leaves
};

async function _kanbanMoveTo(jobId, targetStatus) {
  const action = _KANBAN_ACTION[targetStatus];
  if (!jobId || !action) return;
  await _deps.performJobAction(jobId, action); // POSTs the action, reloads + re-renders
}

// Wire drag-over/drop on the four columns ONCE (they persist across renders);
// per-card dragstart is (re)wired in renderKanban since cards are recreated.
let _kanbanDnDReady = false;
function initKanbanDnD() {
  if (_kanbanDnDReady) return;
  const kanbanView = document.getElementById("kanbanView");
  if (!kanbanView) return;
  kanbanView.querySelectorAll(".kanban-col").forEach((col) => {
    const target = col.dataset.status;
    col.addEventListener("dragover", (e) => {
      e.preventDefault();
      col.classList.add("drag-over");
    });
    col.addEventListener("dragleave", () => col.classList.remove("drag-over"));
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("drag-over");
      const id = _kanbanDragId || e.dataTransfer?.getData("text/plain");
      _kanbanDragId = null;
      if (id && target) _kanbanMoveTo(id, target);
    });
  });
  _kanbanDnDReady = true;
}

export function renderKanban(jobs) {
  const kanbanView = document.getElementById("kanbanView");
  if (!kanbanView) return;
  initKanbanDnD();

  const columns = {
    open: kanbanView.querySelector('.kanban-col[data-status="open"] .cards-container'),
    applied: kanbanView.querySelector('.kanban-col[data-status="applied"] .cards-container'),
    interviewing: kanbanView.querySelector('.kanban-col[data-status="interviewing"] .cards-container'),
    rejected: kanbanView.querySelector('.kanban-col[data-status="rejected"] .cards-container'),
  };

  Object.values(columns).forEach((container) => {
    if (container) container.innerHTML = "";
  });

  // "archived" has no kanban column: picking it archives the job and the
  // card leaves the board (same status the retention auto-archive uses).
  const statusOptions = ["open", "applied", "interviewing", "rejected", "archived"];
  const counts = { open: 0, applied: 0, interviewing: 0, rejected: 0 };
  for (const job of jobs || []) {
    const status = normalizeJobStatus(job.status);
    if (!(status in columns) || !columns[status]) continue;

    counts[status] += 1;
    const sc = scoreCell(job.punteggio_ai);
    const card = document.createElement("article");
    card.className = "kanban-card";
    card.draggable = true;
    card.dataset.id = String(job.id);
    card.dataset.status = status;
    const opts = statusOptions
      .map((s) => `<option value="${s}"${s === status ? " selected" : ""}>${t("jobs." + s)}</option>`)
      .join("");
    card.innerHTML = `
      <strong>${escapeHtml(job.titolo || t("jobs.titleUnavailable"))}</strong>
      <div class="micro">${escapeHtml(job.azienda || t("jobs.companyUnavailable"))}</div>
      <div class="micro">${t("jobs.score")}: <span class="${sc.cls}">${sc.text}</span></div>
      <div class="mini kanban-card-actions">
        <button class="secondary" data-k-detail-id="${job.id}">${t("jobs.details")}</button>
        <select class="kanban-status" data-id="${job.id}" aria-label="${t("jobs.colStatus")}">${opts}</select>
      </div>
    `;
    columns[status].appendChild(card);
  }

  setText("k-count-open", String(counts.open));
  setText("k-count-applied", String(counts.applied));
  setText("k-count-interviewing", String(counts.interviewing));
  setText("k-count-rejected", String(counts.rejected));

  kanbanView.querySelectorAll("button[data-k-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await _deps.showJobDetail(btn.dataset.kDetailId);
      } catch (error) {
        showToast(`${t("toast.detailError")}: ${error.message}`, "info");
      }
    });
  });

  kanbanView.querySelectorAll("select.kanban-status").forEach((sel) => {
    sel.addEventListener("change", () => _kanbanMoveTo(sel.dataset.id, sel.value));
  });

  kanbanView.querySelectorAll(".kanban-card").forEach((card) => {
    card.addEventListener("dragstart", (e) => {
      _kanbanDragId = card.dataset.id;
      e.dataTransfer?.setData("text/plain", card.dataset.id);
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
  });
}
