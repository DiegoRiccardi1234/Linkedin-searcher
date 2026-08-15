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
  // Compare selection lives in compare.js; injected (not imported) because
  // compare.js already imports this module's formatters — see initJobList.
  isCompareSelected: () => false,
  toggleCompare: () => true,
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

// Why a score is what it is: stable codes from the backend (see
// scanner_service), rendered as badges so a hard blocker stops arriving as a
// bare 3/10. Order matters — blockers first.
const FLAG_BADGES = [
  // First: without it, an empty score cell looks like a rendering glitch rather
  // than the deliberate statement that nobody judged this offer.
  { code: "non_valutato", cls: "flag-info", icon: "help", key: "jobs.flag.notEvaluated" },
  { code: "geo_non_ue", cls: "flag-block", icon: "public_off", key: "jobs.flag.geo" },
  { code: "voto_minimo", cls: "flag-block", icon: "school", key: "jobs.flag.grade" },
  // The constraints the user declares and the app enforces. Location and the
  // protected-categories register are not arguable, so they read as blockers;
  // years and degree only lower a ceiling (see WEIGHTED_FLAGS), so they warn.
  { code: "sede_non_raggiungibile", cls: "flag-block", icon: "wrong_location", key: "jobs.flag.location" },
  { code: "categorie_protette", cls: "flag-block", icon: "accessible", key: "jobs.flag.protected" },
  { code: "patente_richiesta", cls: "flag-block", icon: "no_crash", key: "jobs.flag.licence" },
  { code: "esperienza_richiesta", cls: "flag-warn", icon: "work_history", key: "jobs.flag.experience" },
  { code: "titolo_superiore", cls: "flag-warn", icon: "school", key: "jobs.flag.education" },
  { code: "campo_studio", cls: "flag-warn", icon: "menu_book", key: "jobs.flag.degreeField" },
  { code: "lavoro_a_task", cls: "flag-warn", icon: "task_alt", key: "jobs.flag.gig" },
  { code: "ral_sotto_minima", cls: "flag-warn", icon: "payments", key: "jobs.flag.salary" },
  { code: "annuncio_aggregatore", cls: "flag-warn", icon: "content_copy", key: "jobs.flag.aggregator" },
  { code: "descrizione_breve", cls: "flag-info", icon: "notes", key: "jobs.flag.shortDesc" },
  { code: "analisi_locale", cls: "flag-info", icon: "psychology_alt", key: "jobs.flag.heuristic" },
];

// Not a flag on the analysis: these rows have no analysis. It is where the row
// came from, and it explains at a glance why there is no title and no score —
// the posting never went through Job Finder, only the confirmation email did.
export function sourceBadgeHtml(job) {
  if ((job?.fonte || "") !== "mail") return "";
  const label = t("jobs.flag.mailImport");
  return (
    `<span class="job-flag flag-info" title="${escapeHtml(t("jobs.flag.mailImportLong"))}">` +
    `<span class="material-symbols-outlined">mail</span><span>${escapeHtml(label)}</span></span>`
  );
}

export function flagBadgesHtml(flags, { compact = false } = {}) {
  const codes = new Set(Array.isArray(flags) ? flags : []);
  return FLAG_BADGES.filter((f) => codes.has(f.code))
    .map((f) => {
      const label = t(f.key);
      // The tooltip repeated the label, which told a hovering user nothing.
      // Where a "…Long" key exists it explains the badge instead. A missing key
      // comes back as the key itself, so the check is against that, not against
      // falsiness.
      const longKey = `${f.key}Long`;
      const long = t(longKey);
      const title = long === longKey ? label : long;
      const text = compact ? "" : `<span>${escapeHtml(label)}</span>`;
      return (
        `<span class="job-flag ${f.cls}" title="${escapeHtml(title)}">` +
        `<span class="material-symbols-outlined">${f.icon}</span>${text}</span>`
      );
    })
    .join("");
}

