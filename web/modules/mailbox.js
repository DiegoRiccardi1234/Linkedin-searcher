// The settings card for the mailbox: connect it, prove it works, and see what
// it would do before letting it do anything.
//
// The order of the buttons is the order of trust. Save and test come first, then
// "test recognition", which runs the rules over real mail and reports what it
// WOULD have marked without writing a thing, and only then the checks that
// actually change the archive.

import { api, escapeHtml, showToast } from "./helpers.js";
import { t, applyTranslations } from "./i18n.js";

let _deps = {
  loadJobs: async () => {},
};

export function initMailbox(deps) {
  _deps = { ..._deps, ...deps };
}

const $ = (id) => document.getElementById(id);

function _setState(text, kind = "info") {
  const el = $("mailState");
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind;
}

function _showOutput(text) {
  const el = $("mailOutput");
  if (!el) return;
  el.hidden = false;
  el.classList.remove("hidden");
  el.textContent = text;
}

function _toggleAuthBlocks() {
  const graph = $("mailAuth")?.value === "graph";
  $("mailPasswordBlock")?.classList.toggle("hidden", graph);
  $("mailGraphBlock")?.classList.toggle("hidden", !graph);
}

const _STATE_KEYS = {
  ok: "mail.state.ok",
  unconfigured: "mail.state.unconfigured",
  reauth_required: "mail.state.reauthRequired",
  auth_failed: "mail.state.authFailed",
  error: "mail.state.error",
};

export async function loadMailboxStatus() {
  let status;
  try {
    status = await api("/api/mail/status");
  } catch {
    return; // a diagnostic panel must never break the settings page
  }
  const card = $("mailboxCard");
  if (card) card.dataset.state = status.configured ? "configured" : "empty";
  if ($("mailAddress") && !$("mailAddress").value) $("mailAddress").value = status.address || "";
  if ($("mailAuth") && status.auth) $("mailAuth").value = status.auth;
  if ($("mailHost") && !$("mailHost").value) $("mailHost").value = status.host || "";
  if ($("mailFolder") && !$("mailFolder").value) $("mailFolder").value = status.folder || "INBOX";
  if ($("mailEnabled")) $("mailEnabled").checked = Boolean(status.enabled);
  if ($("mailInterval")) $("mailInterval").value = status.interval_minutes || 15;
  _toggleAuthBlocks();

  const key = _STATE_KEYS[status.state] || "mail.state.unconfigured";
  const kind = status.state === "ok" ? "ok" : status.state === "unconfigured" ? "info" : "warn";
  const pending = status.pending_count
    ? ` · ${t("mail.pendingCount").replace("{n}", String(status.pending_count))}`
    : "";
  _setState(t(key) + pending, kind);
  await loadMailReview();
}

export async function loadMailReview() {
  const box = $("mailReviewBox");
  const list = $("mailReviewList");
  if (!box || !list) return;
  let items = [];
  try {
    items = (await api("/api/mail/review")).items || [];
  } catch {
    return;
  }
  box.classList.toggle("hidden", items.length === 0);
  list.innerHTML = items
    .map(
      (item) => `
      <label class="mail-review-row">
        <input type="checkbox" class="mail-review-pick" value="${item.job_id}" />
        <span class="mail-review-job">${escapeHtml(item.job_title || "?")} — ${escapeHtml(item.company || "?")}</span>
        <span class="micro">${escapeHtml(item.subject || "")} · ${escapeHtml(item.from_domain || "")}</span>
      </label>`,
    )
    .join("");
  applyTranslations(box);
}

async function _save() {
  const payload = {
    address: $("mailAddress")?.value.trim() || "",
    auth: $("mailAuth")?.value || "password",
    host: $("mailHost")?.value.trim() || "",
    folder: $("mailFolder")?.value.trim() || "INBOX",
    enabled: Boolean($("mailEnabled")?.checked),
    interval_minutes: Number($("mailInterval")?.value || 15),
  };
  // Empty means "leave what is stored" here, not "delete it": a user reopening
  // settings must not wipe the password by pressing Save.
  const secret = $("mailSecret")?.value || "";
  if (secret) payload.secret = secret;
  const clientId = $("mailClientId")?.value.trim() || "";
  if (clientId) payload.client_id = clientId;

  await api("/api/mail/config", { method: "POST", body: JSON.stringify(payload) });
  if ($("mailSecret")) $("mailSecret").value = "";
  showToast(t("toast.mail.saved"), "info");
  await loadMailboxStatus();
}

