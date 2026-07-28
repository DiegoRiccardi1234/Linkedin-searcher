// Provider/settings UI: AI provider cards, model selectors, key save + set
// primary, key-status normalization, and the chat provider/model override
// (toast + selector). Core refresh callbacks are injected via setProviderDeps
// to avoid a circular import with app.js (loadHealth / loadKeysStatus run
// after a key is saved).
import { api, escapeHtml, setText, showToast, truncate } from "./helpers.js";
import { t, applyTranslations } from "./i18n.js";

let _deps = {
  loadHealth: async () => {},
  loadKeysStatus: async () => {},
  refreshOnboardingPlaceholder: async () => {},
};

export function setProviderDeps(d) {
  _deps = { ..._deps, ...d };
}

// Track record per provider, read from usage_log — free, no inference, and it
// survives a restart. Without it a provider that fails every scoring call looks
// exactly like one that works: the only clue was a scan full of unscored jobs.
export async function loadProviderHealth() {
  const box = document.getElementById("providerHealth");
  if (!box) return;
  let providers = [];
  try {
    ({ providers = [] } = await api("/api/providers/health"));
  } catch {
    box.innerHTML = "";
    return; // a diagnostics panel must never be the thing that breaks the page
  }
  if (!providers.length) {
    box.innerHTML = "";
    return;
  }
  const rows = providers
    .map((p) => {
      const rate = Math.round((p.success_rate || 0) * 100);
      const icon = rate >= 80 ? "✅" : rate >= 40 ? "⚠️" : "❌";
      const worst = (p.models || []).find((m) => m.unfit);
      const note = worst ? ` · ${escapeHtml(truncate(worst.model, 28))} ${t("settings.providers.healthUnfit")}` : "";
      return (
        `<div class="provider-health-row"><span>${icon} ${escapeHtml(p.provider)}</span>` +
        `<span class="micro">${rate}% · ${p.calls} ${t("settings.providers.scoreboardCalls")}${note}</span></div>`
      );
    })
    .join("");
  box.innerHTML =
    `<div class="micro provider-health-title">${t("settings.providers.healthTitle")}</div>${rows}`;
}

const PROVIDER_KEY_IDS = ["cerebrasKey", "groqKey", "openaiKey", "anthropicKey", "googleKey", "openrouterKey", "deepseekKey", "xaiKey", "glmKey", "mistralKey"];

// `free`/`signup`/`hint` exist because the card said nothing about what a
// provider costs or where to get a key: a new user saw ten identical boxes and
// no reason to pick any of them. Limits verified 2026-07-27; they change, so
// the hint says what you get, not a promise.
const PROVIDER_CATALOG = [
  {
    name: "google",
    label: "Google AI Studio",
    icon: "language",
    placeholder: "AI...",
    free: true,
    signup: "https://aistudio.google.com/apikey",
    hint: "1500 req/day · Gemini Flash · no card",
  },
  {
    name: "groq",
    label: "Groq",
    icon: "memory",
    placeholder: "gsk_...",
    free: true,
    signup: "https://console.groq.com/keys",
    hint: "30 req/min · very fast · no card",
  },
  {
    name: "cerebras",
    label: "Cerebras",
    icon: "bolt",
    placeholder: "sk-...",
    free: true,
    signup: "https://cloud.cerebras.ai",
    hint: "1M tokens/day · 8K context on free · no card",
  },
  {
    name: "openrouter",
    label: "OpenRouter",
    icon: "hub",
    placeholder: "sk-or-v1-...",
    free: true,
    signup: "https://openrouter.ai/keys",
    hint: "many :free models · ~200 req/day · one key, many models",
  },
  {
    name: "mistral",
    label: "Mistral",
    icon: "air",
    placeholder: "...",
    free: true,
    signup: "https://console.mistral.ai/api-keys",
    hint: "free Experiment tier · rate-limited · no card",
  },
  {
    name: "custom",
    label: "Local / custom endpoint",
    icon: "dns",
    placeholder: "optional",
    free: true,
    endpoint: true,
    hint: "Ollama, LM Studio, vLLM or any OpenAI-compatible gateway",
  },
  { name: "deepseek", label: "DeepSeek", icon: "psychology", placeholder: "sk-..." },
  { name: "openai", label: "OpenAI", icon: "neurology", placeholder: "sk-..." },
  { name: "anthropic", label: "Anthropic", icon: "auto_awesome", placeholder: "sk-ant-..." },
  { name: "xai", label: "xAI (Grok)", icon: "rocket_launch", placeholder: "xai-..." },
  { name: "glm", label: "Zhipu GLM", icon: "token", placeholder: "..." },
];

