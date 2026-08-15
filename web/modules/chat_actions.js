// What the coach can do to the app, and the button that has to be pressed first.
//
// There was one action, FILL_SCAN_FORM, and it applied itself: the answer
// arrived, the search form was rewritten and the view jumped. Useful when the
// model got it right, startling when it did not. There are four now, and none
// of them happens without a click — the model proposes, the user decides, the
// browser writes. The server drops any type or profile field it does not
// recognise before it ever reaches this file.

import { showToast } from "./helpers.js";
import { t } from "./i18n.js";

let _deps = {};

export function initChatActions(deps) {
  _deps = { ..._deps, ...deps };
}

function _describe(action) {
  const list = (values) => (values || []).join(", ");
  switch (action.type) {
    case "FILL_SCAN_FORM":
      return t("chatAction.fillScan", {
        terms: list(action.keywords) || "—",
        places: list(action.locations) || "—",
      });
    case "ADD_ROLES":
      return t("chatAction.addRoles", {
        roles: (action.roles || []).map((r) => r.label).join(", "),
      });
    case "SET_PROFILE_FIELD":
      return t("chatAction.setField", {
        field: t(`readiness.item.${_fieldToItem(action.field)}`) || action.field,
        value: action.label || String(action.value),
      });
    case "OPEN_JOB":
      return t("chatAction.openJob");
    default:
      return "";
  }
}

//: The envelope names the field, the checklist names the item; they overlap
//: but are not the same vocabulary.
function _fieldToItem(field) {
  return { base_cities: "work_rule", work_modes: "work_rule", min_ral: "ral_min" }[field] || field;
}

async function _apply(action) {
  switch (action.type) {
    case "FILL_SCAN_FORM": {
      const { getKeywords, getLocations, activateView } = _deps;
      getKeywords?.addMultiple(action.keywords || []);
      getLocations?.addMultiple(action.locations || []);
      const countrySel = document.getElementById("scanCountry");
      if (countrySel && action.country) {
        const cv = String(action.country).toLowerCase();
        if ([...countrySel.options].some((o) => o.value === cv)) {
          countrySel.value = cv;
          localStorage.setItem("scanCountry", cv);
        }
      }
      const remote = document.getElementById("remoteToggle");
      if (remote && typeof action.is_remote === "boolean") remote.checked = action.is_remote;
      activateView?.("job-search");
      return true;
    }
    case "ADD_ROLES": {
      const roles = (action.roles || []).map((r) => r.label).filter(Boolean);
      const keywords = (action.roles || []).flatMap((r) => r.keywords || [r.label]);
      await _deps.addRoles?.(roles, keywords);
      return true;
    }
    case "SET_PROFILE_FIELD": {
      const body = {};
      if (action.field === "min_ral" || action.field === "goal") {
        // These two live in the onboarding preferences, not on the profile.
        await _deps.savePreference?.(
          action.field === "min_ral" ? "onboarding_ral_min" : "onboarding_goal",
          String(action.value),
        );
      } else {
        body[action.field] = action.value;
        await _deps.patchProfile?.(body);
      }
      _deps.invalidateReadiness?.();
      await _deps.renderReadinessStrips?.();
      return true;
    }
    case "OPEN_JOB":
      await _deps.showJobDetail?.(action.job_id);
      return true;
    default:
      return false;
  }
}

/** Render the proposal under an assistant message. Nothing runs until clicked. */
export function renderChatAction(container, action) {
  if (!container || !action || !action.type) return;
  const text = _describe(action);
  if (!text) return;
  const card = document.createElement("div");
  card.className = "chat-action-card";
  card.innerHTML = `
    <p class="chat-action-text"></p>
    <div class="chat-action-buttons">
      <button type="button" class="primary" data-chat-action-apply>${t("chatAction.apply")}</button>
      <button type="button" class="secondary" data-chat-action-dismiss>${t("chatAction.dismiss")}</button>
    </div>`;
  card.querySelector(".chat-action-text").textContent = text;
  card.querySelector("[data-chat-action-apply]").addEventListener("click", async () => {
    try {
      const done = await _apply(action);
      if (done) {
        card.innerHTML = `<p class="chat-action-done">${t("chatAction.done")}</p>`;
        showToast(t("chatAction.done"), "info");
      }
    } catch (error) {
      showToast(`${t("chatAction.failed")}: ${error.message}`, "error");
    }
  });
  card.querySelector("[data-chat-action-dismiss]").addEventListener("click", () => card.remove());
  container.appendChild(card);
}
