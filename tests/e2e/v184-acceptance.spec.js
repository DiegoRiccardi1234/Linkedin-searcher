// Everything v1.8.4 added, pressed rather than assumed.
//
// The audit that produced this release was static: it proved every control was
// wired to an endpoint, which is not the same as proving it does anything when
// you click it. This project has already paid for that difference — the
// end-of-scan audit had green unit tests and was broken at the call site for
// weeks — so the new surface gets driven for real before it ships.

const { test, expect } = require("@playwright/test");

const VIEWS = ["dashboard", "job-search", "jobs", "profile", "settings", "info"];
const WIDTHS = [1024, 1280, 1366, 1920];

async function open(page) {
  await page.addInitScript(() => {
    localStorage.setItem("tutorialSeen", "1");
    localStorage.setItem("language", "en");
  });
  await page.goto("/");
  await expect(page.locator(".brand")).toBeVisible();
}

test("every view opens, with no console error and no sideways scroll", async ({ page }) => {
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await open(page);

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 768 });
    for (const density of ["comfortable", "compact"]) {
      await page.evaluate((d) => document.documentElement.setAttribute("data-density", d), density);
      for (const view of VIEWS) {
        await page.locator(`.topnav .nav-link[data-view="${view}"]`).click();
        await expect(page.locator(`#view-${view}`)).toHaveClass(/is-active/);
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow, `${view} at ${width}px / ${density}`).toBe(0);
      }
    }
  }
  expect(errors, errors.join("\n")).toEqual([]);
});

test("no raw translation key is ever shown, in any of the five languages", async ({ page }) => {
  await open(page);
  for (const lang of ["en", "it", "es", "fr", "de"]) {
    await page.evaluate((l) => localStorage.setItem("language", l), lang);
    await page.reload();
    await expect(page.locator(".brand")).toBeVisible();
    for (const view of VIEWS) {
      await page.locator(`.topnav .nav-link[data-view="${view}"]`).click();
      const raw = await page.evaluate(() =>
        [...document.querySelectorAll("[data-i18n]")]
          .filter((el) => el.textContent.trim() === el.getAttribute("data-i18n"))
          .map((el) => el.getAttribute("data-i18n")),
      );
      expect(raw, `${lang} / ${view}`).toEqual([]);
    }
  }
});

test("the four endpoints that had no button now have one", async ({ page }) => {
  await open(page);

  // Applications CSV — the export of what was SENT, not of the archive.
  await page.locator('.topnav .nav-link[data-view="jobs"]').click();
  const csv = page.locator("#exportApplicationsBtn");
  await expect(csv).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download").catch(() => null),
    csv.click(),
  ]);
  expect(download, "the applications export produced no file").not.toBeNull();

  // Undoing a mailbox marking: present, and hidden until there is one to undo.
  await expect(page.locator("#detailUndoMailBtn")).toHaveCount(1);
  await expect(page.locator("#detailUndoMailBtn")).toBeHidden();

  // Renaming a chat session — the toast has existed in five languages for
  // releases, the button did not.
  await expect(page.locator("#chatSessionRename")).toHaveCount(1);

  // Removing a score verdict lives inside the detail drawer and only appears
  // once a verdict exists, so its absence here is the correct state.
  await expect(page.locator("#scoreFbClear")).toHaveCount(0);
});

test("the mailbox card exposes the window, the dry run and the three body modes", async ({
  page,
}) => {
  await open(page);
  await page.locator('.topnav .nav-link[data-view="settings"]').click();

  await expect(page.locator("#mailRecoveryDryBtn")).toBeVisible();
  await expect(page.locator("#mailRecoveryDays option")).toHaveText(["90", "180", "365"]);

  // toHaveValues is about a select's selection, not about its options.
  const modes = await page
    .locator("#mailBodyMode option")
    .evaluateAll((els) => els.map((el) => el.value));
  expect(modes).toEqual(["never", "ask", "always"]);

  // Every position round-trips to the server, rather than living in the DOM.
  // Asserting the DEFAULT here would be asserting the last run's leftovers: the
  // dev database persists, and "ask is the default" belongs to a unit test with
  // a fresh one. What e2e can prove is that the choice survives a reload — and
  // that it saves at all with no mailbox connected, which is the state someone
  // switching this to "never" is most likely to be in.
  for (const wanted of ["never", "always", "ask"]) {
    await page.locator("#mailBodyMode").selectOption(wanted);
    const saved = page.waitForResponse(
      (r) => r.url().includes("/api/mail/config") && r.request().method() === "POST",
    );
    await page.locator("#mailSaveBtn").click();
    await saved;
    await page.reload();
    await page.locator('.topnav .nav-link[data-view="settings"]').click();
    await expect(page.locator("#mailBodyMode")).toHaveValue(wanted);
  }
});

test("the reminder settings save, and the notification is off until asked for", async ({
  page,
}) => {
  await open(page);
  await page.locator('.topnav .nav-link[data-view="profile"]').click();

  const toggle = page.locator('input[data-feature="reminder_notify"]');
  await expect(toggle).toBeVisible();

  // Both directions, starting from whatever the last run left: the dev database
  // persists, and `setChecked` fires no change event when the box is already in
  // the wanted state, so the sequence has to begin by flipping it.
  const start = await toggle.isChecked();
  for (const wanted of [!start, start]) {
    const saved = page.waitForResponse(
      (r) => r.url().includes("/api/preferences") && r.request().method() === "POST",
    );
    await page.locator('input[data-feature="reminder_notify"]').setChecked(wanted);
    await saved;
    await page.reload();
    await page.locator('.topnav .nav-link[data-view="profile"]').click();
    const after = page.locator('input[data-feature="reminder_notify"]');
    if (wanted) await expect(after).toBeChecked();
    else
      await expect(
        after,
        "an unset flag must read as off, the same way the server reads it",
      ).not.toBeChecked();
  }

  const days = page.locator("#reminderStaleDays");
  await expect(days).toBeVisible();
  // A different value from whatever is there: `change` does not fire when the
  // field is filled with what it already held, and the previous run left one.
  const wanted = (await days.inputValue()) === "21" ? "14" : "21";
  const savedDays = page.waitForResponse(
    (r) => r.url().includes("/api/preferences") && r.request().method() === "POST",
  );
  await days.fill(wanted);
  await days.blur();
  await savedDays;

  await page.reload();
  await page.locator('.topnav .nav-link[data-view="profile"]').click();
  await expect(page.locator("#reminderStaleDays")).toHaveValue(wanted);
});

test("the key diagnostics panel is reachable instead of written into the void", async ({
  page,
}) => {
  await open(page);
  await page.locator('.topnav .nav-link[data-view="settings"]').click();
  const details = page.locator(".keys-status-details");
  await expect(details).toBeVisible();
  await details.locator("summary").click();
  await expect(page.locator("#keysStatus")).toBeVisible();
  await expect(page.locator("#keysStatus")).not.toBeEmpty();
});
