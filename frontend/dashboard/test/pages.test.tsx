import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, findAllByText, flush, mount, text } from "./helpers/render";
import { StockPage } from "../src/pages/StockPage";
import { TodayPage } from "../src/pages/TodayPage";
import { OrdersPage } from "../src/pages/OrdersPage";
import { AuditPage } from "../src/pages/AuditPage";
import { ForecastPage } from "../src/pages/ForecastPage";
import { HomePage } from "../src/pages/HomePage";
import type { OrderRow } from "../src/lib/selectors/orders";
import type { TodaySummary } from "../src/lib/selectors/today";
import { buildViews } from "../src/lib/views";

afterEach(cleanup);

const heading = (el: HTMLElement) => text(el.querySelector("h2"));

describe("StockPage", () => {
  const rows = [
    { sku: "a", name: "Arun ice cream box", onHand: 2, onHandText: "2 boxes", lowStock: true, daysToExpiry: null, expiryText: null },
    { sku: "b", name: "Sakthi curd 1 L", onHand: 28, onHandText: "28 L", lowStock: false, daysToExpiry: 1, expiryText: "Expires tomorrow" },
    { sku: "c", name: "Aavin milk 500 ml", onHand: 6, onHandText: "6 L", lowStock: false, daysToExpiry: -1, expiryText: "Expired" },
  ];

  it("renders an accessible table with text (not colour-only) status labels", () => {
    const el = mount(<StockPage rows={rows} />);
    expect(heading(el)).toBe("Stock");
    const table = el.querySelector("table")!;
    expect(text(table.querySelector("caption"))).toMatch(/stock/i);
    const trs = table.querySelectorAll("tr");
    expect(trs).toHaveLength(4); // header + 3
    expect(text(trs[1])).toContain("Arun ice cream box");
    expect(findAllByText(trs[1], "*", "Low stock")).not.toHaveLength(0);
    expect(findAllByText(trs[2], "*", "Expires tomorrow")).not.toHaveLength(0);
    expect(findAllByText(trs[2], "*", "OK")).not.toHaveLength(0);
    expect([...table.querySelectorAll("thead th")].map((h) => h.getAttribute("scope"))).toEqual(["col", "col", "col", "col"]);
    // The product name is the row header.
    expect(trs[1].querySelector("th[scope=row] .cell__main")?.textContent).toBe("Arun ice cream box");
  });

  it("marks soon and past expiry with distinct styles as well as words", () => {
    const el = mount(<StockPage rows={rows} />);
    const tomorrow = findAllByText(el, ".expiry", "Expires tomorrow")[0];
    const expired = findAllByText(el, ".expiry", "Expired")[0];
    expect(tomorrow.className).toContain("expiry--soon");
    expect(expired.className).toContain("expiry--past");
  });

  it("folds expiry under the product name for narrow cards (wide-only column, narrow-only line)", () => {
    const el = mount(<StockPage rows={rows} />);
    expect([...el.querySelectorAll("thead th.wide-only")].map((h) => text(h))).toEqual(["Expiry"]);
    const fold = el.querySelectorAll("tbody tr")[1].querySelector("th[scope=row] .narrow-only")!;
    expect(text(fold)).toBe("Expires tomorrow");
    expect(text(el.querySelectorAll("tbody tr")[0].querySelector("th[scope=row] .narrow-only"))).toBe("Expiry not recorded");
  });

  it("says when there are no products yet", () => {
    const el = mount(<StockPage rows={[]} />);
    expect(text(el)).toContain("No products yet");
  });
});

