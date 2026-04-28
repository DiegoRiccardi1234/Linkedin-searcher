import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

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

function _renderExperience(summary) {
  const el = document.getElementById("profileExperience");
  if (!el) return;
  const narrative = summary?.experience;
  const level = summary?.experience_level;
  const years = summary?.years_experience;
  if (narrative) {
    if (Array.isArray(narrative)) {
      el.innerHTML = narrative.map((item) => `<p>${escapeHtml(String(item))}</p>`).join("");
    } else {
      el.innerHTML = `<p>${escapeHtml(String(narrative))}</p>`;
    }
    return;
  }
  if (level || years) {
    const parts = [];
    if (level) parts.push(escapeHtml(String(level)));
    if (years) parts.push(`${escapeHtml(String(years))} ${t("profile.years") || "years"}`);
    el.innerHTML = `<p>${parts.join(" · ")}</p>`;
    return;
  }
  el.innerHTML = `<p class="micro">${t("profile.emptyField") || "—"}</p>`;
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
            ${
              isActive
                ? `<span class="badge">${t("profile.active") || "Active"}</span>`
                : `<button type="button" class="ghost-btn profile-activate-btn" data-id="${p.id}" data-i18n="profile.setActive">Set active</button>`
            }
          </div>
        `;
      })
      .join("");
  } catch (err) {
    el.innerHTML = `<p class="micro">${escapeHtml(err.message)}</p>`;
  }
}

export async function loadProfile() {
  try {
    const payload = await api("/api/profile");
    _state.profile = payload.profile;
    const empty = document.getElementById("profileEmpty");
    const content = document.getElementById("profileContent");
    if (!_state.profile) {
      empty?.classList.remove("hidden");
      content?.classList.add("hidden");
      return;
    }
    empty?.classList.add("hidden");
    content?.classList.remove("hidden");
    const summary = _state.profile.summary_json || {};
    _renderChips("profileRoles", summary.preferred_roles || [], "preferred_roles");
    _renderChips("profileSkills", summary.skills || [], "skills");
    _renderChips("profileLanguages", summary.languages || [], "languages");
    _renderExperience(summary);
    _renderMarkdown(_state.profile.markdown || "");
    _renderMeta(_state.profile);
    await _renderHistory();
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
}
