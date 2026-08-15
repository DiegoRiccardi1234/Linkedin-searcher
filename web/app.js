import { api, escapeHtml, setText, truncate, showToast, renderCoachMarkdown } from "./modules/helpers.js";
import { initTheme } from "./modules/theme.js";
import { initLayout, syncStickyOffset } from "./modules/layout.js";
import { loadShortlist as _loadShortlistApi, addToShortlist as _addToShortlistApi, removeFromShortlist as _removeFromShortlistApi } from "./modules/shortlist.js";
import { initI18n, t, loadLanguage, getCurrentLang, onLanguageChange } from "./modules/i18n.js";
import {
  loadProfile as loadProfileView,
  bindProfileEvents,
  addRolesToProfile,
  loadMatchingFacts,
  initMatchingFacts,
} from "./modules/profile.js";
import { appState } from "./modules/state.js";
import { loadAnalytics, loadUsage } from "./modules/analytics.js";
import {
  readFeatureFlags,
  syncFeatureToggles,
  setupGenerationButton,
  loadSkillGap,
  loadSchedulerStatus,
  initFeatures,
} from "./modules/features.js";
import {
  checkForUpdate,
  populateSystemInfo,
  wireSystemSettings,
  wirePostScanModal,
  showPostScanModal,
} from "./modules/update.js";
import {
  renderProviderCards,
  normalizeKeyStatus,
  setPrimaryProviderValue,
  updateProvidersMetadata,
  populateModelOptions,
  onSaveProviderKey,
  onRemoveProviderKey,
  onSetPrimaryProvider,
  fetchAndRenderProviderModels,
  probeProviderModels,
  onSaveModelOverride,
  populateChatModelSelector,
  populateChatProviderSelector,
  maybeOfferPersistChatOverride,
  loadProviderHealth,
  setProviderDeps,
} from "./modules/providers.js";
import { initModelPicker, refreshModelPickerLabel } from "./modules/model_picker.js";
import {
  initReminders,
  loadReminders,
  reminderEditorHtml,
  wireReminderEditor,
} from "./modules/reminders.js";
import { initSavedSearches, loadSavedSearches } from "./modules/saved_searches.js";
import { initLocalModels, loadLocalModels } from "./modules/local_models.js";
import { initWatchlist, loadWatchlist } from "./modules/watchlist.js";
import {
  initJobBuckets,
  initJobList,
  initJobSorting,
  loadJobs,
  setJobsBucket,
} from "./modules/job_list.js";
import { initSubtabs, panelOf, setSubtabBadge, showSubtab } from "./modules/subtabs.js";
import { initChatActions, renderChatAction } from "./modules/chat_actions.js";
import {
  ensureProfileReady,
  fetchReadiness,
  initReadiness,
  invalidateReadiness,
  renderReadinessStrips,
} from "./modules/readiness.js";
import { initCompare, isSelected, toggleCompare } from "./modules/compare.js";
import {
  initJobDetail,
  showJobDetail,
  performJobAction,
  toggleFavorite,
  loadRecommendations,
  openJobDetail,
  closeJobDetail,
} from "./modules/job_detail.js";
import { initScan, readScanConfig, applyScanConfig } from "./modules/scan.js";
import { initRescore, wireRescoreBulk } from "./modules/rescore.js";
import { initApplyWatch, wireApplyWatch } from "./modules/apply_watch.js";
import { initMailbox, wireMailbox, loadMailboxStatus } from "./modules/mailbox.js";

// Global safety nets: surface otherwise-silent async failures in the console.
window.addEventListener("unhandledrejection", (e) => console.error("Unhandled promise rejection:", e.reason));
window.addEventListener("error", (e) => console.error("Uncaught error:", e.error || e.message));

initTheme();
initLayout();

// Inject core refresh callbacks the provider module needs after a key save
// (avoids a circular import). loadHealth/loadKeysStatus/refreshOnboardingPlaceholder
// are hoisted function declarations defined below.
setProviderDeps({ loadHealth, loadKeysStatus, refreshOnboardingPlaceholder });

onLanguageChange(() => {
  if (typeof loadChatPrompts === "function") {
    loadChatPrompts().catch(() => {});
  }
  refreshModelPickerLabel();
});

// Language selector
const langSelect = document.getElementById('langSelect');
if (langSelect) {
  langSelect.value = getCurrentLang();
  langSelect.addEventListener('change', async () => {
    await loadLanguage(langSelect.value);
    showToast(t("toast.languageChanged") || "Language updated", "info");
  });
}

// Quit button — the windowless build has no terminal to close, so this is how
// the user stops the app. Confirm, then ask the server to shut down; the server
// dies mid-request so the fetch aborts (ignored), and we show a "closed" screen.
const quitBtn = document.getElementById("quitApp");
if (quitBtn) {
  quitBtn.addEventListener("click", () => {
    if (!confirm(t("quit.confirm") || "Close Job Finder?")) return;
    fetch("/api/system/shutdown", { method: "POST" }).catch(() => {});
    const overlay = document.getElementById("appClosedOverlay");
    if (overlay) overlay.classList.remove("hidden");
  });
}


// Which view is on screen. It used to live only in a CSS class, which is fine
// for painting and useless for anything that needs to KNOW — the chat asking
// what the user is looking at, or a deep link that has to open a sub-tab.
let _currentView = "dashboard";

export function getCurrentView() {
  return _currentView;
}

function activateView(viewName, { tab = null } = {}) {
  // v1.3.0: navigation is no longer gated by provider configuration. The
  // warning banner + onboarding placeholder guide the user instead.
  _currentView = viewName;
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("is-active", section.id === `view-${viewName}`);
  });
  if (tab) showSubtab(viewName, tab);

  document.querySelectorAll(".nav-link").forEach((btn) => {
    const target = btn.dataset.view;
    btn.classList.toggle("is-active", target === viewName);
    btn.classList.remove("tab-locked");
  });

  document.querySelectorAll(".rail-link").forEach((btn) => {
    const target = btn.dataset.view;
    btn.classList.toggle("is-active", target === viewName);
    btn.classList.remove("tab-locked");
  });

  // v1.3.2: hide the chat coach sidebar on the Info view so reading docs
  // is not crowded by the chat panel. v1.7.7: the Jobs archive joins it — a
  // 9-column table plus a 4-column board cannot share the row with a 300px+
  // rail on a 1366px laptop (it pushed the board past the viewport).
  const railless = viewName === "info" || viewName === "jobs";
  const rail = document.querySelector(".right-rail");
  if (rail) rail.classList.toggle("hidden", railless);
  document.body.classList.toggle("rail-hidden", railless);

  // Mobile chrome: navigating closes any open menu/drawer and the chat FAB
  // is suppressed on the Info view (where the rail is hidden).
  rail?.classList.remove("drawer-open");
  document.getElementById("topnav")?.classList.remove("open");
  document.getElementById("navToggle")?.setAttribute("aria-expanded", "false");
  const overlay = document.getElementById("mobileOverlay");
  if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
  // The rail stays out of the archive's way — a nine-column table and a
  // four-column board cannot share a 1366px row with a 300px panel — but the
  // coach is exactly who you want to ask "which of these do I send first", so
  // the button that opens it as a drawer stays. Only the Info page, which is
  // documentation, has neither.
  const fab = document.getElementById("chatFab");
  if (fab) fab.classList.toggle("hidden", viewName === "info");
  syncStickyOffset();
}

/**
 * Bring an element into view wherever it is hiding — wrong view, closed
 * sub-tab, or just below the fold.
 *
 * Every "go to Settings and scroll to the provider cards" in this file used to
 * be activateView() + scrollIntoView(), which stops working the moment the
 * target sits in a sub-tab that is not open: no error, no scroll, nothing.
 */
export function revealElement(target, opts = {}) {
  const el = typeof target === "string" ? document.getElementById(target) : target;
  if (!el) return false;
  const view = el.closest(".view");
  if (view) activateView(view.id.replace(/^view-/, ""));
  const panel = panelOf(el);
  if (panel) showSubtab(panel.group, panel.id);
  if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "center", ...opts });
  return true;
}

// Exposed like ChatSessions is: the whole point of this function is that it
// works from anywhere, and "anywhere" includes a test driving the page.
window.revealElement = revealElement;

function roleLabel(role) {
  if (role === "assistant") return "Coach";
  if (role === "user") return "You";
  return "System";
}