// Presets for the custom endpoint: the two local runners people actually use.
export const LOCAL_PRESETS = [
  { id: "ollama", label: "Ollama", url: "http://localhost:11434/v1" },
  { id: "lmstudio", label: "LM Studio", url: "http://localhost:1234/v1" },
];

const _providerCardModelCache = {};
const _providerCardFetchTimes = {};

let _chatOverrideToastShown = false;
let _lastChatOverrideProvider = null;
let _lastChatOverrideModel = null;

function _maybeOfferPersistChatOverride(providerVal, modelVal) {
  if (!providerVal) return;
  if (providerVal === _lastChatOverrideProvider && modelVal === _lastChatOverrideModel) return;
  _lastChatOverrideProvider = providerVal;
  _lastChatOverrideModel = modelVal;
  if (_chatOverrideToastShown) return;
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
      await _deps.loadKeysStatus();
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
  const loadingLabel = t("toast.modelsLoading") || "Loading models...";
  sel.innerHTML = `<option value="">⏳ ${loadingLabel}</option>`;
  sel.disabled = true;
  try {
    const data = _providerCardModelCache[providerName] || (await fetchProviderModels(providerName, false));
    const models = Array.isArray(data.models) ? data.models : [];
    const recommended = data.recommended || null;
    const autoText = recommended ? `${autoLabel} (→ ${recommended})` : autoLabel;
    sel.innerHTML = `<option value="">${autoText}</option>`;

    // Mirror Settings card ordering so users see the same list everywhere:
    // OpenRouter splits Free/Paid groups (alpha within each), other
    // providers sort alphabetically. Recommended (⭐) is hoisted to the top
    // of its group regardless.
    const appendOption = (m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m === recommended ? `⭐ ${m}` : m;
      sel.appendChild(opt);
    };
    const appendSeparator = (label) => {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = label;
      opt.disabled = true;
      sel.appendChild(opt);
    };

    if (providerName === "openrouter" && models.length > 30) {
      const { free, paid } = _splitFreePaid(models);
      const hoist = (arr) => {
        if (!recommended) return arr;
        const i = arr.indexOf(recommended);
        if (i <= 0) return arr;
        const copy = arr.slice();
        copy.splice(i, 1);
        copy.unshift(recommended);
        return copy;
      };
      const freeLabel = t("settings.providers.freeGroup") || "── Free ──";
      const paidLabel = t("settings.providers.paidGroup") || "── Paid ──";
      const freeSorted = hoist(free);
      const paidSorted = hoist(paid);
      if (freeSorted.length) {
        appendSeparator(freeLabel);
        for (const m of freeSorted) appendOption(m);
      }
      if (paidSorted.length) {
        appendSeparator(paidLabel);
        for (const m of paidSorted) appendOption(m);
      }
    } else {
      const sorted = _sortModelsAlpha(models);
      if (recommended) {
        const i = sorted.indexOf(recommended);
        if (i > 0) {
          sorted.splice(i, 1);
          sorted.unshift(recommended);
        }
      }
      for (const m of sorted) appendOption(m);
    }

    sel.disabled = models.length === 0;
  } catch (err) {
    const failLabel = t("toast.modelsFailed") || "Failed to load models";
    sel.innerHTML = `<option value="" disabled>${failLabel}</option>`;
    sel.disabled = true;
  }
}

