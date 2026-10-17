import { describe, it, expect } from "vitest";
import { ORDER_STATES } from "@aira/contracts";
import { stockRows } from "../src/lib/selectors/stock";
import { todaySummary } from "../src/lib/selectors/today";
import { ORDER_FLOW, orderRows, splitOrders } from "../src/lib/selectors/orders";
import { auditRows } from "../src/lib/selectors/audit";

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
const START = Date.UTC(2026, 9, 9, 18, 30); // 2026-10-10 00:00 IST
const DAY = 86_400_000;

const products: any[] = [
  { id: "sakthi_curd_1l", sku: "sakthi_curd_1l", name: "Sakthi curd 1 L", brand: "Sakthi", unit: "L", price: 60, reorderThreshold: 5 },
  { id: "aavin_milk_500ml", sku: "aavin_milk_500ml", name: "Aavin milk 500 ml", brand: "Aavin", unit: "pack", price: 25, reorderThreshold: 10 },
  { id: "arun_icecream_box", sku: "arun_icecream_box", name: "Arun ice cream box", brand: "Arun", unit: "box", price: 180, reorderThreshold: 3 },
  { id: "new_sku", sku: "new_sku", name: "Aavin ghee 200 ml", brand: "Aavin", unit: "pack", price: 150, reorderThreshold: 2 },
];
const inventory: any[] = [
  { id: "sakthi_curd_1l", onHand: 28, batches: [{ batchId: "b1", qty: 22, expiryDate: "2026-10-14" }, { batchId: "b2", qty: 6, expiryDate: "2026-10-11" }] },
  { id: "aavin_milk_500ml", onHand: 40, batches: [{ batchId: "m1", qty: 0, expiryDate: "2026-10-09" }, { batchId: "m2", qty: 40, expiryDate: "2026-10-12" }] },
  { id: "arun_icecream_box", onHand: 2, batches: [] },
];

describe("stockRows", () => {
  const rows = stockRows(products, inventory, NOW);
  it("treats a product with no inventory doc as 0 and low stock", () => {
    const r = rows.find((x) => x.sku === "new_sku")!;
    expect(r.onHand).toBe(0);
    expect(r.onHandText).toBe("0 packs");
    expect(r.lowStock).toBe(true);
    expect(r.daysToExpiry).toBeNull();
    expect(r.expiryText).toBeNull();
  });
  it("flags low stock at or below the reorder threshold", () => {
    expect(rows.find((x) => x.sku === "arun_icecream_box")!.lowStock).toBe(true);
    expect(rows.find((x) => x.sku === "sakthi_curd_1l")!.lowStock).toBe(false);
  });
  it("uses the nearest expiry among batches that still have stock", () => {
    const curd = rows.find((x) => x.sku === "sakthi_curd_1l")!;
    expect(curd.daysToExpiry).toBe(1);
    expect(curd.expiryText).toBe("Expires tomorrow");
    const milk = rows.find((x) => x.sku === "aavin_milk_500ml")!;
    expect(milk.daysToExpiry).toBe(2); // the empty, already-expired batch m1 is ignored
  });
  it("sorts low stock first, then soonest expiry, then name", () => {
    expect(rows.map((r) => r.sku)).toEqual(["new_sku", "arun_icecream_box", "sakthi_curd_1l", "aavin_milk_500ml"]);
  });
  it("names jars as jars (catalog unit 'jar')", () => {
    const jarRows = stockRows(
      [{ id: "aavin_ghee_200ml", name: "Aavin ghee 200 ml", unit: "jar", reorderThreshold: 2 } as any],
      [{ id: "aavin_ghee_200ml", onHand: 3, batches: [] } as any],
      NOW,
    );
    expect(jarRows[0].onHandText).toBe("3 jars");
    expect(stockRows(
      [{ id: "g", name: "G", unit: "jar", reorderThreshold: 0 } as any],
      [{ id: "g", onHand: 1, batches: [] } as any],
      NOW,
    )[0].onHandText).toBe("1 jar");
  });
  it("survives an inventory doc with no batches field and a non-numeric onHand", () => {
    const r = stockRows(
      [{ id: "x", name: "X", unit: "pack", reorderThreshold: 1 } as any],
      [{ id: "x", onHand: "oops" } as any],
      NOW,
    )[0];
    expect(r).toMatchObject({ onHand: 0, onHandText: "0 packs", lowStock: true, daysToExpiry: null });
  });
});