async function addRolesToShortlist(keywords, label) {
  const kws = (keywords || []).filter(Boolean).map(String);
  if (!kws.length) return;
  await _addToShortlistApi(kws);
  if (window.getKeywords && typeof window.getKeywords.addMultiple === "function") {
    window.getKeywords.addMultiple(kws);
  }
  const msg = t("coach.savedToShortlist") || "Role added to your search";
  showToast(`${msg}${label ? ": " + label : ""}`, "info");
}

function appendChat(role, content, extras) {
  const box = document.getElementById("chatBox");
  if (!box) return;
  // A real message means the empty-state suggestion chips must go.
  clearChatEmptyState();

  const item = document.createElement("div");
  item.className = `chat-item ${role}`;

  const roleDiv = document.createElement("div");
  roleDiv.className = "role";
  roleDiv.textContent = roleLabel(role);

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.innerHTML = renderCoachMarkdown(content);
  } else {
    bubble.innerHTML = escapeHtml(content).replaceAll("\n", "<br>");
  }

  item.appendChild(roleDiv);
  item.appendChild(bubble);

  const roles = extras && Array.isArray(extras.suggested_roles) ? extras.suggested_roles : [];
  if (role === "assistant" && roles.length) {
    const pillRow = document.createElement("div");
    pillRow.className = "role-pill-row";
    for (const r of roles) {
      if (!r || !r.label) continue;
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "role-pill";
      pill.textContent = r.label;
      const kws = Array.isArray(r.keywords) && r.keywords.length ? r.keywords : [r.label];
      pill.addEventListener("click", async () => {
        const profileAdded = await addRolesToProfile([r.label]);
        const kwAdded =
          window.getKeywords && typeof window.getKeywords.addMultiple === "function"
            ? window.getKeywords.addMultiple(kws)
            : false;
        try {
          await _addToShortlistApi(kws);
        } catch (err) {
          /* shortlist API best-effort; chip still added locally */
        }
        pill.classList.add("is-added");
        if (profileAdded || kwAdded) {
          showToast(t("coach.savedToShortlist") || "Added to your search", "info");
        }
      });
      pillRow.appendChild(pill);
    }
    if (pillRow.childElementCount) item.appendChild(pillRow);
  }

  // Degraded reply: the LLM failed and a canned fallback was returned. Flag it
  // with a subtle inline pill so the answer isn't mistaken for a full one.
  if (role === "assistant" && extras && extras.degraded) {
    const note = document.createElement("div");
    note.className = "chat-degraded-note";
    note.innerHTML = `<span class="material-symbols-outlined">warning</span><span>${escapeHtml(t("chat.degradedNote") || "Reduced answer — LLM unavailable")}</span>`;
    item.appendChild(note);
  }

  box.appendChild(item);
  box.scrollTop = box.scrollHeight;
}

// Tracks whether at least one provider key is saved. Updated by loadHealth().
// While ``false``, the banner stays visible and non-dashboard tabs are gated.
let _setupReady = true;

function ensureNoKeyBanner(show, message) {
  _setupReady = !show;
  let banner = document.getElementById("noApiKeyBanner");
  if (!show) {
    if (banner) banner.remove();
    return;
  }
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "noApiKeyBanner";
    banner.className = "no-key-banner";
    document.body.insertBefore(banner, document.body.firstChild);
  }
  // Non-dismissable: removed the close button. The banner clears itself once
  // ``loadHealth()`` sees a configured provider on the next render.
  banner.innerHTML = `
    <span class="material-symbols-outlined">warning</span>
    <span class="no-key-banner-text">${escapeHtml(message)}</span>
    <span class="no-key-banner-hint">${t("banner.signupHint")}</span>
    <a href="https://cloud.cerebras.ai/?utm_source=jobfinder" target="_blank" rel="noopener noreferrer" class="no-key-banner-link no-key-banner-link--primary">${t("banner.signupCerebras")}</a>
    <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" class="no-key-banner-link">${t("banner.signupGroq")}</a>
    <a href="#" id="noApiKeyBannerLink" class="no-key-banner-link">${t("banner.openSettings")}</a>
  `;
  banner.querySelector("#noApiKeyBannerLink").addEventListener("click", (e) => {
    e.preventDefault();
    revealElement("providerCards");
  });
}

async function loadHealth() {
  const health = await api("/api/health");
  setText("providerBadge", `Provider: ${health.provider.active_provider}`);
  setText("modelBadge", `Model: ${health.provider.active_model}`);

  const active = String(health.provider.active_provider || "").toLowerCase();
  const missing = !active || active === "none" || active === "fallback" || health.provider.available === false;
  ensureNoKeyBanner(missing, t("banner.noKey"));

  const prefs = health.preferences || {};
  appState.featureFlags = readFeatureFlags(prefs);
  syncFeatureToggles();
  const linkedinInput = document.getElementById("linkedinUrl");
  if (linkedinInput && prefs.linkedin_url) {
    linkedinInput.value = prefs.linkedin_url;
  }
  const linkedinText = document.getElementById("linkedinText");
  if (linkedinText && prefs.linkedin_profile_text) {
    linkedinText.value = prefs.linkedin_profile_text;
  }
  // Read back, or the field shows the placeholder 7 whatever you saved — the
  // same "written and never re-read" shape as the panels that were invisible.
  const staleDays = document.getElementById("reminderStaleDays");
  if (staleDays && prefs.reminder_stale_days) staleDays.value = prefs.reminder_stale_days;

  const dedupSel = document.getElementById("dedupModeSelect");
  if (dedupSel) {
    const mode = prefs.dedup_mode || "city";
    if (["exact", "city", "title_company"].includes(mode)) dedupSel.value = mode;
  }

  const keys = health.keys || {};
  const status = normalizeKeyStatus(keys, health.provider || {});
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(health.provider || {}, keys.preferred_model || "");
  renderProviderCards(keys, health.provider || {});
  loadProviderHealth();
  showKeysStatus(status);
}

async function loadKeysStatus() {
  const payload = await api("/api/providers/keys/status");
  const keys = payload.keys || {};
  const provider = payload.provider || {};
  const status = normalizeKeyStatus(keys, provider);
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(provider, keys.preferred_model || "");
  renderProviderCards(keys, provider);
  loadProviderHealth();
  showKeysStatus(status);
}

// #keysStatus ships with class="hidden" and nothing ever took it off, so this
// JSON was written into an element nobody could see. It is a diagnostic dump,
// not a feature: the provider cards above are the real UI. Kept, but behind a
// details element that starts closed, instead of written into the void.
function showKeysStatus(status) {
  const el = document.getElementById("keysStatus");
  if (!el) return;
  el.textContent = JSON.stringify(status, null, 2);
  el.classList.remove("hidden");
}

async function loadProfiles() {
  const payload = await api("/api/profiles");
  // The matching facts belong to the ACTIVE profile: refresh them together.
  loadMatchingFacts();
  const select = document.getElementById("profileSelect");
  select.innerHTML = "";

  const active = String(payload.active_profile_id || "");
  for (const profile of payload.profiles || []) {
    const option = document.createElement("option");
    option.value = String(profile.id);
    option.textContent = `${profile.id} - ${profile.source_name}`;
    if (String(profile.id) === active) option.selected = true;
    select.appendChild(option);
  }

  if (!select.value && select.options.length > 0) {
    select.value = select.options[0].value;
  }
}

async function activateProfile(profileId) {
  if (!profileId) return;
  await api(`/api/profiles/${profileId}/activate`, { method: "POST" });
  showToast(t("toast.profileActive", { id: profileId }), "info");
  // Refresh the views that depend on the active profile so they don't go stale.
  await Promise.allSettled([
    loadRecommendations(),
    loadAnalytics(),
    loadSkillGap(),
    loadReminders(),
  ]);
}

async function loadChatPrompts() {
  const wrap = document.getElementById("chatQuickPrompts");
  if (!wrap) return;

  wrap.innerHTML = "";
  try {
    // The page the user is on decides what they are most likely to ask next:
    // a question typed on the settings page is about settings, whatever their
    // CV says. The suggestions used to be picked from the CV alone.
    const query = new URLSearchParams({
      lang: getCurrentLang() || "en",
      view: getCurrentView(),
    });
    const payload = await api(`/api/chat/prompts?${query.toString()}`);
    const prompts = (payload.prompts || []).slice(0, 4);
    for (const prompt of prompts) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.textContent = prompt;
      btn.addEventListener("click", async () => {
        await sendChatMessage(prompt);
      });
      wrap.appendChild(btn);
    }
  } catch (error) {
    showToast(`${t("toast.quickPromptsUnavail")}: ${error.message}`, "info");
  }
}

