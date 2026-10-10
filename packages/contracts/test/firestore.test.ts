import { describe, it, expect } from "vitest";
import { fsTimeToDate } from "../src/index";
import type {
  Product,
  Supplier,
  Order,
  Delivery,
  InventoryDoc,
  LedgerTxn,
  Location,
  Sale,
  Payment,
  AuditLog,
  Shop,
  MemoryEvent,
  ShopPrefs,
  ShopEvent,
  RuntimeConfig,
} from "../src/index";

// These samples are checked by `tsc --noEmit` (npm run typecheck). The runtime
// assertions keep the field names from spec §5 visible in test output.
const ts = "2026-10-10T06:00:00.000Z";

const ghee = {
  sku: "aavin_ghee_200ml", name: "Aavin ghee 200 ml", brand: "Aavin", unit: "jar", packSizeL: 0.2,
  price: 150, costPrice: 135, reorderThreshold: 4, shelfLifeDays: 180,
  aliases: { ta: ["நெய்"], hi: ["घी"], en: ["ghee"] },
} satisfies Product;

const paneer = {
  sku: "aavin_paneer_200g", name: "Aavin paneer 200 g", brand: "Aavin", unit: "pack", packSizeG: 200,
  price: 90, costPrice: 80, reorderThreshold: 5, shelfLifeDays: 7,
  aliases: { ta: ["பனீர்"], hi: ["पनीर"], en: ["paneer"] },
} satisfies Product;

const supplier = {
  id: "sakthi_vendor", name: "Sakthi vendor", phone: "+910000000000", channel: "whatsapp",
  products: ["sakthi_curd_500ml", "sakthi_curd_1l"],
} satisfies Supplier;

const order = {
  id: "o1", supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "ORDER_PLACED",
  idemKey: "k1", createdAt: ts, updatedAt: ts,
} satisfies Order;

const delivery = {
  id: "d1", orderId: "o1",
  items: [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28, method: "one_by_one", confidence: 0.9 }],
  invoice: null, discrepancies: [{ sku: "sakthi_curd_1l", expected: 30, actual: 28 }], state: "DISCREPANCY",
} satisfies Delivery;

const inventory = {
  sku: "sakthi_curd_1l", onHand: 28, batches: [{ batchId: "b1", qty: 28, expiryDate: "2026-10-17" }], updatedAt: ts,
} satisfies InventoryDoc;

const ledger = {
  id: "t1", sku: "sakthi_curd_1l", delta: 28, reason: "DELIVERY", refType: "delivery", refId: "d1",
  idemKey: "k2", at: ts, actor: "aira",
} satisfies LedgerTxn;

const location = {
  sku: "sakthi_curd_1l", zone: "fridge2/middle/left", confirmedBy: "user", confirmedAt: ts, confidence: 1,
} satisfies Location;

const sale = {
  id: "sl1", items: [{ sku: "sakthi_curd_1l", qty: 2, unitPrice: 60, lineTotal: 120 }], total: 120,
  state: "SALE_COMPLETED", paymentId: "p1", idemKey: "k3", createdAt: ts, completedAt: ts,
} satisfies Sale;

const payment = {
  id: "p1", direction: "in", method: "upi", amount: 120, state: "CONFIRMED",
  evidence: { type: "soundbox", detail: "received rupees 120" }, refType: "sale", refId: "sl1",
} satisfies Payment;

const audit = {
  id: "a1", actor: "owner", action: "sale_pay", refType: "sale", refId: "sl1", outcome: "SALE_COMPLETED", at: ts,
} satisfies AuditLog;

const shop = {
  name: "Murugan Dairy", ownerUid: "u1", city: "Madurai", currency: "INR", timezone: "Asia/Kolkata",
} satisfies Shop;

const memory = { id: "m1", type: "location", text: "curd is in fridge 2", at: ts } satisfies MemoryEvent;

const prefs = {
  speechRate: 1, verbosity: "short", productAliases: {}, supplierAliases: {},
} satisfies ShopPrefs;

const event = {
  topic: "sales", payload: { shop_id: "murugan_dairy", sale_id: "sl1", sku: "sakthi_curd_1l", qty: 2 }, ts,
} satisfies ShopEvent;

const runtime = {
  pointing_model: "gemini-robotics-er-2-preview", whatsapp_enabled: false,
} satisfies RuntimeConfig;

describe("fsTimeToDate", () => {
  it("accepts ISO strings, Firestore Timestamp-like objects and toDate() objects", () => {
    expect(fsTimeToDate(ts).toISOString()).toBe(ts);
    expect(fsTimeToDate({ seconds: 1760076000, nanoseconds: 500_000_000 }).getTime()).toBe(1760076000500);
    const d = new Date(ts);
    expect(fsTimeToDate({ seconds: 0, nanoseconds: 0, toDate: () => d })).toBe(d);
  });
});

describe("Firestore document types (spec §5)", () => {
  it("products support every unit plus packSizeL / packSizeG", () => {
    expect(ghee.unit).toBe("jar");
    expect(paneer.packSizeG).toBe(200);
  });

  it("sample docs keep the spec field names", () => {
    expect(Object.keys(supplier)).toEqual(["id", "name", "phone", "channel", "products"]);
    expect(order.state).toBe("ORDER_PLACED");
    expect(delivery.items[0].countedQty).toBe(28);
    expect(inventory.batches[0].expiryDate).toBe("2026-10-17");
    expect(ledger.reason).toBe("DELIVERY");
    expect(location.confirmedBy).toBe("user");
    expect(sale.completedAt).toBe(ts);
    expect(payment.evidence.type).toBe("soundbox");
    expect(audit.actor).toBe("owner");
    expect(shop.currency).toBe("INR");
    expect(memory.type).toBe("location");
    expect(prefs.verbosity).toBe("short");
    expect(event.topic).toBe("sales");
    expect(runtime.whatsapp_enabled).toBe(false);
  });
});
