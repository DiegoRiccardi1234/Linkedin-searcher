async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Errore API ${response.status}`);
  }
  return response.json();
}

let selectedJobId = null;
const PROVIDER_KEY_IDS = ["cerebrasKey", "groqKey", "openaiKey", "anthropicKey", "googleKey"];

function hasAnyProviderConfigured(keys) {
  return Boolean(
    keys.cerebras_configured
      || keys.groq_configured
      || keys.openai_configured
      || keys.anthropic_configured
      || keys.google_configured,
  );
}

function normalizeKeyStatus(keys = {}, provider = {}) {
  return {
    cerebras_configured: !!keys.cerebras_configured,
    groq_configured: !!keys.groq_configured,
    openai_configured: !!keys.openai_configured,
    anthropic_configured: !!keys.anthropic_configured,
    google_configured: !!keys.google_configured,
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
  if (role === "user") return "Tu";
  return "Sistema";
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
  setText("modelBadge", `Modello: ${health.provider.active_model}`);

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
  const primaryProvider = document.getElementById("primaryProvider").value.trim();

  const payload = {};
  if (cerebras) payload.cerebras_api_key = cerebras;
  if (groq) payload.groq_api_key = groq;
  if (openai) payload.openai_api_key = openai;
  if (anthropic) payload.anthropic_api_key = anthropic;
  if (google) payload.google_api_key = google;
  payload.primary_provider = primaryProvider;

  if (
    !payload.cerebras_api_key
    && !payload.groq_api_key
    && !payload.openai_api_key
    && !payload.anthropic_api_key
    && !payload.google_api_key
    && !payload.primary_provider
  ) {
    appendChat("system", "Inserisci almeno una key o scegli un provider primario.");
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
  appendChat("system", "Configurazione provider salvata. Motore AI ricaricato.");
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
  appendChat("system", `Profilo attivo impostato su ID ${profileId}.`);
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
  setText("detailTitle", job.titolo || "Titolo non disponibile");
  setText("detailCompany", job.azienda || "Azienda non disponibile");
  setText(
    "detailMeta",
    `${job.sede || "Sede N/D"} | Score ${job.punteggio_ai || 0}/10 | ${job.modalita || "Modalita N/D"}`,
  );
  setText("detailAdvice", job.consiglio || "Valuta il fit e prepara una candidatura mirata.");

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
    document.getElementById("coverLetterOutput").value = "";
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
            <h4>Match Score</h4>
            <div class="score-xl">${score}/10</div>
            <div class="text-sm mt-8 text-center">${escapeHtml((analysis ? analysis.consiglio : null) || job.consiglio || "")}</div>
          </div>
          <div class="info-card">
            <h4>Dettagli Posizione</h4>
            ${ralSpan}
            <div class="info-tag"><strong>Contratto:</strong> ${escapeHtml((analysis ? analysis.contratto : null) || "N/D")}</div>
            <div class="info-tag"><strong>Remote:</strong> ${escapeHtml((analysis ? analysis.smart_working : null) || job.modalita || "N/D")}</div>
            <div class="info-tag"><strong>Esperienza:</strong> ${escapeHtml((analysis ? analysis.anni_esperienza_richiesti : null) || "N/D")}</div>
            <div class="info-tag"><strong>Skill Code:</strong> ${escapeHtml((analysis ? analysis.programmazione_richiesta : null) || "N/D")}</div>
            <div class="info-tag"><strong>Neolaureati:</strong> ${escapeHtml((analysis ? analysis.adatta_neolaureati : null) || "N/D")}</div>
          </div>
        </div>
        <div class="mt-16">
          <h4>Vantaggi e Svantaggi</h4>
          <ul class="pros-cons">
            <li class="pro">✅ ${escapeHtml((analysis ? analysis.punti_forza_per_diego : null) || "N/D")}</li>
            <li class="con">❌ ${escapeHtml((analysis ? analysis.punti_deboli_per_diego : null) || "N/D")}</li>
          </ul>
        </div>
        <div class="info-card mt-8">
            <p class="text-sm">💡 <strong>Giudizio AI:</strong> ${escapeHtml((analysis ? analysis.riassunto : null) || "")}</p>
        </div>
        <div class="mt-16">
          <h4>Meta Annuncio</h4>
          <p class="text-sm text-dim">Ricerca: ${escapeHtml(job.ricerca_usata)} | Fonte: ${escapeHtml(job.fonte || "App")} | Rilevato: ${escapeHtml(job.first_seen_at || "")} | Azienda Rep: ${escapeHtml((analysis ? analysis.reputazione_azienda : null) || "N/D")}</p>
        </div>
      </div>
    `;
  }
  activateView("detail");
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
  const consiglio = escapeHtml(job.consiglio || "Valuta il match");
  const title = escapeHtml(job.titolo || "Titolo non disponibile");
  const company = escapeHtml(job.azienda || "Azienda non disponibile");
  const newTag = job.is_new ? "<span class=\"pill-new\">Nuovo</span>" : "";
  const favoriteText = job.is_favorite ? "Togli Preferito" : "Preferito";
  const nextFavorite = job.is_favorite ? "0" : "1";
  const linkHtml = job.link ? `<div style="margin-top: 4px"><a href="${job.link}" target="_blank" rel="noopener">🔗 Link all'offerta</a></div>` : "";

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
        <button class="secondary" data-rec-action="detail" data-id="${job.id}">Dettaglio</button>
        <button data-rec-action="applied" data-id="${job.id}">Candida ora</button>
        <button class="danger" data-rec-action="rejected" data-id="${job.id}">Scarta</button>
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
      container.innerHTML = '<article class="rec-card"><div class="rec-title">Nessuna raccomandazione disponibile</div><div>Carica il CV e avvia una scansione per vedere i migliori job.</div></article>';
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
          appendChat("system", `Errore azione rapida: ${error.message}`);
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
          appendChat("system", `Errore preferito: ${error.message}`);
        }
      });
    });
  } catch (error) {
    container.innerHTML = `<article class="rec-card"><div class="rec-title">Errore caricamento</div><div>${escapeHtml(error.message)}</div></article>`;
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
    appendChat("system", `Prompt rapidi non disponibili: ${error.message}`);
  }
}