let _chatSending = false;
async function sendChatMessage(message) {
  const text = String(message || "").trim();
  if (!text || _chatSending) return; // ignore rapid double-sends

  _chatSending = true;
  const chatInputEl = document.getElementById("chatInput");
  const chatSendBtn = document.querySelector("#chatForm button[type='submit'], #chatForm button");
  if (chatInputEl) chatInputEl.disabled = true;
  if (chatSendBtn) chatSendBtn.disabled = true;

  appendChat("user", text);

  const chatBox = document.getElementById("chatBox");
  let pendingEl = null;
  if (chatBox) {
    pendingEl = document.createElement("div");
    pendingEl.className = "chat-item assistant pending";
    pendingEl.innerHTML = `<div class="role">${roleLabel("assistant")}</div><div class="bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>`;
    chatBox.appendChild(pendingEl);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  try {
    const providerSelector = document.getElementById("chatModelSelector");
    const modelSelector = document.getElementById("chatModelSelectorModel");
    const providerVal = providerSelector && providerSelector.value ? providerSelector.value : null;
    const modelVal = modelSelector && modelSelector.value ? modelSelector.value : null;

    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: (window.ChatSessions?.active || ChatSessions.active || "default"), provider: providerVal, model: modelVal, view: getCurrentView() }),
    });

    maybeOfferPersistChatOverride(providerVal, modelVal);
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    appendChat("assistant", result.answer || t("chat.noResponse"), { suggested_roles: result.suggested_roles, degraded: result.degraded === true });
    if (typeof refreshChatSessions === "function") {
      refreshChatSessions().then(renderChatSessionDropdown).catch(() => {});
    }

    // The coach proposes; the user decides. This used to rewrite the search
    // form and jump views on its own — helpful when the model was right, and
    // startling when it was not.
    if (result.action) {
      renderChatAction(document.getElementById("chatBox"), result.action);
    }
    // Preferences the message stated in passing. They used to be written on
    // the spot; now they are offered, one card each.
    for (const proposal of result.proposals || []) {
      renderChatAction(document.getElementById("chatBox"), proposal);
    }
  } catch (error) {
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    const isNoProvider = error && (error.status === 412 || /412|no_provider_configured|noProvider/i.test(error.message || ""));
    if (isNoProvider) {
      appendChat("assistant", t("errors.noProviderToast") || "Configure an AI provider key first to use the chat.");
      try {
        revealElement("providerCards");
      } catch (_) {}
    } else {
      appendChat("assistant", `${t("toast.chatError")}: ${error.message}`);
    }
  } finally {
    _chatSending = false;
    if (chatInputEl) chatInputEl.disabled = false;
    if (chatSendBtn) chatSendBtn.disabled = false;
    if (chatInputEl) chatInputEl.focus();
  }
}

// ── Shared job-display helpers ────────────────────────────────────────────


async function loadChatHistory() {
  const { messages } = await api("/api/chat/history?session_id=default&limit=20");
  const box = document.getElementById("chatBox");
  box.innerHTML = "";
  for (const msg of messages) {
    // The role pills are stored with the message, so they come back on reload
    // instead of disappearing the moment the page refreshed.
    appendChat(msg.role, msg.content, msg.meta || null);
  }
}

document.getElementById("linkedinForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("linkedinUrl").value.trim();
  const text = document.getElementById("linkedinText")?.value.trim() || "";
  const status = document.getElementById("linkedinStatus");
  try {
    const res = await api("/api/profile/linkedin", {
      method: "POST",
      body: JSON.stringify({ url, text }),
    });
    let msg;
    if (text) msg = t("profile.linkedinPasteSaved");
    else if (res.fetched) msg = t("profile.linkedinFetched");
    else if (url) msg = t("profile.linkedinBlocked");
    else msg = t("toast.linkedinSaved");
    if (status) {
      status.textContent = msg;
      status.classList.remove("hidden");
      setTimeout(() => status.classList.add("hidden"), 5000);
    }
    showToast(msg, "info");
  } catch (error) {
    showToast(`${t("toast.linkedinError")}: ${error.message}`, "info");
  }
});

document.getElementById("cvForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById("cvFile");
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  const submitBtn = document.getElementById("cvPickBtn");
  const originalLabel = submitBtn ? submitBtn.innerHTML : "";
  const dropzone = document.getElementById("cvDropzone");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.cvAnalyzing")}`;
  }
  // The upload now starts from the dropzone, so that is where the user is
  // looking: the button spinner alone left them staring at a static box.
  dropzone?.classList.add("is-busy");
  showToast(t("toast.cvAnalyzing"), "info");

  try {
    const response = await fetch(
      `/api/upload-cv?lang=${encodeURIComponent(getCurrentLang() || "en")}`,
      {
        method: "POST",
        body: formData,
      },
    );
    if (!response.ok) {
      showCvSummary(`${t("toast.uploadError")}: ${await response.text()}`);
      showToast(t("toast.uploadError") || "Upload failed", "error");
      return;
    }

    const payload = await response.json();
    // Was JSON.stringify(payload) — the user got the raw API response dumped
    // into the page. Show what the AI actually understood.
    showCvSummary(cvSummaryText(payload));
    await loadProfiles();
    await loadProfileView();
    await loadRecommendations();
    if (typeof refreshOnboardingPlaceholder === "function") {
      refreshOnboardingPlaceholder().catch(() => {});
    }

    if (payload.summary_method === "llm") {
      const msg = payload.retries
        ? (t("toast.cvLlmRetried") || "AI summary ready (retried {n}×)").replace("{n}", payload.retries)
        : (t("toast.cvLlmOk") || "AI summary ready");
      showToast(msg, "info");
    } else {
      showToast(
        t("toast.cvHeuristic") || "AI was busy — used a quick fallback. Re-upload later for full analysis.",
        "info",
      );
    }
  } catch (err) {
    showToast(`${t("toast.uploadError") || "Upload failed"}: ${err.message}`, "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = originalLabel;
    }
    dropzone?.classList.remove("is-busy");
  }
});

// #cvSummary ships with class="hidden" and nothing ever took it off, so both
// the summary of what the AI understood AND the server's reason for a failed
// upload were written into an element nobody could see — the user got a generic
// "Upload failed" toast and no way to find out why.
function showCvSummary(text) {
  setText("cvSummary", text);
  document.getElementById("cvSummary")?.classList.toggle("hidden", !text);
}

// The upload response is a status envelope; the readable part is the profile
// the parser built from the CV.
function cvSummaryText(payload) {
  const summary = payload?.summary || payload?.profile?.summary_json || {};
  const bits = [];
  if (summary.name) bits.push(summary.name);
  if (summary.title || summary.headline) bits.push(summary.title || summary.headline);
  const skills = Array.isArray(summary.skills) ? summary.skills.slice(0, 12) : [];
  if (skills.length) bits.push(`${t("profile.skills")}: ${skills.join(", ")}`);
  const roles = Array.isArray(summary.preferred_roles) ? summary.preferred_roles.slice(0, 6) : [];
  if (roles.length) bits.push(`${t("profile.roles")}: ${roles.join(", ")}`);
  const languages = Array.isArray(summary.languages) ? summary.languages : [];
  if (languages.length) bits.push(`${t("profile.languages")}: ${languages.join(", ")}`);
  if (payload?.deduplicated) bits.push(t("toast.cvAlreadyUploaded"));
  return bits.join("\n") || t("profile.cvParsed");
}

(() => {
  const dz = document.getElementById("cvDropzone");
  const fileInput = document.getElementById("cvFile");
  if (!dz || !fileInput) return;
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("is-dragover");
    }),
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("is-dragover");
    }),
  );
  dz.addEventListener("drop", (e) => {
    if (e.dataTransfer?.files?.length) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });
  fileInput.addEventListener("change", () => {
    const name = fileInput.files?.[0]?.name;
    const text = dz.querySelector(".cv-dropzone-text");
    if (name && text) {
      text.textContent = name;
      dz.classList.add("has-file");
    }
    // Picking a file IS the request to upload it. Dropping a CV and having
    // nothing happen until you found the button was the whole friction.
    if (fileInput.files?.length) document.getElementById("cvForm")?.requestSubmit();
  });
  document.getElementById("cvPickBtn")?.addEventListener("click", () => fileInput.click());
})();