// "Last seen" ages a job better than its posting date: the scan re-sees live
// postings, so a job that stopped showing up is probably closed.
export function freshnessHtml(lastSeenAt) {
  if (!lastSeenAt) return "";
  const seen = new Date(lastSeenAt);
  if (isNaN(seen.getTime())) return "";
  const days = Math.floor((Date.now() - seen.getTime()) / 86400000);
  if (days < 7) return "";
  const stale = days >= 30;
  const label = stale
    ? t("jobs.freshness.likelyExpired")
    : t("jobs.freshness.daysAgo").replace("{n}", String(days));
  return (
    `<span class="job-flag ${stale ? "flag-warn" : "flag-info"}" title="${escapeHtml(label)}">` +
    `<span class="material-symbols-outlined">schedule</span><span>${escapeHtml(label)}</span></span>`
  );
}

// The posting was opened and nothing has come back yet. Not a flag from
// analysis_json like the badges above — those describe the OFFER, this one
// describes what the user did — so it lives beside them rather than in the list.
export function pendingBadgeHtml(job) {
  if (!job || !job.link_opened_at || normalizeJobStatus(job.status) !== "open") return "";
  const label = t("jobs.applyPending");
  return (
    `<span class="job-flag flag-warn" title="${escapeHtml(label)}">` +
    `<span class="material-symbols-outlined">hourglass_top</span>` +
    `<span>${escapeHtml(label)}</span></span>`
  );
}

// Client-side ordering. The server always returns score-desc; the table had no
// way to answer "what came in most recently" or "who is hiring" without
// re-reading every row by eye.
let _sort = { key: null, dir: -1 };

function _sortJobs(jobs) {
  if (!_sort.key) return jobs;
  const key = _sort.key;
  const numeric = key === "punteggio_ai";
  return [...jobs].sort((a, b) => {
    const va = a[key] ?? (numeric ? -1 : "");
    const vb = b[key] ?? (numeric ? -1 : "");
    if (numeric) return (Number(va) - Number(vb)) * _sort.dir;
    return String(va).localeCompare(String(vb), getCurrentLang()) * _sort.dir;
  });
}

function _paintSortHeaders() {
  document.querySelectorAll("th.th-sort").forEach((th) => {
    const active = th.dataset.sort === _sort.key;
    th.classList.toggle("is-sorted", active);
    th.dataset.dir = active ? (_sort.dir === 1 ? "asc" : "desc") : "";
  });
}

function _kanbanBadges(job) {
  const badges =
    flagBadgesHtml(job.flags, { compact: true }) +
    freshnessHtml(job.last_seen_at) +
    pendingBadgeHtml(job);
  return badges ? `<div class="job-flags">${badges}</div>` : "";
}

export function initJobSorting() {
  document.querySelectorAll("th.th-sort").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      // Same column toggles direction; a new column starts descending for the
      // score (best first) and ascending for text.
      if (_sort.key === key) _sort.dir = -_sort.dir;
      else _sort = { key, dir: key === "punteggio_ai" ? -1 : 1 };
      _paintSortHeaders();
      loadJobs();
    });
  });
}

