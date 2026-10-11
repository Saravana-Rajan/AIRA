// End-to-end: the real phone app (Vite dev server) in a real browser, talking over a real WebSocket to the
// mock aira-live (backend, AIRA_MOCK=1). The backend scripts the session through its /e2e routes
// (aira_live/devtools/e2e_server.py): a confirm proposal and a live delivery count. The double-tap "yes"
// travels the real path (confirm message -> confirm.resolve -> committer) and the backend's answer comes back
// as the caption. axe checks every screen (no serious or critical WCAG 2.1 A/AA violations), and one test
// runs at 360 px. Run it with `node e2e/run.mjs`; without the backend every test is skipped.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const http = process.env.AIRA_E2E_HTTP;
test.skip(!http, "No mock backend: run `node e2e/run.mjs`, which starts one");

const SURFACE = "AIRA. Double-tap to talk. Long-press to stop.";
const PROMPT = "Sale: 2 × Sakthi curd 1 L — ₹120. Confirm?";

/** axe with WCAG 2.1 A and AA rules; any serious or critical violation fails, with where it is. */
async function expectNoSeriousA11yIssues(page, where) {
  // A card that just appeared is still fading in (the `rise` animation starts at opacity 0); axe reads the
  // colours as they are at that instant. Let the entrance animations finish; the infinite pulses are skipped.
  await page.evaluate(() =>
    Promise.all(
      document.getAnimations()
        .filter((a) => Number.isFinite(a.effect?.getComputedTiming().endTime))
        .map((a) => a.finished.catch(() => {})),
    ),
  );
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(bad, `axe violations on ${where}`).toEqual([]);
}

/** The backend's own view of the session: one phone connected over /ws. */
async function expectOneSession(request) {
  await expect.poll(async () => (await (await request.get(`${http}/e2e/sessions`)).json()).sessions.length).toBe(1);
}

/**
 * Opens the app, taps Start, passes the device check and taps Start talking; resolves once the socket is
 * connected (the chip says Ready). Chromium's fake mic beeps and its fake camera shows a test pattern; the mock
 * backend passes the camera picture without a model (aira_live.device_check, mock mode).
 */
async function start(page, request, checkA11y = false) {
  await page.goto("/");
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Checking your microphone and camera" })).toBeVisible();
  expect((await (await request.get(`${http}/e2e/sessions`)).json()).sessions.length).toBe(0); // no voice yet
  const go = page.getByRole("button", { name: /Start talking/ });
  await expect(go).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.check [role="status"]')).toContainText("Camera works. I can see mock camera.");
  if (checkA11y) await expectNoSeriousA11yIssues(page, "Device check");
  await go.click();
  const chip = page.locator(".status-chip");
  await expect(chip).toHaveAttribute("data-kind", "ready");
  await expect(chip).toHaveText("Ready");
  await expectOneSession(request);
  return page.getByRole("button", { name: SURFACE });
}

test.beforeEach(async ({ page }) => {
  // Keep the run offline and quick: web fonts and the bump guard's detector model are not needed here.
  await page.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
});

test("start, connect, confirm a sale with a double-tap, watch a delivery count, accessible", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "AIRA" })).toBeVisible();
  await expect(page.getByText("Sunrise Dairy")).toBeVisible();
  await expectNoSeriousA11yIssues(page, "Onboarding");

  const surface = await start(page, request, true);
  await expect(surface).toContainText("Double-tap anywhere to talk");
  await expectNoSeriousA11yIssues(page, "Ready screen");

  // The backend proposes a sale: the confirm card asks for a gesture.
  const proposed = await request.post(`${http}/e2e/confirm`, { data: { kind: "sale", prompt: PROMPT } });
  expect(proposed.ok()).toBe(true);
  const { confirmId } = await proposed.json();
  const card = page.locator(".task-card");
  await expect(card).toHaveAttribute("data-confirm", "true");
  await expect(card).toContainText("Confirm sale");
  await expect(card).toContainText(PROMPT);
  await expect(card).toContainText("Double-tap for yes, long-press for no.");
  await expect(page.locator(".status-chip")).toHaveText("Yes or no?");
  await expect(page.locator(".caption--aira")).toContainText(PROMPT);
  await expectNoSeriousA11yIssues(page, "Confirm card");

  // Double-tap = yes. The backend commits it and answers; the card leaves the question state.
  await surface.dblclick({ position: { x: 180, y: 300 } });
  await expect(page.locator(".caption--aira")).toContainText("Done.");
  await expect(card).not.toHaveAttribute("data-confirm", "true");
  await expect(card).toHaveAttribute("data-tone", "ok");
  await expect(card).toContainText("Sale");
  await expect.poll(async () => (await (await request.get(`${http}/e2e/confirm/${confirmId}`)).json()).status).toBe("committed");

  // A delivery count streams in: the chip and the card show progress.
  await request.post(`${http}/e2e/count`, { data: { counted: 12, expected: 30, final: false } });
  const chip = page.locator(".status-chip");
  await expect(chip).toHaveAttribute("data-kind", "counting");
  await expect(chip).toHaveText("Counting 12 / 30");
  await expect(card).toContainText("Delivery");
  await expect(card).toContainText("12 / 30 counted");
  await expect(card.locator(".task-card__fill")).toHaveAttribute("style", /--progress:\s*0\.4/);
  await expectNoSeriousA11yIssues(page, "Counting");

  await request.post(`${http}/e2e/count`, { data: { counted: 30, expected: 30, final: true } });
  await expect(chip).toHaveText("Ready");
  await expect(card).toHaveAttribute("data-tone", "ok");
  await expect(card).toContainText("30 / 30 counted");
});

test("a short count ends in a warning", async ({ page, request }) => {
  await start(page, request);
  await request.post(`${http}/e2e/count`, { data: { counted: 28, expected: 30, final: true } });
  const chip = page.locator(".status-chip");
  await expect(chip).toHaveAttribute("data-kind", "warning");
  await expect(chip).toHaveText("2 short");
  await expect(page.locator(".task-card")).toHaveAttribute("data-tone", "warn");
});

test("fits a 360 px phone without horizontal scroll, accessible", async ({ page, request }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Start" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  await expectNoSeriousA11yIssues(page, "Onboarding (360 px)");

  await start(page, request);
  await request.post(`${http}/e2e/confirm`, { data: { kind: "sale", prompt: PROMPT } });
  const card = page.locator(".task-card");
  await expect(card).toHaveAttribute("data-confirm", "true");

  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  for (const sel of [".status-chip", ".captions", ".task-card"]) {
    const box = await page.locator(sel).boundingBox();
    expect(box, `${sel} is drawn`).not.toBeNull();
    expect(box.x, `${sel} left edge`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${sel} right edge`).toBeLessThanOrEqual(360);
    expect(box.y + box.height, `${sel} bottom edge`).toBeLessThanOrEqual(740);
  }
  await expectNoSeriousA11yIssues(page, "Confirm card (360 px)");
});