{
  const providerCardsEl = document.getElementById("providerCards");
  if (providerCardsEl) {
    providerCardsEl.addEventListener("click", async (event) => {
      const target = event.target.closest("button");
      if (!target) return;
      const card = target.closest(".provider-card");
      if (!card) return;
      const name = card.dataset.provider;

      if (target.classList.contains("provider-toggle-visibility")) {
        const input = card.querySelector(".provider-key-input");
        if (input) input.type = input.type === "password" ? "text" : "password";
        return;
      }
      if (target.hasAttribute("data-provider-remove")) {
        if (!window.confirm(t("settings.providers.removeKey"))) return;
        await onRemoveProviderKey(name);
        return;
      }
      if (target.classList.contains("provider-preset-btn")) {
        const endpoint = card.querySelector(".provider-endpoint-input");
        if (endpoint) endpoint.value = target.dataset.url || "";
        return;
      }
      if (target.classList.contains("provider-save-btn")) {
        const input = card.querySelector(".provider-key-input");
        const value = input ? input.value.trim() : "";
        // A local model server authenticates nobody: for the custom provider
        // the endpoint is what has to be filled in, not the key.
        const endpointEl = card.querySelector(".provider-endpoint-input");
        const endpoint = endpointEl ? endpointEl.value.trim() : "";
        if (!value && !endpoint) {
          showToast(t("toast.enterKeyOrProvider"), "info");
          return;
        }
        await onSaveProviderKey(name, value);
        return;
      }
      if (target.classList.contains("provider-refresh-btn")) {
        await fetchAndRenderProviderModels(name, true);
        return;
      }
      if (target.classList.contains("provider-probe-btn")) {
        await probeProviderModels(name);
        return;
      }
      if (target.classList.contains("provider-probe-confirm-btn")) {
        await probeProviderModels(name, { confirm: true });
        return;
      }
    });

    providerCardsEl.addEventListener("change", async (event) => {
      const card = event.target.closest(".provider-card");
      if (!card) return;
      const name = card.dataset.provider;
      if (event.target.matches('input[name="primaryProviderRadio"]')) {
        if (event.target.checked) {
          const select = card.querySelector(".provider-model-select");
          const modelOverride = select ? select.value : "";
          await onSetPrimaryProvider(name, modelOverride);
        }
        return;
      }
      if (event.target.classList.contains("provider-model-select")) {
        const radio = card.querySelector('input[name="primaryProviderRadio"]');
        if (radio && radio.checked) {
          await onSetPrimaryProvider(name, event.target.value);
        } else {
          // Model only applies to the primary provider — nudge the user.
          showToast(t("settings.providers.setPrimaryFirst"), "info");
        }
      }
    });

    providerCardsEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (!event.target.classList.contains("provider-key-input")) return;
      event.preventDefault();
      const card = event.target.closest(".provider-card");
      const saveBtn = card?.querySelector(".provider-save-btn");
      if (saveBtn) saveBtn.click();
    });
  }
  // Per-context model override selects (Settings "AI models" card).
  for (const id of ["scoringModelSelect", "chatModelOverrideSelect", "cvModelSelect"]) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => onSaveModelOverride(id, el.value));
  }
}

// Snapshot / restore the Job Search filter state — shared by saved searches (F7).
const _chatForm = document.getElementById("chatForm");
if (_chatForm) _chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  input.style.height = "auto";
  await sendChatMessage(message);
});

const _chatInputEl = document.getElementById("chatInput");
if (_chatInputEl) {
  // Enter sends the message; Shift+Enter inserts a newline.
  _chatInputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (_chatForm) _chatForm.requestSubmit();
    }
  });
  // Auto-grow the textarea up to the CSS max-height as the user types.
  _chatInputEl.addEventListener("input", () => {
    _chatInputEl.style.height = "auto";
    _chatInputEl.style.height = `${Math.min(_chatInputEl.scrollHeight, 140)}px`;
  });
}

const _quickRecommendBtn = document.getElementById("quickRecommendBtn");
if (_quickRecommendBtn) _quickRecommendBtn.addEventListener("click", async () => {
  await sendChatMessage("Recommend the top 5 jobs I should apply for today, in priority order.");
});

