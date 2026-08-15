// Sub-tabs inside a view.
//
// Settings had eight full-width cards stacked into seven screens and Profile
// four, so finding anything meant scrolling past everything. The app had no
// tab component at all — the only tab-like thing was the table/kanban pair,
// wired by hand to two hardcoded ids — so this is that component, once,
// declarative, and shared by both pages.
//
// Markup contract (no framework, no build step):
//
//   <div class="subtabs" data-subtabs="settings" role="tablist">
//     <button data-subtab="ai" ...><span data-i18n="settings.tabs.ai">IA</span>
//       <span class="subtab-badge hidden">0</span></button>
//   </div>
//   <div data-subtabs-panels="settings">
//     <section data-subtab-panel="ai">…</section>
//   </div>
//
// Panels are hidden with a class, never with the `hidden` attribute or by
// removing them: `applyTranslations()`, `getElementById` and every existing
// loader keep working on a panel nobody is looking at.

const _onChange = new Map();

function _tabs(group) {
  return Array.from(document.querySelectorAll(`[data-subtabs="${group}"] [data-subtab]`));
}

function _panels(group) {
  return Array.from(
    document.querySelectorAll(`[data-subtabs-panels="${group}"] [data-subtab-panel]`),
  );
}

export function activeSubtab(group) {
  const tab = _tabs(group).find((t) => t.classList.contains("is-active"));
  return tab ? tab.dataset.subtab : null;
}

export function showSubtab(group, id, { silent = false } = {}) {
  const tabs = _tabs(group);
  if (!tabs.length) return false;
  const target = tabs.some((t) => t.dataset.subtab === id) ? id : tabs[0].dataset.subtab;
  if (activeSubtab(group) === target && silent) return true;

  tabs.forEach((tab) => {
    const on = tab.dataset.subtab === target;
    tab.classList.toggle("is-active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
    // Roving tabindex: one stop for the whole strip, arrows move inside it.
    tab.tabIndex = on ? 0 : -1;
  });
  _panels(group).forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.subtabPanel === target);
  });
  try {
    localStorage.setItem(`subtab:${group}`, target);
  } catch {
    /* private mode: the tabs work, they just are not remembered */
  }
  if (!silent) _onChange.get(group)?.(target);
  return true;
}

export function setSubtabBadge(group, id, count) {
  const tab = _tabs(group).find((t) => t.dataset.subtab === id);
  const badge = tab?.querySelector(".subtab-badge");
  if (!badge) return;
  const n = Number(count) || 0;
  badge.textContent = String(n);
  badge.classList.toggle("hidden", n === 0);
}

export function initSubtabs(group, { defaultTab = null, persist = true, onChange = null } = {}) {
  const strip = document.querySelector(`[data-subtabs="${group}"]`);
  if (!strip || strip.dataset.subtabsReady === "1") return;
  strip.dataset.subtabsReady = "1";
  if (onChange) _onChange.set(group, onChange);

  strip.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-subtab]");
    if (tab) showSubtab(group, tab.dataset.subtab);
  });
  strip.addEventListener("keydown", (event) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    const tabs = _tabs(group);
    const current = tabs.findIndex((t) => t.classList.contains("is-active"));
    let next = current;
    if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    event.preventDefault();
    showSubtab(group, tabs[next].dataset.subtab);
    tabs[next].focus();
  });

  let initial = defaultTab;
  if (persist) {
    try {
      initial = localStorage.getItem(`subtab:${group}`) || defaultTab;
    } catch {
      initial = defaultTab;
    }
  }
  // Silent: restoring where the user was is not the user choosing again, and a
  // lazy loader firing at boot for a tab nobody opened is what this replaces.
  showSubtab(group, initial || _tabs(group)[0]?.dataset.subtab, { silent: true });
}

/** The sub-tab panel that contains this element, if any. */
export function panelOf(el) {
  const panel = el?.closest?.("[data-subtab-panel]");
  if (!panel) return null;
  const group = panel.closest("[data-subtabs-panels]")?.dataset.subtabsPanels;
  return group ? { group, id: panel.dataset.subtabPanel } : null;
}