async function sendChatMessage(message) {
  const text = String(message || "").trim();
  if (!text) return;

  appendChat("user", text);
  activateView("detail");
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message: text, session_id: "default" }),
    });
    appendChat("assistant", result.answer || "Nessuna risposta disponibile.");
  } catch (error) {
    appendChat("assistant", `Errore chat: ${error.message}`);
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
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${job.is_new ? '<span class="pill-new">Nuovo</span>' : ''}</td>
      <td>${job.punteggio_ai || 0}/10</td>
      <td>${truncate(job.consiglio || "")}</td>
      <td>${truncate(job.titolo || "")}</td>
      <td>${truncate(job.azienda || "")}</td>
      <td>${job.status}</td>
      <td>
        <button data-detail-id="${job.id}" class="secondary">Dettaglio</button>
        ${job.link ? `<a href="${job.link}" target="_blank" rel="noopener" style="margin-left: 8px;">🔗</a>` : ''}
      </td>
      <td>
        <div class="mini">
          <button data-action="applied" data-id="${job.id}">Candidata</button>
          <button data-action="rejected" data-id="${job.id}" class="danger">Scarta</button>
          <button data-action="reopened" data-id="${job.id}" class="secondary">Riapri</button>
          <button data-favorite="${job.is_favorite ? "0" : "1"}" data-id="${job.id}" class="secondary">${job.is_favorite ? "Togli Preferito" : "Preferito"}</button>
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
        appendChat("system", `Errore azione: ${error.message}`);
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
        appendChat("system", `Errore preferito: ${error.message}`);
      }
    });
  });

  body.querySelectorAll("button[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.detailId;
      try {
        await showJobDetail(id);
      } catch (error) {
        appendChat("system", `Errore dettaglio: ${error.message}`);
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
    status.textContent = "URL LinkedIn salvato con successo.";
    status.classList.remove("hidden");
    setTimeout(() => status.classList.add("hidden"), 3000);
    appendChat("system", "URL di LinkedIn salvato. L'AI lo userà nelle prossime analisi.");
  } catch (error) {
    appendChat("system", `Errore salvataggio LinkedIn: ${error.message}`);
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
    setText("cvSummary", `Errore upload: ${await response.text()}`);
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
    setText("keysStatus", `Errore salvataggio key: ${error.message}`);
  }
});

