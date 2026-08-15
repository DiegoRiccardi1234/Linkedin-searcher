// What the app still needs to know, shown where it can be answered.
//
// The server has always known which facts were missing — matching-facts has
// returned a `missing` list since 1.8.0 — and the page never read it. Now one
// answer feeds three places: the strip at the top of each profile tab, the
// checklist on the dashboard, and the gate in front of a scan. They cannot
// disagree, because there is only one of them.

import { api } from "./helpers.js";
import { t } from "./i18n.js";

let _cache = null;

//: Where each missing item can actually be answered. A strip that names a gap
//: without offering the field is just a complaint.
const FOCUS = {
  search_terms: { view: "job-search", el: "keywordsInput" },
  location: { view: "job-search", el: "locationsInput" },
  cv: { view: "profile", el: "cvFile" },
  goal: { view: "profile", el: "obGoal" },
  ral_min: { view: "profile", el: "obRalMin" },
  years_experience: { view: "profile", el: "factsEditBtn" },
  education_level: { view: "profile", el: "factsEditBtn" },
  grade: { view: "profile", el: "factsEditBtn" },
  degree_fields: { view: "profile", el: "factsEditBtn" },
  driving_licence: { view: "profile", el: "factsEditBtn" },
  protected_category: { view: "profile", el: "factsEditBtn" },
  work_rule: { view: "profile", el: "factCities" },
};

const SOURCE_KEY = {
  cv: "readiness.sourceCv",
  manuale: "readiness.sourceManual",
  last_scan: "readiness.sourceLastScan",
  shortlist: "readiness.sourceShortlist",
  profile: "readiness.sourceProfile",
  watchlist: "readiness.sourceWatchlist",
};

export async function fetchReadiness({ force = false } = {}) {
  if (_cache && !force) return _cache;
  try {
    _cache = await api("/api/profile/readiness");
  } catch {
    // A missing checklist must never be the reason something else fails.
    _cache = null;
  }
  return _cache;
}

export function invalidateReadiness() {
  _cache = null;
}

function _chip(item) {
  const label = t(`readiness.item.${item.id}`) || item.id;
  if (item.status === "ok") {
    const origin = SOURCE_KEY[item.source] ? t(SOURCE_KEY[item.source]) : "";
    const value = item.value ? `: ${item.value}` : "";
    return `<span class="readiness-chip is-ok" title="${origin}">${label}${value}</span>`;
  }
  const cls = item.severity === "blocking" ? "is-blocking" : "is-warning";
  return `<button type="button" class="readiness-chip ${cls}" data-readiness-fix="${item.id}">${label}</button>`;
}

/** Fill every `[data-readiness-group]` strip on the page. */
export async function renderReadinessStrips() {
  const strips = document.querySelectorAll("[data-readiness-group]");
  if (!strips.length) return;
  const report = await fetchReadiness({ force: true });
  if (!report) return;
  for (const strip of strips) {
    const group = strip.dataset.readinessGroup;
    const items = report.items.filter((i) => i.group === group);
    const missing = items.filter((i) => i.status === "missing");
    if (!missing.length) {
      strip.innerHTML = `<span class="readiness-done">${t("readiness.allSet")}</span>`;
      continue;
    }
    const blocking = missing.filter((i) => i.severity === "blocking");
    const optional = missing.filter((i) => i.severity !== "blocking");
    const section = (label, list) =>
      list.length
        ? `<div class="readiness-row"><span class="readiness-label">${label}</span>${list
            .map(_chip)
            .join("")}</div>`
        : "";
    strip.innerHTML =
      section(t("readiness.blocking"), blocking) + section(t("readiness.optional"), optional);
  }
}

/**
 * The pre-scan gate: two things, and only two.
 *
 * Everything else the app does not know is a warning, on the same principle
 * the scoring follows — an unknown fact never hides an offer, so it cannot
 * stop a scan either.
 */
export async function ensureProfileReady({
  showToast,
  revealElement,
  terms = [],
  locations = [],
  isRemote = false,
}) {
  // What is on the form right now answers the question just as well as what is
  // stored — the server has not seen these tags yet, and refusing a scan
  // someone has literally just typed the terms for would be absurd.
  const answered = new Set();
  if (terms.length) answered.add("search_terms");
  if (locations.length || isRemote) answered.add("location");
  if (answered.has("search_terms") && answered.has("location")) return true;

  const report = await fetchReadiness({ force: true });
  if (!report) return true; // never let a checklist be the reason a scan cannot run
  const blocking = report.blocking.filter((id) => !answered.has(id));
  if (!blocking.length) return true;
  const names = blocking.map((id) => t(`readiness.item.${id}`) || id).join(", ");
  showToast(t("readiness.scanBlockedBody", { items: names }), "error");
  const first = FOCUS[blocking[0]];
  if (first && revealElement) {
    revealElement(first.el);
    document.getElementById(first.el)?.focus?.();
  }
  return false;
}

/** Wire the "fill this in" chips once; they work on any strip. */
export function initReadiness({ revealElement }) {
  document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-readiness-fix]");
    if (!chip) return;
    const target = FOCUS[chip.dataset.readinessFix];
    if (!target) return;
    revealElement(target.el);
    document.getElementById(target.el)?.focus?.();
  });
}
