// @vitest-environment node
// The end-to-end smoke test seeds the Firestore emulator with this demo shop, then checks what the
// pages show. Running the same docs through the real selectors here proves, without an emulator,
// that the seed and the texts the e2e test expects agree with each other.
import { describe, expect, it } from "vitest";
import { buildDemoShop, curdInventoryDoc, DEMO_SHOP_ID, loadCatalog, OTHER_SHOP_ID } from "./e2e/demoShop.mjs";
import { normalize, type Normalized } from "../src/lib/normalize";
import { stockRows } from "../src/lib/selectors/stock";
import { todaySummary } from "../src/lib/selectors/today";
import { orderRows } from "../src/lib/selectors/orders";
import { auditRows } from "../src/lib/selectors/audit";
import type { ShopData } from "../src/lib/live";

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
const LATE = Date.UTC(2026, 9, 10, 18, 29, 30); // 2026-10-10 23:59:30 IST

/** What the browser SDK hands the listeners: a Timestamp (toMillis) where the seed wrote a Date. */
function asSdk(v: unknown): unknown {
  if (v instanceof Date) {
    const ms = v.getTime();
    return { toMillis: () => ms };
  }
  if (Array.isArray(v)) return v.map(asSdk);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, asSdk(x)]));
  return v;
}

/** The ShopData the dashboard would build for one shop from the seeded docs. */
function shopData(nowMs: number, shopId: string = DEMO_SHOP_ID): ShopData {
  const out: Record<string, Normalized<Record<string, unknown>>[]> = {
    products: [], inventory: [], sales: [], payments: [], orders: [], deliveries: [], suppliers: [], auditLogs: [],
  };
  for (const d of buildDemoShop(nowMs).docs) {
    const parts = d.path.split("/");
    if (parts[0] !== "shops" || parts[1] !== shopId || parts.length !== 4) continue;
    out[parts[2]]?.push(normalize(parts[3], asSdk(d.data) as Record<string, unknown>));
  }
  return out as unknown as ShopData;
}

describe("demo shop seed for the e2e smoke test", () => {
  const { docs, expected } = buildDemoShop(NOW);
  const data = shopData(NOW);

  it("writes only full document paths, under the demo shop and one other shop", () => {
    for (const d of docs) {
      const parts = d.path.split("/");
      expect(parts.length % 2, d.path).toBe(0);
      expect(parts[0]).toBe("shops");
      expect([DEMO_SHOP_ID, OTHER_SHOP_ID]).toContain(parts[1]);
    }
    expect(DEMO_SHOP_ID).toBe("sunrise_dairy");
    expect(new Set(docs.map((d) => d.path)).size).toBe(docs.length);
  });

  it("uses the real Sunrise Dairy catalog for names, units and prices", () => {
    const catalog = loadCatalog();
    const curd = catalog.products.find((p) => p.sku === "sakthi_curd_1l")!;
    const seeded = docs.find((d) => d.path === `shops/${DEMO_SHOP_ID}/products/sakthi_curd_1l`)!;
    expect(seeded.data.name).toBe(curd.name);
    expect(seeded.data.unit).toBe(curd.unit);
    expect(seeded.data.price).toBe(curd.price);
    expect(expected.shopName).toBe(catalog.shop.name);
  });

  it("matches the demo story on the Stock page: 28 L curd expiring tomorrow, ice cream low", () => {
    const rows = stockRows(data.products, data.inventory, NOW);
    const curd = rows.find((r) => r.name === expected.curd.name)!;
    expect(curd.onHandText).toBe(expected.curd.onHandText);
    expect(expected.curd.onHandText).toBe("28 L");
    expect(curd.expiryText).toBe(expected.curd.expiryText);
    expect(expected.curd.expiryText).toBe("Expires tomorrow");
    expect(curd.lowStock).toBe(false);
    const low = rows.find((r) => r.name === expected.lowStock.name)!;
    expect(low.lowStock).toBe(true);
    expect(rows.filter((r) => r.lowStock).map((r) => r.name)).toEqual([expected.lowStock.name]);
  });

  it("matches the Today page: a cash sale and a UPI sale, totals from catalog prices", () => {
    const t = todaySummary(data.sales, data.payments, data.products, NOW);
    expect(t.salesCount).toBe(expected.salesCount);
    expect(t.revenueText).toBe(expected.revenueText);
    expect(t.byMethodText.cash).toBe(expected.cashText);
    expect(t.byMethodText.upi).toBe(expected.upiText);
    expect(t.topProduct).toBe(expected.topProduct);
    expect(t.byMethod.upi).toBeGreaterThan(0);
    expect(t.byMethod.cash + t.byMethod.upi).toBe(t.revenue);
  });

  it("still counts both sales as today when the test runs just before IST midnight", () => {
    const t = todaySummary(shopData(LATE).sales, shopData(LATE).payments, shopData(LATE).products, LATE);
    expect(t.revenueText).toBe(buildDemoShop(LATE).expected.revenueText);
  });

  it("matches the Orders page: the Sakthi delivery is 2 L short and says so in words", () => {
    const rows = orderRows(data.orders, data.deliveries, data.suppliers, data.products);
    expect(rows).toHaveLength(1);
    const o = rows[0];
    expect(o.supplierName).toBe(expected.supplierName);
    expect(o.isDiscrepancy).toBe(true);
    expect(o.stateLabel).toBe("Discrepancy");
    expect(o.discrepancyTexts).toEqual([expected.discrepancyText]);
    expect(expected.discrepancyText).toBe(`${expected.curd.name}: counted 28 L of 30 L ordered`);
    expect(o.vendorReply).toBe(expected.vendorReply);
  });

  it("matches the Audit page: actions newest first, in plain words", () => {
    expect(auditRows(data.auditLogs).map((r) => r.actionText)).toEqual(expected.auditActions);
  });

  it("puts a product in another shop that the dashboard must never show", () => {
    const other = shopData(NOW, OTHER_SHOP_ID);
    expect(other.products.map((p) => p.name)).toEqual([expected.otherShopProduct]);
    expect(data.products.map((p) => p.name)).not.toContain(expected.otherShopProduct);
  });

  it("writes time fields the way aira-live does: sale times as Timestamps, the rest as ISO strings", () => {
    const sale = docs.find((d) => d.path.includes("/sales/"))!;
    expect(sale.data.completedAt).toBeInstanceOf(Date);
    const pay = docs.find((d) => d.path.includes("/payments/"))!;
    expect(typeof pay.data.createdAt).toBe("string");
    const audit = docs.find((d) => d.path.includes("/auditLogs/"))!;
    expect(typeof audit.data.at).toBe("string");
  });

  it("builds a curd stock change for the realtime check", () => {
    const d = curdInventoryDoc(NOW, 27);
    expect(d.path).toBe(`shops/${DEMO_SHOP_ID}/inventory/sakthi_curd_1l`);
    expect(d.data.onHand).toBe(27);
    const rows = stockRows(data.products, [normalize("sakthi_curd_1l", asSdk(d.data) as Record<string, unknown>)], NOW);
    expect(rows.find((r) => r.name === expected.curd.name)!.onHandText).toBe("27 L");
  });
});