describe("todaySummary", () => {
  const sales: any[] = [
    { id: "s1", state: "SALE_COMPLETED", completedAt: START + 3_600_000, total: 50, items: [{ sku: "aavin_milk_500ml", qty: 2, unitPrice: 25, lineTotal: 50 }] },
    { id: "s2", state: "SALE_COMPLETED", completedAt: START + 7_200_000, total: 180, items: [{ sku: "arun_icecream_box", qty: 1, unitPrice: 180, lineTotal: 180 }] },
    { id: "s3", state: "SALE_PENDING", completedAt: null, total: 60, items: [{ sku: "sakthi_curd_1l", qty: 1, unitPrice: 60, lineTotal: 60 }] },
    { id: "s4", state: "SALE_COMPLETED", completedAt: START - 60_000, total: 999, items: [{ sku: "sakthi_curd_1l", qty: 1, unitPrice: 999, lineTotal: 999 }] }, // 23:59 IST yesterday
  ];
  const payments: any[] = [
    { id: "p1", direction: "in", method: "cash", amount: 50, state: "CONFIRMED", createdAt: START + 3_600_000 },
    { id: "p2", direction: "in", method: "upi", amount: 180, state: "CONFIRMED", createdAt: START + 7_200_000 },
    { id: "p3", direction: "in", method: "upi", amount: 60, state: "PENDING", createdAt: START + 7_300_000 },
    { id: "p4", direction: "out", method: "cash", amount: 1000, state: "CONFIRMED", createdAt: START + 1_000 },
  ];
  it("counts only sales COMPLETED within today's IST day", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.salesCount).toBe(2);
    expect(s.revenue).toBe(230);
    expect(s.revenueText).toBe("₹230");
  });
  it("picks the top product by revenue and names it", () => {
    expect(todaySummary(sales, payments, products, NOW).topProduct).toBe("Arun ice cream box");
  });
  it("sums only confirmed incoming payments by method", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.byMethod).toEqual({ cash: 50, upi: 180 });
    expect(s.byMethodText).toEqual({ cash: "₹50", upi: "₹180" });
  });
  it("returns zeros on an empty day", () => {
    const s = todaySummary([], [], products, NOW);
    expect(s).toMatchObject({
      salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null,
      byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" },
    });
  });
  it("uses the IST day at both edges: 23:50 IST counts for that day, 00:10 IST the next day does not", () => {
    const edge: any[] = [
      { id: "late", state: "SALE_COMPLETED", completedAt: START + DAY - 10 * 60_000, total: 25, items: [] },
      { id: "next", state: "SALE_COMPLETED", completedAt: START + DAY + 10 * 60_000, total: 40, items: [] },
    ];
    const lateEvening = START + DAY - 60_000; // 23:59 IST
    expect(todaySummary(edge, [], products, lateEvening)).toMatchObject({ salesCount: 1, revenue: 25 });
    expect(todaySummary(edge, [], products, START + DAY + 20 * 60_000)).toMatchObject({ salesCount: 1, revenue: 40 });
  });
  it("reads ISO-string times and falls back to createdAt when completedAt is missing", () => {
    const iso: any[] = [
      { id: "a", state: "SALE_COMPLETED", completedAt: "2026-10-10T05:00:00Z", total: 30, items: [] },
      { id: "b", state: "SALE_COMPLETED", createdAt: "2026-10-10T04:00:00Z", total: 20, items: [] },
    ];
    expect(todaySummary(iso, [], products, NOW)).toMatchObject({ salesCount: 2, revenue: 50 });
  });
  it("counts a payment without createdAt when it belongs to a sale completed today", () => {
    const todaySales: any[] = [
      { id: "s9", state: "SALE_COMPLETED", completedAt: START + 1_000, total: 75, items: [], paymentId: "pay_by_id" },
      { id: "s10", state: "SALE_COMPLETED", completedAt: START + 2_000, total: 40, items: [] },
      { id: "old", state: "SALE_COMPLETED", completedAt: START - DAY, total: 500, items: [] },
    ];
    const pays: any[] = [
      { id: "pay_by_id", direction: "in", method: "upi", amount: 75, state: "CONFIRMED", refType: "other", refId: "x" },
      { id: "pay_by_ref", direction: "in", method: "cash", amount: 40, state: "CONFIRMED", refType: "sale", refId: "s10" },
      { id: "pay_old", direction: "in", method: "cash", amount: 500, state: "CONFIRMED", refType: "sale", refId: "old" },
      { id: "pay_orphan", direction: "in", method: "cash", amount: 9, state: "CONFIRMED", refType: "sale", refId: "nope" },
    ];
    expect(todaySummary(todaySales, pays, products, NOW).byMethod).toEqual({ cash: 40, upi: 75 });
  });
  it("averages the day's sales and counts the top product's units", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.avgText).toBe("₹115");
    expect(s.topProductQty).toBe("1 box");
  });
  it("lists today's sales newest first with IST times, item lines and the payment method", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.recent.map((r) => r.id)).toEqual(["s2", "s1"]);
    expect(s.recent[0]).toEqual({ id: "s2", time: "2:00 am", items: "1 × Arun ice cream box", amountText: "₹180", method: "pending" });
    expect(s.recent[1]).toMatchObject({ time: "1:00 am", items: "2 × Aavin milk 500 ml", amountText: "₹50" });
  });
  it("maps a sale to its payment by paymentId or by the payment's sale reference, else Pending", () => {
    const todaySales: any[] = [
      { id: "s9", state: "SALE_COMPLETED", completedAt: START + 1_000, total: 75, items: [], paymentId: "pay_by_id" },
      { id: "s10", state: "SALE_COMPLETED", completedAt: START + 2_000, total: 40, items: [] },
      { id: "s11", state: "SALE_COMPLETED", completedAt: START + 3_000, total: 10, items: [] },
    ];
    const pays: any[] = [
      { id: "pay_by_id", direction: "in", method: "upi", amount: 75, state: "CONFIRMED", refType: "other", refId: "x" },
      { id: "pay_by_ref", direction: "in", method: "cash", amount: 40, state: "CONFIRMED", refType: "sale", refId: "s10" },
      { id: "pay_pending", direction: "in", method: "upi", amount: 10, state: "PENDING", refType: "sale", refId: "s11" },
    ];
    const s = todaySummary(todaySales, pays, products, NOW);
    expect(s.recent.map((r) => [r.id, r.method])).toEqual([["s11", "pending"], ["s10", "cash"], ["s9", "upi"]]);
  });
  it("keeps the items line short and the table to 20 rows", () => {
    const many: any[] = Array.from({ length: 25 }, (_, i) => ({
      id: `m${i}`, state: "SALE_COMPLETED", completedAt: START + 1_000 * (i + 1), total: 25,
      items: [{ sku: "aavin_milk_500ml", qty: 2, lineTotal: 50 }, { sku: "sakthi_curd_1l", qty: 1, lineTotal: 60 }, { sku: "arun_icecream_box", qty: 3, lineTotal: 540 }],
    }));
    const s = todaySummary(many, [], products, NOW);
    expect(s.recent).toHaveLength(20);
    expect(s.recent[0].id).toBe("m24");
    expect(s.recent[0].items.length).toBeLessThanOrEqual(60);
    expect(s.recent[0].items.endsWith("…")).toBe(true);
  });
  it("has no average, no top product units and no rows on an empty day", () => {
    expect(todaySummary([], [], products, NOW)).toMatchObject({ avgText: "—", topProductQty: null, recent: [] });
  });
});

