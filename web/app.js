import { api, escapeHtml, setText, truncate, showToast, renderCoachMarkdown } from "./modules/helpers.js";
import { initTheme } from "./modules/theme.js";
import { loadShortlist as _loadShortlistApi, addToShortlist as _addToShortlistApi } from "./modules/shortlist.js";
import { initI18n, t, loadLanguage, getCurrentLang, onLanguageChange } from "./modules/i18n.js";
import { loadProfile as loadProfileView, bindProfileEvents, addRolesToProfile } from "./modules/profile.js";

initTheme();

onLanguageChange(() => {
  if (typeof loadChatPrompts === "function") {
    loadChatPrompts().catch(() => {});
  }
});

// Language selector
const langSelect = document.getElementById('langSelect');
if (langSelect) {
  langSelect.value = getCurrentLang();
  langSelect.addEventListener('change', () => {
    loadLanguage(langSelect.value);
  });
}

let selectedJobId = null;
const PROVIDER_KEY_IDS = ["cerebrasKey", "groqKey", "openaiKey", "anthropicKey", "googleKey", "openrouterKey"];

const PROVIDER_CATALOG = [
  { name: "cerebras", label: "Cerebras", icon: "bolt", placeholder: "sk-..." },
  { name: "groq", label: "Groq", icon: "memory", placeholder: "gsk_..." },
  { name: "openai", label: "OpenAI", icon: "neurology", placeholder: "sk-..." },
  { name: "anthropic", label: "Anthropic", icon: "auto_awesome", placeholder: "sk-ant-..." },
  { name: "google", label: "Google", icon: "language", placeholder: "AI..." },
  { name: "openrouter", label: "OpenRouter", icon: "hub", placeholder: "sk-or-v1-..." },
];

const _providerCardModelCache = {};
const _providerCardFetchTimes = {};

let _chatOverrideToastShown = false;
let _lastChatOverrideProvider = null;
let _lastChatOverrideModel = null;

function _maybeOfferPersistChatOverride(providerVal, modelVal) {
  if (_chatOverrideToastShown) return;
  if (!providerVal) return;
  _chatOverrideToastShown = true;

  const body = (t("chat.saveAsDefaultBody") || "Save {provider} / {model} to Settings")
    .replace("{provider}", providerVal)
    .replace("{model}", modelVal || t("chat.modelAuto") || "auto");

  const wrapper = document.createElement("div");
  wrapper.className = "chat-override-toast";
  wrapper.innerHTML = `
    <div class="chat-override-toast-body">
      <strong>${t("chat.saveAsDefault") || "Use as default?"}</strong>
      <div class="micro">${body}</div>
    </div>
    <div class="chat-override-toast-actions">
      <button type="button" class="secondary" data-action="yes">${t("common.yes") || "Yes"}</button>
      <button type="button" class="ghost-btn" data-action="no">${t("common.no") || "No"}</button>
    </div>
  `;
  document.body.appendChild(wrapper);

  wrapper.querySelector('[data-action="yes"]').addEventListener("click", async () => {
    try {
      await api("/api/providers/keys", {
        method: "POST",
        body: JSON.stringify({
          primary_provider: providerVal,
          preferred_model: modelVal || "",
        }),
      });
      await loadKeysStatus();
      showToast(t("toast.providerSaved"), "info");
    } catch (err) {
      showToast(`${t("toast.keySaveError")}: ${err.message}`, "error");
    } finally {
      wrapper.remove();
    }
  });
  wrapper.querySelector('[data-action="no"]').addEventListener("click", () => wrapper.remove());

  setTimeout(() => { if (wrapper.isConnected) wrapper.remove(); }, 12000);
}

async function _populateChatModelSelector(providerName) {
  const sel = document.getElementById("chatModelSelectorModel");
  if (!sel) return;
  const autoLabel = t("chat.modelAuto") || "Auto model";
  if (!providerName) {
    sel.innerHTML = `<option value="">${autoLabel}</option>`;
    sel.disabled = true;
    return;
  }
  sel.innerHTML = `<option value="">${autoLabel}</option>`;
  sel.disabled = true;
  try {
    const data = _providerCardModelCache[providerName] || (await fetchProviderModels(providerName, false));
    const models = Array.isArray(data.models) ? data.models : [];
    const recommended = data.recommended || null;
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m === recommended ? `⭐ ${m}` : m;
      sel.appendChild(opt);
    }
    sel.disabled = models.length === 0;
  } catch (err) {
    sel.disabled = true;
  }
}

function _refreshChatProviderSelectorOptions() {
  const sel = document.getElementById("chatModelSelector");
  if (!sel) return;
  const meta = _providersMetadataCache || {};
  const previous = sel.value;
  Array.from(sel.options).forEach((opt) => {
    if (!opt.value) return;
    const available = meta[opt.value]?.available !== false && meta[opt.value]?.available !== undefined;
    opt.disabled = !available;
    opt.textContent = opt.value.charAt(0).toUpperCase() + opt.value.slice(1) + (available ? "" : " (no key)");
  });
  if (previous && sel.querySelector(`option[value="${previous}"]`)?.disabled) {
    sel.value = "";
  }
}

function _providerCardEl(name) {
  return document.querySelector(`#providerCards .provider-card[data-provider="${name}"]`);
}

