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

// Which affordance a queued row shows depends on the body-reading mode, and
// the row renderer has no status object of its own.
let _bodyMode = "ask";

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
  // Both Microsoft modes need an app id and the device-code flow; only the
  // scope and the transport differ, so they share the same block.
  const oauth = ["graph", "imap_oauth"].includes($("mailAuth")?.value);
  $("mailPasswordBlock")?.classList.toggle("hidden", oauth);
  $("mailGraphBlock")?.classList.toggle("hidden", !oauth);
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
  _bodyMode = status.body_mode || "ask";
  // Only while the user has not touched it. Since the mailbox got its own tab
  // this function also runs on every visit, and it was overwriting a choice
  // made a second earlier — so the next save posted the old value back.
  const bodySelect = $("mailBodyMode");
  if (bodySelect && bodySelect.dataset.touched !== "1") bodySelect.value = _bodyMode;
  _toggleAuthBlocks();

  const key = _STATE_KEYS[status.state] || "mail.state.unconfigured";
  const kind = status.state === "ok" ? "ok" : status.state === "unconfigured" ? "info" : "warn";
  const pending = status.pending_count
    ? ` · ${t("mail.pendingCount").replace("{n}", String(status.pending_count))}`
    : "";
  _setState(t(key) + pending, kind);
  // The API has always answered with review_count and nobody read it, so the
  // only way to learn there were proposals waiting was to open Settings and
  // scroll to the bottom of the third card.
  setMailBadge(status.review_count);
  await loadMailReview();
}

