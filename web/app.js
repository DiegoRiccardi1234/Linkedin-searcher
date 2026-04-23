
// ─── i18n System ──────────────────────────────────────────────
let _i18nStrings = {};
let _i18nFallback = {};
let _currentLang = localStorage.getItem('language') || 'en';
const _i18nMissingReported = new Set();

function _reportMissingKey(key) {
  if (_i18nMissingReported.has(key)) return;
  _i18nMissingReported.add(key);
  console.warn(`[i18n] missing translation for "${key}" (lang=${_currentLang})`);
}

function t(key, params = {}) {
  const keys = key.split('.');
  let val = keys.reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), _i18nStrings);
  let usedFallback = false;
  if (val === undefined) {
    val = keys.reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), _i18nFallback);
    usedFallback = val !== undefined;
  }
  if (val === undefined) {
    _reportMissingKey(key);
    return key;
  }
  if (usedFallback && _currentLang !== 'en') {
    _reportMissingKey(key);
  }
  return String(val).replace(/\{(\w+)\}/g, (_, p) => (params[p] !== undefined ? params[p] : `{${p}}`));
}

async function loadLanguage(lang) {
  try {
    const res = await fetch(`/web/i18n/${lang}.json`);
    if (!res.ok) throw new Error(res.status);
    _i18nStrings = await res.json();
  } catch {
    _i18nStrings = _i18nFallback;
  }
  _currentLang = lang;
  localStorage.setItem('language', lang);
  document.documentElement.setAttribute('lang', lang);
  applyTranslations();

  // Notify backend of language change
  fetch('/api/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: 'ui_language', value: lang }),
  }).catch(() => {});
}

function applyTranslations() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    const val = t(key);
    if (val !== key) el.textContent = val;
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    const val = t(key);
    if (val !== key) el.placeholder = val;
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    const val = t(key);
    if (val !== key) el.title = val;
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    const key = el.getAttribute('data-i18n-html');
    const val = t(key);
    if (val !== key) el.innerHTML = val;
  });
}

async function initI18n() {
  try {
    const res = await fetch('/web/i18n/en.json');
    _i18nFallback = await res.json();
  } catch { _i18nFallback = {}; }
  await loadLanguage(_currentLang);
}

// Language selector
const langSelect = document.getElementById('langSelect');
if (langSelect) {
  langSelect.value = _currentLang;
  langSelect.addEventListener('change', () => {
    loadLanguage(langSelect.value);
  });
}

// Theme Toggle
const themeToggle = document.getElementById('themeToggle');
if (themeToggle) {
    themeToggle.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
    });
}
// Apply saved theme
const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `API Error ${response.status}`);
  }
  return response.json();
}

let selectedJobId = null;
const PROVIDER_KEY_IDS = ["cerebrasKey", "groqKey", "openaiKey", "anthropicKey", "googleKey", "openrouterKey"];

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

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function roleLabel(role) {
  if (role === "assistant") return "Coach";
  if (role === "user") return "You";
  return "System";
}

function appendChat(role, content) {
  const box = document.getElementById("chatBox");
  if (!box) return;

  const item = document.createElement("div");
  item.className = `chat-item ${role}`;
  const safeContent = escapeHtml(content).replaceAll("\n", "<br>");
  item.innerHTML = `
    <div class="role">${roleLabel(role)}</div>
    <div class="bubble">${safeContent}</div>
  `;
  box.appendChild(item);
  box.scrollTop = box.scrollHeight;
}

async function loadHealth() {
  const health = await api("/api/health");
  setText("providerBadge", `Provider: ${health.provider.active_provider}`);
  setText("modelBadge", `Model: ${health.provider.active_model}`);

  const prefs = health.preferences || {};
  const linkedinInput = document.getElementById("linkedinUrl");
  if (linkedinInput && prefs.linkedin_url) {
    linkedinInput.value = prefs.linkedin_url;
  }

  const keys = health.keys || {};
  const configured = hasAnyProviderConfigured(keys);
  setKeysSectionMode(configured);
  const status = normalizeKeyStatus(keys, health.provider || {});
  setPrimaryProviderValue(status.primary_provider);
  setText("keysStatus", JSON.stringify(status, null, 2));
}

function setKeysSectionMode(configured, forceExpanded = false) {
  const form = document.getElementById("keysForm");
  const collapsedRow = document.getElementById("keysCollapsedRow");
  const status = document.getElementById("keysStatus");

  if (configured && !forceExpanded) {
    form.classList.add("hidden");
    collapsedRow.classList.remove("hidden");
    status.classList.add("hidden");
  } else {
    form.classList.remove("hidden");
    collapsedRow.classList.add("hidden");
    status.classList.remove("hidden");
  }
}