async function _connectMicrosoft() {
  const start = await api("/api/mail/oauth/start", { method: "POST", body: "{}" });
  const box = $("mailDeviceCode");
  if (box) box.classList.remove("hidden");
  if ($("mailUserCode")) $("mailUserCode").textContent = start.user_code || "";
  const link = $("mailVerifyLink");
  if (link) {
    link.href = start.verification_uri || "";
    link.textContent = start.verification_uri || "";
  }
  const deadline = Date.now() + (start.expires_in || 900) * 1000;
  const interval = Math.max(3, Number(start.interval || 5)) * 1000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, interval));
    let poll;
    try {
      poll = await api("/api/mail/oauth/poll", { method: "POST", body: "{}" });
    } catch {
      break;
    }
    if (poll.status === "complete") {
      box?.classList.add("hidden");
      showToast(t("toast.mail.connected"), "info");
      await loadMailboxStatus();
      return;
    }
    if (poll.status === "failed") break;
  }
  box?.classList.add("hidden");
  _setState(t("mail.state.error"), "warn");
}

export function wireMailbox() {
  $("mailAuth")?.addEventListener("change", _toggleAuthBlocks);

  $("mailSaveBtn")?.addEventListener("click", async () => {
    try {
      await _save();
    } catch (error) {
      _setState(`${t("mail.state.error")}: ${error.message}`, "warn");
    }
  });

  $("mailTestBtn")?.addEventListener("click", async () => {
    _setState(t("mail.testing"), "info");
    try {
      const out = await api("/api/mail/test", { method: "POST", body: "{}" });
      _setState(t(_STATE_KEYS[out.state] || "mail.state.error"), out.ok ? "ok" : "warn");
    } catch (error) {
      _setState(`${t("mail.testFailed")}: ${error.message}`, "warn");
    }
  });

  $("mailDisconnectBtn")?.addEventListener("click", async () => {
    if (!window.confirm(t("mail.disconnectConfirm"))) return;
    await api("/api/mail/disconnect", { method: "POST", body: "{}" });
    if ($("mailSecret")) $("mailSecret").value = "";
    showToast(t("toast.mail.disconnected"), "info");
    await loadMailboxStatus();
  });

  $("mailConnectBtn")?.addEventListener("click", async () => {
    try {
      await _connectMicrosoft();
    } catch (error) {
      _setState(`${t("mail.state.error")}: ${error.message}`, "warn");
    }
  });

  $("mailDryRunBtn")?.addEventListener("click", async () => {
    _showOutput(t("mail.checking"));
    try {
      const out = await api("/api/mail/dry-run", { method: "POST", body: "{}" });
      _showOutput(
        t("mail.dryRunResult")
          .replace("{checked}", String(out.checked ?? 0))
          .replace("{matched}", String(out.matched ?? 0))
          .replace("{ambiguous}", String(out.ambiguous ?? 0)),
      );
      await loadMailReview();
    } catch (error) {
      _showOutput(`${t("mail.state.error")}: ${error.message}`);
    }
  });

  $("mailCheckBtn")?.addEventListener("click", async () => {
    try {
      await api("/api/mail/check", { method: "POST", body: "{}" });
      _showOutput(t("mail.checking"));
      // The check runs in the background; give it a moment, then re-read.
      setTimeout(async () => {
        await loadMailboxStatus();
        await _deps.loadJobs();
      }, 4000);
    } catch (error) {
      _showOutput(`${t("mail.state.error")}: ${error.message}`);
    }
  });

  $("mailRecoveryBtn")?.addEventListener("click", () => {
    const source = new EventSource("/api/mail/recovery/stream?days=90");
    _showOutput(t("mail.recovery.running"));
    source.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      if (data.status === "progress") {
        _showOutput(`${t("mail.recovery.running")} ${data.current}/${data.total}`);
      } else if (data.status === "complete") {
        source.close();
        const truncated = data.truncated ? ` ${t("mail.recovery.truncated")}` : "";
        _showOutput(
          t("mail.recovery.done").replace("{n}", String(data.proposals ?? 0)) + truncated,
        );
        await loadMailReview();
      } else if (data.status === "error") {
        source.close();
        _showOutput(`${t("mail.state.error")}: ${data.error}`);
      }
    };
    source.onerror = () => source.close();
  });

  $("mailReviewApplyBtn")?.addEventListener("click", async () => {
    const picked = [...document.querySelectorAll(".mail-review-pick:checked")].map((el) =>
      Number(el.value),
    );
    if (!picked.length) return;
    await api("/api/mail/review/resolve", {
      method: "POST",
      body: JSON.stringify({ apply: picked, dismiss: [] }),
    });
    showToast(t("toast.mail.applied"), "info");
    await loadMailReview();
    await _deps.loadJobs();
  });

  $("mailReviewDismissBtn")?.addEventListener("click", async () => {
    const all = [...document.querySelectorAll(".mail-review-pick")].map((el) => Number(el.value));
    await api("/api/mail/review/resolve", {
      method: "POST",
      body: JSON.stringify({ apply: [], dismiss: all }),
    });
    await loadMailReview();
  });
}
