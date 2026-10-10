// The demo shop the end-to-end smoke test seeds into the Firestore emulator, and the texts the pages
// must then show. Plain ESM so Node, Playwright and Vitest all load it without a build step.
//
// Product names, units and prices come from content/catalog/murugan_dairy.json (demo prices, not
// real MRP). Time fields follow aira-live: sale times are server Timestamps (written here as Date),
// payment, order and audit times are ISO strings (now_iso()).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const DEMO_SHOP_ID = "murugan_dairy";
/** A second shop the dashboard must never show, whatever the URL asks for. */
export const OTHER_SHOP_ID = "someone_else";

const CATALOG_URL = new URL("../../../../content/catalog/murugan_dairy.json", import.meta.url);
const IST_OFFSET_MS = 330 * 60_000;
const DAY_MS = 86_400_000;

const CURD = "sakthi_curd_1l";
const MILK = "aavin_milk_500ml";
const ICE_CREAM = "arun_icecream_box";

export function loadCatalog() {
  return JSON.parse(readFileSync(fileURLToPath(CATALOG_URL), "utf8"));
}

/** "YYYY-MM-DD" of the IST calendar day offsetDays after the one containing nowMs. */
function istDate(nowMs, offsetDays) {
  return new Date(nowMs + IST_OFFSET_MS + offsetDays * DAY_MS).toISOString().slice(0, 10);
}

/** Integer rupees with Indian grouping, written independently of the app's formatter. */
function rupees(n) {
  return `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(n)}`;
}

function product(catalog, sku) {
  const p = catalog.products.find((x) => x.sku === sku);
  if (!p) throw new Error(`The catalog has no product ${sku}`);
  return p;
}

/** shops/murugan_dairy/inventory/sakthi_curd_1l: one batch that expires tomorrow (IST). */
export function curdInventoryDoc(nowMs, onHand) {
  return {
    path: `shops/${DEMO_SHOP_ID}/inventory/${CURD}`,
    data: {
      sku: CURD,
      onHand,
      batches: [{ batchId: "e2e_curd_batch", qty: onHand, expiryDate: istDate(nowMs, 1) }],
      updatedAt: new Date(nowMs).toISOString(),
    },
  };
}

/**
 * The demo story: 28 L of Sakthi curd after a delivery that came 2 L short of the 30 L ordered,
 * a cash sale of milk and a UPI sale of ice cream (confirmed by the soundbox), and ice cream low.
 */