function _formatRelative(epoch) {
  if (!epoch) return "";
  const seconds = Math.max(0, Math.floor((Date.now() / 1000) - Number(epoch)));
  if (seconds < 60) return t("settings.providers.justNow") || "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function renderProviderCards(keys, providerMeta) {
  const container = document.getElementById("providerCards");
  if (!container) return;
  const configuredKey = (n) => Boolean(keys?.[`${n}_configured`]);
  const primary = String(keys?.primary_provider || providerMeta?.active_provider || "").toLowerCase();
  const activeAvailable = providerMeta?.available !== false;

  container.innerHTML = PROVIDER_CATALOG.map((p) => {
    const configured = configuredKey(p.name);
    const isPrimary = primary === p.name && activeAvailable;
    const state = configured ? (isPrimary ? "active" : "configured") : "empty";
    return `
      <article class="provider-card" data-provider="${p.name}" data-state="${state}">
        <header class="provider-card-head">
          <span class="material-symbols-outlined provider-card-icon">${p.icon}</span>
          <h4 class="provider-card-title">${p.label}</h4>
          <label class="provider-primary-radio" title="${t("settings.providers.setPrimary")}">
            <input type="radio" name="primaryProviderRadio" value="${p.name}" ${isPrimary ? "checked" : ""} ${configured ? "" : "disabled"} />
            <span class="micro" data-i18n="settings.providers.setPrimary">Set as primary</span>
          </label>
        </header>
        <div class="provider-card-body">
          <label class="field-label provider-key-row">
            <span class="micro" data-i18n="settings.providers.apiKey">API Key</span>
            <div class="key-input-row">
              <input type="password" class="provider-key-input" placeholder="${p.placeholder}" autocomplete="off" />
              <button type="button" class="ghost-btn provider-toggle-visibility" title="Show/Hide">
                <span class="material-symbols-outlined">visibility</span>
              </button>
            </div>
          </label>
          <button type="button" class="secondary provider-save-btn" data-i18n="settings.providers.saveAndFetch">Save &amp; fetch models</button>
          <label class="field-label provider-model-row">
            <span class="micro" data-i18n="settings.providers.model">Model</span>
            <select class="provider-model-select" ${configured ? "" : "disabled"}>
              <option value="" data-i18n="settings.providers.modelAuto">Auto (provider default)</option>
            </select>
          </label>
          <div class="provider-status">
            <span class="provider-status-text micro"></span>
            <button type="button" class="ghost-btn provider-refresh-btn" title="${t("settings.providers.refresh")}" ${configured ? "" : "disabled"}>
              <span class="material-symbols-outlined">refresh</span>
            </button>
          </div>
        </div>
      </article>
    `;
  }).join("");

  for (const p of PROVIDER_CATALOG) {
    if (configuredKey(p.name)) {
      void fetchAndRenderProviderModels(p.name, false);
    } else {
      _setProviderStatusText(p.name, t("settings.providers.addKey"));
    }
  }
}

function _setProviderStatusText(name, text, kind = "info") {
  const card = _providerCardEl(name);
  if (!card) return;
  const el = card.querySelector(".provider-status-text");
  if (el) {
    el.textContent = text || "";
    el.dataset.kind = kind;
  }
}

function _setProviderCardState(name, state) {
  const card = _providerCardEl(name);
  if (card) card.dataset.state = state;
}