document.getElementById("showKeysFormBtn").addEventListener("click", () => {
  setKeysSectionMode(false, true);
});

document.getElementById("scanForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const termsText = document.getElementById("searchTerms").value.trim();
  const terms = termsText ? termsText.split("\n").map((x) => x.trim()).filter(Boolean) : [];
  
  const siteCheckboxes = document.querySelectorAll('input[name="scanSites"]:checked');
  const selectedSites = Array.from(siteCheckboxes).map(cb => cb.value);

  const payload = {
    search_terms: terms,
    location: document.getElementById("locationInput").value.trim() || null,
    is_remote: document.getElementById("remoteOnly").checked,
    sites: selectedSites.length > 0 ? selectedSites : ["linkedin", "indeed"],
  };

  setText("scanOutput", "Scansione in corso...");
  try {
    const result = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setText("scanOutput", JSON.stringify(result, null, 2));
    await Promise.all([loadJobs(), loadRecommendations()]);
  } catch (error) {
    setText("scanOutput", `Errore scansione: ${error.message}`);
  }
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
  appendChat("system", "Annuncio manuale aggiunto e analizzato.");
});

document.getElementById("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  await sendChatMessage(message);
});

document.getElementById("quickRecommendBtn").addEventListener("click", async () => {
  activateView("detail");
  await sendChatMessage("Consigliami i 5 lavori migliori da candidare oggi, in ordine di priorita.");
});

document.getElementById("refreshRecommendationsBtn").addEventListener("click", loadRecommendations);

document.getElementById("focusOpenBtn").addEventListener("click", async () => {
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
  activateView("detail");
  await sendChatMessage("Consigliami i lavori piu forti su cui candidarmi adesso, con ordine di priorita.");
});

document.getElementById("detailApplyNowBtn").addEventListener("click", async () => {
  if (!selectedJobId) {
    appendChat("system", "Apri prima un annuncio in dettaglio.");
    return;
  }
  try {
    await performJobAction(selectedJobId, "applied");
    appendChat("system", "Candidatura marcata come inviata.");
  } catch (error) {
    appendChat("system", `Errore candidatura: ${error.message}`);
  }
});

const genCovBtn = document.getElementById("generateCoverLetterBtn");
if (genCovBtn) {
  genCovBtn.addEventListener("click", async () => {
    if (!selectedJobId) return;
    const outBox = document.getElementById("coverLetterBox");
    const outTxt = document.getElementById("coverLetterOutput");
    
    outBox.style.display = "block";
    outTxt.value = "Generazione in corso (potrebbe richiedere qualche secondo)...";
    genCovBtn.disabled = true;

    try {
      const payload = await api(`/api/jobs/${selectedJobId}/cover-letter`, { method: "POST" });
      outTxt.value = payload.cover_letter || "Nessun risultato ricevuto.";
    } catch (error) {
      outTxt.value = `Errore generazione: ${error.message}`;
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
  appendChat("system", `CSV esportato: ${result.file}`);
});

async function bootstrap() {
  activateView("dashboard");
  await loadHealth();
  await loadKeysStatus();
  await loadProfiles();
  await Promise.all([loadJobs(), loadRecommendations()]);
  await loadChatPrompts();
  await loadChatHistory();
}

bootstrap().catch((error) => {
  console.error(error);
  appendChat("system", `Errore inizializzazione: ${error.message}`);
});