const _refreshRecommendationsBtn = document.getElementById("refreshRecommendationsBtn");
if (_refreshRecommendationsBtn) _refreshRecommendationsBtn.addEventListener("click", async () => {
  const btn = _refreshRecommendationsBtn;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.recsRefreshing") || "Refreshing..."}`;
  try {
    await loadRecommendations();
  } catch (err) {
    showToast(`${t("toast.recsFailed") || "Refresh failed"}: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
});

const _focusOpenBtn = document.getElementById("focusOpenBtn");
if (_focusOpenBtn) _focusOpenBtn.addEventListener("click", async () => {
  setJobsBucket("to_review");
  activateView("jobs");
  await loadJobs();
});

document.querySelectorAll("[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view || "dashboard";
    activateView(view);
    if (view === "profile") {
      loadProfileView().catch(() => {});
      renderReadinessStrips();
    }
    if (view === "jobs") {
      loadJobs().catch(() => {});
    }
    loadChatPrompts().catch(() => {});
    if (view === "mail") {
      // At boot only the status is fetched, for the badge; the queue itself
      // is worth a request when someone actually opens the tab.
      loadMailboxStatus().catch(() => {});
    }
  });
});

bindProfileEvents();

// The keyword box read the shortlist while the CV wrote `preferred_roles`, so
// after uploading a CV it stayed empty — and an empty box used to mean "search
// for whatever this app was written for". Both boxes are now filled from the
// same chain the scan would follow, visibly and editable.
async function prefillSearchForm() {
  const report = await fetchReadiness();
  if (!report) return;
  if (window.getKeywords && !window.getKeywords.getTags().length) {
    window.getKeywords.addMultiple(report.suggested_terms || []);
  }
  if (window.getLocations && !window.getLocations.getTags().length) {
    window.getLocations.addMultiple(report.suggested_locations || []);
  }
}

const _primaryProviderEl = document.getElementById("primaryProvider");
if (_primaryProviderEl) {
  _primaryProviderEl.addEventListener("change", () => {
    populateModelOptions(_primaryProviderEl.value, "");
  });
}

const _chatProviderEl = document.getElementById("chatModelSelector");
if (_chatProviderEl) {
  // Options are data-driven from PROVIDER_CATALOG (single source of truth).
  populateChatProviderSelector();
  _chatProviderEl.addEventListener("change", () => {
    populateChatModelSelector(_chatProviderEl.value);
  });
  // Unified popover that mirrors the (now hidden) provider/model selects.
  initModelPicker();
}

// ─── Job Search ──────────────────────────────────────────
function _refreshChipState() {
  const chipsEl = document.getElementById("wizardRoleSuggestions");
  if (!chipsEl) return;
  const tags = (typeof getKeywords !== "undefined" ? getKeywords.getTags() : []).map((t) => t.toLowerCase());
  chipsEl.querySelectorAll(".chip-suggestion").forEach((chip) => {
    const role = (chip.textContent || "").toLowerCase();
    chip.classList.toggle("is-added", tags.includes(role));
  });
}

function updateWizardReview() {
  _refreshChipState();
}

async function loadWizardProfile() {
  const summaryEl = document.getElementById("wizardProfileSummary");
  const chipsEl = document.getElementById("wizardRoleSuggestions");
  if (!summaryEl || !chipsEl) return;
  chipsEl.innerHTML = "";
  try {
    const data = await api("/api/profile");
    const profile = data.profile;
    if (!profile) {
      summaryEl.innerHTML = `<em>${t("jobSearch.noProfile")}</em>`;
      return;
    }
    const summary = profile.summary_json || {};
    const skills = Array.isArray(summary.skills) ? summary.skills.slice(0, 12) : [];
    const roles = Array.isArray(summary.preferred_roles) ? summary.preferred_roles : [];
    const skillList = skills.length ? skills.map((s) => `<span class="search-tag">${escapeHtml(s)}</span>`).join("") : `<em>—</em>`;
    summaryEl.innerHTML = `
      <div class="search-summary-row">
        <span class="search-summary-label">${t("jobSearch.detectedSkills")}</span>
        <div class="search-summary-tags">${skillList}</div>
      </div>
    `;
    if (!roles.length) {
      chipsEl.innerHTML = `<em class="micro">${t("jobSearch.noRoles") || "Add roles to your Profile to get suggestions here."}</em>`;
    }
    for (const role of roles) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip-suggestion";
      chip.textContent = role;
      chip.addEventListener("click", () => {
        if (typeof getKeywords !== "undefined") {
          getKeywords.addMultiple([role]);
          updateWizardReview();
        }
      });
      chipsEl.appendChild(chip);
    }
    _refreshChipState();
  } catch (err) {
    summaryEl.innerHTML = `<em>${t("jobSearch.noProfile")}</em>`;
  }
}

document.querySelectorAll('[data-view="job-search"]').forEach((btn) => {
  btn.addEventListener("click", () => {
    loadWizardProfile();
    updateWizardReview();
  });
});

document.querySelectorAll('input[name="scanSites"], #remoteToggle').forEach((el) => {
  el.addEventListener("change", updateWizardReview);
});

["keywordsContainer", "locationsContainer"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  new MutationObserver(updateWizardReview).observe(el, { childList: true, subtree: true });
});

document.getElementById("railRecommendBtn").addEventListener("click", async () => {
  await sendChatMessage("Recommend the strongest jobs I should apply for right now, in priority order.");
});

document.getElementById("detailApplyNowBtn").addEventListener("click", async () => {
  if (!appState.selectedJobId) {
    showToast(t("toast.openJobFirst"), "info");
    return;
  }
  try {
    await performJobAction(appState.selectedJobId, "applied");
    showToast(t("toast.appMarked"), "info");
  } catch (error) {
    showToast(`${t("toast.actionError")}: ${error.message}`, "info");
  }
});

const undoMailBtn = document.getElementById("detailUndoMailBtn");
if (undoMailBtn) {
  undoMailBtn.addEventListener("click", async () => {
    const jobId = Number(undoMailBtn.dataset.jobId || appState.selectedJobId);
    if (!jobId) return;
    try {
      await api(`/api/mail/undo/${jobId}`, { method: "POST", body: "{}" });
      undoMailBtn.style.display = "none";
      showToast(t("toast.mail.undone"), "info");
      await loadJobs();
    } catch (error) {
      showToast(`${t("toast.actionError")}: ${error.message}`, "info");
    }
  });
}

const notAppliedBtn = document.getElementById("detailNotAppliedBtn");
if (notAppliedBtn) {
  notAppliedBtn.addEventListener("click", async () => {
    const jobId = Number(notAppliedBtn.dataset.jobId || appState.selectedJobId);
    if (!jobId) return;
    try {
      await api(`/api/jobs/${jobId}/link-opened/clear`, { method: "POST", body: "{}" });
      notAppliedBtn.style.display = "none";
      await loadJobs();
    } catch (error) {
      showToast(`${t("toast.actionError")}: ${error.message}`, "info");
    }
  });
}

const genCovBtn = document.getElementById("generateCoverLetterBtn");
if (genCovBtn) {
  genCovBtn.addEventListener("click", async () => {
    if (!appState.selectedJobId) return;
    const outBox = document.getElementById("coverLetterBox");
    const outTxt = document.getElementById("coverLetterOutput");

    outBox.style.display = "block";
    outTxt.textContent = t("toast.generating");
    genCovBtn.disabled = true;
    const originalLabel = genCovBtn.innerHTML;
    genCovBtn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.coverLetterGenerating") || "Generating..."}`;
    showToast(t("toast.coverLetterGenerating") || "Generating cover letter...", "info");

    try {
      const payload = await api(`/api/jobs/${appState.selectedJobId}/cover-letter`, { method: "POST" });
      outTxt.textContent = payload.cover_letter || t("toast.noResult");
      showToast(t("toast.coverLetterReady") || "Cover letter ready", "info");
    } catch (error) {
      outTxt.textContent = `${t("toast.genError")}: ${error.message}`;
      showToast(`${t("toast.coverLetterFailed") || "Cover letter failed"}: ${error.message}`, "error");
    } finally {
      genCovBtn.disabled = false;
      genCovBtn.innerHTML = originalLabel;
    }
  });
}

initFeatures({ loadJobs });
initReminders({ onOpenJob: showJobDetail });
initSavedSearches({
  readConfig: readScanConfig,
  applyConfig: applyScanConfig,
  submitScan: () => document.getElementById("scanForm")?.requestSubmit(),
});
initWatchlist();
initLocalModels();

document.getElementById("refreshJobsBtn").addEventListener("click", loadJobs);
document.getElementById("onlyNew").addEventListener("change", loadJobs);
document.getElementById("onlyFavorites").addEventListener("change", loadJobs);
document.getElementById("searchText").addEventListener("change", loadJobs);
document.getElementById("minScore").addEventListener("change", loadJobs);
document.getElementById("maxAgeDays").addEventListener("change", loadJobs);
document.getElementById("remoteOnly").addEventListener("change", loadJobs);
document.getElementById("applicableOnly")?.addEventListener("change", loadJobs);
document.getElementById("fromMail")?.addEventListener("change", loadJobs);
initJobSorting();
initJobBuckets();
// Wired here rather than at the end of bootstrap(): a tab strip needs no
// data, and bootstrap awaits a dozen requests first — long enough for a
// click on Settings to land on a strip that was not listening yet.
// Both panels are already loaded once at boot below; the tabs only decide
// what is on screen, so there is nothing to re-fetch on a switch.
initSubtabs("settings", { defaultTab: "ai" });
initReadiness({ revealElement });
initSubtabs("profile", {
  defaultTab: "about",
  // The matching facts and the goals were fetched once at boot and never
  // again, so reopening the tab showed whatever was true when the app
  // started — including values a scan had changed since.
  onChange: (tab) => {
    if (tab === "constraints") loadMatchingFacts().catch(() => {});
    renderReadinessStrips();
  },
});
{
  const usageRangeSel = document.getElementById("usageRange");
  if (usageRangeSel) usageRangeSel.addEventListener("change", () => loadUsage());
}
document.getElementById("profileSelect").addEventListener("change", async (event) => {
  await activateProfile(event.target.value);
});

const _dedupModeSelect = document.getElementById("dedupModeSelect");
if (_dedupModeSelect) {
  _dedupModeSelect.addEventListener("change", async () => {
    try {
      await api("/api/preferences", {
        method: "POST",
        body: JSON.stringify({ key: "dedup_mode", value: _dedupModeSelect.value }),
      });
      showToast(t("settings.dedup.saved") || "Saved", "info");
    } catch (err) {
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
    }
  });
}

const _profileDeleteBtn = document.getElementById("profileDeleteBtn");
if (_profileDeleteBtn) {
  _profileDeleteBtn.addEventListener("click", async () => {
    const sel = document.getElementById("profileSelect");
    const id = sel?.value;
    if (!id) return;
    if (!window.confirm(t("profile.confirmDelete") || "Delete this CV?")) return;
    try {
      await api(`/api/profiles/${id}`, { method: "DELETE" });
      showToast(t("profile.deleted") || "Profile deleted", "info");
      await loadProfiles();
      await Promise.allSettled([
        loadRecommendations(),
        loadAnalytics(),
        loadSkillGap(),
        loadReminders(),
      ]);
    } catch (err) {
      showToast(`${t("profile.deleteFailed") || "Delete failed"}: ${err.message}`, "error");
    }
  });
}

document.getElementById("exportCsvBtn").addEventListener("click", () => {
  const hasJobs =
    document.querySelectorAll(".jobs-section table tbody tr").length > 0 ||
    document.querySelectorAll("#kanbanView [draggable='true']").length > 0;
  if (!hasJobs) {
    showToast(t("jobs.exportEmpty"), "info");
    return;
  }
  // Let the browser download the CSV instead of writing a file server-side.
  const a = document.createElement("a");
  a.href = "/api/export/csv";
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
});

// The applications, not the whole archive: what was sent, with which CV, and how
// it ended. A plain link, like the score-feedback export — no JS needed to
// download a file the server already knows how to produce.
document.getElementById("exportApplicationsBtn")?.addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = "/api/applications/export?format=csv";
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
});

document.getElementById("deleteAllJobsBtn").addEventListener("click", async () => {
  if (!confirm(t("jobs.deleteAllConfirm"))) return;
  try {
    const res = await api("/api/jobs", { method: "DELETE" });
    showToast(t("jobs.deletedAll", { count: res.deleted }), "info");
    await Promise.all([loadJobs(), loadRecommendations()]);
  } catch (error) {
    showToast(`${t("toast.deleteError")}: ${error.message}`, "info");
  }
});

