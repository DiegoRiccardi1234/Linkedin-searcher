import { syncFeatureToggles } from "./features.js";
import { api, escapeHtml, renderCoachMarkdown, showToast } from "./helpers.js";
import { getCurrentLang, t } from "./i18n.js";

const FIELDS = ["preferred_roles", "skills", "languages"];

const _state = {
  profile: null,
};

function _chipContainerId(field) {
  return field === "preferred_roles" ? "profileRoles"
    : field === "skills" ? "profileSkills"
    : field === "languages" ? "profileLanguages"
    : "";
}

function _renderChips(containerId, items, field) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!items || !items.length) {
    el.innerHTML = `<span class="micro chip-list-empty">${t("profile.emptyField") || "—"}</span>`;
    return;
  }
  el.innerHTML = items
    .map(
      (item, idx) => `
        <span class="chip" data-idx="${idx}" data-field="${field}">
          <span class="chip-label">${escapeHtml(item)}</span>
          <button type="button" class="chip-remove" data-idx="${idx}" data-field="${field}" title="${t("profile.remove") || "Remove"}">×</button>
        </span>
      `,
    )
    .join("");
}

function _activeList(field) {
  const summary = _state.profile?.summary_json || {};
  return Array.isArray(summary[field]) ? [...summary[field]] : [];
}

/** A year count as a person would say it: months under a year, years above. */
function _formatYears(years) {
  if (years < 1) {
    const months = Math.round(years * 12) || 1;
    const word = months === 1 ? t("profile.month") || "month" : t("profile.months") || "months";
    return `${months} ${word}`;
  }
  // Integers stay integers: toLocaleString drops the ".0" that String() keeps.
  const shown = years.toLocaleString(undefined, { maximumFractionDigits: 1 });
  const word = years === 1 ? t("profile.year") || "year" : t("profile.years") || "years";
  return `${shown} ${word}`;
}

function _renderExperience(summary) {
  const el = document.getElementById("profileExperience");
  if (!el) return;
  const level = summary?.experience_level;
  const years = summary?.years_experience;
  const narrative = summary?.summary || summary?.experience;
  const strengths = Array.isArray(summary?.strengths) ? summary.strengths : [];
  const industries = Array.isArray(summary?.industries) ? summary.industries : [];
  const education = summary?.education || summary?.graduation_year;

  const parts = [];
  const headline = [];
  if (level) headline.push(`<span class="exp-level">${escapeHtml(String(level))}</span>`);
  // Under a year reads in months. The old floor was ">= 1", which hid every
  // graduate's first internship behind a bare "Junior" — the same half year the
  // scoring path used to round away.
  if (years !== undefined && years !== null && years !== "" && Number(years) > 0) {
    headline.push(`<span class="exp-years">${escapeHtml(_formatYears(Number(years)))}</span>`);
  }
  if (headline.length) {
    parts.push(`<p class="exp-headline">${headline.join(" · ")}</p>`);
  }
  if (narrative) {
    if (Array.isArray(narrative)) {
      parts.push(narrative.map((item) => `<p>${escapeHtml(String(item))}</p>`).join(""));
    } else {
      parts.push(`<p class="exp-summary">${escapeHtml(String(narrative))}</p>`);
    }
  }
  if (strengths.length) {
    parts.push(
      `<div class="exp-meta"><strong>${t("profile.strengths") || "Strengths"}:</strong> ${strengths.map(escapeHtml).join(", ")}</div>`,
    );
  }
  if (industries.length) {
    parts.push(
      `<div class="exp-meta"><strong>${t("profile.industries") || "Industries"}:</strong> ${industries.map(escapeHtml).join(", ")}</div>`,
    );
  }
  if (education) {
    // education is either a graduation year (string/number) or an object
    // {level, field} from the LLM summary — format the object, don't String() it.
    const eduText =
      education && typeof education === "object"
        ? [education.level, education.field].filter(Boolean).join(" — ")
        : String(education);
    if (eduText) {
      parts.push(
        `<div class="exp-meta"><strong>${t("profile.education") || "Education"}:</strong> ${escapeHtml(eduText)}</div>`,
      );
    }
  }
  el.innerHTML = parts.length ? parts.join("\n") : `<p class="micro">${t("profile.emptyField") || "—"}</p>`;
}