// ── Per-context model overrides (Settings "AI models" card) ─────────────────
function _fillOverrideSelect(sel, providerName, models, recommended, selectedValue) {
  const autoBase = t("settings.models.auto") || "Auto (recommended)";
  sel.innerHTML = `<option value="">${recommended ? `${autoBase} (→ ${recommended})` : autoBase}</option>`;
  const add = (m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m === recommended ? `⭐ ${m}` : m;
    sel.appendChild(opt);
  };
  const sep = (label) => {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = label;
    opt.disabled = true;
    sel.appendChild(opt);
  };
  if (providerName === "openrouter" && models.length > 30) {
    const { free, paid } = _splitFreePaid(models);
    if (free.length) {
      sep(t("settings.providers.freeGroup") || "── Free ──");
      free.forEach(add);
    }
    if (paid.length) {
      sep(t("settings.providers.paidGroup") || "── Paid ──");
      paid.forEach(add);
    }
  } else {
    _sortModelsAlpha(models).forEach(add);
  }
  sel.value = selectedValue || "";
  sel.disabled = models.length === 0;
}

export async function populateModelOverrides(keys) {
  const rows = [
    { el: document.getElementById("scoringModelSelect"), val: keys?.scoring_model || "" },
    { el: document.getElementById("chatModelOverrideSelect"), val: keys?.chat_model || "" },
    { el: document.getElementById("cvModelSelect"), val: keys?.cv_model || "" },
  ].filter((r) => r.el);
  if (!rows.length) return;
  const primary = String(keys?.primary_provider || "").toLowerCase();
  let models = [];
  let recommended = null;
  if (primary) {
    try {
      const data = _providerCardModelCache[primary] || (await fetchProviderModels(primary, false));
      models = Array.isArray(data.models) ? data.models : [];
      recommended = data.recommended || null;
    } catch (_) {
      /* leave Auto-only */
    }
  }
  for (const { el, val } of rows) _fillOverrideSelect(el, primary, models, recommended, val);
}

const _OVERRIDE_FIELD = {
  scoringModelSelect: "scoring_model",
  chatModelOverrideSelect: "chat_model",
  cvModelSelect: "cv_model",
};

export async function onSaveModelOverride(selectId, value) {
  const field = _OVERRIDE_FIELD[selectId];
  if (!field) return;
  try {
    await api("/api/providers/keys", { method: "POST", body: JSON.stringify({ [field]: value }) });
    showToast(t("settings.models.saved") || "Saved", "info");
  } catch (err) {
    showToast(String(err?.message || err), "error");
  }
}

