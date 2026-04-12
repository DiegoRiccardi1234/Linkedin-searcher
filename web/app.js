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

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function appendChat(role, content) {
  const box = document.getElementById("chatBox");
  const item = document.createElement("div");
  item.className = "chat-item";
  item.innerHTML = `<div class="role">${role}</div><div>${content.replaceAll("\n", "<br>")}</div>`;
  box.appendChild(item);
  box.scrollTop = box.scrollHeight;
}

async function loadHealth() {
  const health = await api("/api/health");
  setText("providerBadge", `Provider: ${health.provider.active_provider}`);
  setText("modelBadge", `Modello: ${health.provider.active_model}`);
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
      <td><button data-detail-id="${job.id}" class="secondary">Dettaglio</button></td>
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
      await api(`/api/jobs/${id}/action`, {
        method: "POST",
        body: JSON.stringify({ action, notes: "" }),
      });
      await loadJobs();
    });
  });

  body.querySelectorAll("button[data-favorite]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const fav = btn.dataset.favorite === "1";
      await api(`/api/jobs/${id}/favorite`, {
        method: "POST",
        body: JSON.stringify({ is_favorite: fav }),
      });
      await loadJobs();
    });
  });

  body.querySelectorAll("button[data-detail-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.detailId;
      const payload = await api(`/api/jobs/${id}`);
      const job = payload.job || {};
      const analysis = job.analysis || {};

      const detail = {
        id: job.id,
        titolo: job.titolo,
        azienda: job.azienda,
        status: job.status,
        score: job.punteggio_ai,
        consiglio: job.consiglio,
        ricerca_usata: job.ricerca_usata,
        modalita: job.modalita,
        first_seen_at: job.first_seen_at,
        last_seen_at: job.last_seen_at,
        analysis,
      };
      setText("jobDetail", JSON.stringify(detail, null, 2));
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
});

document.getElementById("scanForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const termsText = document.getElementById("searchTerms").value.trim();
  const terms = termsText ? termsText.split("\n").map((x) => x.trim()).filter(Boolean) : [];

  const payload = {
    search_terms: terms,
    location: document.getElementById("locationInput").value.trim() || null,
    is_remote: document.getElementById("remoteOnly").checked,
  };

  setText("scanOutput", "Scansione in corso...");
  try {
    const result = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setText("scanOutput", JSON.stringify(result, null, 2));
    await loadJobs();
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

  await loadJobs();
  appendChat("system", "Annuncio manuale aggiunto e analizzato.");
});

document.getElementById("chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;

  appendChat("user", message);
  input.value = "";

  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id: "default" }),
    });
    appendChat("assistant", result.answer);
  } catch (error) {
    appendChat("assistant", `Errore chat: ${error.message}`);
  }
});

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
  await loadHealth();
  await loadProfiles();
  await loadJobs();
  await loadChatHistory();
}

bootstrap().catch((error) => {
  console.error(error);
  appendChat("system", `Errore inizializzazione: ${error.message}`);
});