function _renderMarkdown(markdown) {
  const el = document.getElementById("profileMarkdown");
  if (!el) return;
  if (!markdown) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = _formatCvText(markdown);
}

const _DATE_PREFIX_RE = /^\s*(?:\d{1,2}\/\d{4}|\d{4})\s*[-–]\s*(?:\d{1,2}\/\d{4}|\d{4}|presente|present|current|in corso)/i;
const _SECTION_KEYWORDS_RE = /^(profilo|profile|esperienza|experience|esperienza\s+lavorativa|work\s+experience|istruzione|education|formazione|skill|skills|competenze|competenze\s+tecniche|hard\s+skills|soft\s+skills|lingue|languages|certificazioni|certifications|progetti|projects|interessi|interests|hobby|contatti|contacts)\b/i;

function _formatCvText(raw) {
  const text = String(raw || "");
  const lines = text.split(/\r?\n/);
  const out = [];
  let nameAssigned = false;
  let contactAssigned = false;
  let inList = false;

  const flushList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      out.push('<div class="cv-spacer"></div>');
      continue;
    }
    const safe = escapeHtml(trimmed);

    if (!nameAssigned) {
      flushList();
      out.push(`<h2 class="cv-name">${safe}</h2>`);
      nameAssigned = true;
      continue;
    }
    if (!contactAssigned && (trimmed.includes("@") || trimmed.includes("|"))) {
      flushList();
      out.push(`<p class="cv-contact">${safe.replace(/\s*\|\s*/g, ' <span class="cv-sep">·</span> ')}</p>`);
      contactAssigned = true;
      continue;
    }

    const isAllCaps = trimmed.length > 2 && trimmed === trimmed.toUpperCase() && /[A-Z]/.test(trimmed);
    if (isAllCaps || _SECTION_KEYWORDS_RE.test(trimmed)) {
      flushList();
      out.push(`<h3 class="cv-section">${safe}</h3>`);
      continue;
    }

    const bulletMatch = trimmed.match(/^[-•·]\s+(.*)$/);
    if (bulletMatch) {
      if (!inList) { out.push('<ul class="cv-list">'); inList = true; }
      out.push(`<li>${escapeHtml(bulletMatch[1])}</li>`);
      continue;
    }

    if (_DATE_PREFIX_RE.test(trimmed)) {
      flushList();
      out.push(`<p class="cv-role">${safe}</p>`);
      continue;
    }

    flushList();
    out.push(`<p class="cv-line">${safe}</p>`);
  }
  flushList();
  return out.join("\n");
}

function _renderMeta(profile) {
  const el = document.getElementById("profileMeta");
  if (!el) return;
  const created = profile?.created_at || "";
  const source = profile?.source_name || "";
  el.textContent = source ? `${source} · ${created}` : created;
}