// Shared modal accessibility: Escape-to-close + Tab focus-trap. Focus is moved
// into the modal on open by each caller, so keydown reaches this handler.
function enableModalDismiss(modal, closeFn) {
  if (!modal) return;
  modal.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeFn();
      return;
    }
    if (e.key !== "Tab") return;
    const focusables = [
      ...modal.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((el) => el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
}

// F5 — manually add a job (referrals, career-page finds). POSTs to the existing
// /api/jobs/manual, which also AI-scores it against the active profile.
{
  const openBtn = document.getElementById("addManualJobBtn");
  const modal = document.getElementById("manualJobModal");
  const form = document.getElementById("manualJobForm");
  if (openBtn && modal && form) {
    const close = () => modal.classList.add("hidden");
    enableModalDismiss(modal, close);
    openBtn.addEventListener("click", () => {
      form.reset();
      const iu = document.getElementById("mjImportUrl");
      const pt = document.getElementById("mjPasteText");
      if (iu) iu.value = "";
      if (pt) pt.value = "";
      modal.classList.remove("hidden");
      document.getElementById("mjTitolo").focus();
    });
    const jobSearchImportBtn = document.getElementById("jobSearchImportBtn");
    if (jobSearchImportBtn) {
      jobSearchImportBtn.addEventListener("click", () => {
        form.reset();
        const iu = document.getElementById("mjImportUrl");
        const pt = document.getElementById("mjPasteText");
        if (iu) iu.value = "";
        if (pt) pt.value = "";
        modal.classList.remove("hidden");
        if (iu) {
          iu.scrollIntoView({ block: "center" });
          iu.focus();
        }
      });
    }
    modal.querySelectorAll("[data-close-manual]").forEach((b) => b.addEventListener("click", close));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submit = document.getElementById("mjSubmit");
      const payload = {
        titolo: document.getElementById("mjTitolo").value.trim(),
        azienda: document.getElementById("mjAzienda").value.trim(),
        sede: document.getElementById("mjSede").value.trim(),
        link: document.getElementById("mjLink").value.trim(),
        descrizione: document.getElementById("mjDescrizione").value.trim(),
      };
      if (!payload.titolo || !payload.azienda) return;
      const orig = submit.textContent;
      submit.disabled = true;
      submit.textContent = t("manualJob.adding");
      try {
        await api("/api/jobs/manual", { method: "POST", body: JSON.stringify(payload) });
        showToast(t("manualJob.added"), "info");
        close();
        await Promise.all([loadJobs(), loadRecommendations()]);
      } catch (err) {
        showToast(`${t("manualJob.addError")}: ${err.message}`, "info");
        // The job may have been inserted before scoring failed — reflect it.
        await loadJobs().catch(() => {});
      } finally {
        submit.disabled = false;
        submit.textContent = orig;
      }
    });

    // Import from a URL (with pasted-text fallback) → LLM extracts the fields
    // → same /api scoring path as a manual add.
    const importBtn = document.getElementById("mjImportBtn");
    if (importBtn) {
      importBtn.addEventListener("click", async () => {
        const url = (document.getElementById("mjImportUrl").value || "").trim();
        const text = (document.getElementById("mjPasteText").value || "").trim();
        if (!url && !text) {
          showToast(t("manualJob.importNeedInput"), "info");
          return;
        }
        const orig = importBtn.textContent;
        importBtn.disabled = true;
        importBtn.textContent = t("manualJob.importing");
        try {
          const res = await api("/api/jobs/import", {
            method: "POST",
            body: JSON.stringify({ url, text }),
          });
          showToast(res.used_fallback ? t("manualJob.importedFromText") : t("manualJob.added"), "info");
          close();
          await Promise.all([loadJobs(), loadRecommendations()]);
        } catch (err) {
          // 422 fetch_failed → nudge the user to paste the posting text instead.
          showToast(`${t("manualJob.addError")}: ${err.message}`, "info");
          const ta = document.getElementById("mjPasteText");
          if (ta) ta.focus();
        } finally {
          importBtn.disabled = false;
          importBtn.textContent = orig;
        }
      });
    }
  }
}

// Lift two blocks out of the dashboard so they work from any tab: the job
// archive moves into its own #view-jobs tab, and the job-detail panel becomes a
// shared right-side drawer. Reparenting keeps their existing listeners/children.
function setupSharedLayout() {
  const jobsView = document.getElementById("view-jobs");
  const jobsSection = document.querySelector(".jobs-section");
  if (jobsView && jobsSection && jobsSection.parentElement !== jobsView) {
    jobsView.appendChild(jobsSection);
  }
  const detail = document.getElementById("jobDetailInline");
  if (detail && detail.parentElement !== document.body) {
    document.body.appendChild(detail);
    detail.style.display = ""; // now controlled via .is-open, not inline display
  }
  document.getElementById("jobDetailBackdrop")?.addEventListener("click", closeJobDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeJobDetail();
  });
}

async function bootstrap() {
  await initI18n();
  refreshModelPickerLabel();
  // loadJobs: after an on-demand re-score the list still shows "to evaluate".
  initJobDetail({ pinJobToActiveSession, loadJobs });
  initRescore({ loadJobs });
  wireRescoreBulk();
  // The pending badge appears on the row that was just clicked, so the list has
  // to be re-read once the open is recorded.
  initApplyWatch({ onOpened: () => loadJobs() });
  wireApplyWatch();
  initMailbox({ loadJobs });
  wireMailbox();
  initJobList({
    showJobDetail,
    performJobAction,
    toggleFavorite,
    isCompareSelected: isSelected,
    toggleCompare,
  });
  initCompare();
  initScan({
    getKeywords,
    getLocations,
    ensureProviderConfigured,
    // Two gates now: a key to score with, and something of the user's to
    // search for. The second one used to be covered by a built-in default.
    ensureProfileReady: () =>
      ensureProfileReady({
        showToast,
        revealElement,
        terms: getKeywords.getTags(),
        locations: getLocations.getTags(),
        isRemote: document.getElementById("remoteToggle")?.checked || false,
      }),
  });
  setupSharedLayout();
  activateView("dashboard");
  await loadHealth();
  await loadKeysStatus();
  await loadMailboxStatus();
  await loadProfiles();
  await Promise.all([loadJobs(), loadRecommendations()]);
  await loadAnalytics();
  await loadUsage();
  await loadSkillGap();
  await loadReminders();
  await loadSavedSearches();
  await loadWatchlist();
  // Probes the GPU and asks Ollama: slow enough to keep off the critical path,
  // and useless until the user opens Settings anyway.
  initMatchingFacts();
  loadLocalModels();
  await loadSchedulerStatus();
  await loadChatPrompts();
  // i18n is ready here, so the session dropdown / empty-state get localised
  // labels (no boot race). Sessions first: history loads the ACTIVE one
  // (not hardcoded "default"), so the panel matches the restored session.
  await initChatSessions().catch((e) => console.error("initChatSessions failed:", e));
  await reloadChatHistoryForActive();
  // Shows only if the conversation is empty, with localised suggestion labels.
  renderChatEmptyState();
}

bootstrap().catch((error) => {
  console.error(error);
  showToast(`${t("toast.initError")}: ${error.message}`, "info");
});


const closeDetailBtn = document.getElementById('closeDetailBtn');
if (closeDetailBtn) {
    closeDetailBtn.addEventListener('click', closeJobDetail);
}


// The kanban's columns ARE the buckets, so the tab strip has nothing to say
// there and the board always asks for the whole archive.
function _syncBucketStrip(kanban) {
  document.getElementById("jobBuckets")?.classList.toggle("hidden", kanban);
  document.querySelector(".jobs-count-line")?.classList.toggle("hidden", kanban);
}

document.getElementById("viewTableBtn")?.addEventListener("click", e => {
    document.getElementById("tableView").classList.add("is-active");
    document.getElementById("kanbanView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewKanbanBtn").classList.remove("is-active");
  _syncBucketStrip(false);
  loadJobs();
});

document.getElementById("viewKanbanBtn")?.addEventListener("click", e => {
    document.getElementById("kanbanView").classList.add("is-active");
    document.getElementById("tableView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewTableBtn").classList.remove("is-active");
  _syncBucketStrip(true);
  loadJobs();
});

// Tag Input UI Logic
function setupTagInput(containerId, inputId, onRemove) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    const tags = [];

    if (!container || !input) return { getTags: () => [], addMultiple: () => false, clear: () => {} };

    function renderTags() {
        container.querySelectorAll('.tag').forEach(el => el.remove());
        tags.forEach((tagText, index) => {
            const tagEl = document.createElement('span');
            tagEl.className = 'tag';
            tagEl.textContent = tagText;
            
            const removeBtn = document.createElement('span');
            removeBtn.className = 'remove-tag material-symbols-outlined';
            removeBtn.textContent = 'close';
            removeBtn.onclick = () => {
                const [removed] = tags.splice(index, 1);
                renderTags();
                if (typeof onRemove === 'function') onRemove(removed);
            };
            
            tagEl.appendChild(removeBtn);
            container.insertBefore(tagEl, input);
        });
    }

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const val = input.value.trim();
            if (val && !tags.includes(val)) {
                tags.push(val);
                input.value = '';
                renderTags();
            }
        }
    });

    return {
        getTags: () => tags,
        addMultiple: (newTags) => {
            if (!Array.isArray(newTags)) return false;
            let added = false;
            for(const nt of newTags) {
                const val = (nt||'').trim();
                if(val && !tags.includes(val)) {
                    tags.push(val);
                    added = true;
                }
            }
            if(added) renderTags();
            return added;
        },
        clear: () => { tags.length = 0; renderTags(); }
    };
}