describe("TodayPage", () => {
  const summary: TodaySummary = {
    salesCount: 2, revenue: 230, revenueText: "₹230", topProduct: "Arun ice cream box",
    byMethod: { cash: 50, upi: 180 }, byMethodText: { cash: "₹50", upi: "₹180" },
  };

  it("shows revenue, sales count, top product and payments by method", () => {
    const el = mount(<TodayPage summary={summary} />);
    expect(heading(el)).toBe("Today");
    expect(findAllByText(el, "dd", "₹230")).toHaveLength(1);
    expect(findAllByText(el, "dd", "₹50")).toHaveLength(1);
    expect(findAllByText(el, "dd", "₹180")).toHaveLength(1);
    expect(findAllByText(el, "dd", "2")).toHaveLength(1);
    expect(findAllByText(el, "dd", "Arun ice cream box")).toHaveLength(1);
    expect(findAllByText(el, "dt", "Cash received")).toHaveLength(1);
    expect(findAllByText(el, "dt", "UPI received")).toHaveLength(1);
  });

  it("draws the cash/UPI split as a decorative bar with the share in proportion", () => {
    const el = mount(<TodayPage summary={summary} />);
    const bar = el.querySelector(".split")!;
    expect(bar.getAttribute("aria-hidden")).toBe("true");
    const cash = bar.querySelector<HTMLElement>(".split__cash")!;
    expect(cash.style.width).toBe(`${(50 / 230) * 100}%`);
  });

  it("reads plainly on a day with no sales", () => {
    const el = mount(<TodayPage summary={{ ...summary, salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null, byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" } }} />);
    expect(text(el)).toContain("No sales yet today");
    expect(el.querySelector(".split")).toBeNull();
  });
});

describe("OrdersPage", () => {
  const row: OrderRow = {
    id: "o1", supplierName: "Sakthi vendor", itemsText: "Sakthi curd 1 L: 30 L", state: "DISCREPANCY",
    stateLabel: "Discrepancy", stepIndex: 2, isDiscrepancy: true, vendorReply: "OK, delivery at 6 pm",
    discrepancyTexts: ["Sakthi curd 1 L: expected 30, got 28"], updatedText: "6:05 pm", deliveryByText: null, note: null,
  };

  it("announces discrepancies in words with each mismatch listed", () => {
    const el = mount(<OrdersPage rows={[row]} />);
    expect(heading(el)).toBe("Orders & deliveries");
    expect(findAllByText(el, "*", "Discrepancy").length).toBeGreaterThan(0);
    expect(findAllByText(el, "li", "Sakthi curd 1 L: expected 30, got 28")).toHaveLength(1);
    expect(text(el)).toMatch(/OK, delivery at 6 pm/);
    expect(text(el)).toContain("Updated 6:05 pm");
    const article = el.querySelector("article")!;
    expect(article.className).toContain("order--discrepancy");
    expect(article.getAttribute("aria-labelledby")).toBe(article.querySelector("h3")!.id);
  });

  it("draws the order progress as an ordered list with the current step marked", () => {
    const el = mount(<OrdersPage rows={[row]} />);
    const track = el.querySelector("ol[aria-label='Order progress']")!;
    const steps = [...track.querySelectorAll("li")];
    expect(steps).toHaveLength(6);
    expect(steps.map((s) => s.getAttribute("aria-current"))).toEqual([null, null, "step", null, null, null]);
    expect(text(steps[2])).toBe("Discrepancy found");
    expect(steps[0].className).toContain("done");
    expect(text(steps[0])).toContain("done"); // screen-reader text, not only a tick
    expect(steps[2].className).toContain("problem");
  });

  it("marks the current step of a normal order without any discrepancy text", () => {
    const el = mount(<OrdersPage rows={[{ ...row, id: "o2", state: "DELIVERY_PENDING", stateLabel: "Delivery pending", stepIndex: 1, isDiscrepancy: false, discrepancyTexts: [], vendorReply: null }]} />);
    const steps = [...el.querySelectorAll("ol[aria-label='Order progress'] li")];
    expect(steps[1].getAttribute("aria-current")).toBe("step");
    expect(text(steps[1])).toBe("Delivery pending");
    expect(text(el)).not.toMatch(/discrepanc/i);
    expect(text(el)).not.toMatch(/Vendor replied/);
  });

  it("says when there are no orders yet", () => {
    const el = mount(<OrdersPage rows={[]} />);
    expect(text(el)).toContain("No supplier orders yet");
  });

  it("shows the delivery time and note as small read-only text, and nothing when absent", () => {
    const el = mount(<OrdersPage rows={[{ ...row, deliveryByText: "6 am tomorrow (12 Oct, 6:00 am)", note: "send fresh stock" }]} />);
    const extras = el.querySelector(".order__extras")!;
    expect(text(extras)).toContain("Deliver by: 6 am tomorrow (12 Oct, 6:00 am)");
    expect(text(extras)).toContain("Note: send fresh stock");
    const bare = mount(<OrdersPage rows={[row]} />);
    expect(bare.querySelector(".order__extras")).toBeNull();
    expect(text(bare)).not.toMatch(/Deliver by|Note:/);
  });

  describe("orders the owner never sent (DRAFT)", () => {
    const draft: OrderRow = {
      ...row, id: "d1", supplierName: "Aavin vendor", itemsText: "Aavin Milk 500 ml: 20 L (40 packs)", state: "DRAFT",
      stateLabel: "Draft", stepIndex: -1, isDiscrepancy: false, discrepancyTexts: [], vendorReply: null, updatedText: "5:40 pm",
    };
    const placed: OrderRow = { ...row, id: "p1", state: "ORDER_PLACED", stateLabel: "Order placed", stepIndex: 0, isDiscrepancy: false, discrepancyTexts: [], vendorReply: null };

    it("keeps a draft out of the order cards and lists it under 'Not sent' as plain text, with no progress track", () => {
      const el = mount(<OrdersPage rows={[draft, placed]} />);
      const cards = [...el.querySelectorAll("article")];
      expect(cards).toHaveLength(1);
      expect(text(cards[0].querySelector("h3"))).toBe("Sakthi vendor");
      const group = el.querySelector<HTMLElement>(".drafts")!;
      expect(group).not.toBeNull();
      expect(text(group.querySelector("h3"))).toBe("Not sent");
      expect(group.getAttribute("aria-labelledby")).toBe(group.querySelector("h3")!.id);
      expect(group.querySelector("ol, .track, .badge, article")).toBeNull();
      const items = [...group.querySelectorAll("li")].map((li) => text(li));
      expect(items).toEqual(["Aavin vendor · Aavin Milk 500 ml: 20 L (40 packs) · 5:40 pm"]);
      expect(text(el)).not.toContain("Draft");
      expect(text(el)).not.toContain("—");
    });

    it("shows no 'Not sent' group when every order went out", () => {
      const el = mount(<OrdersPage rows={[placed]} />);
      expect(el.querySelector(".drafts")).toBeNull();
      expect(text(el)).not.toContain("Not sent");
    });

    it("still says there are no supplier orders when only drafts exist", () => {
      const el = mount(<OrdersPage rows={[draft]} />);
      expect(el.querySelectorAll("article")).toHaveLength(0);
      expect(text(el)).toContain("No supplier orders yet");
      expect(el.querySelector(".drafts")).not.toBeNull();
    });

    it("leaves drafts off the overview card entirely and counts only sent orders in the link", () => {
      const el = mount(<OrdersPage rows={[draft, { ...draft, id: "d2" }, placed, { ...placed, id: "p2" }, { ...placed, id: "p3" }, { ...placed, id: "p4" }]} limit={3} />);
      expect(el.querySelectorAll("article")).toHaveLength(3);
      expect(el.querySelector(".drafts")).toBeNull();
      expect(text(el.querySelector("a.more"))).toBe("See all 4 orders");
    });
  });
});

describe("AuditPage", () => {
  const rows = [
    { id: "a1", timeText: "6:05 pm", actorLabel: "AIRA", actionText: "Sale completed", outcomeText: "OK", evidenceUri: "https://example.test/e.jpg" },
    { id: "a2", timeText: "6:00 pm", actorLabel: "Owner", actionText: "Order confirmed", outcomeText: "OK", evidenceUri: "gs://aira-evidence/x.jpg" },
    { id: "a3", timeText: "5:55 pm", actorLabel: "System", actionText: "Expiry check", outcomeText: "OK", evidenceUri: null },
  ];

  it("lists recent actions in a captioned table and resolves evidence links", async () => {
    const resolve = vi.fn(async (uri: string) => (uri.startsWith("https://") ? uri : null));
    const el = mount(<AuditPage rows={rows} resolveEvidence={resolve} />);
    expect(heading(el)).toBe("Audit trail");
    const table = el.querySelector("table")!;
    expect(text(table.querySelector("caption"))).toMatch(/actions/i);
    expect(table.querySelectorAll("tbody tr")).toHaveLength(3);
    await flush();
    const link = el.querySelector<HTMLAnchorElement>("a[href='https://example.test/e.jpg']")!;
    expect(text(link)).toBe("View evidence");
    expect(link.rel).toContain("noopener");
    expect(text(table.querySelectorAll("tbody tr")[1])).toContain("Kept private");
    expect(resolve).toHaveBeenCalledTimes(2);
  });

  it("folds who and outcome into the action cell for narrow cards", () => {
    const el = mount(<AuditPage rows={rows} resolveEvidence={async () => null} />);
    expect([...el.querySelectorAll("thead th.wide-only")].map((h) => text(h))).toEqual(["Who", "Outcome"]);
    expect(text(el.querySelector("tbody tr .narrow-only"))).toBe("By AIRA. Outcome: OK");
  });

  it("shows the sentence and the readable outcome the selector wrote, in both the wide and narrow layouts", () => {
    const el = mount(
      <AuditPage
        resolveEvidence={async () => null}
        rows={[{ ...rows[0], id: "x1", actorLabel: "Owner", actionText: "Ordered 20 L (40 packs) of Aavin Milk 500 ml from Aavin vendor", outcomeText: "Sent on Telegram" }]}
      />,
    );
    const tds = el.querySelectorAll("tbody tr td");
    expect(text(tds[2].querySelector(".cell__main"))).toBe("Ordered 20 L (40 packs) of Aavin Milk 500 ml from Aavin vendor");
    expect(text(tds[2].querySelector(".narrow-only"))).toBe("By Owner. Outcome: Sent on Telegram");
    expect(text(tds[3])).toBe("Sent on Telegram");
  });

  it("built from live data, an order row names the supplier and the litres, not a raw code", () => {
    const views = buildViews({
      products: [{ id: "aavin_milk_500ml", sku: "aavin_milk_500ml", name: "Aavin Milk 500 ml", unit: "L", packSizeL: 0.5 }],
      inventory: [], sales: [], payments: [], deliveries: [], suppliers: [],
      orders: [{ id: "o1", supplierId: "aavin_vendor", supplierName: "Aavin vendor", items: [{ sku: "aavin_milk_500ml", qty: 20, unit: "L" }], state: "ORDER_PLACED", updatedAt: 1 }],
      auditLogs: [{ id: "a1", actor: "owner", action: "order_placed", refType: "order", refId: "o1", outcome: "telegram", at: 1 }],
    } as never, 1);
    const el = mount(<AuditPage rows={views.audit} resolveEvidence={async () => null} />);
    expect(text(el.querySelector("tbody .cell__main"))).toBe("Ordered 20 L (40 packs) of Aavin Milk 500 ml from Aavin vendor");
    expect(text(el.querySelectorAll("tbody td")[3])).toBe("Sent on Telegram");
  });

  it("says when nothing has been recorded yet", () => {
    const el = mount(<AuditPage rows={[]} resolveEvidence={async () => null} />);
    expect(text(el)).toContain("No actions recorded yet");
  });
});

describe("ForecastPage", () => {
  it("shows a clear message when the forecast is unavailable", () => {
    const el = mount(<ForecastPage items={null} loading={false} />);
    expect(heading(el)).toBe("Forecast");
    expect(findAllByText(el, "[role=status]", "Forecast unavailable")).toHaveLength(1);
  });

  it("shows a loading state", () => {
    const el = mount(<ForecastPage items={null} loading={true} />);
    expect(findAllByText(el, "[role=status]", "Loading forecast…")).toHaveLength(1);
  });

  it("shows suggested orders when available", () => {
    const el = mount(<ForecastPage loading={false} items={[{ sku: "s", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 }]} />);
    const row = el.querySelector("tbody tr")!;
    expect(row.querySelector("th[scope=row] .cell__main")?.textContent).toBe("Sakthi curd 1 L");
    expect(findAllByText(row, "td", "26.4")).toHaveLength(1);
    expect(findAllByText(row, "td", "30")).toHaveLength(1);
    // In a narrow card the detail columns fold into the product cell (CSS container query).
    expect(text(row.querySelector("th[scope=row] .narrow-only"))).toBe("Sells 26.4 a day, 12 on hand");
    expect([...el.querySelectorAll("thead th.wide-only")].map((h) => text(h))).toEqual(["Sold per day", "On hand"]);
  });

  it("says so when no reorder is needed", () => {
    const items = [
      { sku: "s", name: "Sakthi curd 1 L", dailyDemand: 2.1, onHand: 40, suggestedOrder: 0 },
      { sku: "m", name: "Aavin milk 500 ml", dailyDemand: 5, onHand: 60, suggestedOrder: 0 },
    ];
    const el = mount(<ForecastPage loading={false} items={items} />);
    expect(text(el)).toContain("No reorders needed right now.");
    expect(text(el)).not.toContain("Not enough sales yet");
    expect(text(el)).not.toContain("Demo history");
  });

  it("says there is not enough sales history yet when the forecast has no products", () => {
    // aira-live leaves out every product with fewer than 3 days of sales, so an empty list is not "all stocked up".
    const el = mount(<ForecastPage loading={false} items={[]} />);
    expect(text(el)).toContain("Not enough sales yet to forecast.");
    expect(text(el)).not.toContain("No reorders needed");
  });

  it("never says no reorders are needed while one is suggested", () => {
    const items = [
      { sku: "s", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 },
      { sku: "m", name: "Aavin milk 500 ml", dailyDemand: 5, onHand: 60, suggestedOrder: 0 },
    ];
    const el = mount(<ForecastPage loading={false} items={items} />);
    expect(text(el)).not.toContain("No reorders needed");
    expect(text(el)).not.toContain("Not enough sales yet");
    expect(el.querySelectorAll("tbody tr")).toHaveLength(2);
  });

  it("says 'demo history' when backfilled demo sales feed the numbers", () => {
    const items = [{ sku: "s", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 }];
    const el = mount(<ForecastPage loading={false} items={items} demoHistory={true} />);
    expect(findAllByText(el, "[role=note]", /^Demo history/)).toHaveLength(1);
    expect(text(el.querySelector("caption"))).toContain("demo history");
    const real = mount(<ForecastPage loading={false} items={items} demoHistory={false} />);
    expect(text(real)).not.toContain("Demo history");
  });
});

describe("overview cards stay short", () => {
  const order = (i: number): OrderRow => ({
    id: `o${i}`, supplierName: `Vendor ${i}`, itemsText: "Aavin Milk 500 ml: 10 L (20 packs)", state: "ORDER_PLACED",
    stateLabel: "Order placed", stepIndex: 0, isDiscrepancy: false, vendorReply: null, discrepancyTexts: [], updatedText: "",
    deliveryByText: null, note: null,
  });

  it("OrdersPage shows the first few orders and links to the rest", () => {
    const el = mount(<OrdersPage rows={[1, 2, 3, 4, 5].map(order)} limit={3} />);
    expect(el.querySelectorAll("article")).toHaveLength(3);
    const more = el.querySelector<HTMLAnchorElement>("a.more")!;
    expect(more.getAttribute("href")).toBe("#/orders");
    expect(text(more)).toBe("See all 5 orders");
  });

  it("AuditPage shows the newest actions and links to the full trail", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({
      id: `a${i}`, timeText: "6:05 pm", actorLabel: "AIRA", actionText: "Sale completed", outcomeText: "OK", evidenceUri: null,
    }));
    const el = mount(<AuditPage rows={rows} resolveEvidence={async () => null} limit={8} />);
    expect(el.querySelectorAll("tbody tr")).toHaveLength(8);
    const more = el.querySelector<HTMLAnchorElement>("a.more")!;
    expect(more.getAttribute("href")).toBe("#/audit");
    expect(text(more)).toBe("See all 12 actions");
  });

  it("the overview puts orders with a discrepancy first, so a short list never hides one", () => {
    const rows = [order(1), order(2), order(3), { ...order(4), state: "DISCREPANCY", stateLabel: "Discrepancy", isDiscrepancy: true, stepIndex: 2 }];
    const views = { stock: [], prices: [], audit: [], orders: rows, today: { salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null, byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" } } };
    const el = mount(<HomePage views={views} forecast={null} forecastLoading={false} resolveEvidence={async () => null} />);
    const titles = [...el.querySelectorAll("article h3")].map((h) => text(h));
    expect(titles).toEqual(["Vendor 4", "Vendor 1", "Vendor 2"]);
  });

  it("the overview also puts a short count first while the delivery is still being checked", () => {
    const short = { ...order(4), state: "DELIVERY_PENDING", stateLabel: "Delivery pending", stepIndex: 1, discrepancyTexts: ["Sakthi curd 1 L: counted 28 L of 30 L ordered"] };
    const views = { stock: [], prices: [], audit: [], orders: [order(1), order(2), order(3), short], today: { salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null, byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" } } };
    const el = mount(<HomePage views={views} forecast={null} forecastLoading={false} resolveEvidence={async () => null} />);
    const titles = [...el.querySelectorAll("article h3")].map((h) => text(h));
    expect(titles).toEqual(["Vendor 4", "Vendor 1", "Vendor 2"]);
    expect(text(el)).toContain("The delivery does not match the order");
  });

  it("the overview's top orders are real orders: newer drafts the owner said no to never push them out", () => {
    const draft = (i: number): OrderRow => ({ ...order(i), supplierName: `Draft ${i}`, state: "DRAFT", stateLabel: "Draft", stepIndex: -1 });
    const rows = [draft(1), draft(2), draft(3), draft(4), order(5), order(6)];
    const views = { stock: [], prices: [], audit: [], orders: rows, today: { salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null, byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" } } };
    const el = mount(<HomePage views={views} forecast={null} forecastLoading={false} resolveEvidence={async () => null} />);
    expect([...el.querySelectorAll("article h3")].map((h) => text(h))).toEqual(["Vendor 5", "Vendor 6"]);
    expect(el.querySelector(".drafts")).toBeNull();
    expect(text(el)).not.toMatch(/Draft/);
  });

  it("shows no link when everything fits", () => {
    const el = mount(<OrdersPage rows={[order(1)]} limit={3} />);
    expect(el.querySelector("a.more")).toBeNull();
  });
});
