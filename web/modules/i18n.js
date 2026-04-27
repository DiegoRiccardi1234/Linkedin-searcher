// Localization module.
// Exposes:
//   t(key, params)               — translate a dotted key with {placeholder} interpolation
//   getCurrentLang()             — currently active locale code
//   loadLanguage(lang)           — switch to a locale, persist preference, re-translate the DOM
//   initI18n()                   — load English fallback once + apply current locale at boot
//   onLanguageChange(callback)   — register a side-effect to run after every loadLanguage()

let _i18nStrings = {};
let _i18nFallback = {};
let _currentLang = localStorage.getItem("language") || "en";
const _i18nMissingReported = new Set();
let _onLanguageChange = null;

function _reportMissingKey(key) {
  if (_i18nMissingReported.has(key)) return;
  _i18nMissingReported.add(key);
  console.warn(`[i18n] missing translation for "${key}" (lang=${_currentLang})`);
}

export function t(key, params = {}) {
  const keys = key.split(".");
  let val = keys.reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), _i18nStrings);
  let usedFallback = false;
  if (val === undefined) {
    val = keys.reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), _i18nFallback);
    usedFallback = val !== undefined;
  }
  if (val === undefined) {
    _reportMissingKey(key);
    return key;
  }
  if (usedFallback && _currentLang !== "en") {
    _reportMissingKey(key);
  }
  return String(val).replace(/\{(\w+)\}/g, (_, p) =>
    params[p] !== undefined ? params[p] : `{${p}}`,
  );
}

export function getCurrentLang() {
  return _currentLang;
}

export function onLanguageChange(callback) {
  _onLanguageChange = typeof callback === "function" ? callback : null;
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    const val = t(key);
    if (val !== key) el.textContent = val;
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    const val = t(key);
    if (val !== key) el.placeholder = val;
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    const val = t(key);
    if (val !== key) el.title = val;
  });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => {
    const key = el.getAttribute("data-i18n-html");
    const val = t(key);
    if (val !== key) el.innerHTML = val;
  });
}

export async function loadLanguage(lang) {
  try {
    const res = await fetch(`/web/i18n/${lang}.json`);
    if (!res.ok) throw new Error(res.status);
    _i18nStrings = await res.json();
  } catch {
    _i18nStrings = _i18nFallback;
  }
  _currentLang = lang;
  localStorage.setItem("language", lang);
  document.documentElement.setAttribute("lang", lang);
  applyTranslations();
  if (_onLanguageChange) {
    try {
      const ret = _onLanguageChange(lang);
      if (ret && typeof ret.catch === "function") ret.catch(() => {});
    } catch {
      /* swallow callback errors */
    }
  }

  // Notify backend of language change.
  fetch("/api/preferences", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: "ui_language", value: lang }),
  }).catch(() => {});
}

export async function initI18n() {
  try {
    const res = await fetch("/web/i18n/en.json");
    _i18nFallback = await res.json();
  } catch {
    _i18nFallback = {};
  }
  await loadLanguage(_currentLang);
}
