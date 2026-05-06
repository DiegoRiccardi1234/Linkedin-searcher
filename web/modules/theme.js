// Theme toggle (light/dark) with localStorage persistence.

import { showToast } from "./helpers.js";
import { t } from "./i18n.js";

function setIcon(theme) {
  const span = document.querySelector("#themeToggle .material-symbols-outlined");
  if (span) span.textContent = theme === "dark" ? "light_mode" : "dark_mode";
}

export function initTheme() {
  const saved = localStorage.getItem("theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
  setIcon(saved);

  const toggle = document.getElementById("themeToggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    setIcon(next);
    const label = next === "dark"
      ? (t("toast.themeDark") || "Dark theme")
      : (t("toast.themeLight") || "Light theme");
    showToast(label, "info");
  });
}
