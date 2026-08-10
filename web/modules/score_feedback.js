// Was this score right? The app has always asserted a 0-10 match with total
// confidence and never asked. One click is the whole contract; the expected
// score and the reason are optional, because demanding them collects nothing.
import { api, showToast } from "./helpers.js";
import { t } from "./i18n.js";

export function scoreFeedbackHtml(current) {
  const verdict = current && current.verdict ? String(current.verdict) : "";
  const expected =
    current && current.expected_score !== null && current.expected_score !== undefined
      ? String(current.expected_score)
      : "";
  const reason = current && current.reason ? String(current.reason) : "";
  const detailsOpen = verdict === "down" ? " open" : "";
  return `
    <div class="score-feedback">
      <span class="score-feedback-q">${t("feedback.question")}</span>
      <div class="score-feedback-btns">
        <button type="button" id="scoreFbUp" class="ghost-btn small${verdict === "up" ? " is-active" : ""}" aria-pressed="${verdict === "up"}" title="${t("feedback.agree")}">
          <span class="material-symbols-outlined">thumb_up</span>
        </button>
        <button type="button" id="scoreFbDown" class="ghost-btn small${verdict === "down" ? " is-active" : ""}" aria-pressed="${verdict === "down"}" title="${t("feedback.disagree")}">
          <span class="material-symbols-outlined">thumb_down</span>
        </button>
        ${
          verdict
            ? `<button type="button" id="scoreFbClear" class="ghost-btn small" title="${t("feedback.clear")}">
          <span class="material-symbols-outlined">backspace</span>
        </button>`
            : ""
        }
      </div>
      <details class="score-feedback-more"${detailsOpen}>
        <summary>${t("feedback.addDetail")}</summary>
        <div class="score-feedback-row">
          <input type="number" id="scoreFbExpected" min="0" max="10" step="1" value="${expected}" placeholder="${t("feedback.expectedPlaceholder")}" />
          <input type="text" id="scoreFbReason" value="${reason.replace(/"/g, "&quot;")}" placeholder="${t("feedback.reasonPlaceholder")}" />
        </div>
      </details>
    </div>`;
}

export function wireScoreFeedback(jobId) {
  const up = document.getElementById("scoreFbUp");
  const down = document.getElementById("scoreFbDown");
  if (!up || !down) return;

  const send = async (verdict) => {
    const expectedEl = document.getElementById("scoreFbExpected");
    const reasonEl = document.getElementById("scoreFbReason");
    const raw = expectedEl && expectedEl.value !== "" ? Number(expectedEl.value) : null;
    const expected = raw === null || Number.isNaN(raw) ? null : Math.round(raw);
    try {
      await api(`/api/jobs/${jobId}/score-feedback`, {
        method: "POST",
        body: JSON.stringify({
          verdict,
          expected_score: expected,
          reason: reasonEl ? reasonEl.value.trim() : "",
        }),
      });
      up.classList.toggle("is-active", verdict === "up");
      down.classList.toggle("is-active", verdict === "down");
      up.setAttribute("aria-pressed", String(verdict === "up"));
      down.setAttribute("aria-pressed", String(verdict === "down"));
      showToast(t("feedback.saved"), "info");
    } catch (err) {
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
    }
  };

  up.addEventListener("click", () => send("up"));
  down.addEventListener("click", () => send("down"));

  // You could give a verdict and never take it back: the DELETE endpoint has
  // been there, and tested, with nothing to press.
  document.getElementById("scoreFbClear")?.addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${jobId}/score-feedback`, { method: "DELETE" });
      for (const button of [up, down]) {
        button.classList.remove("is-active");
        button.setAttribute("aria-pressed", "false");
      }
      document.getElementById("scoreFbClear")?.remove();
      showToast(t("feedback.cleared"), "info");
    } catch (err) {
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
    }
  });
}

// Summary card in Analytics: how often the scores match the user's own reading.
export async function loadScoreFeedbackSummary() {
  const box = document.getElementById("scoreFeedbackSummary");
  if (!box) return;
  let data;
  try {
    data = await api("/api/score-feedback/summary");
  } catch {
    return;
  }
  if (!data || !data.total) {
    box.innerHTML = `<p class="micro">${t("feedback.summaryEmpty")}</p>`;
    return;
  }
  const parts = [
    `<strong>${data.agreement}%</strong> ${t("feedback.agreementLabel")}`,
    `${data.total} ${t("feedback.casesLabel")}`,
  ];
  if (data.avg_gap !== null && data.avg_gap !== undefined) {
    parts.push(`${t("feedback.avgGapLabel")}: ${data.avg_gap}`);
  }
  box.innerHTML =
    `<p class="text-sm">${parts.join(" · ")}</p>` +
    `<a class="ghost-btn small" href="/api/score-feedback/export" download>` +
    `<span class="material-symbols-outlined">download</span> ${t("feedback.export")}</a>`;
}