describe("orderRows", () => {
  const suppliers: any[] = [{ id: "sakthi_vendor", name: "Sakthi vendor" }];
  const orders: any[] = [
    { id: "o1", supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "DISCREPANCY", vendorReply: { text: "OK, delivery at 6 pm", at: "2026-10-10T12:30:00Z", channel: "whatsapp" }, updatedAt: Date.UTC(2026, 9, 10, 12, 35) },
    { id: "o2", supplierId: "missing_vendor", items: [{ sku: "aavin_milk_500ml", qty: 20 }], state: "ORDER_PLACED", updatedAt: Date.UTC(2026, 9, 10, 4, 0) },
  ];
  const deliveries: any[] = [
    { id: "d1", orderId: "o1", discrepancies: [{ sku: "sakthi_curd_1l", expected: 30, actual: 28 }, { field: "total", expected: 1500, actual: 1600 }] },
  ];
  const rows = orderRows(orders, deliveries, suppliers, products, NOW);

  it("ORDER_FLOW only uses states defined in the contracts", () => {
    for (const s of ORDER_FLOW) expect(ORDER_STATES).toContain(s);
    expect(ORDER_STATES).toContain("DISCREPANCY");
    expect(ORDER_FLOW).not.toContain("DISCREPANCY");
  });
  it("labels a discrepancy in words and lists every mismatch", () => {
    const r = rows.find((x) => x.id === "o1")!;
    expect(r.isDiscrepancy).toBe(true);
    expect(r.stateLabel).toBe("Discrepancy");
    expect(r.stepIndex).toBe(ORDER_FLOW.indexOf("DELIVERY_RECONCILED"));
    expect(r.discrepancyTexts).toEqual(["Sakthi curd 1 L: expected 30, got 28", "total: expected 1500, got 1600"]);
    expect(r.vendorReply).toBe("OK, delivery at 6 pm");
    expect(r.itemsText).toBe("Sakthi curd 1 L: 30 L");
    expect(r.supplierName).toBe("Sakthi vendor");
  });
  it("sorts newest first and survives an unknown supplier", () => {
    expect(rows.map((x) => x.id)).toEqual(["o1", "o2"]);
    const r = rows.find((x) => x.id === "o2")!;
    expect(r.supplierName).toBe("missing_vendor");
    expect(r.vendorReply).toBeNull();
    expect(r.stepIndex).toBe(0);
    expect(r.isDiscrepancy).toBe(false);
    expect(r.discrepancyTexts).toEqual([]);
  });
  it("shows the update time in IST and sorts ISO-string times too", () => {
    expect(rows.find((x) => x.id === "o1")!.updatedText).toBe("6:05 pm");
    const isoRows = orderRows(
      [
        { id: "old", supplierId: "sakthi_vendor", items: [], state: "CLOSED", updatedAt: "2026-10-09T10:00:00Z" },
        { id: "new", supplierId: "sakthi_vendor", items: [], state: "DRAFT", updatedAt: "2026-10-10T10:00:00Z" },
      ] as any[],
      [], suppliers, products,
    );
    expect(isoRows.map((x) => x.id)).toEqual(["new", "old"]);
    expect(isoRows[0].stepIndex).toBe(-1); // a DRAFT has not reached the first step yet
    expect(isoRows[1].stepIndex).toBe(ORDER_FLOW.indexOf("CLOSED"));
  });
  it("reads the vendor reply object the order flow saves, and names from the order when docs are missing", () => {
    // kanish/02 saves vendorReply as { text, at, channel } and copies supplierName / item names onto the order.
    const [r] = orderRows(
      [{
        id: "o3", supplierId: "gone_vendor", supplierName: "Arun ice cream agent", state: "ORDER_PLACED",
        items: [{ sku: "not_in_catalog", name: "Arun cone pack", qty: 12 }],
        vendorReply: { text: "Sending tomorrow 7 am", at: "2026-10-10T12:00:00Z", channel: "whatsapp" },
        updatedAt: "2026-10-10T12:00:00Z",
      }] as any[],
      [], suppliers, products,
    );
    expect(r.vendorReply).toBe("Sending tomorrow 7 am");
    expect(r.supplierName).toBe("Arun ice cream agent");
    expect(r.itemsText).toBe("Arun cone pack: 12");
    const [blank] = orderRows(
      [{ id: "o4", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED", vendorReply: { at: "x" }, updatedAt: 1 }] as any[],
      [], suppliers, products,
    );
    expect(blank.vendorReply).toBeNull();
  });
  it("carries the delivery time and note the owner asked for, with the parsed time in IST", () => {
    const [r] = orderRows(
      [{
        id: "o5", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED", updatedAt: 1,
        deliveryBy: { text: "6 am tomorrow", at: "2026-10-12T06:00:00+05:30" }, note: "send fresh stock",
      }] as any[],
      [], suppliers, products,
    );
    expect(r.deliveryByText).toBe("6 am tomorrow (12 Oct, 6:00 am)");
    expect(r.note).toBe("send fresh stock");
    const [raw] = orderRows(
      [{ id: "o6", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED", updatedAt: 1, deliveryBy: { text: "before the rush", at: null } }] as any[],
      [], suppliers, products,
    );
    expect(raw.deliveryByText).toBe("before the rush");
    expect(raw.note).toBeNull();
    const [dateOnly] = orderRows(
      [{ id: "o7", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED", updatedAt: 1, deliveryBy: { text: "tomorrow", at: "2026-10-12" } }] as any[],
      [], suppliers, products,
    );
    expect(dateOnly.deliveryByText).toBe("tomorrow (12 Oct)");
    const [none] = orderRows([{ id: "o8", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED", updatedAt: 1 }] as any[], [], suppliers, products);
    expect(none.deliveryByText).toBeNull();
    expect(none.note).toBeNull();
  });
});

// Every discrepancy kind the backend writes (backend/aira_live/business/reconcile.py), as one plain sentence.
describe("orderRows discrepancy lines", () => {
  const suppliers: any[] = [{ id: "sakthi_vendor", name: "Sakthi vendor" }];
  const order: any = { id: "o1", supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "DISCREPANCY", updatedAt: 1 };
  const lines = (discrepancies: any[], items: any[] = []) =>
    orderRows([order], [{ id: "d1", orderId: "o1", items, discrepancies }] as any[], suppliers, products)[0].discrepancyTexts;

  it("the demo delivery (30 ordered, 28 counted, bill says 30) reads as two lines that agree", () => {
    const t = lines(
      [
        { kind: "SHORT_DELIVERY", sku: "sakthi_curd_1l", expected: 30, actual: 28 },
        { kind: "INVOICE_QTY_MISMATCH", sku: "sakthi_curd_1l", expected: 28, actual: 30 },
      ],
      [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28 }],
    );
    expect(t).toEqual(["Sakthi curd 1 L: counted 28 L of 30 L ordered", "Sakthi curd 1 L: bill says 30, counted 28"]);
  });

  it("writes one plain sentence per kind, with names, units and rupees", () => {
    expect(lines([{ kind: "OVER_DELIVERY", sku: "aavin_milk_500ml", expected: 20, actual: 24 }]))
      .toEqual(["Aavin milk 500 ml: counted 24 packs, ordered 20 packs"]);
    expect(lines([{ kind: "UNORDERED_ITEM", sku: "arun_icecream_box", expected: 0, actual: 4 }]))
      .toEqual(["Arun ice cream box: not ordered, 4 boxes delivered"]);
    expect(lines([{ kind: "INVOICE_UNREADABLE", fields: ["total", "lines"] }]))
      .toEqual(["Bill unreadable: please check it again"]);
    expect(lines([{ kind: "UNKNOWN_INVOICE_LINE", name: "Paneer 200 g" }]))
      .toEqual(["Bill has an item not ordered: Paneer 200 g"]);
    expect(lines([{ kind: "PRICE_MISMATCH", sku: "sakthi_curd_1l", expected: 50, actual: 55 }]))
      .toEqual(["Sakthi curd 1 L: bill price ₹55, agreed ₹50"]);
    expect(lines([{ kind: "LINE_TOTAL_MISMATCH", sku: "sakthi_curd_1l", expected: 1050, actual: 1100 }]))
      .toEqual(["Sakthi curd 1 L: bill line total ₹1,100, expected ₹1,050"]);
    expect(lines([{ kind: "INVOICE_QTY_MISMATCH", sku: "aavin_milk_500ml", expected: 20, actual: 24 }]))
      .toEqual(["Aavin milk 500 ml: bill says 24, counted 20"]);
    expect(lines([{ kind: "TOTAL_MISMATCH", expected: 105000, actual: 110000 }]))
      .toEqual(["Bill total ₹1,10,000, lines add up to ₹1,05,000"]);
  });

  it("shows paise on a price only when there are some, so two different prices never read the same", () => {
    expect(lines([{ kind: "PRICE_MISMATCH", sku: "aavin_milk_500ml", expected: 24.5, actual: 25.25 }]))
      .toEqual(["Aavin milk 500 ml: bill price ₹25.25, agreed ₹24.50"]);
  });

  it("never prints undefined, whatever the entry is missing", () => {
    const t = lines([
      { kind: "UNKNOWN_INVOICE_LINE" },
      { kind: "SOMETHING_NEW", sku: "sakthi_curd_1l" },
      { kind: "SOMETHING_NEW", sku: "sakthi_curd_1l", expected: 3, actual: 4 },
      { kind: "SHORT_DELIVERY", sku: "sakthi_curd_1l" },
      { kind: "PRICE_MISMATCH", sku: "unknown_sku", expected: 50 },
      { kind: "TOTAL_MISMATCH" },
      {},
    ]);
    expect(t).toEqual([
      "Bill has an item that was not ordered",
      "Sakthi curd 1 L: does not match",
      "Sakthi curd 1 L: expected 3, got 4",
      "Sakthi curd 1 L: count does not match the order",
      "unknown_sku: bill price does not match the agreed price",
      "Bill total does not match its lines",
      "item: does not match",
    ]);
    for (const line of t) expect(line).not.toMatch(/undefined|null|NaN|—/);
  });

  it("flags a count that differs from the order before any discrepancy is written", () => {
    expect(lines([], [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28 }]))
      .toEqual(["Sakthi curd 1 L: counted 28 L of 30 L ordered"]);
    expect(lines([], [{ sku: "aavin_milk_500ml", expectedQty: 20, countedQty: 24 }]))
      .toEqual(["Aavin milk 500 ml: counted 24 packs, ordered 20 packs"]);
    // not in the catalog: the item's own name, and no unit to guess
    expect(lines([], [{ sku: "arun_cone", name: "Arun cone pack", expectedQty: 12, countedQty: 10 }]))
      .toEqual(["Arun cone pack: counted 10 of 12 ordered"]);
  });

  it("says nothing about items not counted yet or counted right", () => {
    expect(lines([], [
      { sku: "sakthi_curd_1l", expectedQty: 30, countedQty: null },
      { sku: "aavin_milk_500ml", expectedQty: 20, countedQty: 20 },
    ])).toEqual([]);
  });

  it("never shows the same short count twice once the backend writes it", () => {
    const items = [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28 }];
    expect(lines([{ kind: "SHORT_DELIVERY", sku: "sakthi_curd_1l", expected: 30, actual: 28 }], items))
      .toEqual(["Sakthi curd 1 L: counted 28 L of 30 L ordered"]);
    expect(lines([{ kind: "OVER_DELIVERY", sku: "sakthi_curd_1l", expected: 30, actual: 28 }], items)).toHaveLength(1);
    // an older entry with no kind is the same count comparison
    expect(lines([{ sku: "sakthi_curd_1l", expected: 30, actual: 28 }], items)).toEqual(["Sakthi curd 1 L: expected 30, got 28"]);
    // a bill problem for the same product does not cover the count
    expect(lines([{ kind: "PRICE_MISMATCH", sku: "sakthi_curd_1l", expected: 50, actual: 55 }], items))
      .toEqual(["Sakthi curd 1 L: counted 28 L of 30 L ordered", "Sakthi curd 1 L: bill price ₹55, agreed ₹50"]);
  });
});

describe("auditRows", () => {
  it("sorts newest first and humanises actor + action", () => {
    const rows = auditRows([
      { id: "a1", actor: "owner", action: "order_confirmed", refType: "order", refId: "o1", outcome: "ok", at: 1000 } as any,
      { id: "a2", actor: "aira", action: "sale_completed", refType: "sale", refId: "s2", outcome: "ok", evidenceUri: "gs://aira-evidence/s2.jpg", at: 2000 } as any,
    ]);
    expect(rows[0]).toMatchObject({ id: "a2", actorLabel: "AIRA", actionText: "Sale completed", outcomeText: "OK", evidenceUri: "gs://aira-evidence/s2.jpg" });
    expect(rows[1]).toMatchObject({ id: "a1", actorLabel: "Owner", actionText: "Order confirmed", evidenceUri: null });
  });
  it("shows the time in IST, reads ISO strings and keeps unknown actors as written", () => {
    const rows = auditRows([
      { id: "a3", actor: "system", action: "expiry_check", refType: "inventory", refId: "x", outcome: "ok", at: "2026-10-10T12:35:00Z" } as any,
      { id: "a4", actor: "helper", action: "note", refType: "x", refId: "y", outcome: "ok", at: Date.UTC(2026, 9, 10, 3, 30) } as any,
    ], NOW);
    expect(rows[0]).toMatchObject({ id: "a3", actorLabel: "System", actionText: "Expiry check", timeText: "6:05 pm" });
    expect(rows[1]).toMatchObject({ id: "a4", actorLabel: "helper", timeText: "9:00 am" });
  });
});

// The real Sunrise Dairy catalog shapes (shared/catalog/sunrise_dairy.json): milk and curd are sold by the litre.
const catalogProducts: any[] = [
  { id: "aavin_milk_500ml", sku: "aavin_milk_500ml", name: "Aavin Milk 500 ml", unit: "L", packSizeL: 0.5 },
  { id: "sakthi_curd_1l", sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L", unit: "L", packSizeL: 1 },
  { id: "aavin_paneer_200g", sku: "aavin_paneer_200g", name: "Aavin Paneer 200 g", unit: "pack", packSizeG: 200 },
  { id: "aavin_ghee_200ml", sku: "aavin_ghee_200ml", name: "Aavin Ghee 200 ml", unit: "jar", packSizeL: 0.2 },
  { id: "arun_icecream_box", sku: "arun_icecream_box", name: "Arun Ice Cream Family Box", unit: "box", active: false },
];

describe("order quantities in their selling unit", () => {
  const one = (items: any[], extra: any = {}) =>
    orderRows([{ id: "o", supplierId: "aavin_vendor", supplierName: "Aavin vendor", items, state: "ORDER_PLACED", updatedAt: 1, ...extra }], [], [], catalogProducts)[0];

  it("reads litres as litres, with the pack count for half-litre pouches", () => {
    const r = one([
      { sku: "aavin_milk_500ml", qty: 20, unit: "L" },
      { sku: "sakthi_curd_1l", qty: 30, unit: "L" },
      { sku: "aavin_paneer_200g", qty: 25, unit: "pack" },
      { sku: "aavin_ghee_200ml", qty: 4, unit: "jar" },
    ]);
    expect(r.itemsText).toBe("Aavin Milk 500 ml: 20 L (40 packs), Sakthi Curd 1 L: 30 L, Aavin Paneer 200 g: 25 packs, Aavin Ghee 200 ml: 4 jars");
  });
  it("uses the product's unit when the line has none, and the line's unit when the product is gone", () => {
    expect(one([{ sku: "aavin_milk_500ml", qty: 20 }]).itemsText).toBe("Aavin Milk 500 ml: 20 L (40 packs)");
    expect(one([{ sku: "gone", name: "Mystery milk", qty: 4, unit: "L" }]).itemsText).toBe("Mystery milk: 4 L");
  });
  it("never shows the old pack-count look (×) for an order line", () => {
    expect(one([{ sku: "aavin_milk_500ml", qty: 20, unit: "L" }]).itemsText).not.toContain("×");
  });

  it("puts litres and pack counts inside the per-kind mismatch sentences; the bill quantity stays bare", () => {
    const mismatch = (discrepancies: any[], items: any[] = []) => orderRows(
      [{ id: "o", supplierId: "aavin_vendor", items: [{ sku: "aavin_milk_500ml", qty: 20, unit: "L" }, { sku: "sakthi_curd_1l", qty: 30, unit: "L" }], state: "DISCREPANCY", updatedAt: 1 }] as any[],
      [{ id: "d", orderId: "o", items, discrepancies }] as any[],
      [], catalogProducts,
    )[0].discrepancyTexts;
    expect(mismatch([
      { kind: "SHORT_DELIVERY", sku: "aavin_milk_500ml", expected: 20, actual: 19 },
      { kind: "OVER_DELIVERY", sku: "sakthi_curd_1l", expected: 30, actual: 32 },
      { kind: "UNORDERED_ITEM", sku: "aavin_paneer_200g", expected: 0, actual: 5 },
      { kind: "INVOICE_QTY_MISMATCH", sku: "aavin_milk_500ml", expected: 38, actual: 40 },
    ])).toEqual([
      "Aavin Milk 500 ml: counted 19 L (38 packs) of 20 L (40 packs) ordered",
      "Sakthi Curd 1 L: counted 32 L, ordered 30 L",
      "Aavin Paneer 200 g: not ordered, 5 packs delivered",
      "Aavin Milk 500 ml: bill says 40, counted 38",
    ]);
    // a short count read from the delivery items before reconcile writes it
    expect(mismatch([], [{ sku: "aavin_milk_500ml", expectedQty: 20, countedQty: 19 }]))
      .toEqual(["Aavin Milk 500 ml: counted 19 L (38 packs) of 20 L (40 packs) ordered"]);
  });
});

describe("orders the owner never sent (DRAFT)", () => {
  const rows = orderRows(
    [
      { id: "said_no", supplierId: "aavin_vendor", supplierName: "Aavin vendor", items: [{ sku: "aavin_milk_500ml", qty: 20, unit: "L" }], state: "DRAFT", updatedAt: 300 },
      { id: "sent_later", supplierId: "aavin_vendor", supplierName: "Aavin vendor", items: [], state: "ORDER_PLACED", updatedAt: 200 },
      { id: "short", supplierId: "sakthi_vendor", supplierName: "Sakthi curd vendor", items: [], state: "DISCREPANCY", updatedAt: 100 },
    ] as any[],
    [], [], catalogProducts,
  );
  it("keeps drafts out of the sent list and in a separate not-sent list, newest first", () => {
    const { sent, notSent } = splitOrders(rows);
    expect(sent.map((r) => r.id)).toEqual(["sent_later", "short"]);
    expect(notSent.map((r) => r.id)).toEqual(["said_no"]);
  });
  it("treats a draft the owner later sent (ORDER_PLACED) as a normal order", () => {
    const placed = rows.map((r) => (r.id === "said_no" ? { ...r, state: "ORDER_PLACED", stepIndex: 0 } : r));
    expect(splitOrders(placed).notSent).toEqual([]);
    expect(splitOrders(placed).sent).toHaveLength(3);
  });
});

describe("audit rows say what happened, to what, in plain words", () => {
  const orders: any[] = [
    { id: "o_milk", supplierId: "aavin_vendor", supplierName: "Aavin vendor", items: [{ sku: "aavin_milk_500ml", qty: 20, unit: "L" }], state: "ORDER_PLACED", updatedAt: 1 },
    { id: "o_curd", supplierId: "sakthi_vendor", supplierName: "Sakthi curd vendor", items: [{ sku: "sakthi_curd_1l", qty: 30, unit: "L" }], state: "DISCREPANCY", updatedAt: 1 },
    {
      id: "o_mixed", supplierId: "aavin_vendor", supplierName: "Aavin vendor", state: "ORDER_PLACED", updatedAt: 1,
      items: [{ sku: "aavin_milk_500ml", qty: 10, unit: "L" }, { sku: "aavin_paneer_200g", qty: 25, unit: "pack" }, { sku: "aavin_ghee_200ml", qty: 4, unit: "jar" }],
    },
    { id: "o_two", supplierId: "aavin_vendor", supplierName: "Aavin vendor", state: "ORDER_PLACED", updatedAt: 1, items: [{ sku: "aavin_paneer_200g", qty: 1, unit: "pack" }, { sku: "aavin_ghee_200ml", qty: 2, unit: "jar" }] },
  ];
  const deliveries: any[] = [{ id: "d_curd", orderId: "o_curd", items: [], discrepancies: [] }];
  const refs = { products: catalogProducts, orders, deliveries, suppliers: [] };
  let t = 0;
  const row = (action: string, refType: string, refId: string, outcome: unknown, actor = "owner") =>
    auditRows([{ id: `a${++t}`, actor, action, refType, refId, outcome, at: 1000 } as any], NOW, refs)[0];

  it("names the order's supplier and items, and how the order went out", () => {
    expect(row("order_placed", "order", "o_milk", "telegram")).toMatchObject({
      actionText: "Ordered 20 L (40 packs) of Aavin Milk 500 ml from Aavin vendor", outcomeText: "Sent on Telegram",
    });
    expect(row("order_placed", "order", "o_curd", "demo")).toMatchObject({
      actionText: "Ordered 30 L of Sakthi Curd 1 L from Sakthi curd vendor", outcomeText: "Sent to the demo vendor",
    });
    expect(row("order_placed", "order", "o_two", "manual")).toMatchObject({
      actionText: "Ordered 1 pack of Aavin Paneer 200 g and 2 jars of Aavin Ghee 200 ml from Aavin vendor", outcomeText: "Sent by the owner",
    });
    expect(row("order_placed", "order", "o_mixed", "whatsapp")).toMatchObject({
      actionText: "Ordered 10 L (20 packs) of Aavin Milk 500 ml, 25 packs of Aavin Paneer 200 g and 1 more item from Aavin vendor",
      outcomeText: "Sent on WhatsApp",
    });
  });
  it("tells four Telegram orders apart instead of four identical rows", () => {
    const rows = auditRows(
      ["o_milk", "o_curd", "o_mixed", "o_two"].map((id, i) => ({ id: `t${i}`, actor: "owner", action: "order_placed", refType: "order", refId: id, outcome: "telegram", at: i } as any)),
      NOW, refs,
    );
    expect(new Set(rows.map((r) => r.actionText)).size).toBe(4);
  });
  it("dates an older audit sentence like any other row", () => {
    const [r] = auditRows([{ id: "y", actor: "owner", action: "order_placed", refType: "order", refId: "o_milk", outcome: "telegram", at: Date.UTC(2026, 9, 9, 5, 0) } as any], NOW, refs);
    expect(r).toMatchObject({ timeText: "Yesterday, 10:30 am", actionText: "Ordered 20 L (40 packs) of Aavin Milk 500 ml from Aavin vendor" });
  });
  it("still reads plainly when the order is not loaded", () => {
    expect(row("order_placed", "order", "o_old", "telegram")).toMatchObject({ actionText: "Order placed", outcomeText: "Sent on Telegram" });
  });

  it("writes a supplier payment in rupees with Indian grouping and says how it was checked", () => {
    expect(row("supplier_paid", "order", "o_curd", "cash_count:980")).toMatchObject({
      actionText: "Paid Sakthi curd vendor ₹980", outcomeText: "Cash, checked by camera",
    });
    expect(row("supplier_paid", "order", "o_milk", "user_confirm:12500")).toMatchObject({
      actionText: "Paid Aavin vendor ₹12,500", outcomeText: "UPI, owner confirmed",
    });
    expect(row("supplier_paid", "order", "o_old", "soundbox:500")).toMatchObject({
      actionText: "Paid a supplier ₹500", outcomeText: "Soundbox confirmed",
    });
  });

  it("writes a delivery count with the product name and its litres or packs", () => {
    expect(row("delivery_counted", "delivery", "d_curd", "sakthi_curd_1l=28.0")).toMatchObject({
      actionText: "Counted 28 L of Sakthi Curd 1 L from Sakthi curd vendor", outcomeText: "Added to stock",
    });
    expect(row("delivery_counted", "delivery", "d_gone", "aavin_milk_500ml=19.0")).toMatchObject({
      actionText: "Counted 19 L (38 packs) of Aavin Milk 500 ml", outcomeText: "Added to stock",
    });
  });

  it("says whether the bill matched, and whose bill it was", () => {
    expect(row("invoice_checked", "delivery", "d_curd", "DELIVERY_RECONCILED", "aira")).toMatchObject({
      actionText: "Checked the bill from Sakthi curd vendor", outcomeText: "Bill matches",
    });
    expect(row("invoice_checked", "delivery", "d_gone", "DISCREPANCY", "aira")).toMatchObject({
      actionText: "Checked the bill", outcomeText: "Bill does not match",
    });
  });

  it("names products and suppliers changed by voice", () => {
    expect(row("product_added", "product", "aavin_paneer_200g", "added")).toMatchObject({ actionText: "New product: Aavin Paneer 200 g", outcomeText: "Added" });
    expect(row("product_updated", "product", "aavin_paneer_200g", "price")).toMatchObject({ actionText: "Changed Aavin Paneer 200 g", outcomeText: "Price changed" });
    expect(row("product_updated", "product", "aavin_paneer_200g", "name,price")).toMatchObject({ outcomeText: "Name and price changed" });
    expect(row("product_updated", "product", "aavin_paneer_200g", "name,price,supplier")).toMatchObject({ outcomeText: "Name, price and supplier changed" });
    expect(row("product_disabled", "product", "arun_icecream_box", "disabled")).toMatchObject({ actionText: "Stopped selling Arun Ice Cream Family Box", outcomeText: "Taken off sale" });
    expect(row("stock_received", "product", "sakthi_curd_1l", "+10")).toMatchObject({ actionText: "Received 10 L of Sakthi Curd 1 L without an order", outcomeText: "Added to stock" });
    // suppliers are server-only, so the name comes from an order to that supplier
    expect(row("supplier_added", "supplier", "aavin_vendor", "telegram")).toMatchObject({ actionText: "New supplier: Aavin vendor", outcomeText: "Orders go on Telegram" });
    expect(row("supplier_added", "supplier", "new_vendor", "call")).toMatchObject({ actionText: "New supplier added", outcomeText: "Orders by phone call" });
  });

  it("falls back to plain words for unknown actions and outcomes, never undefined or a raw code", () => {
    expect(row("stock_moved", "product", "x", "zone=fridge2")).toMatchObject({ actionText: "Stock moved", outcomeText: "Zone fridge2" });
    expect(row("refund_given", "sale", "s1", "cash_count:980")).toMatchObject({ actionText: "Refund given", outcomeText: "Cash count 980" });
    expect(row("delivery_counted", "delivery", "d_curd", "garbage")).toMatchObject({ actionText: "Delivery counted", outcomeText: "Garbage" });
    expect(row("supplier_paid", "order", "o_curd", "cheque")).toMatchObject({ actionText: "Supplier paid", outcomeText: "Cheque" });
    const blank = auditRows([{ id: "b", actor: "owner", at: 1 } as any], NOW, refs)[0];
    expect(blank).toMatchObject({ actionText: "Action not recorded", outcomeText: "Not recorded" });
    const outcomes = ["ok", "discrepancy", "needs_recount", ""].map((o) => row("expiry_check", "inventory", "x", o, "system").outcomeText);
    expect(outcomes).toEqual(["OK", "Discrepancy", "Needs recount", "Not recorded"]);
  });

  it("never shows undefined, an em dash or a raw code with = or : in any row", () => {
    const logs = [
      ["order_placed", "order", "o_milk", "telegram"], ["supplier_paid", "order", "o_x", "cash_count:980"],
      ["delivery_counted", "delivery", "d_x", "nope_sku=4.0"], ["invoice_checked", "delivery", "d_x", "WEIRD_STATE"],
      ["product_updated", "product", "gone", "price"], ["mystery", "x", "y", "a=b:c"], [undefined, undefined, undefined, undefined],
    ];
    const rows = auditRows(logs.map(([action, refType, refId, outcome], i) => ({ id: `z${i}`, actor: "owner", action, refType, refId, outcome, at: i } as any)), NOW, refs);
    for (const r of rows) {
      expect(`${r.actionText} ${r.outcomeText}`).not.toMatch(/undefined|null|—/);
      expect(r.outcomeText).not.toMatch(/[=:]/);
    }
  });
});

describe("stockRows and products changed by voice", () => {
  it("hides a product with active === false and shows a new one added by voice", () => {
    const rows = stockRows(
      [
        ...products,
        { id: "arun_old", sku: "arun_old", name: "Old box", unit: "box", reorderThreshold: 3, active: false } as any,
        { id: "aavin_lassi_200ml", sku: "aavin_lassi_200ml", name: "Aavin Lassi 200 ml", unit: "pack", reorderThreshold: 2, active: true } as any,
      ],
      inventory,
      NOW,
    );
    const skus = rows.map((r) => r.sku);
    expect(skus).not.toContain("arun_old");
    expect(skus).toContain("aavin_lassi_200ml");
    expect(skus).toContain("new_sku");
  });
});

describe("times on older rows carry their day", () => {
  it("today shows the time, yesterday says so, older rows show the date", () => {
    const rows = auditRows([
      { id: "t", actor: "aira", action: "x", refType: "x", refId: "x", outcome: "ok", at: Date.UTC(2026, 9, 10, 5, 0) } as any,
      { id: "y", actor: "aira", action: "x", refType: "x", refId: "x", outcome: "ok", at: Date.UTC(2026, 9, 9, 5, 0) } as any,
      { id: "o", actor: "aira", action: "x", refType: "x", refId: "x", outcome: "ok", at: Date.UTC(2026, 9, 8, 5, 6) } as any,
    ], NOW);
    expect(rows.map((r) => r.timeText)).toEqual(["10:30 am", "Yesterday, 10:30 am", "8 Oct, 10:36 am"]);
  });
  it("an order last updated two days ago says the date", () => {
    const [r] = orderRows([{ id: "o9", supplierId: "sakthi_vendor", items: [], state: "ORDER_PLACED",
      updatedAt: Date.UTC(2026, 9, 8, 5, 6) }] as any[], [], [], products, NOW);
    expect(r.updatedText).toBe("8 Oct, 10:36 am");
  });
});

