/**
 * "Here is what I read out of your CV. Correct me." — shown once, after an upload.
 *
 * These seven facts decide whether an offer is applicable at all, and until now
 * they were deduced silently: the upload showed a prose summary of skills and
 * roles, and the things that actually hide jobs — years of experience, degree
 * level and subject, mark, where you can work, licence, protected-categories
 * register — were filled in behind the user's back and never shown for
 * confirmation. Three of them had no control anywhere in the app at all, so the
 * readiness chip that said "degree subject is missing" pointed at a panel with
 * no field to fix it.
 *
 * Two rules this file exists to honour:
 *
 * - **Confirming is not overriding.** A value read from the CV that the user
 *   agrees with must stay sourced to the CV. Writing it back as a manual
 *   correction would make it immune to the next upload — which is exactly the
 *   defect this release fixed for years of experience, and re-creating it here
 *   with a different field would be worse for having been done on purpose. So
 *   only fields the user actually CHANGED are sent.
 * - **An unanswered fact stays unknown, and unknown blocks nothing.** "I'll do
 *   it later" is a real answer, and the card does not come back on its own.
 */

import { api, showToast } from "./helpers.js";
import { t } from "./i18n.js";

/** Facts shown, in the order a person would check them. */
const ROWS = [
  { key: "years_experience", kind: "number", el: "cvrYears", step: "any", min: 0, max: 50 },
  { key: "education_level", kind: "select", el: "cvrEducation" },
  { key: "degree_fields", kind: "list", el: "cvrFields" },
  { key: "grade", kind: "number", el: "cvrGrade", min: 60, max: 110 },
  { key: "work_rule", kind: "list", el: "cvrCities", from: "base_cities" },
  { key: "driving_licence", kind: "tri", el: "cvrLicence" },
  { key: "protected_category", kind: "tri", el: "cvrProtected" },
];

let _facts = null;
let _initial = {};
const $ = (id) => document.getElementById(id);

/** The value a control currently holds, in the shape PATCH /api/profile wants. */
function readControl(row) {
  const el = $(row.el);
  if (!el) return undefined;
  const raw = String(el.value ?? "").trim();
  if (row.kind === "number") {
    if (raw === "") return null; // present-and-null clears the override
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  if (row.kind === "list") {
    return raw
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  }
  if (row.kind === "tri") {
    // "" is the honest third answer: nobody said, and nobody has to.
    return raw === "" ? undefined : raw === "1";
  }
  return raw;
}

/** Two values are the same answer. Lists compare as sets of trimmed strings. */
function same(a, b) {
  if (Array.isArray(a) || Array.isArray(b)) {
    const norm = (v) => (Array.isArray(v) ? v.map((x) => String(x).trim().toLowerCase()) : []);
    const [x, y] = [norm(a), norm(b)];
    return x.length === y.length && x.every((v, i) => v === y[i]);
  }
  if (a === null || a === undefined) return b === null || b === undefined;
  return String(a) === String(b);
}

function provenanceBadge(origin) {
  const label =
    origin === "cv"
      ? t("profile.matching.fromCv")
      : origin === "manuale"
        ? t("profile.matching.fromYou")
        : t("profile.matching.missing");
  const cls = origin === "mancante" ? "flag-block" : "flag-info";
  return `<span class="job-flag ${cls}">${label}</span>`;
}

function fillControls() {
  const levels = _facts.education_levels || [];
  const sel = $("cvrEducation");
  if (sel) {
    sel.innerHTML =
      `<option value="">${t("profile.matching.missing")}</option>` +
      levels.map((l) => `<option value="${l}">${l}</option>`).join("");
    sel.value = _facts.education_level || "";
  }
  const set = (id, value) => {
    const el = $(id);
    if (el) el.value = value;
  };
  set("cvrYears", _facts.years_experience ?? "");
  set("cvrGrade", _facts.grade ?? "");
  set("cvrFields", (_facts.degree_fields || []).join(", "));
  set("cvrCities", (_facts.base_cities || []).join(", "));
  set("cvrLicence", _facts.driving_licence === null ? "" : _facts.driving_licence ? "1" : "0");
  set(
    "cvrProtected",
    _facts.protected_category === null ? "" : _facts.protected_category ? "1" : "0",
  );
}

function paintProvenance() {
  const sources = _facts.sources || {};
  for (const row of ROWS) {
    const cell = document.querySelector(`[data-cvr-origin="${row.key}"]`);
    if (cell) cell.innerHTML = provenanceBadge(sources[row.key] || "mancante");
  }
}

function close() {
  $("cvReviewModal")?.classList.add("hidden");
}

async function confirmFacts() {
  const body = {};
  for (const row of ROWS) {
    const now = readControl(row);
    if (now === undefined) continue;
    if (!same(now, _initial[row.key])) {
      body[row.from || (row.key === "work_rule" ? "base_cities" : row.key)] = now;
    }
  }
  // An empty body is the common case and it is the point: agreeing with the CV
  // must not rewrite the fact as a manual override, or the next upload would no
  // longer be able to correct it.
  if (Object.keys(body).length) {
    try {
      await api("/api/profile", { method: "PATCH", body: JSON.stringify(body) });
    } catch (err) {
      showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      return;
    }
  }
  close();
  showToast(t("cvReview.saved"), "info");
}

/**
 * Show the card for the facts as they stand right now.
 *
 * Deliberately called only after an upload — never from bootstrap. A panel that
 * reappears on every visit stops being a question and becomes furniture.
 */
export async function showCvReview() {
  const modal = $("cvReviewModal");
  if (!modal) return;
  try {
    _facts = await api("/api/profile/matching-facts");
  } catch {
    return; // a checklist must never be the reason something else fails
  }
  _initial = {};
  for (const row of ROWS) {
    _initial[row.key] =
      row.key === "work_rule" ? _facts.base_cities || [] : _facts[row.key] ?? null;
  }
  fillControls();
  paintProvenance();
  modal.classList.remove("hidden");
  modal.focus?.();
}

export function initCvReview({ enableModalDismiss } = {}) {
  const modal = $("cvReviewModal");
  if (!modal) return;
  enableModalDismiss?.(modal, close);
  modal.querySelectorAll("[data-close-cvreview]").forEach((b) =>
    b.addEventListener("click", close),
  );
  $("cvrConfirmBtn")?.addEventListener("click", confirmFacts);
}