const getKeywords = setupTagInput('keywordsContainer', 'keywordsInput', (term) => { _removeFromShortlistApi(term); });
const getLocations = setupTagInput('locationsContainer', 'locationsInput');
window.getKeywords = getKeywords;
// The locations box had no loader at all, which is why it was always empty and
// the app filled the gap with a city of its own.
window.getLocations = getLocations;

initChatActions({
  getKeywords,
  getLocations,
  activateView,
  showJobDetail,
  addRoles: async (roles, keywords) => {
    await addRolesToProfile(roles);
    window.getKeywords?.addMultiple(keywords);
    await _addToShortlistApi(keywords);
  },
  patchProfile: (body) =>
    api("/api/profile", { method: "PATCH", body: JSON.stringify(body) }),
  savePreference: (key, value) =>
    api("/api/preferences", { method: "POST", body: JSON.stringify({ key, value }) }),
  invalidateReadiness,
  renderReadinessStrips,
});

async function loadRoleShortlist() {
  const roles = await _loadShortlistApi();
  if (roles.length && getKeywords && typeof getKeywords.addMultiple === "function") {
    getKeywords.addMultiple(roles);
  }
  // Whatever the shortlist did not cover — the roles read off the CV, the last
  // search actually run — comes from the same chain the scan follows.
  await prefillSearchForm();
}
loadRoleShortlist();

// ─── Update Banner ──────────────────────────────────────────────

// ─── Chat empty state + first-time tutorial ─────────────────────
const CHAT_SUGGESTION_KEYS = [
  'chat.suggestions.roles',
  'chat.suggestions.top5',
];

function renderChatEmptyState() {
  const box = document.getElementById('chatBox');
  if (!box) return;
  if (box.querySelector('.chat-item') || box.querySelector('.chat-empty')) return;
  const empty = document.createElement('div');
  empty.className = 'chat-empty';
  const lead = document.createElement('div');
  lead.className = 'lead';
  lead.textContent = t('chat.suggestions.label');
  empty.appendChild(lead);
  for (const key of CHAT_SUGGESTION_KEYS) {
    const label = t(key);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = label;
    btn.addEventListener('click', () => sendChatMessage(label));
    empty.appendChild(btn);
  }
  box.appendChild(empty);
}

function clearChatEmptyState() {
  const box = document.getElementById('chatBox');
  const empty = box && box.querySelector('.chat-empty');
  if (empty) empty.remove();
}

async function showFirstTimeTutorial() {
  if (localStorage.getItem('tutorialSeen')) return;

  // Skip wizard if user is already fully set up.
  let initialStatus = { provider_configured: false, cv_loaded: false };
  try {
    initialStatus = await fetch('/api/setup/status').then((r) => r.json());
  } catch { /* offline ok */ }
  if (initialStatus.provider_configured && initialStatus.cv_loaded) {
    localStorage.setItem('tutorialSeen', '1');
    return;
  }

  const overlay = document.createElement('div');
  overlay.className = 'tutorial-overlay';
  overlay.id = 'tutorialOverlay';
  document.body.appendChild(overlay);

  let currentStep = 0;
  let pollHandle = null;
  let lastStatus = initialStatus;

  const close = () => {
    if (pollHandle) clearInterval(pollHandle);
    overlay.remove();
    localStorage.setItem('tutorialSeen', '1');
  };
  overlay.tabIndex = -1;
  enableModalDismiss(overlay, close);

  const STEPS = [
    {
      key: 'step1',
      titleKey: 'tutorial.step1Title',
      bodyKey: 'tutorial.step1Body',
      ctaKey: 'tutorial.openSettings',
      ctaTarget: 'settings',
      ctaScroll: 'providerCards',
      isDone: (s) => !!s.provider_configured,
    },
    {
      key: 'step2',
      titleKey: 'tutorial.step2Title',
      bodyKey: 'tutorial.step2Body',
      ctaKey: 'tutorial.openProfile',
      ctaTarget: 'profile',
      ctaScroll: 'cvFile',
      isDone: (s) => !!s.cv_loaded,
    },
    {
      key: 'step3',
      titleKey: 'tutorial.step3Title',
      bodyKey: 'tutorial.step3Body',
      ctaKey: 'tutorial.openSearch',
      ctaTarget: 'job-search',
      ctaScroll: null,
      isDone: () => true, // last step always free
    },
  ];

  const render = () => {
    const step = STEPS[currentStep];
    const stepLabel = (t('tutorial.stepLabel') || 'Step {n} of 3').replace('{n}', currentStep + 1);
    const stepperHtml = STEPS.map((_, i) => {
      let cls = 'wizard-dot';
      if (i < currentStep) cls += ' done';
      else if (i === currentStep) cls += ' active';
      return `<span class="${cls}">${i + 1}</span>`;
    }).join('<span class="wizard-line"></span>');
    const isDone = step.isDone(lastStatus);
    const isLast = currentStep === STEPS.length - 1;
    const nextLabel = isLast ? (t('tutorial.finish') || 'Finish') : (t('tutorial.next') || 'Next');
    const nextDisabled = !isDone ? 'disabled' : '';
    overlay.innerHTML = `
      <div class="tutorial-card wizard-card">
        <div class="wizard-stepper">${stepperHtml}</div>
        <p class="wizard-step-label">${stepLabel}</p>
        <h3>${escapeHtml(t(step.titleKey) || step.key)}</h3>
        <p>${escapeHtml(t(step.bodyKey) || '')}</p>
        <div class="tutorial-actions wizard-actions">
          <button type="button" class="ghost-btn" id="wizSkip">${t('tutorial.skip') || 'Skip'}</button>
          <div class="wizard-actions-right">
            ${currentStep > 0 ? `<button type="button" class="ghost-btn" id="wizBack">${t('tutorial.back') || 'Back'}</button>` : ''}
            <button type="button" class="secondary" id="wizCta">${escapeHtml(t(step.ctaKey) || step.ctaTarget)}</button>
            <button type="button" class="action-main" id="wizNext" ${nextDisabled}>${nextLabel}</button>
          </div>
        </div>
      </div>
    `;
    overlay.querySelector('#wizSkip').addEventListener('click', close);
    const back = overlay.querySelector('#wizBack');
    if (back) back.addEventListener('click', () => { currentStep = Math.max(0, currentStep - 1); render(); });
    overlay.querySelector('#wizCta').addEventListener('click', () => {
      try {
        if (step.ctaScroll) revealElement(step.ctaScroll);
        else activateView(step.ctaTarget);
      } catch (_) {}
    });
    overlay.querySelector('#wizNext').addEventListener('click', () => {
      if (!STEPS[currentStep].isDone(lastStatus)) return;
      if (isLast) { close(); return; }
      currentStep = Math.min(STEPS.length - 1, currentStep + 1);
      render();
    });
  };

  render();
  overlay.focus(); // so Escape / Tab-trap work without a prior click

  // Poll setup_status while overlay is open so the Next button enables
  // the moment the user completes the current step in another tab.
  pollHandle = setInterval(async () => {
    try {
      const s = await fetch('/api/setup/status').then((r) => r.json());
      const prevDone = STEPS[currentStep].isDone(lastStatus);
      lastStatus = s;
      const nowDone = STEPS[currentStep].isDone(lastStatus);
      if (prevDone !== nowDone) render();
    } catch { /* ignore */ }
  }, 1500);
}


window.addEventListener('load', () => {
  checkForUpdate().then(populateSystemInfo).catch(() => { /* offline ok */ });
  wireSystemSettings();
  // Defer tutorial to let dashboard render first.
  setTimeout(showFirstTimeTutorial, 800);
  wirePostScanModal();
  wireOnboardingPlaceholder();
  refreshOnboardingPlaceholder().catch(() => {});
  refreshPinnedStrip().catch(() => {});
  wireMobileChrome();
});