async function loadKeysStatus() {
  const payload = await api("/api/providers/keys/status");
  const keys = payload.keys || {};
  const provider = payload.provider || {};
  const configured = hasAnyProviderConfigured(keys);
  setKeysSectionMode(configured);
  const status = normalizeKeyStatus(keys, provider);
  setPrimaryProviderValue(status.primary_provider);
  setText("keysStatus", JSON.stringify(status, null, 2));
}

async function saveKeys() {
  const cerebras = document.getElementById("cerebrasKey").value.trim();
  const groq = document.getElementById("groqKey").value.trim();
  const openai = document.getElementById("openaiKey").value.trim();
  const anthropic = document.getElementById("anthropicKey").value.trim();
  const google = document.getElementById("googleKey").value.trim();
  const openrouter = document.getElementById("openrouterKey").value.trim();
  const primaryProvider = document.getElementById("primaryProvider").value.trim();

  const payload = {};
  if (cerebras) payload.cerebras_api_key = cerebras;
  if (groq) payload.groq_api_key = groq;
  if (openai) payload.openai_api_key = openai;
  if (anthropic) payload.anthropic_api_key = anthropic;
  if (google) payload.google_api_key = google;
  if (openrouter) payload.openrouter_api_key = openrouter;
  payload.primary_provider = primaryProvider;

  if (
    !payload.cerebras_api_key
    && !payload.groq_api_key
    && !payload.openai_api_key
    && !payload.anthropic_api_key
    && !payload.google_api_key
    && !payload.openrouter_api_key
    && !payload.primary_provider
  ) {
    showToast(t("toast.enterKeyOrProvider"), "info");
    return;
  }

  await api("/api/providers/keys", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  for (const inputId of PROVIDER_KEY_IDS) {
    const input = document.getElementById(inputId);
    if (input) input.value = "";
  }
  await loadHealth();
  await loadKeysStatus();
  setKeysSectionMode(true);
  showToast(t("toast.providerSaved"), "info");
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

function truncate(value, max = 120) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max)}...` : text;
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
        <div class="mt-16">
          <h4>${t("offcanvas.listingMeta")}</h4>
          <p class="text-sm text-dim">${t("offcanvas.search")}: ${escapeHtml(job.ricerca_usata)} | ${t("jobs.source")}: ${escapeHtml(job.fonte || "App")} | ${t("offcanvas.found")}: ${escapeHtml(job.first_seen_at || "")} | ${t("offcanvas.companyRep")}: ${escapeHtml((analysis ? analysis.reputazione_azienda : null) || "N/A")}</p>
        </div>
      </div>
    `;
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
    const payload = await api("/api/chat/prompts");
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

  showToast(text, "info");

  try {
    const providerSelector = document.getElementById("chatModelSelector");
    const providerVal = providerSelector && providerSelector.value ? providerSelector.value : null;

    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: "default", provider: providerVal }),
    });
    appendChat("assistant", result.answer || "No response available.");

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
          <button data-action="reopened" data-id="${job.id}" class="secondary">${t("jobs.reopen")}</button>
          <button data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary">${job.is_favorite ? t("jobs.unfavorite") : t("jobs.favorite")}</button>
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

  const response = await fetch("/api/upload-cv", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    setText("cvSummary", `${t("toast.uploadError")}: ${await response.text()}`);
    return;
  }

  const payload = await response.json();
  setText("cvSummary", JSON.stringify(payload, null, 2));
  await loadProfiles();
  await loadRecommendations();
});

document.getElementById("keysForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveKeys();
  } catch (error) {
    setText("keysStatus", `${t("toast.keySaveError")}: ${error.message}`);
  }
});

