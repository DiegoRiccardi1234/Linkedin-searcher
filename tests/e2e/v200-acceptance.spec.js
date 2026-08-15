// Everything v2.0.0 changed, pressed rather than assumed.
//
// Same discipline as the 1.8.4 acceptance run: a control wired to an endpoint
// is not a control that works when you click it. Three of these tests cover the
// failure that made this release necessary — an empty search form used to mean
// "search for what the author of this app was looking for" — and the rest cover
// the surfaces that moved.

const { test, expect } = require("@playwright/test");

const VIEWS = ["dashboard", "job-search", "jobs", "mail", "profile", "settings", "info"];
const WIDTHS = [1024, 1280, 1366, 1920];
const DENSITIES = ["xcompact", "compact", "normal", "comfortable", "large", "xlarge"];

async function open(page) {
  await page.addInitScript(() => {
    localStorage.setItem("tutorialSeen", "1");
    localStorage.setItem("language", "en");
    // Only on the first load of this tab: the reload below is testing that a
    // remembered tab survives one, and wiping it on every navigation would
    // make that test pass for the wrong reason — or fail for one.
    if (!sessionStorage.getItem("e2eStarted")) {
      sessionStorage.setItem("e2eStarted", "1");
      localStorage.removeItem("subtab:settings");
      localStorage.removeItem("subtab:profile");
      localStorage.removeItem("jobsBucket");
    }
  });
  await page.goto("/");
  await expect(page.locator(".brand")).toBeVisible();
}

test("every view opens at every width and density, without sideways scroll", async ({ page }) => {
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await open(page);

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 768 });
    for (const density of DENSITIES) {
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

test("the density menu offers only densities that do something", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.locator('[data-subtabs="settings"] [data-subtab="appearance"]').click();
  const select = page.locator("#densitySelect");
  for (const value of DENSITIES) {
    await select.selectOption(value);
    // Two of the four options used to be accepted by the menu and refused by
    // the code, which silently painted "normal" instead.
    await expect(page.locator("html")).toHaveAttribute("data-density", value);
  }
});

test("the archive's tabs slice the archive and say how much is on screen", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='jobs']").click();

  const tabs = page.locator("#jobBuckets .bucket-tab");
  await expect(tabs).toHaveCount(5);
  await expect(page.locator('#jobBuckets .bucket-tab[data-bucket="to_review"]')).toHaveClass(
    /is-active/,
  );

  // Counts come from the server, one query for all five.
  const counts = await page.evaluate(async () => (await (await fetch("/api/jobs/counts")).json()).counts);
  expect(Object.keys(counts).sort()).toEqual(
    ["all", "applied", "archived", "rejected", "to_review"].sort(),
  );

  await page.locator('#jobBuckets .bucket-tab[data-bucket="all"]').click();
  await expect(page.locator("#jobsShownOf")).not.toBeEmpty();
});

test("a filtered page is not cut by the row cap", async ({ page }) => {
  await open(page);
  // The measured regression: the flag filter ran after the LIMIT, so the same
  // question answered differently depending on how many rows were asked for.
  const [small, large] = await page.evaluate(async () => {
    const ask = async (limit) =>
      (await (await fetch(`/api/jobs?bucket=to_review&applicable_only=true&limit=${limit}`)).json())
        .jobs.length;
    return [await ask(5), await ask(2000)];
  });
  expect(small).toBe(Math.min(5, large));
});

test("the mailbox has its own tab, with the queue in it", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='mail']").click();
  await expect(page.locator("#view-mail #mailboxCard")).toHaveCount(1);
  // It used to live at the bottom of the third card in Settings.
  await expect(page.locator("#view-settings #mailboxCard")).toHaveCount(0);
});

test("sub-tabs show one panel at a time and survive a reload", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator('[data-subtabs-panels="settings"] .subtab-panel.is-active')).toHaveCount(
    1,
  );
  await page.locator('[data-subtabs="settings"] [data-subtab="system"]').click();
  await expect(page.locator("#settings-panel-system")).toHaveClass(/is-active/);

  await page.reload();
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await expect(page.locator("#settings-panel-system")).toHaveClass(/is-active/);
});

