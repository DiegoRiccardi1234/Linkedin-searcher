// Bulk re-scoring of offers already in the archive.
//
// The per-offer button can only fix one verdict at a time, which is no help
// when what changed applies to everything: a better scoring model, a corrected
// blocking rule, a provider that was down for a whole scan. This drives
// /api/jobs/reanalyze/stream and shows progress, because the run takes minutes
// and a silent UI is indistinguishable from a hung one.
import { showToast } from "./helpers.js";
import { t } from "./i18n.js";

let _deps = { loadJobs: async () => {} };
let _source = null;

export function initRescore(deps) {
  _deps = { ..._deps, ...deps };
}

function setLabel(text) {
  const label = document.getElementById("rescoreBulkLabel");
  if (label) label.textContent = text;
}

function finish(btn) {
  _source?.close();
  _source = null;
  btn.classList.remove("is-running");
  btn.disabled = false;
  setLabel(t("jobs.rescoreBulk"));
}

async function confirmScope(scope) {
  // Say the number BEFORE spending: "re-score everything" on a 238-offer archive
  // is minutes of calls, and the user is the one who decides that is worth it.
  const res = await fetch(`/api/jobs/reanalyze/preview?scope=${scope}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const { count } = await res.json();
  if (!count) {
    showToast(t("jobs.rescoreNothing"), "info");
    return 0;
  }
  return window.confirm(t("jobs.rescoreConfirm").replace("{n}", count)) ? count : 0;
}

export function wireRescoreBulk() {
  const btn = document.getElementById("rescoreBulkBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (_source) {
      // Second click while running = stop. The server releases its own lock.
      await fetch("/api/jobs/reanalyze/cancel", { method: "POST" });
      return;
    }
    const scope = document.getElementById("rescoreScope")?.value || "unscored";
    let count = 0;
    try {
      count = await confirmScope(scope);
    } catch (err) {
      showToast(`${t("jobs.rescoreFailed")}: ${err.message}`, "error");
      return;
    }
    if (!count) return;

    btn.classList.add("is-running");
    _source = new EventSource(`/api/jobs/reanalyze/stream?scope=${scope}`);
    _source.onmessage = async (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      if (data.error) {
        showToast(`${t("jobs.rescoreFailed")}: ${data.error}`, "error");
        finish(btn);
        return;
      }
      if (data.status === "scored" || data.status === "failed") {
        setLabel(`${data.current}/${data.total}`);
      }
      if (data.status === "complete") {
        showToast(
          t("jobs.rescoreDone")
            .replace("{n}", data.rivalutate)
            .replace("{c}", data.cambiate),
          "success",
        );
        finish(btn);
        await _deps.loadJobs();
      }
    };
    _source.onerror = () => {
      // A stream that dies mid-run has still written every offer it got to.
      showToast(t("jobs.rescoreInterrupted"), "info");
      finish(btn);
      _deps.loadJobs();
    };
  });
}