// Hamburger nav + off-canvas Career Coach drawer (mobile only). The CSS hides
// the toggle/FAB/overlay on wide viewports, so these handlers are inert there.
function wireMobileChrome() {
  const navToggle = document.getElementById("navToggle");
  const topnav = document.getElementById("topnav");
  const overlay = document.getElementById("mobileOverlay");
  const fab = document.getElementById("chatFab");
  const rail = document.querySelector(".right-rail");

  const showOverlay = () => {
    if (!overlay) return;
    overlay.hidden = false;
    overlay.classList.add("active");
  };
  const closeAll = () => {
    topnav?.classList.remove("open");
    navToggle?.setAttribute("aria-expanded", "false");
    rail?.classList.remove("drawer-open");
    if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
    fab?.classList.remove("hidden");
  };

  navToggle?.addEventListener("click", () => {
    const open = topnav?.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { rail?.classList.remove("drawer-open"); showOverlay(); fab?.classList.remove("hidden"); }
    else if (overlay) { overlay.classList.remove("active"); overlay.hidden = true; }
  });

  fab?.addEventListener("click", () => {
    rail?.classList.add("drawer-open");
    topnav?.classList.remove("open");
    fab.classList.add("hidden");
    showOverlay();
  });

  overlay?.addEventListener("click", closeAll);
}


/* =============================================================== */
/* v1.3.0: Multi-chat sessions                                       */
/* =============================================================== */

const ChatSessions = {
  active: localStorage.getItem("activeChatSession") || "default",
  list: [],
};

async function initChatSessions() {
  await refreshChatSessions();
  renderChatSessionDropdown();
  wireChatSessionUI();
}

async function refreshChatSessions() {
  let sessions = [];
  try {
    const res = await fetch("/api/chat/sessions");
    if (res.ok) {
      const payload = await res.json();
      sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    } else {
      console.warn("chat sessions fetch returned", res.status);
    }
  } catch (err) {
    console.warn("chat sessions fetch failed", err);
  }
  // Always guarantee a usable list — the dropdown must never be empty.
  if (!sessions.length) {
    sessions = [{ id: "default", title: "", created_at: "", updated_at: "" }];
  }
  ChatSessions.list = sessions;
  if (!ChatSessions.list.find((s) => s.id === ChatSessions.active)) {
    ChatSessions.active = ChatSessions.list[0].id;
    localStorage.setItem("activeChatSession", ChatSessions.active);
  }
}

function renderChatSessionDropdown() {
  const sel = document.getElementById("chatSessionSelect");
  if (!sel) return;
  sel.innerHTML = ChatSessions.list.map((s) => {
    const label = (s.title || "").trim() || (s.id === "default" ? (t("chat.defaultSession") || "Default") : s.id);
    return `<option value="${escapeHtml(s.id)}" ${s.id === ChatSessions.active ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}

function wireChatSessionUI() {
  const sel = document.getElementById("chatSessionSelect");
  // The translation for "chat renamed" has existed in all five languages since
  // the endpoint was written. The button never did.
  const renameBtn = document.getElementById("chatSessionRename");
  if (renameBtn && !renameBtn.dataset.wired) {
    renameBtn.dataset.wired = "1";
    renameBtn.addEventListener("click", async () => {
      const current = ChatSessions.list.find((x) => x.id === ChatSessions.active);
      const title = prompt(t("chat.renameSession"), current?.title || "");
      if (title === null) return;
      try {
        await api(`/api/chat/sessions/${ChatSessions.active}`, {
          method: "PATCH",
          body: JSON.stringify({ title: title.trim() }),
        });
        await refreshChatSessions();
        renderChatSessionDropdown();
        showToast(t("toast.chatRenamed"), "info");
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  }

  const newBtn = document.getElementById("chatSessionNew");
  const delBtn = document.getElementById("chatSessionDelete");
  if (sel) {
    sel.addEventListener("change", async () => {
      ChatSessions.active = sel.value;
      localStorage.setItem("activeChatSession", ChatSessions.active);
      await reloadChatHistoryForActive();
      await refreshPinnedStrip();
    });
  }
  if (newBtn) {
    newBtn.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/chat/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "" }),
        }).then((r) => r.json());
        const s = res.session;
        if (s) {
          ChatSessions.active = s.id;
          localStorage.setItem("activeChatSession", ChatSessions.active);
          await refreshChatSessions();
          renderChatSessionDropdown();
          const box = document.getElementById("chatBox");
          if (box) box.innerHTML = "";
          renderChatEmptyState();
          await refreshPinnedStrip();
          showToast(t("toast.chatCreated") || "New chat created", "info");
        }
      } catch (err) {
        showToast(`${t("toast.chatCreateFailed") || "Could not create chat"}: ${err.message}`, "error");
      }
    });
  }
  if (delBtn) {
    delBtn.addEventListener("click", async () => {
      if (!ChatSessions.active) return;
      if (!confirm(t("chat.confirmDeleteSession") || "Delete this chat?")) return;
      try {
        await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}`, { method: "DELETE" });
        ChatSessions.active = "default";
        localStorage.setItem("activeChatSession", "default");
        await refreshChatSessions();
        renderChatSessionDropdown();
        await reloadChatHistoryForActive();
        await refreshPinnedStrip();
        showToast(t("toast.chatDeleted") || "Chat deleted", "info");
      } catch (err) {
        showToast(`${t("toast.chatDeleteFailed") || "Could not delete chat"}: ${err.message}`, "error");
      }
    });
  }
}

async function reloadChatHistoryForActive() {
  const box = document.getElementById("chatBox");
  if (!box) return;
  box.innerHTML = "";
  try {
    const res = await fetch(`/api/chat/history?session_id=${encodeURIComponent(ChatSessions.active)}&limit=30`).then((r) => r.json());
    (res.messages || []).forEach((m) => appendChat(m.role, m.content, m.meta || null));
  } catch (_) {}
}

/* =============================================================== */
/* v1.3.0: Pin jobs into chat                                        */
/* =============================================================== */

async function refreshPinnedStrip() {
  const strip = document.getElementById("chatPinnedStrip");
  if (!strip) return;
  try {
    const res = await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pinned`).then((r) => r.json());
    const jobs = res.jobs || [];
    if (!jobs.length) {
      strip.innerHTML = "";
      strip.classList.add("hidden");
      return;
    }
    strip.classList.remove("hidden");
    strip.innerHTML = jobs.map((j) => `
      <span class="pinned-pill" data-job-id="${j.id}">
        <span class="material-symbols-outlined">push_pin</span>
        <span class="pinned-text">${escapeHtml(j.titolo || "?")} · ${escapeHtml(j.azienda || "?")}</span>
        <button type="button" class="pinned-remove" data-unpin="${j.id}" title="${t("chat.unpin") || "Unpin"}">×</button>
      </span>
    `).join("");
    strip.querySelectorAll("[data-unpin]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const jid = btn.getAttribute("data-unpin");
        await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pin/${jid}`, { method: "DELETE" });
        await refreshPinnedStrip();
      });
    });
  } catch (_) {
    strip.innerHTML = "";
    strip.classList.add("hidden");
  }
}

async function pinJobToActiveSession(jobId) {
  try {
    await fetch(`/api/chat/sessions/${encodeURIComponent(ChatSessions.active)}/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: Number(jobId) }),
    });
    await refreshPinnedStrip();
    showToast(t("chat.pinned") || "Pinned to chat", "info");
  } catch (err) {
    showToast(`${t("chat.pinFailed") || "Pin failed"}: ${err.message}`, "error");
  }
}

/* =============================================================== */
/* v1.3.0: Onboarding placeholder                                   */
/* =============================================================== */

async function refreshOnboardingPlaceholder() {
  const ph = document.getElementById("onboardingPlaceholder");
  if (!ph) return;
  let status;
  try {
    status = await fetch("/api/setup/status").then((r) => r.json());
  } catch (_) {
    return;
  }
  const providerOk = !!status.provider_configured;
  const cvOk = !!status.cv_loaded;
  if (providerOk && cvOk) {
    ph.classList.add("hidden");
    return;
  }
  ph.classList.remove("hidden");
  const s1 = document.getElementById("onbStep1");
  const s2 = document.getElementById("onbStep2");
  if (s1) s1.classList.toggle("done", providerOk);
  if (s2) s2.classList.toggle("done", cvOk);
}

async function ensureProviderConfigured() {
  try {
    const status = await fetch("/api/setup/status").then((r) => r.json());
    if (status.provider_configured) return true;
  } catch (_) {
    return true; // network glitch — let backend reject if needed
  }
  showToast(t("errors.noProviderToast") || "Configure an AI provider key first", "error");
  try {
    revealElement("providerCards");
  } catch (_) {}
  return false;
}

function wireOnboardingPlaceholder() {
  document.querySelectorAll("#onboardingPlaceholder [data-onb-action]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-onb-action");
      try {
        if (target === "settings") revealElement("providerCards");
        else if (target === "profile") revealElement("cvFile");
      } catch (_) {}
    });
  });
}