/** Proposals waiting for an answer, on the nav tab. */
export function setMailBadge(count) {
  const badge = $("mailNavBadge");
  if (!badge) return;
  const n = Number(count) || 0;
  badge.textContent = String(n);
  badge.classList.toggle("hidden", n === 0);
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
  // Resolving or dismissing a proposal reloads this list, so the badge follows
  // the queue without a second request.
  setMailBadge(items.length);
  // One CARD per message. It used to be a two-column grid whose children were
  // the radio options themselves, so with six candidates the first option
  // landed to the right of the company name and the rest snaked across both
  // columns — unreadable exactly when there was most to read. And four
  // confirmations from the same agency on the same day rendered as four
  // identical blocks with nothing saying they were different messages.
  const seq = new Map();
  for (const item of items) {
    const key = `${item.company}|${(item.received_at || "").slice(0, 10)}`;
    seq.set(key, (seq.get(key) || 0) + 1);
  }
  const seen = new Map();
  list.innerHTML = items
    .map((item) => {
      const day = (item.received_at || "").slice(0, 10);
      const key = `${item.company}|${day}`;
      const total = seq.get(key) || 1;
      const n = (seen.get(key) || 0) + 1;
      seen.set(key, n);
      const which =
        total > 1
          ? `<span class="mail-review-seq">${escapeHtml(
              t("mail.review.messageOf").replace("{n}", String(n)).replace("{total}", String(total)),
            )}</span>`
          : "";
      const from = item.sender ? ` · ${escapeHtml(item.sender)}` : "";
      const option = (value, label, checked = false) => `
        <label class="mail-review-option">
          <input type="radio" name="mrev-${item.review_id}" class="mail-review-pick"
                 data-review="${item.review_id}" value="${value}"${checked ? " checked" : ""} />
          <span>${label}</span>
        </label>`;
      const candidates = (item.candidates || [])
        .map((c) =>
          option(
            String(c.job_id),
            `${escapeHtml(c.titolo || "?")} — ${escapeHtml(c.azienda || "?")}`,
          ),
        )
        .join("");
      // "Record it on its own" is the honest default when nothing matches: on a
      // real queue 38 of 53 attach proposals turned out to be roles the archive
      // had never collected.
      const create = option(
        "create",
        `<span data-i18n="mail.review.createEntry">Record it as a new application</span>`,
        !candidates,
      );
      // Per message, not just "ignore them all": one unanswerable proposal used
      // to force a choice between attaching it to the wrong offer and clearing
      // the whole queue.
      const dismiss = option(
        "dismiss",
        `<span data-i18n="mail.review.dismissOne">Ignore this message</span>`,
      );
      // "Ask" mode: the title lives in the body, and the body is only read for
      // the one message you press this on. On an attach row the title is not a
      // nicety but the whole decision — "Teoresi" with six offers in the
      // archive is unanswerable, "Teoresi · AI Engineer" answers itself.
      const roleBit = item.role
        ? `<span class="mail-review-role">${escapeHtml(item.role)}</span>`
        : _bodyMode === "ask"
          ? `<button type="button" class="ghost-btn small mail-review-role-btn"
                     data-review="${item.review_id}" data-i18n="mail.body.fetchOne">Get the job title</button>`
          : "";
      return `
      <article class="mail-review-card" data-review-row="${item.review_id}">
        <header class="mail-review-head">
          <strong>${escapeHtml(item.company || "?")}</strong>
          <span class="micro">${escapeHtml(day)}${from}</span>
          ${which}
          ${roleBit}
        </header>
        <p class="mail-review-question micro" data-i18n="mail.review.pick">Which offer is this about?</p>
        <div class="mail-review-options">${candidates}${create}${dismiss}</div>
      </article>`;
    })
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
    body_mode: $("mailBodyMode")?.value || "",
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
  // Save first: the flow needs the app id and the chosen mode on the server.
  await _save();
  const start = await api("/api/mail/oauth/start", {
    method: "POST",
    body: JSON.stringify({
      client_id: $("mailClientId")?.value.trim() || "",
      auth: $("mailAuth")?.value || "graph",
    }),
  });
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
  $("mailBodyMode")?.addEventListener("change", (event) => {
    event.currentTarget.dataset.touched = "1";
  });
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

  // One runner for both buttons: the dry run and the real sweep differ by a
  // query parameter and by what the final line says, not by their flow.
  function _runRecovery(dryRun) {
    const days = Number($("mailRecoveryDays")?.value || 90);
    const source = new EventSource(
      `/api/mail/recovery/stream?days=${days}${dryRun ? "&dry_run=1" : ""}`,
    );
    _showOutput(t("mail.recovery.running"));
    source.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      if (data.status === "progress") {
        _showOutput(`${t("mail.recovery.running")} ${data.current}/${data.total}`);
      } else if (data.status === "complete") {
        source.close();
        const truncated = data.truncated ? ` ${t("mail.recovery.truncated")}` : "";
        const key = data.dry_run ? "mail.recovery.counted" : "mail.recovery.done";
        _showOutput(
          t(key)
            .replace("{n}", String(data.proposals ?? 0))
            .replace("{checked}", String(data.checked ?? 0))
            .replace("{days}", String(data.days ?? days)) + truncated,
        );
        if (!data.dry_run) await loadMailReview();
      } else if (data.status === "error") {
        source.close();
        _showOutput(`${t("mail.state.error")}: ${data.error}`);
      }
    };
    source.onerror = () => source.close();
  }

  $("mailRecoveryDryBtn")?.addEventListener("click", () => _runRecovery(true));
  $("mailRecoveryBtn")?.addEventListener("click", () => _runRecovery(false));

  $("mailReviewList")?.addEventListener("click", async (event) => {
    const button = event.target.closest?.(".mail-review-role-btn");
    if (!button) return;
    button.disabled = true;
    try {
      const out = await api(`/api/mail/review/${button.dataset.review}/role`, {
        method: "POST",
        body: "{}",
      });
      if (out.role) await loadMailReview();
      else showToast(t("mail.body.notFound"), "info");
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("mailReviewApplyBtn")?.addEventListener("click", async () => {
    const picked = [...document.querySelectorAll(".mail-review-pick:checked")];
    const attach = picked
      .filter((el) => el.value !== "create" && el.value !== "dismiss")
      .map((el) => ({ review_id: Number(el.dataset.review), job_id: Number(el.value) }));
    const create = picked
      .filter((el) => el.value === "create")
      .map((el) => Number(el.dataset.review));
    const dismiss = picked
      .filter((el) => el.value === "dismiss")
      .map((el) => Number(el.dataset.review));
    if (!attach.length && !create.length && !dismiss.length) return;
    await api("/api/mail/review/resolve", {
      method: "POST",
      body: JSON.stringify({ attach, create, dismiss }),
    });
    showToast(t("toast.mail.applied"), "info");
    await loadMailReview();
    await _deps.loadJobs();
  });

  $("mailReviewDismissBtn")?.addEventListener("click", async () => {
    // Every queued message, not every candidate: the unit the user is dismissing
    // is the message, and one message can offer several offers.
    const all = [...document.querySelectorAll("[data-review-row]")].map((el) =>
      Number(el.dataset.reviewRow),
    );
    await api("/api/mail/review/resolve", {
      method: "POST",
      body: JSON.stringify({ attach: [], dismiss: all }),
    });
    await loadMailReview();
  });
}
