// What each model is allowed, in three columns, because they answer three
// different questions: what the app ships knowing, what it has measured on THIS
// key, and what the user typed after looking at their own console.
//
// The third column exists because the first cannot be right for everyone: a
// free tier's limits belong to a project, not to a provider, and the same model
// is five hundred requests a day for one user and fifty for the next.

import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

const box = () => document.getElementById("rateLimitsTable");

function _cell(limit) {
  if (!limit) return "—";
  const bits = [];
  // Both suffixes used to be written in Italian in all five languages.
  if (limit.rpm) bits.push(`${limit.rpm}${t("settings.limits.perMin")}`);
  if (limit.rpd) bits.push(`${limit.rpd}${t("settings.limits.perDay")}`);
  return bits.join(" · ") || "—";
}

export async function loadRateLimits() {
  const wrap = box();
  if (!wrap) return;
  let rows = [];
  try {
    rows = (await api("/api/providers/limits")).limits || [];
  } catch {
    return; // a diagnostic table must never break the settings page
  }
  if (!rows.length) {
    wrap.innerHTML = `<p class="micro">${escapeHtml(t("settings.limits.empty"))}</p>`;
    return;
  }
  wrap.innerHTML = `
    <table class="info-provider-table">
      <thead>
        <tr>
          <th>${escapeHtml(t("settings.limits.model"))}</th>
          <th>${escapeHtml(t("settings.limits.default"))}</th>
          <th>${escapeHtml(t("settings.limits.observed"))}</th>
          <th>${escapeHtml(t("settings.limits.today"))}</th>
          <th>${escapeHtml(t("settings.limits.yours"))}</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row) => `
          <tr data-limit-provider="${escapeHtml(row.provider)}" data-limit-model="${escapeHtml(row.model)}">
            <td><strong>${escapeHtml(row.model)}</strong><br /><span class="micro">${escapeHtml(row.provider)}</span></td>
            <td>${escapeHtml(_cell(row.default))}</td>
            <td>${escapeHtml(_cell(row.observed))}</td>
            <td>${row.used_today}${row.exhausted ? ` <span class="job-flag flag-warn" title="${escapeHtml(t("settings.limits.exhausted"))}">!</span>` : ""}</td>
            <td class="limits-edit">
              <input type="number" min="0" class="limit-rpm" value="${row.override?.rpm || ""}" placeholder="${escapeHtml(t("settings.limits.rpm"))}" />
              <input type="number" min="0" class="limit-rpd" value="${row.override?.rpd || ""}" placeholder="${escapeHtml(t("settings.limits.rpd"))}" />
              <button type="button" class="ghost-btn small limit-save">${escapeHtml(t("settings.limits.save"))}</button>
            </td>
          </tr>`,
          )
          .join("")}
      </tbody>
    </table>`;
}

export function initRateLimits() {
  const wrap = box();
  if (!wrap) return;
  wrap.addEventListener("click", async (event) => {
    const button = event.target.closest(".limit-save");
    if (!button) return;
    const row = button.closest("[data-limit-provider]");
    const rpm = Number(row.querySelector(".limit-rpm").value || 0);
    const rpd = Number(row.querySelector(".limit-rpd").value || 0);
    button.disabled = true;
    try {
      await api("/api/providers/limits", {
        method: "POST",
        body: JSON.stringify({
          provider: row.dataset.limitProvider,
          model: row.dataset.limitModel,
          rpm,
          rpd,
        }),
      });
      showToast(t("settings.limits.saved"), "info");
      await loadRateLimits();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}
