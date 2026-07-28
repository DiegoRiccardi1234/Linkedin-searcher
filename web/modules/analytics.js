// Dashboard analytics charts (Chart.js). Self-contained: owns its three chart
// instances and re-renders them from /api/analytics. Each card shows a
// "no data yet" note instead of an empty/broken canvas when its series is empty.
import { api, showToast } from "./helpers.js";
import { getCurrentLang, t } from "./i18n.js";
import { loadScoreFeedbackSummary } from "./score_feedback.js";

let statusChart = null;
let scoreChart = null;
let topCompaniesChart = null;

function _card(canvas) {
  return canvas.closest(".analytics-card") || canvas.parentElement;
}

function _emptyCard(canvas) {
  if (!canvas) return;
  canvas.style.display = "none";
  const card = _card(canvas);
  if (!card) return;
  let note = card.querySelector(".analytics-empty");
  if (!note) {
    note = document.createElement("p");
    note.className = "analytics-empty";
    card.appendChild(note);
  }
  note.textContent = t("analytics.noData") || "No data yet";
}

function _showCanvas(canvas) {
  if (!canvas) return;
  canvas.style.display = "";
  const card = _card(canvas);
  const note = card && card.querySelector(".analytics-empty");
  if (note) note.remove();
}

function _hasData(obj) {
  const vals = Object.values(obj || {});
  return vals.length > 0 && vals.some((v) => Number(v) > 0);
}

export async function loadAnalytics() {
  // Not a chart and not from /api/analytics: it renders itself and must not be
  // skipped when the charts below have nothing to draw.
  loadScoreFeedbackSummary();
  try {
    const data = await api("/api/analytics");

    const statusCtx = document.getElementById("statusChart");
    if (statusCtx) {
      if (statusChart) statusChart.destroy();
      if (_hasData(data.jobs_by_status)) {
        _showCanvas(statusCtx);
        statusChart = new Chart(statusCtx, {
          type: "doughnut",
          data: {
            labels: Object.keys(data.jobs_by_status).map((k) => t(`jobs.status.${k}`) || k),
            datasets: [
              {
                data: Object.values(data.jobs_by_status),
                backgroundColor: ["#198754", "#dc3545", "#ffc107", "#0d6efd", "#6c757d"],
              },
            ],
          },
          options: { responsive: true },
        });
      } else {
        _emptyCard(statusCtx);
      }
    }

    const scoreCtx = document.getElementById("scoreChart");
    if (scoreCtx) {
      if (scoreChart) scoreChart.destroy();
      // Offers nobody judged get their own bar rather than being folded into
      // "0": a 0 is a verdict, and hiding them would make the bars stop adding
      // up to the archive.
      const unscored = Number(data.unscored || 0);
      const scoreLabels = [...Object.keys(data.score_distribution || {}), t("analytics.unscored")];
      const scoreValues = [...Object.values(data.score_distribution || {}), unscored];
      if (_hasData(data.score_distribution) || unscored > 0) {
        _showCanvas(scoreCtx);
        scoreChart = new Chart(scoreCtx, {
          type: "bar",
          data: {
            labels: scoreLabels,
            datasets: [
              {
                label: t("analytics.matchScore") || "Match Score",
                data: scoreValues,
                backgroundColor: scoreLabels.map((_, i) =>
                  i === scoreLabels.length - 1 ? "#94a3b8" : "#0d6efd",
                ),
              },
            ],
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } } },
        });
      } else {
        _emptyCard(scoreCtx);
      }
    }

    const companiesCtx = document.getElementById("topCompaniesChart");
    if (companiesCtx) {
      if (topCompaniesChart) topCompaniesChart.destroy();
      if (Array.isArray(data.top_companies) && data.top_companies.length) {
        _showCanvas(companiesCtx);
        topCompaniesChart = new Chart(companiesCtx, {
          type: "bar",
          data: {
            labels: data.top_companies.map((c) => c.company),
            datasets: [
              {
                label: t("analytics.topCompanies") || "Top Companies",
                data: data.top_companies.map((c) => c.count),
                backgroundColor: "#635bff",
              },
            ],
          },
          options: {
            indexAxis: "y",
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true } },
          },
        });
      } else {
        _emptyCard(companiesCtx);
      }
    }
  } catch (e) {
    console.error("Failed to load analytics", e);
  }
}

// F1 — AI usage panel. Reads the token pipeline that's recorded on every LLM
// call (GET /api/usage/stats) and shows totals + a per-provider breakdown.
export async function loadUsage(range) {
  const body = document.getElementById("usageBody");
  if (!body) return;
  const sel = document.getElementById("usageRange");
  const r = range || (sel && sel.value) || "today";
  let data;
  try {
    data = await api(`/api/usage/stats?range=${encodeURIComponent(r)}`);
  } catch {
    body.innerHTML = `<p class="analytics-empty">${t("usage.noData")}</p>`;
    return;
  }
  const quota = data.quota || null;
  if (!data.total_calls) {
    body.innerHTML =
      `<p class="analytics-empty">${t("usage.noData")}</p>` + quotaBarHtml(quota);
    wireDailyLimitInput();
    return;
  }
  const fmt = (n) => Number(n || 0).toLocaleString(getCurrentLang());
  const esc = (s) =>
    String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const rows = (data.by_provider || [])
    .map(
      (p) =>
        `<div class="usage-row"><span class="usage-prov">${esc(p.provider)}</span><span class="usage-tok">${fmt(p.total_tokens)}</span></div>`,
    )
    .join("");
  body.innerHTML = `
    <div class="usage-totals">
      <div class="usage-stat"><span class="usage-num">${fmt(data.total_tokens)}</span><span class="usage-lbl">${t("usage.tokens")}</span></div>
      <div class="usage-stat"><span class="usage-num">${fmt(data.total_calls)}</span><span class="usage-lbl">${t("usage.calls")}</span></div>
    </div>
    <div class="usage-list">${rows}</div>
    ${quotaBarHtml(quota)}`;
  wireDailyLimitInput();
}

// Today's requests against the daily ceiling. A free OpenRouter account gets
// 1000 a day shared across every key, and nothing in the app ever said so —
// you found out through a wall of 429s in the middle of a scan.
function quotaBarHtml(quota) {
  if (!quota || !quota.limit) return "";
  const used = Number(quota.used || 0);
  const limit = Number(quota.limit);
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const level = pct >= 100 ? "flag-block" : pct >= 80 ? "flag-warn" : "";
  return `
    <div class="usage-quota">
      <div class="usage-quota-head micro">
        <span>${t("usage.quotaToday")}</span>
        <span class="${level}">${used} / ${limit}</span>
      </div>
      <div class="usage-quota-track"><div class="usage-quota-fill" style="width:${pct}%"></div></div>
      <label class="micro usage-quota-edit">
        <span>${t("settings.usage.limitLabel")}</span>
        <input type="number" id="dailyLimitInput" min="0" step="50" value="${limit}" />
      </label>
    </div>`;
}

// The ceiling could stop a scan outright and there was no way to change it: not
// writable through the API, and no field anywhere. Editing the DB by hand was
// the only remedy.
export function wireDailyLimitInput() {
  const input = document.getElementById("dailyLimitInput");
  if (!input || input.dataset.wired) return;
  input.dataset.wired = "1";
  input.addEventListener("change", async () => {
    const value = String(Math.max(0, Number(input.value) || 0));
    try {
      await api("/api/preferences", {
        method: "POST",
        body: JSON.stringify({ key: "daily_request_limit", value }),
      });
      showToast(t("settings.usage.limitSaved"), "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}
