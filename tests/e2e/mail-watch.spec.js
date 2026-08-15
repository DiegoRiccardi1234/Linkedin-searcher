const { test, expect } = require("@playwright/test");

// The one thing this feature must never do is break the link it listens to.
// Everything else about recognising a confirmation is covered by unit tests;
// what no unit test can see is whether the browser still opens the posting
// after a click handler was added in front of it.

test("opening a posting still opens a tab, and starts the wait", async ({ page, context }) => {
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(String(err)));

  await page.goto("/");
  const created = await page.evaluate(async () => {
    const response = await fetch("/api/jobs/manual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        titolo: "E2E link tracking probe",
        azienda: "Playwright",
        // Points at the app itself so the new tab needs no network.
        link: `${location.origin}/api/health`,
        sede: "Torino",
        descrizione: "Offerta di prova per il tracciamento del link.",
      }),
    });
    return (await response.json()).job_id;
  });

  try {
    // Let bootstrap finish first: reloading over in-flight requests aborts them,
    // and an aborted fetch surfaces as a console error that is about the test,
    // not about the app.
    await page.waitForLoadState("networkidle");
    await page.reload();
    await page.waitForLoadState("networkidle");
    // The jobs table is written inside the dashboard markup but reparented into
    // #view-jobs at runtime, so it is only visible from the Jobs tab.
    await page.locator(".topnav .nav-link[data-view='jobs']").click();
    const link = page.locator(`a[data-job-link="${created}"]`).first();
    await expect(link).toBeVisible();

    const [opened, request] = await Promise.all([
      context.waitForEvent("page"),
      page.waitForRequest((r) => r.url().includes(`/api/jobs/${created}/link-opened`)),
      link.click(),
    ]);
    expect(request.method()).toBe("POST");
    await opened.close();

    // The wait is recorded, and the list says so.
    await expect
      .poll(async () =>
        page.evaluate(async (id) => {
          const jobs = (await (await fetch("/api/jobs")).json()).jobs;
          return Boolean(jobs.find((j) => j.id === id)?.link_opened_at);
        }, created),
      )
      .toBe(true);

    // Let bootstrap finish first: reloading over in-flight requests aborts them,
    // and an aborted fetch surfaces as a console error that is about the test,
    // not about the app.
    await page.waitForLoadState("networkidle");
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.locator(".topnav .nav-link[data-view='jobs']").click();
    await expect(page.locator(".job-flag", { hasText: "hourglass_top" }).first()).toBeVisible();
  } finally {
    await page.evaluate(
      (id) => fetch(`/api/jobs/${id}`, { method: "DELETE" }),
      created,
    );
  }

  expect(consoleErrors, `console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
});

test("the mail tab exposes the mailbox card and its status contract", async ({ page }) => {
  await page.goto("/");
  // The mailbox has its own tab since 2.0.0: deciding what you applied to is
  // not a setting, and the review queue was nine lines at the bottom of a
  // 116-line card, three cards down a seven-screen page.
  await page.locator(".topnav .nav-link[data-view='mail']").click();
  await expect(page.locator("#view-mail")).toHaveClass(/is-active/);
  await expect(page.locator("#mailboxCard")).toHaveCount(1);
  // Disconnected is the only honest default: nothing is read until connected.
  await expect(page.locator("#mailboxCard")).toHaveAttribute("data-state", "empty");

  const status = await page.evaluate(async () => (await fetch("/api/mail/status")).json());
  expect(status).toMatchObject({ configured: false, state: "unconfigured" });
  // A credential must never travel in a status response.
  expect(JSON.stringify(status)).not.toContain("secret");
});
