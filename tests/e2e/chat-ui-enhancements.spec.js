const { test, expect } = require("@playwright/test");

async function setLang(page, lang) {
  await page.evaluate(async (l) => {
    localStorage.setItem("language", l);
    await fetch("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "ui_language", value: l }),
    });
  }, lang);
}

test.describe("Chat UI enhancements", () => {
  test("localized quick prompts honour ?lang=it", async ({ page }) => {
    await page.goto("/");
    await setLang(page, "it");

    const payload = await page.evaluate(async () => {
      const res = await fetch("/api/chat/prompts?lang=it");
      return res.json();
    });
    expect(Array.isArray(payload.prompts)).toBeTruthy();
    expect(payload.prompts.length).toBeGreaterThan(0);
    // At least one Italian word should appear.
    const joined = payload.prompts.join(" ").toLowerCase();
    expect(joined).toMatch(/\b(quali|ruoli|mio|cv|lavori|profilo)\b/);
  });

  test("coach bubbles render markdown bold/italic/list + role pills", async ({ page }) => {
    await page.route("**/api/chat", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "default",
          answer:
            "Ecco alcuni ruoli adatti a te:\n- **Machine Learning Engineer**: ottimo per il tuo *Python*\n- **Data Engineer**: match sul tuo SQL",
          action: null,
          suggested_roles: [
            { label: "Machine Learning Engineer", keywords: ["Machine Learning Engineer", "ML Engineer"] },
            { label: "Data Engineer", keywords: ["Data Engineer"] },
          ],
        }),
      });
    });

    await page.goto("/");
    await page.evaluate(() => {
      if (typeof appendChat === "function") {
        // no-op — make sure function exists
      }
    });

    await page.fill("#chatInput", "mi interessa l'IA");
    await page.locator("#chatForm button[type='submit']").click();

    const bubble = page.locator(".chat-item.assistant .bubble").last();
    await expect(bubble).toBeVisible({ timeout: 5000 });
    await expect(bubble.locator("strong.role-name").first()).toHaveText(/Machine Learning Engineer/);
    await expect(bubble.locator("em.coach-hint").first()).toBeVisible();
    await expect(bubble.locator("ul li")).toHaveCount(2);

    const pills = page.locator(".chat-item.assistant .role-pill");
    await expect(pills).toHaveCount(2);
    await expect(pills.first()).toHaveText(/Machine Learning Engineer/);
  });

  test("clicking a role pill adds keywords to Step 2 and persists", async ({ page }) => {
    const shortlistCalls = [];
    await page.route("**/api/roles/shortlist", async (route) => {
      const req = route.request();
      if (req.method() === "GET") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ roles: [] }) });
      } else if (req.method() === "POST") {
        shortlistCalls.push(JSON.parse(req.postData() || "{}"));
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ roles: JSON.parse(req.postData() || "{}").roles || [] }),
        });
      } else {
        await route.continue();
      }
    });
    await page.route("**/api/chat", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "default",
          answer: "Prova **Cloud Engineer**.",
          action: null,
          suggested_roles: [{ label: "Cloud Engineer", keywords: ["Cloud Engineer", "AWS"] }],
        }),
      });
    });

    await page.goto("/");
    await page.fill("#chatInput", "voglio fare cloud");
    await page.locator("#chatForm button[type='submit']").click();
    // The pill handler loads the profile BEFORE it saves, so the POST lands
    // some milliseconds after the click resolves. Reading `shortlistCalls`
    // straight after the click measured that race, not the feature — and this
    // test had been red since before 07/08 for exactly that reason, unnoticed
    // because the e2e suite does not run in CI.
    const shortlistPost = page.waitForRequest(
      (r) => r.url().includes("/api/roles/shortlist") && r.method() === "POST",
    );
    await page.locator(".role-pill").first().click();
    // Read the payload off the request itself: the route callback that fills
    // `shortlistCalls` is not guaranteed to have run when waitForRequest
    // resolves, so asserting on the closure is a second race on top of the first.
    const sent = JSON.parse((await shortlistPost).postData() || "{}");
    expect(sent.roles).toEqual(expect.arrayContaining(["Cloud Engineer"]));
    await expect.poll(() => shortlistCalls.length).toBeGreaterThan(0);

    // Tag added to Step 2 keywords input
    await expect
      .poll(async () =>
        (
          await page.locator("#keywordsContainer .tag-item, #keywordsContainer .tag").allTextContents()
        ).join(" "),
      )
      .toMatch(/Cloud Engineer/);
  });

  test("coach panel resizes with viewport", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/");
    const wide = await page.locator("#coachPanel").evaluate((el) => el.getBoundingClientRect().width);

    await page.setViewportSize({ width: 1100, height: 900 });
    const narrow = await page.locator("#coachPanel").evaluate((el) => el.getBoundingClientRect().width);

    expect(wide).toBeGreaterThan(narrow);
  });
});