// Build the chat provider-override <select> from PROVIDER_CATALOG so adding a
// provider only needs one edit. Keeps the "Auto" option (value "") and its
// data-i18n attribute, and preserves the current selection.
function populateChatProviderSelector() {
  const sel = document.getElementById("chatModelSelector");
  if (!sel) return;
  const previous = sel.value;
  const autoOpt = sel.querySelector('option[value=""]');
  const autoHtml = autoOpt ? autoOpt.outerHTML : `<option value="">${t("coach.autoApi") || "Auto API"}</option>`;
  sel.innerHTML = autoHtml + PROVIDER_CATALOG
    .map((p) => `<option value="${p.name}" data-label="${escapeHtml(p.label)}">${escapeHtml(p.label)}</option>`)
    .join("");
  if (previous && sel.querySelector(`option[value="${previous}"]`)) sel.value = previous;
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
    const base = opt.dataset.label || (opt.value.charAt(0).toUpperCase() + opt.value.slice(1));
    opt.textContent = base + (available ? "" : " (no key)");
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
          ${p.free ? `<span class="provider-free-badge micro">${t("settings.providers.freeBadge")}</span>` : ""}
          <label class="provider-primary-radio" title="${t("settings.providers.setPrimary")}">
            <input type="radio" name="primaryProviderRadio" value="${p.name}" ${isPrimary ? "checked" : ""} ${configured ? "" : "disabled"} />
            <span class="micro" data-i18n="settings.providers.setPrimary">Set as primary</span>
          </label>
        </header>
        <div class="provider-card-body">
          ${p.hint ? `<p class="micro provider-hint">${escapeHtml(p.hint)}</p>` : ""}
          ${
            p.endpoint
              ? `<label class="field-label provider-endpoint-row">
            <span class="micro">${t("settings.providers.endpoint")}</span>
            <input type="text" class="provider-endpoint-input" placeholder="http://localhost:11434/v1" autocomplete="off" value="${escapeHtml(keys?.custom_base_url || "")}" />
            <span class="provider-presets">${LOCAL_PRESETS.map((preset) => `<button type="button" class="ghost-btn small provider-preset-btn" data-url="${preset.url}">${preset.label}</button>`).join("")}</span>
          </label>`
              : ""
          }
          <label class="field-label provider-key-row">
            <span class="micro">${p.endpoint ? t("settings.providers.apiKeyOptional") : t("settings.providers.apiKey")}</span>
            <div class="key-input-row">
              <input type="password" class="provider-key-input" placeholder="${p.placeholder}" autocomplete="off" />
              <button type="button" class="ghost-btn provider-toggle-visibility" title="${t("settings.providers.showHide")}">
                <span class="material-symbols-outlined">visibility</span>
              </button>
            </div>
          </label>
          ${p.signup && !configured ? `<a class="micro provider-signup" href="${p.signup}" target="_blank" rel="noopener">${t("settings.providers.getKey")} ↗</a>` : ""}
          <button type="button" class="secondary provider-save-btn" data-i18n="settings.providers.saveAndFetch">Save &amp; fetch models</button>
          <label class="field-label provider-model-row">
            <span class="micro" data-i18n="settings.providers.model">Model</span>
            <select class="provider-model-select" ${configured ? "" : "disabled"}>
              <option value="" data-i18n="settings.providers.modelAuto">Auto (provider default)</option>
            </select>
          </label>
          <div class="provider-status">
            <span class="provider-status-text micro"></span>
            ${configured ? `<button type="button" class="ghost-btn provider-probe-btn" title="${t("settings.providers.probe")}">
              <span class="material-symbols-outlined">speed</span>
            </button>` : ""}
            ${configured ? `<button type="button" class="ghost-btn provider-probe-confirm-btn" title="${t("settings.providers.probeConfirm")}">
              <span class="material-symbols-outlined">verified</span>
            </button>` : ""}
            ${configured ? `<button type="button" class="ghost-btn provider-remove-btn danger" data-provider-remove title="${t("settings.providers.removeKey")}">
              <span class="material-symbols-outlined">delete</span>
            </button>` : ""}
            <button type="button" class="ghost-btn provider-refresh-btn" title="${t("settings.providers.refresh")}" ${configured ? "" : "disabled"}>
              <span class="material-symbols-outlined">refresh</span>
            </button>
          </div>
          <div class="provider-probe-results micro" hidden></div>
        </div>
      </article>
    `;
  }).join("");

  // Cards are injected after boot-time applyTranslations(), so translate the
  // freshly-built markup or its data-i18n nodes stay on their English fallback.
  applyTranslations(container);

  for (const p of PROVIDER_CATALOG) {
    if (configuredKey(p.name)) {
      void fetchAndRenderProviderModels(p.name, false);
    } else {
      _setProviderStatusText(p.name, t("settings.providers.addKey"));
    }
  }
  void populateModelOverrides(keys);
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

function _sortModelsAlpha(models) {
  return [...models].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
}

function _splitFreePaid(models) {
  const free = [];
  const paid = [];
  for (const m of models) {
    if (m.endsWith(":free")) free.push(m);
    else paid.push(m);
  }
  return { free: _sortModelsAlpha(free), paid: _sortModelsAlpha(paid) };
}

function _ensureOpenRouterFilter(card, models, recommended, renderOptions) {
  const row = card.querySelector(".provider-model-row");
  if (!row) {
    renderOptions(models);
    return;
  }
  let filterBox = row.querySelector(".or-filter");
  if (!filterBox) {
    filterBox = document.createElement("div");
    filterBox.className = "or-filter";
    filterBox.innerHTML = `
      <input type="search" class="or-filter-search" placeholder="${t("settings.providers.searchPlaceholder")}" />
    `;
    const select = row.querySelector(".provider-model-select");
    row.insertBefore(filterBox, select);
  }
  const searchInput = filterBox.querySelector(".or-filter-search");
  const apply = () => {
    const q = (searchInput.value || "").toLowerCase().trim();
    const matches = (m) => !q || m.toLowerCase().includes(q);
    const { free, paid } = _splitFreePaid(models.filter(matches));
    // Render: Free header (disabled separator) + free alpha,
    // Paid header (disabled separator) + paid alpha. Recommended ⭐ stays
    // inline within its own group so users see why it was picked.
    const freeLabel = t("settings.providers.freeGroup") || "── Free ──";
    const paidLabel = t("settings.providers.paidGroup") || "── Paid ──";
    const ordered = [];
    if (free.length) ordered.push({ separator: true, label: freeLabel }, ...free);
    if (paid.length) ordered.push({ separator: true, label: paidLabel }, ...paid);
    renderOptions(ordered);
  };
  searchInput.oninput = apply;
  apply();
}

function _hoistRecommended(sorted, recommended) {
  if (!recommended) return sorted;
  const i = sorted.indexOf(recommended);
  if (i <= 0) return sorted;
  const copy = sorted.slice();
  copy.splice(i, 1);
  copy.unshift(recommended);
  return copy;
}

// Generic search box for any provider with a long (non-OpenRouter) model list.
// Reuses the same .or-filter markup/CSS but keeps a flat alphabetical list.
function _ensureModelFilter(card, models, recommended, renderOptions) {
  const row = card.querySelector(".provider-model-row");
  if (!row) {
    renderOptions(_hoistRecommended(_sortModelsAlpha(models), recommended));
    return;
  }
  let filterBox = row.querySelector(".or-filter");
  if (!filterBox) {
    filterBox = document.createElement("div");
    filterBox.className = "or-filter";
    filterBox.innerHTML = `
      <input type="search" class="or-filter-search" placeholder="${t("settings.providers.searchPlaceholder")}" />
    `;
    const select = row.querySelector(".provider-model-select");
    row.insertBefore(filterBox, select);
  }
  const searchInput = filterBox.querySelector(".or-filter-search");
  const apply = () => {
    const q = (searchInput.value || "").toLowerCase().trim();
    const filtered = _sortModelsAlpha(models.filter((m) => !q || m.toLowerCase().includes(q)));
    renderOptions(_hoistRecommended(filtered, recommended));
  };
  searchInput.oninput = apply;
  apply();
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

function _fmtNum(val, suffix) {
  return typeof val === "number" ? `${Math.round(val)}${suffix}` : "—";
}

function _renderProbeResults(results, best, mode) {
  if (!results.length) return t("settings.providers.probeEmpty");
  const rows = results
    .slice(0, 14)
    .map((r) => {
      const star = best && r.model === best ? " ⭐" : "";
      let icon;
      let cols;
      if (mode === "stats") {
        // Free health report: live uptime / latency / throughput (no inference).
        const up = typeof r.up5m === "number" ? r.up5m : null;
        const healthy = r.status === 0;
        icon = !healthy ? "❌" : up !== null && up < 90 ? "⚠️" : "✅";
        const upTxt = up !== null ? `${Math.round(up)}%` : "—";
        cols =
          `<span class="micro">${upTxt}</span>` +
          `<span class="micro">${_fmtNum(r.lat_ms, " ms")}</span>` +
          `<span class="micro">${_fmtNum(r.tput, " t/s")}</span>`;
      } else {
        // Confirm probe: the model answered the REAL scoring prompt — ✅ means
        // the reply carried the fields the app needs, ⚠️ means it replied but
        // the answer was unusable (truncated, missing keys).
        icon = r.schema_ok ? "✅" : r.ok ? "⚠️" : "❌";
        const detail = r.ok ? `${r.latency_ms} ms` : r.error || "error";
        cols = `<span class="micro">${escapeHtml(String(detail))}</span>`;
      }
      return `<div class="probe-row"><span class="probe-model">${icon} ${escapeHtml(r.model)}${star}</span>${cols}</div>`;
    })
    .join("");
  return `<div class="probe-list">${rows}</div>`;
}

// Track record of the models that actually ran, from the usage log: free, and
// unlike the live health report it says whether the answers were USABLE.
function _renderScoreboard(records) {
  if (!Array.isArray(records) || !records.length) return "";
  const rows = records
    .slice(0, 8)
    .map((r) => {
      const icon = r.unfit ? "❌" : r.success_rate >= 0.8 ? "✅" : "⚠️";
      const pct = `${Math.round((r.success_rate || 0) * 100)}%`;
      const trunc = r.truncated ? ` · ${r.truncated} ✂` : "";
      return (
        `<div class="probe-row"><span class="probe-model">${icon} ${escapeHtml(r.model)}</span>` +
        `<span class="micro">${pct} · ${r.calls} ${t("settings.providers.scoreboardCalls")}${trunc}</span>` +
        `<span class="micro">${r.median_ms ? `${r.median_ms} ms` : "—"}</span></div>`
      );
    })
    .join("");
  return (
    `<div class="probe-scoreboard"><div class="micro probe-scoreboard-title">` +
    `${t("settings.providers.scoreboardTitle")}</div>` +
    `<div class="probe-list">${rows}</div></div>`
  );
}

// Report a provider's models. Default = a FREE health report from OpenRouter's
// live stats (uptime/latency, no inference, no quota). ``confirm`` micro-probes
// the top few healthiest models with a tiny JSON call to confirm they return
// valid JSON for our schema, seeding the server-side penalty map.
export async function probeProviderModels(name, { confirm = false } = {}) {
  const card = _providerCardEl(name);
  const out = card?.querySelector(".provider-probe-results");
  _setProviderCardState(name, "fetching");
  _setProviderStatusText(name, t("settings.providers.probing"));
  if (out) {
    out.hidden = false;
    out.textContent = t("settings.providers.probing");
  }
  try {
    const url = `/api/providers/${encodeURIComponent(name)}/probe${confirm ? "?confirm=true" : ""}`;
    const res = await fetch(url, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      let detail = "probe_failed";
      try {
        detail = (await res.json()).detail || detail;
      } catch (_) {
        /* noop */
      }
      throw new Error(detail);
    }
    const data = await res.json();
    const results = Array.isArray(data.results) ? data.results : [];
    const mode = data.mode || (confirm ? "probe" : "stats");
    if (out) {
      out.innerHTML =
        _renderProbeResults(results, data.best, mode) + _renderScoreboard(data.scoreboard);
    }
    const doneKey = confirm ? "settings.providers.probeConfirmDone" : "settings.providers.probeDone";
    _setProviderStatusText(name, t(doneKey), "ok");
  } catch (err) {
    if (out) {
      out.hidden = false;
      out.textContent = `${t("settings.providers.probeError")}: ${err.message}`;
    }
    _setProviderStatusText(name, t("settings.providers.probeError"), "warn");
  } finally {
    _setProviderCardState(name, "configured");
  }
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
      // Show which concrete model "Auto" resolves to (recommended), so the
      // default isn't a mystery, e.g. "Auto (→ llama-3.3-70b)".
      const autoText = recommended
        ? `${autoLabel.replace(/\s*\(.*\)\s*$/, "")} (→ ${recommended})`
        : autoLabel;
      const recHint = t("settings.providers.recommendedHint");
      const renderOptions = (filtered) => {
        const opts = [`<option value="">${escapeHtml(autoText)}</option>`];
        for (const entry of filtered) {
          if (typeof entry === "object" && entry && entry.separator) {
            const label = String(entry.label || "──────");
            opts.push(`<option value="" disabled>${label}</option>`);
            continue;
          }
          const m = String(entry);
          const isRec = m === recommended;
          const star = isRec ? "⭐ " : "";
          const titleAttr = isRec ? ` title="${escapeHtml(recHint)}"` : "";
          opts.push(`<option value="${m}"${titleAttr}>${star}${m}</option>`);
        }
        select.innerHTML = opts.join("");
      };
      if (name === "openrouter" && models.length > 30) {
        // OpenRouter: search + alphabetical free-then-paid grouping.
        _ensureOpenRouterFilter(card, models, recommended, renderOptions);
      } else if (models.length > 8) {
        // Any long list (not just OpenRouter): add a plain search filter.
        _ensureModelFilter(card, models, recommended, renderOptions);
      } else {
        // Short list: alphabetical sort, recommended stays at top.
        renderOptions(_hoistRecommended(_sortModelsAlpha(models), recommended));
      }
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
    const detail = String((err && err.message) || "");
    if (detail === "key_missing") {
      // No key on this provider — neutral "add a key" state, never a red error.
      _setProviderCardState(name, "empty");
      _setProviderStatusText(name, t("settings.providers.addKey"));
    } else if (detail === "key_invalid") {
      // Key present but rejected (401) — warn the user, don't hard-error.
      _setProviderCardState(name, "warn");
      _setProviderStatusText(name, t("settings.providers.keyInvalid"), "warn");
    } else {
      _setProviderCardState(name, "error");
      _setProviderStatusText(name, t("settings.providers.fetchFailed"), "error");
    }
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
    // The custom provider is configured by its endpoint: a local model server
    // has no key, so the URL is the thing that must be saved.
    if (name === "custom") {
      payload.custom_base_url = (card.querySelector(".provider-endpoint-input")?.value || "").trim();
    }
    await api("/api/providers/keys", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const input = card.querySelector(".provider-key-input");
    if (input) input.value = "";
    await _deps.loadKeysStatus();
    await _deps.loadHealth();
    await _deps.refreshOnboardingPlaceholder();
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

async function onRemoveProviderKey(name) {
  const card = _providerCardEl(name);
  if (!card) return;
  _setProviderCardState(name, "fetching");
  _setProviderStatusText(name, t("settings.providers.saving"));
  try {
    // Empty string clears the stored key (backend contract).
    const payload = {};
    payload[`${name}_api_key`] = "";
    await api("/api/providers/keys", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    // Mirror onSaveProviderKey's reload so cards/health re-render to empty state.
    await _deps.loadKeysStatus();
    await _deps.loadHealth();
    await _deps.refreshOnboardingPlaceholder();
    showToast(t("toast.providerSaved"), "info");
  } catch (err) {
    _setProviderCardState(name, "error");
    _setProviderStatusText(name, `${t("settings.providers.saveFailed")}: ${err.message}`, "error");
    showToast(`${t("toast.keySaveError")}: ${err.message}`, "error");
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
    await _deps.loadKeysStatus();
    await _deps.loadHealth();
    await _deps.refreshOnboardingPlaceholder();
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
      || keys.openrouter_configured
      || keys.deepseek_configured
      || keys.xai_configured
      || keys.glm_configured
      || keys.mistral_configured,
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
    deepseek_configured: !!keys.deepseek_configured,
    xai_configured: !!keys.xai_configured,
    glm_configured: !!keys.glm_configured,
    mistral_configured: !!keys.mistral_configured,
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

export {
  renderProviderCards,
  normalizeKeyStatus,
  setPrimaryProviderValue,
  updateProvidersMetadata,
  populateModelOptions,
  onSaveProviderKey,
  onRemoveProviderKey,
  onSetPrimaryProvider,
  fetchAndRenderProviderModels,
  populateChatProviderSelector,
  _populateChatModelSelector as populateChatModelSelector,
  _maybeOfferPersistChatOverride as maybeOfferPersistChatOverride,
};