export function buildDemoShop(nowMs, catalog = loadCatalog()) {
  const shop = (rest) => `shops/${DEMO_SHOP_ID}/${rest}`;
  const iso = (ms) => new Date(ms).toISOString();
  // Seconds before now, never before IST midnight, so "Today" holds both sales at any hour.
  const startOfDay = Math.floor((nowMs + IST_OFFSET_MS) / DAY_MS) * DAY_MS - IST_OFFSET_MS;
  const ago = (ms) => Math.max(startOfDay, nowMs - ms);

  const curd = product(catalog, CURD);
  const milk = product(catalog, MILK);
  const iceCream = product(catalog, ICE_CREAM);
  const supplier = catalog.suppliers.find((s) => s.products.includes(CURD));
  if (!supplier) throw new Error(`The catalog has no supplier for ${CURD}`);

  // One litre of milk (two 500 ml pouches; prices are per litre for unit L) and one ice cream box.
  const milkLine = { sku: MILK, qty: 1, unitPrice: milk.price, lineTotal: milk.price };
  const iceLine = { sku: ICE_CREAM, qty: 1, unitPrice: iceCream.price, lineTotal: iceCream.price };

  const { phoneEnv: _phoneEnv, aliases: _aliases, ...supplierDoc } = supplier;
  const { id: _shopId, ...shopDoc } = catalog.shop;

  const docs = [
    { path: `shops/${DEMO_SHOP_ID}`, data: { ...shopDoc, ownerUid: "" } },
    ...[curd, milk, iceCream].map((p) => ({ path: shop(`products/${p.sku}`), data: { ...p } })),
    curdInventoryDoc(nowMs, 28),
    {
      path: shop(`inventory/${MILK}`),
      data: { sku: MILK, onHand: 20, batches: [{ batchId: "e2e_milk_batch", qty: 20, expiryDate: istDate(nowMs, 2) }], updatedAt: iso(nowMs) },
    },
    { path: shop(`inventory/${ICE_CREAM}`), data: { sku: ICE_CREAM, onHand: 2, batches: [], updatedAt: iso(nowMs) } },
    // Supplier phone numbers are never stored in seeds (the catalog reads them from env).
    { path: shop(`suppliers/${supplier.id}`), data: { ...supplierDoc, phone: "" } },
    {
      path: shop("orders/e2e_order_1"),
      data: {
        id: "e2e_order_1", supplierId: supplier.id, items: [{ sku: CURD, qty: 30 }], state: "DISCREPANCY",
        vendorReply: "OK, delivery at 6 pm", idemKey: "e2e-order-1", createdAt: iso(ago(3 * 3_600_000)), updatedAt: iso(nowMs),
      },
    },
    {
      path: shop("deliveries/e2e_delivery_1"),
      data: {
        id: "e2e_delivery_1", orderId: "e2e_order_1",
        items: [{ sku: CURD, expectedQty: 30, countedQty: 28, method: "one_by_one", confidence: 0.93 }],
        invoice: null, discrepancies: [{ sku: CURD, expected: 30, actual: 28 }], state: "DISCREPANCY",
      },
    },
    {
      path: shop("sales/e2e_sale_1"),
      data: {
        id: "e2e_sale_1", items: [milkLine], total: milkLine.lineTotal, state: "SALE_COMPLETED", paymentId: "e2e_pay_1",
        idemKey: "e2e-sale-1", createdAt: new Date(ago(120_000)), completedAt: new Date(ago(90_000)),
      },
    },
    {
      path: shop("sales/e2e_sale_2"),
      data: {
        id: "e2e_sale_2", items: [iceLine], total: iceLine.lineTotal, state: "SALE_COMPLETED", paymentId: "e2e_pay_2",
        idemKey: "e2e-sale-2", createdAt: new Date(ago(60_000)), completedAt: new Date(ago(30_000)),
      },
    },
    {
      path: shop("payments/e2e_pay_1"),
      data: {
        id: "e2e_pay_1", direction: "in", method: "cash", amount: milkLine.lineTotal, state: "CONFIRMED",
        evidence: { type: "cash_count", detail: `counted ${milkLine.lineTotal} rupees` }, refType: "sale", refId: "e2e_sale_1",
        createdAt: iso(ago(100_000)), updatedAt: iso(ago(90_000)),
      },
    },
    {
      path: shop("payments/e2e_pay_2"),
      data: {
        id: "e2e_pay_2", direction: "in", method: "upi", amount: iceLine.lineTotal, state: "CONFIRMED",
        evidence: { type: "soundbox", detail: `received rupees ${iceLine.lineTotal}` }, refType: "sale", refId: "e2e_sale_2",
        createdAt: iso(ago(40_000)), updatedAt: iso(ago(30_000)),
      },
    },
    {
      path: shop("auditLogs/e2e_audit_1"),
      data: { id: "e2e_audit_1", actor: "aira", action: "sale_completed", refType: "sale", refId: "e2e_sale_2", outcome: "ok", at: iso(ago(30_000)) },
    },
    {
      path: shop("auditLogs/e2e_audit_2"),
      data: { id: "e2e_audit_2", actor: "owner", action: "order_confirmed", refType: "order", refId: "e2e_order_1", outcome: "ok", at: iso(ago(3 * 3_600_000)) },
    },
    // Another shop's data: rules deny reading it, and the dashboard must never ask for it.
    { path: `shops/${OTHER_SHOP_ID}`, data: { name: "Another shop", ownerUid: "", city: "Chennai", currency: "INR", timezone: "Asia/Kolkata" } },
    {
      path: `shops/${OTHER_SHOP_ID}/products/other_product`,
      data: { sku: "other_product", name: "Other shop product (must stay hidden)", brand: "Other", unit: "pack", price: 1, costPrice: 1, reorderThreshold: 0, shelfLifeDays: 1 },
    },
  ];

  const revenue = milkLine.lineTotal + iceLine.lineTotal;
  const top = milkLine.lineTotal >= iceLine.lineTotal ? milk : iceCream;
  return {
    docs,
    expected: {
      shopName: catalog.shop.name,
      curd: { name: curd.name, onHandText: "28 L", expiryText: "Expires tomorrow" },
      lowStock: { name: iceCream.name },
      salesCount: 2,
      revenueText: rupees(revenue),
      cashText: rupees(milkLine.lineTotal),
      upiText: rupees(iceLine.lineTotal),
      topProduct: top.name,
      supplierName: supplier.name,
      discrepancyText: `${curd.name}: expected 30, got 28`,
      vendorReply: "OK, delivery at 6 pm",
      auditActions: ["Sale completed", "Order confirmed"],
      otherShopProduct: "Other shop product (must stay hidden)",
    },
  };
}
