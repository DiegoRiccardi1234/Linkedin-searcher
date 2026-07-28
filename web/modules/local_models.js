// Running the scoring model on this PC. The panel answers three questions in
// order: what hardware is there, what already runs on it, and what is worth
// downloading. A model already on disk always wins over a better one that is not.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

let _snapshot = null;

function _hardwareLine(hw) {
  if (!hw) return "";
  const bits = [];
  if (hw.gpu_name) {
    bits.push(
      hw.vram_gb ? `${escapeHtml(hw.gpu_name)} · ${hw.vram_gb} GB VRAM` : escapeHtml(hw.gpu_name),
    );
  } else {
    bits.push(t("local.noGpu"));
  }
  if (hw.ram_gb) bits.push(`${hw.ram_gb} GB RAM`);
  return bits.join(" · ");
}

async function _use(model) {
  try {
    const res = await api("/api/local/use", {
      method: "POST",
      body: JSON.stringify({ model, for_scoring: true }),
    });
    showToast(t("local.nowUsing", { model: res.scoring_model || model }), "info");
    loadLocalModels();
  } catch (err) {
    showToast(`${t("toast.actionError")}: ${err.message}`, "error");
  }
}

// Ollama streams its own progress; we surface the percentage and stop at "success".
function _pull(tag, button) {
  const progress = document.getElementById("localProgress");
  button.disabled = true;
  if (progress) {
    progress.hidden = false;
    progress.textContent = t("local.pullStarting", { model: tag });
  }
  fetch("/api/local/pull", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: tag }),
  })
    .then(async (resp) => {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          let event;
          try {
            event = JSON.parse(line.slice(5).trim());
          } catch {
            continue;
          }
          if (event.error) throw new Error(event.error);
          if (event.total && event.completed && progress) {
            const pct = Math.round((event.completed / event.total) * 100);
            progress.textContent = `${tag}: ${pct}%`;
          } else if (event.status && progress) {
            progress.textContent = `${tag}: ${event.status}`;
          }
        }
      }
      if (progress) progress.textContent = t("local.pullDone", { model: tag });
      showToast(t("local.pullDone", { model: tag }), "info");
      loadLocalModels();
    })
    .catch((err) => {
      if (progress) progress.textContent = "";
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
    })
    .finally(() => {
      button.disabled = false;
    });
}

export async function loadLocalModels() {
  const card = document.getElementById("localModelsCard");
  if (!card) return;
  try {
    _snapshot = await api("/api/local/status");
  } catch {
    return;
  }
  const { hardware, recommendation, ollama, ready } = _snapshot;

  const hw = document.getElementById("localHardware");
  if (hw) hw.textContent = _hardwareLine(hardware);

  const verdict = document.getElementById("localVerdict");
  if (verdict) {
    verdict.textContent = recommendation.reason || "";
    verdict.dataset.verdict = recommendation.verdict || "";
  }

  // Ollama is what downloads and serves the models: without it the rest is moot.
  const readyBox = document.getElementById("localReady");
  if (readyBox) {
    if (!ollama.installed) {
      readyBox.innerHTML = `<p class="micro">${t("local.needOllama")} <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com</a></p>`;
    } else if (!ollama.running) {
      readyBox.innerHTML = `<p class="micro">${t("local.ollamaStopped")}</p>`;
    } else if (!ready.length) {
      readyBox.innerHTML = `<p class="micro">${t("local.noneDownloaded")}</p>`;
    } else {
      readyBox.innerHTML = ready
        .map((m) => {
          // Naming the quantisation is the difference between "12B" and "12B
          // stored in a way that costs 7.5 GB and loses almost no quality".
          const quant = m.quant ? ` · ${escapeHtml(m.quant)}` : "";
          const vram = m.vram_gb ? ` · ~${m.vram_gb} GB VRAM` : "";
          const intact = m.quality_penalty === 0 || m.quality_penalty === -2
            ? ` <span class="micro local-quality-ok">${t("local.qualityIntact")}</span>`
            : "";
          return (
            `<div class="local-model"><span class="local-model-main"><strong>${escapeHtml(m.name)}</strong>` +
            `<span class="micro">${m.params_b ? `${m.params_b}B` : ""}${quant}${vram}${intact}</span></span>` +
            `<button type="button" class="ghost-btn small" data-use="${escapeHtml(m.name)}">${t("local.useForScoring")}</button></div>`
          );
        })
        .join("");
      readyBox.querySelectorAll("[data-use]").forEach((el) => {
        el.addEventListener("click", () => _use(el.dataset.use));
      });
    }
  }

  const box = document.getElementById("localSuggestions");
  if (box) {
    const missing = (recommendation.models || []).filter((m) => !m.installed);
    box.innerHTML = missing.length
      ? `<p class="micro">${t("local.couldAlsoRun")}</p>` +
        missing
          .map(
            (m) =>
              `<div class="local-model"><span class="local-model-main"><strong>${escapeHtml(m.tag)}</strong>` +
              `<span class="micro">${escapeHtml(m.note)}</span></span>` +
              `<button type="button" class="ghost-btn small" data-pull="${escapeHtml(m.tag)}"${ollama.running ? "" : " disabled"}>${t("local.download")}</button></div>`,
          )
          .join("")
      : "";
    box.querySelectorAll("[data-pull]").forEach((el) => {
      el.addEventListener("click", () => _pull(el.dataset.pull, el));
    });
  }

  // What the wider world runs locally, filtered to this card. The hand-written
  // list above ages the day a new model lands; this one does not.
  const discovered = _snapshot.discovered || [];
  const hub = document.getElementById("localDiscovered");
  if (hub) {
    hub.innerHTML = discovered.length
      ? `<p class="micro">${t("local.fromHub")}</p>` +
        discovered
          .map(
            (m) =>
              `<div class="local-model"><span class="local-model-main"><strong>${escapeHtml(m.repo)}</strong>` +
              `<span class="micro">${m.params_b}B · ${escapeHtml(m.quant)} · ~${m.vram_gb} GB VRAM</span></span>` +
              `<button type="button" class="ghost-btn small" data-pull="${escapeHtml(m.pull)}"${ollama.running ? "" : " disabled"}>${t("local.download")}</button></div>`,
          )
          .join("")
      : "";
    hub.querySelectorAll("[data-pull]").forEach((el) => {
      el.addEventListener("click", () => _pull(el.dataset.pull, el));
    });
  }
}

export function initLocalModels() {
  document.getElementById("localRefresh")?.addEventListener("click", loadLocalModels);
}
