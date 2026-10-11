// End-to-end smoke test: the real dashboard (Vite dev server) against the Firestore emulator,
// seeded with the Sunrise Dairy demo story, checked with axe (no serious or critical WCAG 2.1 A/AA
// violations). Run it with `node test/e2e/run.mjs`; without an emulator every test is skipped.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { buildDemoShop, curdInventoryDoc } from "./demoShop.mjs";
import { clearDatabase, commitDocs, E2E_DATABASE, E2E_PROJECT } from "./firestoreRest.mjs";

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
test.skip(!emulatorHost, "No Firestore emulator: run `node test/e2e/run.mjs`, which starts one when Java is available");

const target = { emulatorHost, projectId: E2E_PROJECT, databaseId: E2E_DATABASE };
/** @type {import("./demoShop.mjs").DemoExpected} */
let expected;

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** axe with WCAG 2.1 A and AA rules; any serious or critical violation fails, with where it is. */
async function expectNoSeriousA11yIssues(page, where) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} (${v.impact}): ${v.help} at ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(bad, `axe violations on ${where}`).toEqual([]);
}

/** The page is showing server data: the header says "Live" in words. */
async function expectLive(page) {
  await expect(page.getByRole("status").filter({ hasText: /^Live$/ })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  // Every test starts from the same demo shop.
  await clearDatabase(target);
  const shop = buildDemoShop(Date.now());
  expected = shop.expected;
  await commitDocs({ ...target, docs: shop.docs });
  // Keep the run offline and quick: web fonts are not needed to check the content.
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.abort());
});

test("demo shop: stock, today, orders, audit and forecast, live from Firestore and accessible", async ({ page }) => {
  await page.goto("?demo=1#/stock");
  await expect(page.getByRole("heading", { level: 1, name: expected.shopName })).toBeVisible();
  await expect(page.getByText("Demo shop.")).toBeVisible();
  await expectLive(page);

  const stock = page.getByRole("table", { name: /stock on hand/i });
  await expect(stock.getByRole("row", { name: new RegExp(escape(expected.lowStock.name)) })).toContainText("Low stock");
  const curd = stock.getByRole("row", { name: new RegExp(escape(expected.curd.name)) });
  await expect(curd).toContainText(expected.curd.onHandText);
  await expect(curd).toContainText(expected.curd.expiryText);
  await expectNoSeriousA11yIssues(page, "Stock");

  await page.getByRole("navigation", { name: "Dashboard sections" }).getByRole("link", { name: "Today", exact: true }).click();
  const today = page.getByRole("region", { name: "Today" });
  const figure = (label) => today.locator("div").filter({ has: page.locator("dt", { hasText: label }) }).locator("dd");
  await expect(figure("Revenue")).toHaveText(expected.revenueText);
  await expect(figure("Sales")).toHaveText(String(expected.salesCount));
  await expect(figure("Top product")).toHaveText(expected.topProduct);
  await expect(figure("Cash received")).toHaveText(expected.cashText);
  await expect(figure("UPI received")).toHaveText(expected.upiText);
  await expectNoSeriousA11yIssues(page, "Today");

  await page.getByRole("link", { name: "Orders & deliveries", exact: true }).click();
  const order = page.getByRole("article", { name: expected.supplierName });
  await expect(order.getByText("Discrepancy", { exact: true })).toBeVisible();
  await expect(order.getByRole("list", { name: "Discrepancies" })).toHaveText(expected.discrepancyText);
  await expect(order).toContainText(expected.vendorReply);
  await expectNoSeriousA11yIssues(page, "Orders & deliveries");

  await page.getByRole("link", { name: "Audit trail", exact: true }).click();
  const audit = page.getByRole("table", { name: /recent actions/i });
  for (const action of expected.auditActions) await expect(audit).toContainText(action);
  await expectNoSeriousA11yIssues(page, "Audit trail");

  await page.getByRole("link", { name: "Forecast", exact: true }).click();
  await expect(page.getByText("Forecast unavailable")).toBeVisible();
  await expectNoSeriousA11yIssues(page, "Forecast");
});

test("a signed-out visitor cannot pick another shop with ?shop=", async ({ page }) => {
  await page.goto("?shop=someone_else#/stock");
  await expect(page.getByRole("heading", { level: 1, name: expected.shopName })).toBeVisible();
  await expect(page.getByText("Demo shop.")).toBeVisible();
  const stock = page.getByRole("table", { name: /stock on hand/i });
  await expect(stock).toContainText(expected.curd.name);
  await expect(page.getByText(expected.otherShopProduct)).toHaveCount(0);
});

test("stock changes show up without a reload", async ({ page }) => {
  await page.goto("#/stock");
  await expectLive(page);
  const curd = page.getByRole("table", { name: /stock on hand/i }).getByRole("row", { name: new RegExp(escape(expected.curd.name)) });
  await expect(curd).toContainText(expected.curd.onHandText);
  await commitDocs({ ...target, docs: [curdInventoryDoc(Date.now(), 27)] });
  await expect(curd).toContainText("27 L");
});

test("overview on a phone-width screen is accessible", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("");
  await expectLive(page);
  await expect(page.getByRole("table", { name: /stock on hand/i })).toBeVisible();
  await expect(page.getByRole("region", { name: "Orders & deliveries" })).toContainText(expected.discrepancyText);
  await expect(page.getByText("Forecast unavailable")).toBeVisible();
  await expectNoSeriousA11yIssues(page, "Overview (phone)");
});