async function fetchProviderModels(name, force = false) {
  const url = `/api/providers/${encodeURIComponent(name)}/models${force ? "?force_refresh=1" : ""}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = "fetch_failed";
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) { /* noop */ }
    throw new Error(detail);
  }
  return res.json();
}

async function fetchAndRenderProviderModels(name, force) {
  _setProviderCardState(name, "fetching");
  _setProviderStatusText(name, t("settings.providers.fetching"));
  try {
    const data = await fetchProviderModels(name, force);
    const models = Array.isArray(data.models) ? data.models : [];
    const recommended = data.recommended || null;
    _providerCardModelCache[name] = { models, recommended };
    _providerCardFetchTimes[name] = data.fetched_at || (Date.now() / 1000);

    const card = _providerCardEl(name);
    if (!card) return;
    const select = card.querySelector(".provider-model-select");
    if (select) {
      const autoLabel = t("settings.providers.modelAuto");
      const opts = [`<option value="">${autoLabel}</option>`];
      for (const m of models) {
        const isRec = m === recommended;
        const star = isRec ? "⭐ " : "";
        opts.push(`<option value="${m}">${star}${m}</option>`);
      }
      select.innerHTML = opts.join("");
      select.disabled = false;
      // Pre-select if this provider is the primary and a preferred_model is known
      const primarySelect = document.getElementById("primaryProvider");
      const preferredSelect = document.getElementById("preferredModel");
      if (primarySelect && primarySelect.value === name && preferredSelect && preferredSelect.value) {
        const exists = models.includes(preferredSelect.value);
        if (exists) select.value = preferredSelect.value;
      }
    }
    const statusMsg = models.length
      ? t("settings.providers.modelsLoaded").replace("{count}", String(models.length))
      : t("settings.providers.empty");
    _setProviderStatusText(name, `${statusMsg} · ${_formatRelative(_providerCardFetchTimes[name])}`);
    if (_providerCardEl(name).dataset.state !== "active") {
      _setProviderCardState(name, "configured");
    }

    // Sync legacy hidden selects so chat/populateModelOptions still works
    if (!_providersMetadataCache[name]) _providersMetadataCache[name] = {};
    _providersMetadataCache[name].models = models;
    _providersMetadataCache[name].available = true;
  } catch (err) {
    _setProviderCardState(name, "error");
    _setProviderStatusText(name, t("settings.providers.fetchFailed"), "error");
  }
}

async function onSaveProviderKey(name, keyValue) {
  const card = _providerCardEl(name);
  if (!card) return;
  const saveBtn = card.querySelector(".provider-save-btn");
  if (saveBtn) saveBtn.disabled = true;
  _setProviderCardState(name, "fetching");
  _setProviderStatusText(name, t("settings.providers.saving"));
  try {
    const payload = {};
    payload[`${name}_api_key`] = keyValue;
    await api("/api/providers/keys", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const input = card.querySelector(".provider-key-input");
    if (input) input.value = "";
    await loadKeysStatus();
    await fetchAndRenderProviderModels(name, true);
    showToast(t("toast.providerSaved"), "info");
  } catch (err) {
    _setProviderCardState(name, "error");
    _setProviderStatusText(name, `${t("settings.providers.saveFailed")}: ${err.message}`, "error");
    showToast(`${t("toast.keySaveError")}: ${err.message}`, "error");
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function onSetPrimaryProvider(name, modelOverride) {
  try {
    const payload = { primary_provider: name };
    if (modelOverride !== undefined) payload.preferred_model = modelOverride;
    await api("/api/providers/keys", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadKeysStatus();
    showToast(t("toast.providerSaved"), "info");
  } catch (err) {
    showToast(`${t("toast.keySaveError")}: ${err.message}`, "error");
  }
}

function hasAnyProviderConfigured(keys) {
  return Boolean(
    keys.cerebras_configured
      || keys.groq_configured
      || keys.openai_configured
      || keys.anthropic_configured
      || keys.google_configured
      || keys.openrouter_configured,
  );
}

function normalizeKeyStatus(keys = {}, provider = {}) {
  return {
    cerebras_configured: !!keys.cerebras_configured,
    groq_configured: !!keys.groq_configured,
    openai_configured: !!keys.openai_configured,
    anthropic_configured: !!keys.anthropic_configured,
    google_configured: !!keys.google_configured,
    openrouter_configured: !!keys.openrouter_configured,
    primary_provider: keys.primary_provider || "",
    active_provider: provider.active_provider || "none",
    active_model: provider.active_model || "none",
  };
}

function setPrimaryProviderValue(providerName) {
  const select = document.getElementById("primaryProvider");
  if (!select) return;
  const normalized = String(providerName || "").trim().toLowerCase();
  const exists = Array.from(select.options).some((opt) => opt.value === normalized);
  select.value = exists ? normalized : "";
}

let _providersMetadataCache = {};

function populateModelOptions(providerName, desiredModel) {
  const select = document.getElementById("preferredModel");
  if (!select) return;
  const current = desiredModel !== undefined ? desiredModel : select.value;
  const provider = (providerName || "").trim().toLowerCase();
  const autoLabel = t("settings.modelAuto");
  select.innerHTML = `<option value="">${autoLabel}</option>`;
  const meta = _providersMetadataCache[provider];
  const models = meta && Array.isArray(meta.models) ? meta.models : [];
  for (const m of models) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    select.appendChild(opt);
  }
  const exists = Array.from(select.options).some((opt) => opt.value === current);
  select.value = exists ? current : "";
}

function updateProvidersMetadata(metadata, desiredModel) {
  if (metadata && typeof metadata === "object") {
    _providersMetadataCache = metadata.providers || {};
  }
  const provider = document.getElementById("primaryProvider")?.value || "";
  populateModelOptions(provider, desiredModel);
  _refreshChatProviderSelectorOptions();
}

function activateView(viewName) {
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("is-active", section.id === `view-${viewName}`);
  });

  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.view === viewName);
  });

  document.querySelectorAll(".rail-link").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.view === viewName);
  });
}

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

  box.appendChild(item);
  box.scrollTop = box.scrollHeight;
}

function ensureNoKeyBanner(show, message) {
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
  banner.innerHTML = `
    <span class="material-symbols-outlined">warning</span>
    <span class="no-key-banner-text">${escapeHtml(message)}</span>
    <span class="no-key-banner-hint">${t("banner.signupHint")}</span>
    <a href="https://cloud.cerebras.ai/?utm_source=jobfinder" target="_blank" rel="noopener noreferrer" class="no-key-banner-link no-key-banner-link--primary">${t("banner.signupCerebras")}</a>
    <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" class="no-key-banner-link">${t("banner.signupGroq")}</a>
    <a href="#" id="noApiKeyBannerLink" class="no-key-banner-link">${t("banner.openSettings")}</a>
    <button type="button" id="noApiKeyBannerClose" class="no-key-banner-close" aria-label="close">×</button>
  `;
  banner.querySelector("#noApiKeyBannerClose").addEventListener("click", () => banner.remove());
  banner.querySelector("#noApiKeyBannerLink").addEventListener("click", (e) => {
    e.preventDefault();
    activateView("settings");
    const keys = document.getElementById("providerCards");
    if (keys && keys.scrollIntoView) keys.scrollIntoView({ behavior: "smooth", block: "center" });
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
  const linkedinInput = document.getElementById("linkedinUrl");
  if (linkedinInput && prefs.linkedin_url) {
    linkedinInput.value = prefs.linkedin_url;
  }

  const keys = health.keys || {};
  const status = normalizeKeyStatus(keys, health.provider || {});
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(health.provider || {}, keys.preferred_model || "");
  renderProviderCards(keys, health.provider || {});
  setText("keysStatus", JSON.stringify(status, null, 2));
}

function setKeysSectionMode(_configured, _forceExpanded = false) {
  /* no-op: provider cards manage their own state */
}

async function loadKeysStatus() {
  const payload = await api("/api/providers/keys/status");
  const keys = payload.keys || {};
  const provider = payload.provider || {};
  const status = normalizeKeyStatus(keys, provider);
  setPrimaryProviderValue(status.primary_provider);
  updateProvidersMetadata(provider, keys.preferred_model || "");
  renderProviderCards(keys, provider);
  setText("keysStatus", JSON.stringify(status, null, 2));
}

async function saveKeys() {
  /* legacy entry point — replaced by per-card onSaveProviderKey */
}

async function loadProfiles() {
  const payload = await api("/api/profiles");
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
}

let _matchRadarChart = null;

function renderMatchRadar(axes) {
  const canvas = document.getElementById("detailMatchRadar");
  if (!canvas || typeof window.Chart === "undefined") return;
  const data = axes && typeof axes === "object" ? axes : {};
  const labels = [
    t("offcanvas.axisSkills"),
    t("offcanvas.axisSeniority"),
    t("offcanvas.axisRemote"),
    t("offcanvas.axisSalary"),
    t("offcanvas.axisContract"),
  ];
  const values = [
    Number(data.skills_match) || 0,
    Number(data.seniority_match) || 0,
    Number(data.remote_match) || 0,
    Number(data.salary_match) || 0,
    Number(data.contract_match) || 0,
  ];
  if (_matchRadarChart) {
    _matchRadarChart.destroy();
    _matchRadarChart = null;
  }
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

async function showJobDetail(jobId) {
  const payload = await api(`/api/jobs/${jobId}`);
  const job = payload.job || {};
  const analysis = job.analysis || {};
  selectedJobId = job.id || null;

  setText("detailStatus", `Stato: ${job.status || "open"}`);
  setText("detailTitle", job.titolo || "Title unavailable");
  setText("detailCompany", job.azienda || "Company unavailable");
  setText(
    "detailMeta",
    `${job.sede || "Sede N/D"} | Score ${job.punteggio_ai || 0}/10 | ${job.modalita || "Modalita N/D"}`,
  );

  const detailLinkBtn = document.getElementById("detailLinkBtn");
  if (detailLinkBtn) {
    if (job.link) {
      detailLinkBtn.href = job.link;
      detailLinkBtn.style.display = "flex";
    } else {
      detailLinkBtn.style.display = "none";
    }
  }

  const genBtn = document.getElementById("generateCoverLetterBtn");
  const covBox = document.getElementById("coverLetterBox");
  if (genBtn && covBox) {
    genBtn.style.display = "inline-block";
    covBox.style.display = "none";
    document.getElementById("coverLetterOutput").textContent = "";
  }

  const container = document.getElementById("jobDetailContainer");
  if (container) {
    const score = job.punteggio_ai || 0;
    let ralSpan = "";
    if (analysis && analysis.ral_stimata && analysis.ral_stimata !== "Non stimabile") {
      ralSpan = `<div class="info-tag"><strong>RAL:</strong> ${escapeHtml(analysis.ral_stimata)}</div>`;
    }
    
    container.innerHTML = `
      <div class="modern-detail">
        <div class="modern-detail-grid">
          <div class="info-card highlight" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
            <h4>${t("offcanvas.matchScore")}</h4>
            <div class="score-xl">${score}/10</div>
            <div class="text-sm mt-8 text-center">${escapeHtml((analysis ? analysis.consiglio : null) || job.consiglio || "")}</div>
          </div>
          <div class="info-card">
            <h4>${t("offcanvas.positionDetails")}</h4>
            ${ralSpan}
            <div class="info-tag"><strong>${t("offcanvas.contract")}:</strong> ${escapeHtml((analysis ? analysis.contratto : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.remoteWork")}:</strong> ${escapeHtml((analysis ? analysis.smart_working : null) || job.modalita || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.experience")}:</strong> ${escapeHtml((analysis ? analysis.anni_esperienza_richiesti : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.codingSkills")}:</strong> ${escapeHtml((analysis ? analysis.programmazione_richiesta : null) || "N/A")}</div>
            <div class="info-tag"><strong>${t("offcanvas.graduateFriendly")}:</strong> ${escapeHtml((analysis ? analysis.adatta_neolaureati : null) || "N/A")}</div>
          </div>
        </div>
        <div class="mt-16">
          <h4>${t("offcanvas.prosAndCons")}</h4>
          <ul class="pros-cons">
            <li class="pro">✅ ${escapeHtml((analysis ? analysis.punti_forza_per_diego : null) || "N/A")}</li>
            <li class="con">❌ ${escapeHtml((analysis ? analysis.punti_deboli_per_diego : null) || "N/A")}</li>
          </ul>
        </div>
        <div class="info-card mt-8">
            <p class="text-sm">💡 <strong>${t("offcanvas.aiVerdict")}:</strong> ${escapeHtml((analysis ? analysis.riassunto : null) || "")}</p>
        </div>
        <div class="mt-16 info-card">
          <h4>${t("offcanvas.breakdown")}</h4>
          <canvas id="detailMatchRadar" height="220"></canvas>
        </div>
        <div class="mt-16">
          <h4>${t("offcanvas.listingMeta")}</h4>
          <p class="text-sm text-dim">${t("offcanvas.search")}: ${escapeHtml(job.ricerca_usata)} | ${t("jobs.source")}: ${escapeHtml(job.fonte || "App")} | ${t("offcanvas.found")}: ${escapeHtml(job.first_seen_at || "")} | ${t("offcanvas.companyRep")}: ${escapeHtml((analysis ? analysis.reputazione_azienda : null) || "N/A")}</p>
        </div>
      </div>
    `;
    renderMatchRadar(analysis && analysis.match_axes);
  }
  
  const inlineDetail = document.getElementById('jobDetailInline');
  if (inlineDetail) {
    inlineDetail.style.display = 'block';
    inlineDetail.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

}

async function performJobAction(jobId, action) {
  await api(`/api/jobs/${jobId}/action`, {
    method: "POST",
    body: JSON.stringify({ action, notes: "" }),
  });
  await Promise.all([loadJobs(), loadRecommendations()]);
}

async function toggleFavorite(jobId, isFavorite) {
  await api(`/api/jobs/${jobId}/favorite`, {
    method: "POST",
    body: JSON.stringify({ is_favorite: isFavorite }),
  });
  await Promise.all([loadJobs(), loadRecommendations()]);
}

function recommendationCardHtml(job) {
  const score = Number(job.punteggio_ai || 0);
  const consiglio = escapeHtml(job.consiglio || "Evaluate match");
  const title = escapeHtml(job.titolo || t("jobs.titleUnavailable"));
  const company = escapeHtml(job.azienda || t("jobs.companyUnavailable"));
  const newTag = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
  const favoriteText = job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite");
  const nextFavorite = job.is_favorite ? "0" : "1";
  const linkHtml = job.link ? `<div style="margin-top: 4px"><a href="${job.link}" target="_blank" rel="noopener">🔗 ${t("jobs.linkToOffer")}</a></div>` : "";

  return `
    <article class="rec-card" data-rec-id="${job.id}">
      <div class="rec-head">
        <div class="rec-title">${title}</div>
        <span class="rec-score">${score}/10</span>
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

async function loadRecommendations() {
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

async function loadChatPrompts() {
  const wrap = document.getElementById("chatQuickPrompts");
  if (!wrap) return;

  wrap.innerHTML = "";
  try {
    const payload = await api(`/api/chat/prompts?lang=${encodeURIComponent(getCurrentLang() || "en")}`);
    const prompts = payload.prompts || [];
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

async function sendChatMessage(message) {
  const text = String(message || "").trim();
  if (!text) return;

  appendChat("user", text);

  const chatBox = document.getElementById("chatBox");
  let pendingEl = null;
  if (chatBox) {
    pendingEl = document.createElement("div");
    pendingEl.className = "chat-item assistant pending";
    pendingEl.innerHTML = '<div class="role">AI</div><div class="bubble"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></div>';
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
      body: JSON.stringify({ message: text, session_id: "default", provider: providerVal, model: modelVal }),
    });

    if (providerVal && (providerVal !== _lastChatOverrideProvider || modelVal !== _lastChatOverrideModel)) {
      _maybeOfferPersistChatOverride(providerVal, modelVal);
      _lastChatOverrideProvider = providerVal;
      _lastChatOverrideModel = modelVal;
    }
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    appendChat("assistant", result.answer || "No response available.", { suggested_roles: result.suggested_roles });

    if (result.action && result.action.type === "FILL_SCAN_FORM") {
      // populate tags
      if (!getKeywords?.addMultiple || !getLocations?.addMultiple) {
          console.warn("Tag setups not ready");
      } else {
         const kwTags = getKeywords.addMultiple(result.action.keywords || []);
         const locTags = getLocations.addMultiple(result.action.locations || []);
         if (kwTags || locTags) {
           showToast(t("toast.formFilled"), "info");
           activateView("settings");
         }
      }
    }
  } catch (error) {
    if (pendingEl && pendingEl.parentNode) pendingEl.parentNode.removeChild(pendingEl);
    appendChat("assistant", `${t("toast.chatError")}: ${error.message}`);
  }
}

async function loadJobs() {
  const onlyNew = document.getElementById("onlyNew").checked;
  const onlyFavorites = document.getElementById("onlyFavorites").checked;
  const searchText = document.getElementById("searchText").value.trim();
  const status = document.getElementById("statusFilter").value;
  const minScoreRaw = document.getElementById("minScore").value.trim();
  const maxAgeRaw = document.getElementById("maxAgeDays").value.trim();

  const query = new URLSearchParams({
    only_new: onlyNew ? "true" : "false",
    only_favorites: onlyFavorites ? "true" : "false",
    limit: "250",
  });
  if (searchText) query.set("search_text", searchText);
  if (status) query.set("status", status);
  if (minScoreRaw) query.set("min_score", minScoreRaw);
  if (maxAgeRaw) query.set("max_age_days", maxAgeRaw);

  const { jobs } = await api(`/api/jobs?${query.toString()}`);
  const body = document.getElementById("jobsTableBody");
  body.innerHTML = "";

  for (const job of jobs) {
    const newBadge = job.is_new ? `<span class="pill-new">${t("jobs.newBadge")}</span>` : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${job.punteggio_ai || 0}/10 ${newBadge}</td>
      <td>${truncate(job.titolo || "")}</td>
      <td>${truncate(job.azienda || "")}</td>
      <td>${truncate(job.sede || "")}</td>
      <td>${truncate(job.fonte || "")}</td>
      <td>${truncate(job.consiglio || "")}</td>
      <td>
        <button data-detail-id="${job.id}" class="secondary">${t("jobs.details")}</button>
        ${job.link ? `<a href="${job.link}" target="_blank" rel="noopener" style="margin-left: 8px;">🔗</a>` : ""}
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
        await performJobAction(id, action);
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
        await toggleFavorite(id, fav);
      } catch (error) {
        showToast(`${t("toast.favoriteError")}: ${error.message}`, "info");
      }
    });
  });

  body.querySelectorAll("button[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.detailId;
      try {
        await showJobDetail(id);
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

function normalizeJobStatus(status) {
  const normalized = String(status || "open").trim().toLowerCase();
  if (normalized === "interview") return "interviewing";
  return normalized;
}

function renderKanban(jobs) {
  const kanbanView = document.getElementById("kanbanView");
  if (!kanbanView) return;

  const columns = {
    open: kanbanView.querySelector('.kanban-col[data-status="open"] .cards-container'),
    applied: kanbanView.querySelector('.kanban-col[data-status="applied"] .cards-container'),
    interviewing: kanbanView.querySelector('.kanban-col[data-status="interviewing"] .cards-container'),
    rejected: kanbanView.querySelector('.kanban-col[data-status="rejected"] .cards-container'),
  };

  Object.values(columns).forEach((container) => {
    if (container) container.innerHTML = "";
  });

  const counts = { open: 0, applied: 0, interviewing: 0, rejected: 0 };
  for (const job of jobs || []) {
    const status = normalizeJobStatus(job.status);
    if (!(status in columns) || !columns[status]) continue;

    counts[status] += 1;
    const card = document.createElement("article");
    card.className = "kanban-card";
    card.innerHTML = `
      <strong>${escapeHtml(job.titolo || t("jobs.titleUnavailable"))}</strong>
      <div class="micro">${escapeHtml(job.azienda || t("jobs.companyUnavailable"))}</div>
      <div class="micro">Score: ${job.punteggio_ai || 0}/10</div>
      <div class="mini" style="margin-top:8px;">
        <button class="secondary" data-k-detail-id="${job.id}">${t("jobs.details")}</button>
        <button class="apply-btn" data-k-action="${status === "open" ? "applied" : "reopened"}" data-id="${job.id}">
          ${status === "open" ? t("jobs.apply") : t("jobs.reopen")}
        </button>
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
        await showJobDetail(btn.dataset.kDetailId);
      } catch (error) {
        showToast(`${t("toast.detailError")}: ${error.message}`, "info");
      }
    });
  });

  kanbanView.querySelectorAll("button[data-k-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await performJobAction(btn.dataset.id, btn.dataset.kAction);
      } catch (error) {
        showToast(`${t("toast.actionError")}: ${error.message}`, "info");
      }
    });
  });
}

async function loadChatHistory() {
  const { messages } = await api("/api/chat/history?session_id=default&limit=20");
  const box = document.getElementById("chatBox");
  box.innerHTML = "";
  for (const msg of messages) {
    appendChat(msg.role, msg.content);
  }
}

document.getElementById("linkedinForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("linkedinUrl").value.trim();
  try {
    await api("/api/preferences", {
      method: "POST",
      body: JSON.stringify({ key: "linkedin_url", value: url }),
    });
    const status = document.getElementById("linkedinStatus");
    status.textContent = t("toast.linkedinSaved");
    status.classList.remove("hidden");
    setTimeout(() => status.classList.add("hidden"), 3000);
    showToast(t("toast.linkedinSaved"), "info");
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

  const submitBtn = event.currentTarget.querySelector('button[type="submit"]');
  const originalLabel = submitBtn ? submitBtn.innerHTML : "";
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `<span class="spinner-inline"></span> ${t("toast.cvAnalyzing") || "Analyzing CV with AI..."}`;
  }
  showToast(t("toast.cvAnalyzing") || "Analyzing CV with AI...", "info");

  try {
    const response = await fetch("/api/upload-cv", {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      setText("cvSummary", `${t("toast.uploadError")}: ${await response.text()}`);
      showToast(t("toast.uploadError") || "Upload failed", "error");
      return;
    }

    const payload = await response.json();
    setText("cvSummary", JSON.stringify(payload, null, 2));
    await loadProfiles();
    await loadProfileView();
    await loadRecommendations();

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
  }
});

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
  });
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
      if (target.classList.contains("provider-save-btn")) {
        const input = card.querySelector(".provider-key-input");
        const value = input ? input.value.trim() : "";
        if (!value) {
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
}

document.getElementById("scanForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  
  // Add any pending typed text that wasn't entered
  const kwInputRaw = document.getElementById("keywordsInput").value.trim();
  if (kwInputRaw) {
    getKeywords.addMultiple([kwInputRaw]);
    document.getElementById("keywordsInput").value = '';
  }
  const locInputRaw = document.getElementById("locationsInput").value.trim();
  if (locInputRaw) {
    getLocations.addMultiple([locInputRaw]);
    document.getElementById("locationsInput").value = '';
  }

  const termsText = getKeywords.getTags().join(", ");
  const siteCheckboxes = document.querySelectorAll('input[name="scanSites"]:checked');
  const selectedSites = Array.from(siteCheckboxes).map(cb => cb.value);
  const isRemote = document.getElementById("remoteToggle")?.checked || false;
  const location = getLocations.getTags().join(", ");

  const params = new URLSearchParams();
  if (termsText) params.set("search_terms", termsText);
  if (location) params.set("location", location);
  params.set("is_remote", isRemote);
  params.set("sites", (selectedSites.length > 0 ? selectedSites : ["linkedin", "indeed"]).join(","));

  // Show scan overlay
  const overlay = document.getElementById("scanOverlay");
  const progressText = document.getElementById("scanProgressText");
  const progressFill = document.getElementById("scanProgressFill");
  const feedEl = document.getElementById("scanFeed");
  overlay.style.display = "flex";
  overlay.classList.remove("minimized");
  progressFill.style.width = "5%";
  progressText.textContent = t("scan.connecting");
  if (feedEl) feedEl.innerHTML = "";

  const appendFeed = (iconName, textHtml, chip) => {
    if (!feedEl) return;
    const li = document.createElement("li");
    const chipHtml = chip ? `<span class="feed-chip ${chip.cls || ""}">${chip.label}</span>` : "";
    li.innerHTML = `<span class="material-symbols-outlined feed-icon">${iconName}</span><span class="feed-text">${textHtml}</span>${chipHtml}`;
    feedEl.appendChild(li);
    while (feedEl.children.length > 50) feedEl.removeChild(feedEl.firstChild);
    feedEl.scrollTop = feedEl.scrollHeight;
  };
  const escHtml = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let analysisCount = 0;
  let totalFound = 0;

  const evtSource = new EventSource(`/api/scan/stream?${params.toString()}`);
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.status === "started") {
        const terms = (data.terms || []).join(", ");
        progressText.textContent = `${t("scan.searching")}: ${terms}`;
        progressFill.style.width = "10%";
        appendFeed("travel_explore", t("scan.feedStarted", { terms: escHtml(terms) }));
      } else if (data.status === "scraped") {
        totalFound += data.found || 0;
        progressText.textContent = t("scan.foundJobs", { count: totalFound });
        progressFill.style.width = "30%";
        appendFeed("manage_search", t("scan.feedScraped", { found: data.found || 0, portal: escHtml(data.portal || data.site || "") }));
      } else if (data.status === "analyzed") {
        analysisCount++;
        const pct = Math.min(30 + (analysisCount * 3), 90);
        progressFill.style.width = pct + "%";
        const j = data.job || {};
        const score = Number(j.score || 0);
        const cls = score >= 7 ? "score-high" : score >= 4 ? "score-mid" : "score-low";
        progressText.textContent = t("scan.analyzed", { title: j.titolo || "?", company: j.azienda || "?", score: score });
        appendFeed("check_circle", t("scan.feedAnalyzed", { title: escHtml(j.titolo || "?"), company: escHtml(j.azienda || "?") }), { label: `${score}/10`, cls });
      } else if (data.status === "complete") {
        progressFill.style.width = "100%";
        progressText.textContent = t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 });
        appendFeed("task_alt", t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 }));
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; overlay.classList.remove("minimized"); }, 2500);
        Promise.all([loadJobs(), loadRecommendations()]);
      } else if (data.error) {
        progressText.textContent = `${t("scan.error")}: ${data.error}`;
        appendFeed("error", `${t("scan.error")}: ${escHtml(data.error)}`);
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; overlay.classList.remove("minimized"); }, 3000);
      }
    } catch (_) {}
  };
  evtSource.onerror = () => {
    evtSource.close();
    progressText.textContent = t("scan.connectionLost");
    setTimeout(() => { overlay.style.display = "none"; }, 2000);
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  document.getElementById("cancelScanBtn").onclick = () => {
    evtSource.close();
    overlay.style.display = "none";
    overlay.classList.remove("minimized");
    Promise.all([loadJobs(), loadRecommendations()]);
  };

  const minimizeBtn = document.getElementById("minimizeScanBtn");
  if (minimizeBtn) {
    minimizeBtn.onclick = () => overlay.classList.toggle("minimized");
  }
});

const _chatForm = document.getElementById("chatForm");
if (_chatForm) _chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendChatMessage(message);
});

const _quickRecommendBtn = document.getElementById("quickRecommendBtn");
if (_quickRecommendBtn) _quickRecommendBtn.addEventListener("click", async () => {
  await sendChatMessage("Recommend the top 5 jobs I should apply for today, in priority order.");
});

const _refreshRecommendationsBtn = document.getElementById("refreshRecommendationsBtn");
if (_refreshRecommendationsBtn) _refreshRecommendationsBtn.addEventListener("click", loadRecommendations);

const _focusOpenBtn = document.getElementById("focusOpenBtn");
if (_focusOpenBtn) _focusOpenBtn.addEventListener("click", async () => {
  const status = document.getElementById("statusFilter");
  status.value = "open";
  activateView("dashboard");
  await loadJobs();
  requestAnimationFrame(() => {
    document.querySelector(".jobs-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

document.querySelectorAll("[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view || "dashboard";
    activateView(view);
    if (view === "profile") {
      loadProfileView().catch(() => {});
    }
  });
});

bindProfileEvents();

const _primaryProviderEl = document.getElementById("primaryProvider");
if (_primaryProviderEl) {
  _primaryProviderEl.addEventListener("change", () => {
    populateModelOptions(_primaryProviderEl.value, "");
  });
}

const _chatProviderEl = document.getElementById("chatModelSelector");
if (_chatProviderEl) {
  _chatProviderEl.addEventListener("change", () => {
    _populateChatModelSelector(_chatProviderEl.value);
  });
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
  if (!selectedJobId) {
    showToast(t("toast.openJobFirst"), "info");
    return;
  }
  try {
    await performJobAction(selectedJobId, "applied");
    showToast(t("toast.appMarked"), "info");
  } catch (error) {
    showToast(`${t("toast.actionError")}: ${error.message}`, "info");
  }
});

const genCovBtn = document.getElementById("generateCoverLetterBtn");
if (genCovBtn) {
  genCovBtn.addEventListener("click", async () => {
    if (!selectedJobId) return;
    const outBox = document.getElementById("coverLetterBox");
    const outTxt = document.getElementById("coverLetterOutput");
    
    outBox.style.display = "block";
    outTxt.textContent = t("toast.generating");
    genCovBtn.disabled = true;

    try {
      const payload = await api(`/api/jobs/${selectedJobId}/cover-letter`, { method: "POST" });
      outTxt.textContent = payload.cover_letter || t("toast.noResult");
    } catch (error) {
      outTxt.textContent = `${t("toast.genError")}: ${error.message}`;
    } finally {
      genCovBtn.disabled = false;
    }
  });
}

document.getElementById("refreshJobsBtn").addEventListener("click", loadJobs);
document.getElementById("onlyNew").addEventListener("change", loadJobs);
document.getElementById("onlyFavorites").addEventListener("change", loadJobs);
document.getElementById("searchText").addEventListener("change", loadJobs);
document.getElementById("minScore").addEventListener("change", loadJobs);
document.getElementById("maxAgeDays").addEventListener("change", loadJobs);
document.getElementById("statusFilter").addEventListener("change", loadJobs);
document.getElementById("profileSelect").addEventListener("change", async (event) => {
  await activateProfile(event.target.value);
});

document.getElementById("exportCsvBtn").addEventListener("click", async () => {
  const result = await api("/api/export/csv", { method: "POST" });
  showToast(t("toast.csvExported", { file: result.file }), "info");
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

async function bootstrap() {
  await initI18n();
  activateView("dashboard");
  await loadHealth();
  await loadKeysStatus();
  await loadProfiles();
  await Promise.all([loadJobs(), loadRecommendations()]);
  await loadAnalytics();
  await loadChatPrompts();
  await loadChatHistory();
}

bootstrap().catch((error) => {
  console.error(error);
  showToast(`${t("toast.initError")}: ${error.message}`, "info");
});


const closeDetailBtn = document.getElementById('closeDetailBtn');
if (closeDetailBtn) {
    closeDetailBtn.addEventListener('click', () => {
        const inlineDetail = document.getElementById('jobDetailInline');
        if (inlineDetail) inlineDetail.style.display = 'none';
    });
}


let statusChart = null;
let scoreChart = null;
let topCompaniesChart = null;

async function loadAnalytics() {
    try {
        const data = await api('/api/analytics');
        
        const statusCtx = document.getElementById('statusChart');
        if (statusCtx && data.jobs_by_status) {
            if (statusChart) statusChart.destroy();
            statusChart = new Chart(statusCtx, {
                type: 'doughnut',
                data: {
                    labels: Object.keys(data.jobs_by_status),
                    datasets: [{
                        data: Object.values(data.jobs_by_status),
                        backgroundColor: ['#198754', '#dc3545', '#ffc107', '#0d6efd', '#6c757d']
                    }]
                },
                options: { responsive: true }
            });
        }
        
        const scoreCtx = document.getElementById('scoreChart');
        if (scoreCtx && data.score_distribution) {
            if(scoreChart) scoreChart.destroy();
            scoreChart = new Chart(scoreCtx, {
                type: 'bar',
                data: {
                    labels: Object.keys(data.score_distribution),
                    datasets: [{
                        label: 'Match Score',
                        data: Object.values(data.score_distribution),
                        backgroundColor: '#0d6efd'
                    }]
                },
                options: { responsive: true, scales: { y: { beginAtZero: true } } }
            });
        }
        const companiesCtx = document.getElementById('topCompaniesChart');
        if (companiesCtx && Array.isArray(data.top_companies)) {
            if (topCompaniesChart) topCompaniesChart.destroy();
            topCompaniesChart = new Chart(companiesCtx, {
                type: 'bar',
                data: {
                    labels: data.top_companies.map(c => c.company),
                    datasets: [{
                        label: t('analytics.topCompanies') || 'Top Companies',
                        data: data.top_companies.map(c => c.count),
                        backgroundColor: '#635bff',
                    }],
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: { x: { beginAtZero: true } },
                },
            });
        }
    } catch (e) {
        console.error('Failed to load analytics', e);
    }
}


document.getElementById("viewTableBtn")?.addEventListener("click", e => {
    document.getElementById("tableView").classList.add("is-active");
    document.getElementById("kanbanView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewKanbanBtn").classList.remove("is-active");
});

document.getElementById("viewKanbanBtn")?.addEventListener("click", e => {
    document.getElementById("kanbanView").classList.add("is-active");
    document.getElementById("tableView").classList.remove("is-active");
  e.currentTarget.classList.add("is-active");
    document.getElementById("viewTableBtn").classList.remove("is-active");
  loadJobs();
});

// Tag Input UI Logic
function setupTagInput(containerId, inputId) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    const tags = [];

    if (!container || !input) return { getTags: () => [], addMultiple: () => false };

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
                tags.splice(index, 1);
                renderTags();
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
        }
    };
}

const getKeywords = setupTagInput('keywordsContainer', 'keywordsInput');
const getLocations = setupTagInput('locationsContainer', 'locationsInput');
window.getKeywords = getKeywords;
window.getLocations = getLocations;

async function loadRoleShortlist() {
  const roles = await _loadShortlistApi();
  if (roles.length && getKeywords && typeof getKeywords.addMultiple === "function") {
    getKeywords.addMultiple(roles);
  }
}
loadRoleShortlist();

// ─── Update Banner ──────────────────────────────────────────────
async function checkForUpdate() {
  const banner = document.getElementById('updateBanner');
  if (!banner) return;
  try {
    const info = await api('/api/version');
    if (!info.update_available || !info.latest) return;
    const dismissed = localStorage.getItem('updateDismissed');
    if (dismissed === info.latest) return;

    document.getElementById('updateBannerVersions').textContent =
      `${info.current} → ${info.latest}`;
    const link = document.getElementById('updateBannerLink');
    if (info.release_url) {
      link.href = info.release_url;
      link.classList.remove('hidden');
    } else {
      link.classList.add('hidden');
    }
    banner.classList.remove('hidden');

    document.getElementById('updateBannerClose').onclick = () => {
      localStorage.setItem('updateDismissed', info.latest);
      banner.classList.add('hidden');
    };
    document.getElementById('updateBannerRun').onclick = () => runUpdate(info);
  } catch (err) {
    console.warn('Update check failed:', err);
  }
}

async function runUpdate(info) {
  const modal = document.getElementById('updateModal');
  const log = document.getElementById('updateModalLog');
  const closeBtn = document.getElementById('updateModalClose');
  modal.classList.remove('hidden');
  closeBtn.onclick = () => modal.classList.add('hidden');

  if (info && info.frozen) {
    await runBundleUpdate(log);
  } else {
    await runDevUpdate(log);
  }
}

async function runDevUpdate(log) {
  log.textContent = 'Running update (git pull + pip install)... please wait.\n';
  try {
    const result = await api('/api/update', { method: 'POST' });
    log.textContent = `${result.message}\n\n`;
    for (const step of result.steps || []) {
      log.textContent += `=== ${step.step} (exit ${step.code}) ===\n${step.output}\n\n`;
    }
    if (result.ok) {
      log.textContent += '\n→ Restart the app to load the new version.';
    }
  } catch (err) {
    log.textContent += `\nFAILED: ${err.message}`;
  }
}

async function runBundleUpdate(log) {
  log.textContent = 'Downloading update...\nDo not close this window — the app will restart automatically.\n';
  try {
    const r = await fetch('/api/update/start', { method: 'POST' });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      log.textContent += `\nFAILED to start update: ${err.detail || r.status}`;
      return;
    }
    const data = await r.json();
    log.textContent += `\nUpdater spawned: ${data.from_version} → ${data.next_version}\nWaiting for app to restart...\n`;
  } catch (err) {
    log.textContent += `\nFAILED: ${err.message}`;
    return;
  }

  // Poll /api/health until the new process is up. Expect a window
  // (~5–60 s) where the request fails because the app is being replaced.
  const deadline = Date.now() + 180_000;
  let outageObserved = false;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    try {
      const h = await fetch('/api/health', { cache: 'no-store' });
      if (!h.ok) throw new Error(String(h.status));
      const json = await h.json();
      const ver = (json.version_info && json.version_info.current) || (json.version || '');
      if (outageObserved) {
        log.textContent += `\nNew app reachable (v${ver}). Reloading...`;
        setTimeout(() => window.location.reload(), 1500);
        return;
      }
      // No outage yet — keep waiting; updater hasn't taken JobFinder.exe down.
    } catch {
      outageObserved = true;
      log.textContent += '.';
    }
  }
  log.textContent += '\nTimed out waiting for the new version. Try refreshing this page in a minute.';
}

// ─── Chat empty state + first-time tutorial ─────────────────────
const CHAT_SUGGESTION_KEYS = [
  'chat.suggestions.roles',
  'chat.suggestions.pythonJobs',
  'chat.suggestions.searchTerms',
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

const _origAppendChat = typeof appendChat === 'function' ? appendChat : null;
if (_origAppendChat) {
  window.appendChat = function (role, content) {
    clearChatEmptyState();
    return _origAppendChat(role, content);
  };
}

async function showFirstTimeTutorial() {
  if (localStorage.getItem('tutorialSeen')) return;

  // Only show if user truly has nothing yet.
  let hasCv = false;
  try {
    const health = await api('/api/health');
    hasCv = Boolean(health && health.profile && health.profile.source_name);
  } catch { /* ignore */ }
  if (hasCv) {
    localStorage.setItem('tutorialSeen', '1');
    return;
  }

  const overlay = document.createElement('div');
  overlay.className = 'tutorial-overlay';
  overlay.innerHTML = `
    <div class="tutorial-card">
      <h3>${t('onboarding.title')}</h3>
      <p>${t('onboarding.intro')}</p>
      <ol class="onboarding-steps">
        <li data-step="cv"><span class="step-dot"></span>${t('onboarding.stepCv')}</li>
        <li data-step="scan"><span class="step-dot"></span>${t('onboarding.stepScan')}</li>
        <li data-step="chat"><span class="step-dot"></span>${t('onboarding.stepChat')}</li>
      </ol>
      <div class="tutorial-actions">
        <button type="button" class="secondary" id="tutorialGoto">${t('onboarding.gotoSettings')}</button>
        <button type="button" class="ghost-btn" id="tutorialDismiss">${t('onboarding.dismiss')}</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.querySelector('#tutorialDismiss').addEventListener('click', () => {
    localStorage.setItem('tutorialSeen', '1');
    overlay.remove();
  });
  overlay.querySelector('#tutorialGoto').addEventListener('click', () => {
    localStorage.setItem('tutorialSeen', '1');
    overlay.remove();
    activateView('settings');
    const cvSection = document.getElementById('cvFile');
    if (cvSection && cvSection.scrollIntoView) {
      cvSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
}

window.addEventListener('load', () => {
  checkForUpdate();
  renderChatEmptyState();
  // Defer tutorial to let dashboard render first.
  setTimeout(showFirstTimeTutorial, 800);
});
