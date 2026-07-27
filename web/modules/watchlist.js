// Companies the user follows: a keyword scan never surfaces an employer that
// words its postings differently, so these are searched by name. The list lives
// in the DB (not in the scan form) because it outlives any single search; the
// scan form only carries the on/off switch, which is a preference.
import { api, escapeHtml, showToast } from "./helpers.js";
import { t } from "./i18n.js";

function _relativeSeen(iso) {
  if (!iso) return t("watchlist.neverSeen");
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (days <= 0) return t("watchlist.seenToday");
  return t("watchlist.seenDays", { days });
}

export async function loadWatchlist() {
  const list = document.getElementById("watchlistList");
  const empty = document.getElementById("watchlistEmpty");
  if (!list) return;
  let data;
  try {
    data = await api("/api/watchlist");
  } catch {
    return;
  }
  const companies = data.companies || [];
  const toggle = document.getElementById("watchlistToggle");
  const pill = document.getElementById("watchlistTogglePill");
  if (toggle) toggle.checked = !!data.enabled;
  // The switch only means something once there is something to include.
  if (pill) pill.hidden = companies.length === 0;

  list.innerHTML = "";
  if (empty) empty.style.display = companies.length ? "none" : "block";
  for (const c of companies) {
    const item = document.createElement("div");
    item.className = "watchlist-item" + (c.active ? "" : " is-paused");
    item.innerHTML =
      `<label class="watchlist-active"><input type="checkbox" data-toggle="${c.id}"${c.active ? " checked" : ""} /></label>` +
      `<span class="watchlist-main"><strong>${escapeHtml(c.name)}</strong>` +
      `<span class="watchlist-sub">${escapeHtml(_relativeSeen(c.last_seen_at))}</span></span>` +
      `<button type="button" class="ghost-btn small" data-del="${c.id}" aria-label="${escapeHtml(t("watchlist.remove"))}"><span class="material-symbols-outlined">delete</span></button>`;
    list.appendChild(item);
  }
  list.querySelectorAll("[data-toggle]").forEach((el) => {
    el.addEventListener("change", async () => {
      try {
        await api(`/api/watchlist/${el.dataset.toggle}/active`, {
          method: "POST",
          body: JSON.stringify({ active: el.checked }),
        });
        loadWatchlist();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  });
  list.querySelectorAll("[data-del]").forEach((el) => {
    el.addEventListener("click", async () => {
      try {
        await api(`/api/watchlist/${el.dataset.del}`, { method: "DELETE" });
        loadWatchlist();
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  });

  const box = document.getElementById("watchlistSuggestions");
  if (box) {
    // Already-followed companies are filtered out server-side, on the canonical
    // name — repeating a looser check here would only re-offer what it misses.
    box.innerHTML = "";
    for (const name of data.suggestions || []) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "watchlist-chip";
      chip.textContent = name;
      chip.addEventListener("click", () => follow(name));
      box.appendChild(chip);
    }
  }
}

async function follow(name) {
  const clean = String(name || "").trim();
  if (!clean) return;
  try {
    await api("/api/watchlist", { method: "POST", body: JSON.stringify({ name: clean }) });
    showToast(t("watchlist.added", { name: clean }), "info");
    loadWatchlist();
  } catch (err) {
    showToast(`${t("toast.actionError")}: ${err.message}`, "error");
  }
}

export function initWatchlist() {
  const input = document.getElementById("watchlistInput");
  const addBtn = document.getElementById("watchlistAddBtn");
  const submit = () => {
    if (!input) return;
    follow(input.value);
    input.value = "";
  };
  if (addBtn) addBtn.addEventListener("click", submit);
  if (input) {
    input.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      submit();
    });
  }
  const toggle = document.getElementById("watchlistToggle");
  if (toggle) {
    toggle.addEventListener("change", async () => {
      try {
        await api("/api/preferences", {
          method: "POST",
          body: JSON.stringify({
            key: "watchlist_enabled",
            value: toggle.checked ? "1" : "0",
          }),
        });
      } catch (err) {
        showToast(`${t("toast.actionError")}: ${err.message}`, "error");
      }
    });
  }
}