async function _renderHistory() {
  const el = document.getElementById("profileHistory");
  if (!el) return;
  el.innerHTML = `<p class="micro">${t("profile.loading") || "Loading..."}</p>`;
  try {
    const payload = await api("/api/profiles");
    const list = payload.profiles || [];
    const active = String(payload.active_profile_id || "");
    if (!list.length) {
      el.innerHTML = `<p class="micro">${t("profile.emptyField") || "—"}</p>`;
      return;
    }
    el.innerHTML = list
      .map((p) => {
        const isActive = String(p.id) === active;
        return `
          <div class="profile-history-row ${isActive ? "is-active" : ""}" data-id="${p.id}">
            <div class="profile-history-meta">
              <strong>${escapeHtml(p.source_name || "CV")}</strong>
              <span class="micro">${escapeHtml(p.created_at || "")}</span>
            </div>
            <div class="profile-history-actions">
              ${
                isActive
                  ? `<span class="badge">${t("profile.active") || "Active"}</span>`
                  : `<button type="button" class="ghost-btn profile-activate-btn" data-id="${p.id}">${t("profile.setActive") || "Set active"}</button>`
              }
              <button type="button" class="ghost-btn profile-delete-btn" data-id="${p.id}" title="${t("profile.delete") || "Delete"}">
                <span class="material-symbols-outlined">delete</span>
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  } catch (err) {
    el.innerHTML = `<p class="micro">${escapeHtml(err.message)}</p>`;
  }
}

function _initialsFromName(name) {
  if (!name) return "";
  const words = String(name).trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  return words.slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

function _updateAvatar(profile) {
  const el = document.querySelector(".topbar-right .avatar");
  if (!el) return;
  const name = profile?.name || profile?.summary_json?.name;
  const initials = _initialsFromName(name);
  if (initials) {
    el.textContent = initials;
    el.setAttribute("title", name);
  } else {
    el.textContent = "?";
    el.removeAttribute("title");
  }
}

// ── CV tools panel (AI review + AI improve), shared output + markdown render ──
let _cvImprovedText = ""; // last "improve" output, for "Save as new CV"

function _cvEls() {
  return {
    wrap: document.getElementById("cvToolsOutputWrap"),
    out: document.getElementById("cvToolsOutput"),
    label: document.getElementById("cvToolsLabel"),
    save: document.getElementById("cvSaveAsProfileBtn"),
  };
}

function _showCvOutput(labelText, markdown, { improved = false } = {}) {
  const { wrap, out, label, save } = _cvEls();
  if (!wrap || !out) return;
  wrap.classList.remove("hidden");
  if (label) label.textContent = labelText || "";
  out.innerHTML = renderCoachMarkdown(markdown || "");
  if (save) save.hidden = !improved;
  _cvImprovedText = improved ? markdown || "" : "";
}

async function _runCvTool(kind) {
  const btnId = kind === "improve" ? "cvImproveBtn" : "cvReviewBtn";
  const btn = document.getElementById(btnId);
  if (!btn) return;
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.generating")}`;
  const { wrap, out, label } = _cvEls();
  if (wrap) wrap.classList.remove("hidden");
  if (label) label.textContent = t("toast.generating");
  if (out) out.textContent = "";
  try {
    const lang = encodeURIComponent(getCurrentLang());
    const path =
      kind === "improve"
        ? `/api/profile/cv-improve?lang=${lang}`
        : `/api/profile/cv-review?lang=${lang}`;
    const res = await api(path, { method: "POST" });
    const text = (kind === "improve" ? res.cv_improved : res.cv_review) || t("toast.noResult");
    _showCvOutput(
      kind === "improve" ? t("profile.cvImprovedLabel") : t("profile.cvReviewLabel"),
      text,
      { improved: kind === "improve" },
    );
  } catch (err) {
    _showCvOutput("", `${t("toast.genError")}: ${err.message}`);
    showToast(`${t("toast.genError")}: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

async function _copyCvOutput() {
  const { out } = _cvEls();
  try {
    await navigator.clipboard.writeText((out && out.textContent) || "");
    showToast(t("offcanvas.copied") || "Copied", "info");
  } catch (e) {
    showToast(String(e), "error");
  }
}

async function _saveImprovedAsProfile() {
  if (!_cvImprovedText) return;
  if (!window.confirm(t("profile.cvSaveConfirm") || "Save this improved CV as your active profile?"))
    return;
  try {
    await api("/api/profile/from-text", {
      method: "POST",
      body: JSON.stringify({ markdown: _cvImprovedText, source_name: t("profile.cvImprovedSource") }),
    });
    showToast(t("profile.cvSaved") || "Saved", "info");
    await loadProfile();
  } catch (err) {
    showToast(`${t("toast.genError")}: ${err.message}`, "error");
  }
}

async function _rehydrateCvReview() {
  try {
    const res = await api("/api/profile/cv-review", { method: "GET" });
    if (res && res.cv_review) _showCvOutput(t("profile.cvReviewLabel"), res.cv_review);
  } catch (_) {
    /* noop */
  }
}

// ── Manual CV editing (name + raw text; the text feeds AI scoring) ───────────
function _toggleCvEdit(show) {
  const wrap = document.getElementById("cvEditWrap");
  const md = document.getElementById("profileMarkdown");
  const btn = document.getElementById("cvEditBtn");
  if (!wrap || !md) return;
  if (show) {
    const summary = _state.profile?.summary_json || {};
    document.getElementById("cvEditArea").value = _state.profile?.markdown || "";
    document.getElementById("cvEditName").value = _state.profile?.name || summary.name || "";
    wrap.classList.remove("hidden");
    md.classList.add("hidden");
    btn?.classList.add("hidden");
  } else {
    wrap.classList.add("hidden");
    md.classList.remove("hidden");
    btn?.classList.remove("hidden");
  }
}

async function _saveCvEdit() {
  const markdown = (document.getElementById("cvEditArea")?.value || "").trim();
  const name = (document.getElementById("cvEditName")?.value || "").trim();
  try {
    await api("/api/profile", { method: "PATCH", body: JSON.stringify({ markdown, name }) });
    showToast(t("profile.saved") || "Saved", "info");
    _toggleCvEdit(false);
    await loadProfile();
  } catch (err) {
    showToast(`${t("toast.genError")}: ${err.message}`, "error");
  }
}

const _ONBOARDING_FIELDS = [
  ["obSector", "onboarding_sector"],
  ["obGoal", "onboarding_goal"],
  ["obSeniority", "onboarding_seniority"],
  ["obWorkMode", "onboarding_work_mode"],
  ["obRalMin", "onboarding_ral_min"],
  ["obRalTarget", "onboarding_ral_target"],
];

// Fills the two salary fields from an AI suggestion. It never saves them: the
// numbers go into a negotiation, so the user confirms with "Save goals".
function _applyRalSuggestion(data) {
  const note = document.getElementById("ralSuggestNote");
  if (!data || (!data.min && !data.target)) {
    if (note) note.hidden = true;
    return false;
  }
  const min = document.getElementById("obRalMin");
  const target = document.getElementById("obRalTarget");
  if (min && data.min) min.value = data.min;
  if (target && data.target) target.value = data.target;
  if (note) {
    note.textContent = data.rationale || "";
    note.hidden = !data.rationale;
  }
  return true;
}

async function _suggestRal() {
  const btn = document.getElementById("ralSuggestBtn");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/profile/ral-suggest", { method: "POST" });
    if (_applyRalSuggestion(data)) {
      showToast(t("profile.onboarding.ralSuggested") || "Salary suggested", "info");
    }
  } catch (err) {
    showToast(`${t("toast.genError")}: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Same contract as the salary suggestion: prefill, never save. What you tell
// the app you are looking for is your call — the AI just proposes a starting
// point from your CV and from the market the scans actually found.
function _applyGoalsSuggestion(data) {
  const note = document.getElementById("goalsSuggestNote");
  const fields = [
    ["obSector", data?.sector],
    ["obGoal", data?.goal],
    ["obSeniority", data?.seniority],
    ["obWorkMode", data?.work_mode],
  ];
  const filled = fields.filter(([, value]) => value);
  if (!filled.length) {
    if (note) note.hidden = true;
    return false;
  }
  for (const [id, value] of filled) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
  if (note) {
    note.textContent = data.rationale || "";
    note.hidden = !data.rationale;
  }
  return true;
}

async function _suggestGoals() {
  const btn = document.getElementById("goalsSuggestBtn");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/profile/goals-suggest", { method: "POST" });
    if (_applyGoalsSuggestion(data)) {
      showToast(t("profile.onboarding.goalsSuggested"), "info");
    }
  } catch (err) {
    showToast(`${t("toast.genError")}: ${err.message}`, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _prefillOnboarding() {
  try {
    const health = await api("/api/health");
    const prefs = health.preferences || {};
    for (const [id, key] of _ONBOARDING_FIELDS) {
      const el = document.getElementById(id);
      if (el && prefs[key]) el.value = prefs[key];
    }
    // Rehydrate the last rationales from cache — no tokens spent.
    const cached = await api("/api/profile/ral-suggest");
    const note = document.getElementById("ralSuggestNote");
    if (note && cached && cached.rationale) {
      note.textContent = cached.rationale;
      note.hidden = false;
    }
    const cachedGoals = await api("/api/profile/goals-suggest");
    const goalsNote = document.getElementById("goalsSuggestNote");
    if (goalsNote && cachedGoals && cachedGoals.rationale) {
      goalsNote.textContent = cachedGoals.rationale;
      goalsNote.hidden = false;
    }
  } catch {
    /* best-effort prefill */
  }
}

async function _saveOnboarding() {
  // Every write used to be swallowed and the success toast shown regardless —
  // including for the salary floor, which the scorer then silently never had.
  let failed = 0;
  for (const [id, key] of _ONBOARDING_FIELDS) {
    const el = document.getElementById(id);
    const value = (el?.value || "").trim();
    try {
      await api("/api/preferences", { method: "POST", body: JSON.stringify({ key, value }) });
    } catch {
      failed += 1;
    }
  }
  if (failed) showToast(t("profile.onboarding.saveFailed"), "error");
  else showToast(t("profile.onboarding.saved"), "info");
}

export async function loadProfile() {
  try {
    const payload = await api("/api/profile");
    _state.profile = payload.profile;
    syncFeatureToggles();
    const empty = document.getElementById("profileEmpty");
    // The CV-dependent cards are now spread across four sub-tabs, so one
    // wrapper can no longer hide them — they carry `data-needs-profile` and a
    // class on the view does it. Which also fixes something the wrapper got
    // wrong: the privacy switch used to be hidden until a CV existed, and
    // whether the CV is redacted before it reaches a model is exactly what you
    // want to decide BEFORE uploading one.
    const view = document.getElementById("view-profile");
    if (!_state.profile) {
      _updateAvatar(null);
      empty?.classList.remove("hidden");
      view?.classList.add("no-profile");
      return;
    }
    _updateAvatar(_state.profile);
    empty?.classList.add("hidden");
    view?.classList.remove("no-profile");
    const summary = _state.profile.summary_json || {};
    _renderChips("profileRoles", summary.preferred_roles || [], "preferred_roles");
    _renderChips("profileSkills", summary.skills || [], "skills");
    _renderChips("profileLanguages", summary.languages || [], "languages");
    _renderExperience(summary);
    _renderMarkdown(_state.profile.markdown || "");
    _renderMeta(_state.profile);
    await _renderHistory();
    // Reset the CV tools panel for this profile, then rehydrate its cached review.
    document.getElementById("cvToolsOutputWrap")?.classList.add("hidden");
    _cvImprovedText = "";
    await _rehydrateCvReview();
  } catch (err) {
    showToast(`${t("profile.loadFailed") || "Profile load failed"}: ${err.message}`, "error");
  }
}

async function _persistField(field, list) {
  try {
    const body = {};
    body[field] = list;
    const res = await api("/api/profile", {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    _state.profile = res.profile;
    const summary = _state.profile?.summary_json || {};
    _renderChips(_chipContainerId(field), summary[field] || [], field);
    showToast(t("profile.chipSaved") || "Saved", "info");
    return true;
  } catch (err) {
    showToast(`${t("profile.saveFailed") || "Save failed"}: ${err.message}`, "error");
    return false;
  }
}

async function _activateProfile(id) {
  try {
    await api(`/api/profiles/${id}/activate`, { method: "POST" });
    await loadProfile();
    showToast(t("profile.activated") || "Profile activated", "info");
  } catch (err) {
    showToast(`${t("toast.keySaveError") || "Error"}: ${err.message}`, "error");
  }
}

async function _deleteProfile(id) {
  if (!window.confirm(t("profile.confirmDelete") || "Delete this CV from the history?")) return;
  try {
    await api(`/api/profiles/${id}`, { method: "DELETE" });
    if (_state.profile && Number(_state.profile.id) === Number(id)) {
      _state.profile = null;
    }
    await loadProfile();
    showToast(t("profile.deleted") || "Profile deleted", "info");
  } catch (err) {
    showToast(`${t("profile.deleteFailed") || "Delete failed"}: ${err.message}`, "error");
  }
}

/**
 * Public helper: append `roles` to the active profile's preferred_roles list,
 * de-duplicating case-insensitively. Used by the chat coach to push AI-suggested
 * roles directly into the user's profile (the Job Search wizard reads them).
 */
export async function addRolesToProfile(roles) {
  const incoming = (Array.isArray(roles) ? roles : [roles])
    .map((r) => String(r || "").trim())
    .filter(Boolean);
  if (!incoming.length) return false;

  if (!_state.profile) {
    try {
      const payload = await api("/api/profile");
      _state.profile = payload.profile;
    } catch (err) {
      showToast(`${t("profile.loadFailed") || "Profile load failed"}: ${err.message}`, "error");
      return false;
    }
    if (!_state.profile) {
      showToast(t("profile.empty") || "Upload a CV first", "info");
      return false;
    }
  }

  const current = _activeList("preferred_roles");
  const lower = new Set(current.map((r) => r.toLowerCase()));
  const merged = [...current];
  for (const role of incoming) {
    if (!lower.has(role.toLowerCase())) {
      merged.push(role);
      lower.add(role.toLowerCase());
    }
  }
  if (merged.length === current.length) return false;
  return _persistField("preferred_roles", merged);
}

export function bindProfileEvents() {
  const root = document.getElementById("view-profile");
  if (!root) return;

  root.addEventListener("click", async (event) => {
    const target = event.target.closest("button");
    if (!target) return;

    if (target.id === "cvReviewBtn") {
      await _runCvTool("review");
      return;
    }
    if (target.id === "cvImproveBtn") {
      await _runCvTool("improve");
      return;
    }
    if (target.id === "cvToolsCopyBtn") {
      await _copyCvOutput();
      return;
    }
    if (target.id === "cvSaveAsProfileBtn") {
      await _saveImprovedAsProfile();
      return;
    }
    if (target.id === "cvEditBtn") {
      _toggleCvEdit(true);
      return;
    }
    if (target.id === "cvEditCancelBtn") {
      _toggleCvEdit(false);
      return;
    }
    if (target.id === "cvEditSaveBtn") {
      await _saveCvEdit();
      return;
    }
    if (target.classList.contains("chip-remove")) {
      const field = target.dataset.field;
      const idx = parseInt(target.dataset.idx || "-1", 10);
      const list = _activeList(field);
      if (idx < 0 || idx >= list.length) return;
      list.splice(idx, 1);
      await _persistField(field, list);
      return;
    }
    if (target.classList.contains("profile-add-btn")) {
      const field = target.dataset.field;
      const row = target.closest(".profile-edit-row");
      const input = row?.querySelector(".profile-add-input");
      const value = (input?.value || "").trim();
      if (!value) return;
      const list = _activeList(field);
      if (!list.some((existing) => existing.toLowerCase() === value.toLowerCase())) {
        list.push(value);
        await _persistField(field, list);
      }
      if (input) input.value = "";
      return;
    }
    if (target.classList.contains("profile-activate-btn")) {
      const id = target.dataset.id;
      if (id) await _activateProfile(parseInt(id, 10));
      return;
    }
    if (target.classList.contains("profile-delete-btn")) {
      const id = target.dataset.id;
      if (id) await _deleteProfile(parseInt(id, 10));
    }
  });

  root.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (!event.target.classList.contains("profile-add-input")) return;
    event.preventDefault();
    const row = event.target.closest(".profile-edit-row");
    const btn = row?.querySelector(".profile-add-btn");
    if (btn) btn.click();
  });

  const onboardingForm = document.getElementById("onboardingForm");
  if (onboardingForm) {
    onboardingForm.addEventListener("submit", (event) => {
      event.preventDefault();
      _saveOnboarding();
    });
  }
  document.getElementById("ralSuggestBtn")?.addEventListener("click", _suggestRal);
  document.getElementById("goalsSuggestBtn")?.addEventListener("click", _suggestGoals);
  _prefillOnboarding();
}

// ── Matching facts: the four things that decide applicability ────────────────
// Rendered with their provenance (from the CV / entered by you / MISSING) because
// a fact the parser failed to read now silently stops blocking, and the user has
// to be able to see that and fix it.
let _facts = null;

const _FACT_ROWS = [
  { key: "years_experience", i18n: "profile.matching.years" },
  { key: "education_level", i18n: "profile.matching.education" },
  { key: "grade", i18n: "profile.matching.grade" },
  // Shown as well as editable now. They gate offers exactly like the three
  // above — a missing degree subject or an unanswered licence question is the
  // difference between a field role being takeable or not — and the panel used
  // to report neither.
  { key: "degree_fields", i18n: "readiness.item.degree_fields" },
  { key: "driving_licence", i18n: "readiness.item.driving_licence" },
  { key: "protected_category", i18n: "readiness.item.protected_category" },
];

function _sourceTag(origin) {
  const key =
    origin === "manuale"
      ? "profile.matching.fromYou"
      : origin === "cv"
        ? "profile.matching.fromCv"
        : "profile.matching.missing";
  const cls = origin === "mancante" ? "flag-block" : "flag-info";
  return `<span class="job-flag ${cls}">${escapeHtml(t(key))}</span>`;
}

/** A fact as a person reads it: a list joins, a yes/no is a word, unset is a dash.
 *
 * `String(false)` renders "false", and false is a real answer here — "I do not
 * hold a licence" is the whole reason the licence check can block anything.
 */
function _readableFact(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "boolean") return value ? t("common.yes") : t("common.no");
  return String(value);
}

export async function loadMatchingFacts() {
  const view = document.getElementById("matchingFactsView");
  if (!view) return;
  try {
    _facts = await api("/api/profile/matching-facts");
  } catch {
    return;
  }
  const rows = _FACT_ROWS.map((row) => {
    const value = _facts[row.key];
    const shown = _readableFact(value);
    return (
      `<div class="profile-experience-row"><span>${escapeHtml(t(row.i18n))}</span>` +
      `<strong>${escapeHtml(String(shown))}</strong> ${_sourceTag(_facts.sources?.[row.key])}</div>`
    );
  });
  rows.push(
    `<div class="profile-experience-row"><span>${escapeHtml(t("profile.matching.rule"))}</span>` +
      `<strong>${escapeHtml(_facts.rule_summary || "—")}</strong> ${_sourceTag(_facts.sources?.work_rule)}</div>`,
  );
  view.innerHTML = rows.join("");
}

function _openFactsEditor() {
  if (!_facts) return;
  const sel = document.getElementById("factEducation");
  if (sel) {
    sel.innerHTML =
      `<option value=""></option>` +
      (_facts.education_levels || [])
        .map((lvl) => `<option value="${escapeHtml(lvl)}">${escapeHtml(lvl)}</option>`)
        .join("");
    sel.value = _facts.education_level || "";
  }
  const years = document.getElementById("factYears");
  if (years) years.value = _facts.years_experience ?? "";
  const fields = document.getElementById("factFields");
  if (fields) fields.value = (_facts.degree_fields || []).join(", ");
  const tri = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.value = value === null || value === undefined ? "" : value ? "1" : "0";
  };
  tri("factLicence", _facts.driving_licence);
  tri("factProtected", _facts.protected_category);
  const grade = document.getElementById("factGrade");
  if (grade) grade.value = _facts.grade ?? "";
  const cities = document.getElementById("factCities");
  if (cities) cities.value = (_facts.base_cities || []).join(", ");
  const modes = new Set(_facts.work_modes || []);
  document
    .querySelectorAll("#factModes input[type=checkbox]")
    .forEach((el) => (el.checked = modes.has(el.value)));
  document.getElementById("matchingFactsEdit")?.classList.remove("hidden");
  document.getElementById("matchingFactsView")?.classList.add("hidden");
}

function _closeFactsEditor() {
  document.getElementById("matchingFactsEdit")?.classList.add("hidden");
  document.getElementById("matchingFactsView")?.classList.remove("hidden");
}

async function _saveFacts() {
  // Three answers, not two. An emptied box means "clear this override" and the
  // server now acts on it; a control that isn't there, or a value that won't
  // parse, must mean "leave it alone" instead — JSON.stringify drops undefined
  // keys, and an absent key is what the server reads as untouched. Collapsing
  // the last two onto null would let one bad keystroke wipe a saved fact with a
  // success message on top.
  const num = (id) => {
    const raw = document.getElementById(id)?.value.trim();
    if (raw === undefined) return undefined;
    if (raw === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : undefined;
  };
  const tri = (id, key) => {
    const raw = document.getElementById(id)?.value;
    if (raw === undefined || raw === "") return {};
    return { [key]: raw === "1" };
  };
  const body = {
    years_experience: num("factYears"),
    grade: num("factGrade"),
    education_level: document.getElementById("factEducation")?.value || "",
    base_cities: (document.getElementById("factCities")?.value || "")
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean),
    work_modes: [...document.querySelectorAll("#factModes input:checked")].map((el) => el.value),
    degree_fields: (document.getElementById("factFields")?.value || "")
      .split(",")
      .map((f) => f.trim())
      .filter(Boolean),
    // Three states, and the empty one is an answer: nobody said, and nobody has
    // to — an unknown fact blocks nothing.
    ...tri("factLicence", "driving_licence"),
    ...tri("factProtected", "protected_category"),
  };
  try {
    await api("/api/profile", { method: "PATCH", body: JSON.stringify(body) });
    showToast(t("profile.matching.saved"), "info");
    _closeFactsEditor();
    await loadMatchingFacts();
  } catch (err) {
    showToast(`${t("toast.actionError")}: ${err.message}`, "error");
  }
}

export function initMatchingFacts() {
  document.getElementById("factsEditBtn")?.addEventListener("click", _openFactsEditor);
  document.getElementById("factsCancelBtn")?.addEventListener("click", _closeFactsEditor);
  document.getElementById("factsSaveBtn")?.addEventListener("click", _saveFacts);
}
