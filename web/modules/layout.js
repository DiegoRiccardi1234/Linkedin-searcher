// Two things the CSS cannot work out on its own: how tall the stuck-to-the-top
// chrome currently is, and which density the user picked.
//
// Both exist because the app opens in the user's own browser, so Windows
// display scaling applies directly: a 1920x1080 screen at 125% is a 1536px CSS
// viewport, at 150% a 1280px one. The layout has to hold up there, and the user
// gets a way to buy back a step of room.

import { showToast } from "./helpers.js";
import { t } from "./i18n.js";

const DENSITIES = new Set(["normal", "compact"]);

// The topbar and the two banners are all `position: sticky; top: 0`, so they
// stack visually and anything sticking below them (the chat rail) has to start
// under their combined height — which was hardcoded to 80px.
export function syncStickyOffset() {
  const parts = [
    document.querySelector(".topbar"),
    document.querySelector(".update-banner:not(.hidden)"),
    document.querySelector(".no-key-banner:not(.hidden)"),
  ];
  const total = parts.reduce((sum, el) => sum + (el ? el.getBoundingClientRect().height : 0), 0);
  document.documentElement.style.setProperty("--sticky-offset", `${Math.round(total) || 72}px`);
}

export function applyDensity(value) {
  const density = DENSITIES.has(value) ? value : "normal";
  document.documentElement.setAttribute("data-density", density);
  localStorage.setItem("density", density);
  const select = document.getElementById("densitySelect");
  if (select && select.value !== density) select.value = density;
  return density;
}

export function initLayout() {
  applyDensity(localStorage.getItem("density") || "normal");
  syncStickyOffset();

  const select = document.getElementById("densitySelect");
  select?.addEventListener("change", () => {
    applyDensity(select.value);
    // The offset changes with the type scale.
    syncStickyOffset();
    showToast(t("settings.appearance.densitySaved"), "info");
  });

  // Banners appear and disappear after boot (update check, key removal), and
  // the topbar itself reflows on resize.
  window.addEventListener("resize", syncStickyOffset);
  const observer = new MutationObserver(syncStickyOffset);
  observer.observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "hidden"],
    childList: true,
  });
}