export async function loadJobs() {
  const onlyNew = document.getElementById("onlyNew").checked;
  const onlyFavorites = document.getElementById("onlyFavorites").checked;
  const remoteOnly = document.getElementById("remoteOnly").checked;
  const applicableOnly = document.getElementById("applicableOnly")?.checked || false;
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
  if (applicableOnly) query.set("applicable_only", "true");
  if (searchText) query.set("search_text", searchText);
  if (status) query.set("status", status);
  if (minScoreRaw) query.set("min_score", minScoreRaw);
  if (maxAgeRaw) query.set("max_age_days", maxAgeRaw);

  const COLS = 9;
  const body = document.getElementById("jobsTableBody");
  const fullRow = (cls, msg) => `<tr><td colspan="${COLS}" class="${cls}">${msg}</td></tr>`;
  // Discarding a job reloads the list, and emptying <tbody> collapses the page
  // height — at which point the browser has nowhere to scroll to and jumps back
  // to the top. Remember where the user was and put them back after the render.
  const scroller = body.closest(".table-wrap");
  const prevWindowY = window.scrollY;
  const prevInnerY = scroller ? scroller.scrollTop : 0;
  const restoreScroll = () => {
    if (scroller && prevInnerY) scroller.scrollTop = prevInnerY;
    if (prevWindowY) window.scrollTo({ top: prevWindowY, behavior: "instant" });
  };
  body.innerHTML = fullRow("table-empty", "…");

  let jobs;
  try {
    ({ jobs } = await api(`/api/jobs?${query.toString()}`));
  } catch (err) {
    console.error("loadJobs failed", err);
    body.innerHTML = fullRow("table-empty table-error", t("jobs.loadError") || "Couldn't load jobs.");
    restoreScroll();
    return;
  }

  jobs = _sortJobs(jobs);
  body.innerHTML = "";
  if (!jobs.length) {
    const filtered =
      onlyNew ||
      onlyFavorites ||
      remoteOnly ||
      applicableOnly ||
      searchText ||
      status ||
      minScoreRaw ||
      maxAgeRaw;
    body.innerHTML = fullRow(
      "table-empty",
      filtered ? t("jobs.emptyFiltered") || "No jobs match these filters."
               : t("jobs.emptyNoJobs") || "No jobs yet — run your first scan.",
    );
    renderKanban(jobs);
    restoreScroll();
    return;
  }

  for (const job of jobs) {
    const newBadge = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
    const sc = scoreCell(job.punteggio_ai);
    const badges =
      sourceBadgeHtml(job) +
      flagBadgesHtml(job.flags, { compact: true }) +
      freshnessHtml(job.last_seen_at) +
      pendingBadgeHtml(job);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="${sc.cls}">${sc.text}</span> ${newBadge}</td>
      <td>${statusPillHtml(job.status)}</td>
      <td>${escapeHtml(truncate(job.titolo || t("jobs.titleUnavailable")))}${badges ? ` <span class="job-flags">${badges}</span>` : ""}</td>
      <td>${escapeHtml(truncate(job.azienda || ""))}</td>
      <td>${escapeHtml(truncate(job.sede || ""))}</td>
      <td>${escapeHtml(truncate(job.fonte || ""))}</td>
      <td>${escapeHtml(truncate(job.consiglio || (sc.cls === "score-none" ? t("jobs.adviceToEvaluate") : "")))}</td>
      <td>
        <label class="compare-pick" title="${escapeHtml(t("compare.pick"))}"><input type="checkbox" class="compare-check" data-compare-id="${job.id}"${_deps.isCompareSelected(job.id) ? " checked" : ""} /><span class="material-symbols-outlined">compare_arrows</span></label>
        <button data-detail-id="${job.id}" class="secondary">${t("jobs.details")}</button>
        ${job.link ? `<a href="${escapeHtml(job.link)}" target="_blank" rel="noopener" data-job-link="${job.id}" style="margin-left: 8px;" title="${t("jobs.openPosting")}" aria-label="${t("jobs.openPosting")}">🔗</a>` : ""}
      </td>
      <td>
        <div class="mini">
          <button class="apply-btn" data-action="applied" data-id="${job.id}">${t("jobs.apply")}</button>
          <button data-action="rejected" data-id="${job.id}" class="danger">${t("jobs.skip")}</button>
          <button data-action="reopened" data-id="${job.id}" class="secondary icon-btn" title="${t("jobs.reopen")}" aria-label="${t("jobs.reopen")}"><span class="material-symbols-outlined">restart_alt</span></button>
          <button data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary icon-btn${job.is_favorite ? " is-active" : ""}" title="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}" aria-label="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}"><span class="material-symbols-outlined">${job.is_favorite ? "star" : "star_border"}</span></button>
          <button data-delete-id="${job.id}" class="danger icon-btn" title="${t("jobs.delete")}" aria-label="${t("jobs.delete")}"><span class="material-symbols-outlined">delete</span></button>
        </div>
      </td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll("input.compare-check").forEach((box) => {
    box.addEventListener("change", () => {
      // toggleCompare refuses past the max and says so; keep the box in sync.
      if (!_deps.toggleCompare(box.dataset.compareId)) box.checked = false;
    });
  });

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
  restoreScroll();
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
      <button type="button" data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="kanban-fav-btn icon-btn${job.is_favorite ? " is-active" : ""}" title="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}" aria-label="${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}"><span class="material-symbols-outlined">${job.is_favorite ? "star" : "star_border"}</span></button>
      ${_kanbanBadges(job)}
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

  kanbanView.querySelectorAll("button.kanban-fav-btn").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation(); // the card is draggable and clickable
      _deps.toggleFavorite(btn.dataset.id, btn.dataset.favorite === "1");
    });
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