document.getElementById("showKeysFormBtn").addEventListener("click", () => {
  setKeysSectionMode(false, true);
});

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
  overlay.style.display = "flex";
  progressFill.style.width = "5%";
  progressText.textContent = t("scan.connecting");

  let analysisCount = 0;
  let totalFound = 0;

  const evtSource = new EventSource(`/api/scan/stream?${params.toString()}`);
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.status === "started") {
        progressText.textContent = `${t("scan.searching")}: ${(data.terms || []).join(", ")}`;
        progressFill.style.width = "10%";
      } else if (data.status === "scraped") {
        totalFound += data.found || 0;
        progressText.textContent = t("scan.foundJobs", { count: totalFound });
        progressFill.style.width = "30%";
      } else if (data.status === "analyzed") {
        analysisCount++;
        const pct = Math.min(30 + (analysisCount * 3), 90);
        progressFill.style.width = pct + "%";
        const j = data.job || {};
        progressText.textContent = t("scan.analyzed", { title: j.titolo || "?", company: j.azienda || "?", score: j.score || 0 });
      } else if (data.status === "complete") {
        progressFill.style.width = "100%";
        progressText.textContent = t("scan.complete", { newJobs: data.totale_nuovi || 0, analyzed: data.totale_analizzati || 0 });
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; }, 2000);
        Promise.all([loadJobs(), loadRecommendations()]);
      } else if (data.error) {
        progressText.textContent = `${t("scan.error")}: ${data.error}`;
        evtSource.close();
        setTimeout(() => { overlay.style.display = "none"; }, 3000);
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
    Promise.all([loadJobs(), loadRecommendations()]);
  };
});

document.getElementById("manualForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    titolo: document.getElementById("manualTitolo").value.trim(),
    azienda: document.getElementById("manualAzienda").value.trim(),
    sede: document.getElementById("manualSede").value.trim(),
    link: document.getElementById("manualLink").value.trim(),
    descrizione: document.getElementById("manualDescrizione").value.trim(),
  };

  await api("/api/jobs/manual", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  await Promise.all([loadJobs(), loadRecommendations()]);
  showToast(t("toast.manualAdded"), "info");
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
});

document.querySelectorAll("[data-view]").forEach((btn) => {
  btn.addEventListener("click", () => {
    activateView(btn.dataset.view || "dashboard");
  });
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


function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
    
    container.appendChild(toast);
}


const closeDetailBtn = document.getElementById('closeDetailBtn');
if (closeDetailBtn) {
    closeDetailBtn.addEventListener('click', () => {
        const inlineDetail = document.getElementById('jobDetailInline');
        if (inlineDetail) inlineDetail.style.display = 'none';
    });
}


let statusChart = null;
let scoreChart = null;

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
    document.getElementById('updateBannerRun').onclick = runUpdate;
  } catch (err) {
    console.warn('Update check failed:', err);
  }
}

async function runUpdate() {
  const modal = document.getElementById('updateModal');
  const log = document.getElementById('updateModalLog');
  const closeBtn = document.getElementById('updateModalClose');
  log.textContent = 'Running update... please wait.\n';
  modal.classList.remove('hidden');
  closeBtn.onclick = () => modal.classList.add('hidden');

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

// ─── Chat empty state + first-time tutorial ─────────────────────
const CHAT_SUGGESTIONS = [
  'Which roles best fit my CV?',
  'Find Python jobs in Milan',
  'Suggest search terms for a Junior QA role',
  'Recommend the top 5 jobs to apply for',
];

function renderChatEmptyState() {
  const box = document.getElementById('chatBox');
  if (!box) return;
  if (box.querySelector('.chat-item') || box.querySelector('.chat-empty')) return;
  const empty = document.createElement('div');
  empty.className = 'chat-empty';
  empty.innerHTML = `
    <div class="lead">Try asking the coach to find roles for you:</div>
  `;
  for (const text of CHAT_SUGGESTIONS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = text;
    btn.addEventListener('click', () => sendChatMessage(text));
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

function showFirstTimeTutorial() {
  if (localStorage.getItem('tutorialSeen')) return;
  const overlay = document.createElement('div');
  overlay.className = 'tutorial-overlay';
  overlay.innerHTML = `
    <div class="tutorial-card">
      <h3>Meet your AI Career Coach</h3>
      <p>Use the chat on the right to:</p>
      <ul>
        <li>Discover which roles match your CV</li>
        <li>Get search keywords for a specific city</li>
        <li>Pick the best jobs to apply for first</li>
      </ul>
      <div class="tutorial-actions">
        <button type="button" class="ghost-btn" id="tutorialDismiss">Got it</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  overlay.querySelector('#tutorialDismiss').addEventListener('click', () => {
    localStorage.setItem('tutorialSeen', '1');
    overlay.remove();
  });
}

window.addEventListener('load', () => {
  checkForUpdate();
  renderChatEmptyState();
  // Defer tutorial to let dashboard render first.
  setTimeout(showFirstTimeTutorial, 800);
});