test("reaching a card inside a closed sub-tab still works", async ({ page }) => {
  await open(page);
  // Every "go to Settings and scroll to the provider cards" in the app breaks
  // silently the moment its target sits in a tab nobody opened.
  await page.locator(".topnav .nav-link[data-view='settings']").click();
  await page.locator('[data-subtabs="settings"] [data-subtab="system"]').click();
  await page.locator(".topnav .nav-link[data-view='dashboard']").click();

  await page.evaluate(() => window.revealElement("providerCards"));
  await expect(page.locator("#view-settings")).toHaveClass(/is-active/);
  await expect(page.locator("#providerCards")).toBeVisible();
});

test("the profile says what is missing, and each gap leads to its field", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='profile']").click();
  await expect(page.locator('[data-readiness-group="target"]')).not.toBeEmpty();

  const report = await page.evaluate(async () =>
    (await fetch("/api/profile/readiness")).json(),
  );
  expect(Array.isArray(report.blocking)).toBe(true);
  // Only two things can ever block: what to search for, and where.
  for (const id of report.blocking) expect(["search_terms", "location"]).toContain(id);
});

test("a scan with nothing of yours to search for asks instead of guessing", async ({ page }) => {
  await open(page);
  // Straight at the stream, so the answer is the server's and not the form's.
  const first = await page.evaluate(async () => {
    const resp = await fetch("/api/scan/stream?sites=linkedin");
    const reader = resp.body.getReader();
    const { value } = await reader.read();
    await reader.cancel();
    return new TextDecoder().decode(value);
  });
  // Either it refuses (a profile with nothing stored) or it starts a real scan
  // (a profile that has said what it wants). What it must never do is fall back
  // to the app's own terms.
  if (first.includes("error")) {
    expect(first).toContain("missing_essentials");
  } else {
    expect(first).not.toContain("AI QA");
  }
});

test("the coach knows which page the question came from", async ({ page }) => {
  await open(page);
  const settings = await page.evaluate(async () =>
    (await (await fetch("/api/chat/prompts?lang=en&view=settings")).json()).prompts,
  );
  const mail = await page.evaluate(async () =>
    (await (await fetch("/api/chat/prompts?lang=en&view=mail")).json()).prompts,
  );
  expect(settings[0]).not.toBe(mail[0]);
});

test("the mail filter is an origin, and composes with the tabs", async ({ page }) => {
  await open(page);
  // Not a sixth tab: those partition the archive by state and their counts add
  // up to the total. Where a row came from is a different question.
  const [all, fromMail] = await page.evaluate(async () => {
    const counts = async (query) =>
      (await (await fetch(`/api/jobs/counts${query}`)).json()).counts;
    return [await counts(""), await counts("?from_mail=true")];
  });
  for (const bucket of Object.keys(all)) {
    expect(fromMail[bucket]).toBeLessThanOrEqual(all[bucket]);
  }
  await page.locator(".topnav .nav-link[data-view='jobs']").click();
  await expect(page.locator("#fromMail")).toBeVisible();
});

test("the review queue is one card per message, each answerable on its own", async ({ page }) => {
  await open(page);
  await page.locator(".topnav .nav-link[data-view='mail']").click();
  const cards = page.locator(".mail-review-card");
  const count = await cards.count();
  test.skip(count === 0, "no proposals queued in this database");

  // The layout this replaces was a two-column grid whose children were the
  // options, so the first one sat next to the company name.
  const first = cards.first();
  await expect(first.locator(".mail-review-options")).toHaveCount(1);
  // Ignoring one unanswerable proposal used to mean clearing the whole queue.
  await expect(first.locator('input[value="dismiss"]')).toHaveCount(1);
  await expect(first.locator('input[value="create"]')).toHaveCount(1);
});
