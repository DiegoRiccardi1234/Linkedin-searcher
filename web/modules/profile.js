import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

const FIELDS = ["preferred_roles", "skills", "languages"];

const _state = {
  profile: null,
  pending: { preferred_roles: null, skills: null, languages: null },
};

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
  if (_state.pending[field] !== null) return _state.pending[field];
  const summary = _state.profile?.summary_json || {};
  return Array.isArray(summary[field]) ? [...summary[field]] : [];
}

function _commitPending(field, list) {
  _state.pending[field] = list;
  _renderChips(_chipContainerId(field), list, field);
}

function _chipContainerId(field) {
  return field === "preferred_roles" ? "profileRoles"
    : field === "skills" ? "profileSkills"
    : field === "languages" ? "profileLanguages"
    : "";
}

function _renderExperience(summary) {
  const el = document.getElementById("profileExperience");
  if (!el) return;
  const exp = summary?.experience;
  if (!exp) {
    el.innerHTML = `<p class="micro">${t("profile.emptyField") || "—"}</p>`;
    return;
  }
  if (Array.isArray(exp)) {
    el.innerHTML = exp.map((item) => `<p>${escapeHtml(String(item))}</p>`).join("");
  } else {
    el.innerHTML = `<p>${escapeHtml(String(exp))}</p>`;
  }
}

function _renderMarkdown(markdown) {
  const el = document.getElementById("profileMarkdown");
  if (!el) return;
  if (!markdown) {
    el.textContent = "";
    return;
  }
  el.textContent = markdown;
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
  for (const f of FIELDS) _state.pending[f] = null;
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

async function _activateProfile(id) {
  try {
    await api(`/api/profiles/${id}/activate`, { method: "POST" });
    await loadProfile();
    showToast(t("profile.activated") || "Profile activated", "info");
  } catch (err) {
    showToast(`${t("toast.keySaveError") || "Error"}: ${err.message}`, "error");
  }
}

async function _saveField(field) {
  const list = _activeList(field);
  try {
    const body = {};
    body[field] = list;
    const res = await api("/api/profile", {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    _state.profile = res.profile;
    _state.pending[field] = null;
    showToast(t("profile.saved") || "Profile saved", "info");
    await loadProfile();
  } catch (err) {
    showToast(`${t("profile.saveFailed") || "Save failed"}: ${err.message}`, "error");
  }
}

function _toggleEdit(field) {
  const row = document.querySelector(`.profile-edit-row[data-field="${field}"]`);
  if (!row) return;
  row.classList.toggle("hidden");
}

export function bindProfileEvents() {
  const root = document.getElementById("view-profile");
  if (!root) return;

  root.addEventListener("click", async (event) => {
    const target = event.target.closest("button");
    if (!target) return;

    if (target.classList.contains("profile-edit-toggle")) {
      _toggleEdit(target.dataset.target);
      return;
    }
    if (target.classList.contains("chip-remove")) {
      const field = target.dataset.field;
      const idx = parseInt(target.dataset.idx || "-1", 10);
      const list = _activeList(field);
      list.splice(idx, 1);
      _commitPending(field, list);
      return;
    }
    if (target.classList.contains("profile-add-btn")) {
      const field = target.dataset.field;
      const row = target.closest(".profile-edit-row");
      const input = row?.querySelector(".profile-add-input");
      const value = (input?.value || "").trim();
      if (!value) return;
      const list = _activeList(field);
      if (!list.includes(value)) list.push(value);
      _commitPending(field, list);
      if (input) input.value = "";
      return;
    }
    if (target.classList.contains("profile-save-btn")) {
      const field = target.dataset.field;
      await _saveField(field);
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
