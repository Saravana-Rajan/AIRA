import { describe, it, expect } from "vitest";
import { ORDER_STATES } from "@aira/contracts";
import { stockRows } from "../src/lib/selectors/stock";
import { todaySummary } from "../src/lib/selectors/today";
import { ORDER_FLOW, orderRows } from "../src/lib/selectors/orders";
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
  const rows = orderRows(orders, deliveries, suppliers, products);

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
    expect(r.itemsText).toBe("Sakthi curd 1 L × 30");
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
    expect(r.itemsText).toBe("Arun cone pack × 12");
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
    expect(rows[0]).toMatchObject({ id: "a2", actorLabel: "AIRA", actionText: "Sale completed", outcome: "ok", evidenceUri: "gs://aira-evidence/s2.jpg" });
    expect(rows[1]).toMatchObject({ id: "a1", actorLabel: "Owner", actionText: "Order confirmed", evidenceUri: null });
  });
  it("shows the time in IST, reads ISO strings and keeps unknown actors as written", () => {
    const rows = auditRows([
      { id: "a3", actor: "system", action: "expiry_check", refType: "inventory", refId: "x", outcome: "ok", at: "2026-10-10T12:35:00Z" } as any,
      { id: "a4", actor: "helper", action: "note", refType: "x", refId: "y", outcome: "ok", at: Date.UTC(2026, 9, 10, 3, 30) } as any,
    ]);
    expect(rows[0]).toMatchObject({ id: "a3", actorLabel: "System", actionText: "Expiry check", timeText: "6:05 pm" });
    expect(rows[1]).toMatchObject({ id: "a4", actorLabel: "helper", timeText: "9:00 am" });
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
