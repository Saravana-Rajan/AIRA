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
    id: "o1", supplierName: "Sakthi vendor", itemsText: "Sakthi curd 1 L × 30", state: "DISCREPANCY",
    stateLabel: "Discrepancy", stepIndex: 2, isDiscrepancy: true, vendorReply: "OK, delivery at 6 pm",
    discrepancyTexts: ["Sakthi curd 1 L: expected 30, got 28"], updatedText: "6:05 pm",
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
});

describe("AuditPage", () => {
  const rows = [
    { id: "a1", timeText: "6:05 pm", actorLabel: "AIRA", actionText: "Sale completed", outcome: "ok", evidenceUri: "https://example.test/e.jpg" },
    { id: "a2", timeText: "6:00 pm", actorLabel: "Owner", actionText: "Order confirmed", outcome: "ok", evidenceUri: "gs://aira-evidence/x.jpg" },
    { id: "a3", timeText: "5:55 pm", actorLabel: "System", actionText: "Expiry check", outcome: "ok", evidenceUri: null },
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

  it("writes each outcome in plain sentence case", () => {
    const el = mount(
      <AuditPage
        resolveEvidence={async () => null}
        rows={[
          { ...rows[0], id: "x1", outcome: "ok" },
          { ...rows[0], id: "x2", outcome: "discrepancy" },
          { ...rows[0], id: "x3", outcome: "needs_recount" },
          { ...rows[0], id: "x4", outcome: "" },
        ]}
      />,
    );
    const outcomes = [...el.querySelectorAll("tbody tr")].map((r) => text(r.querySelectorAll("td")[3]));
    expect(outcomes).toEqual(["OK", "Discrepancy", "Needs recount", "Not recorded"]);
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
    const el = mount(<ForecastPage loading={false} items={[]} />);
    expect(text(el)).toContain("No reorders needed right now");
    expect(text(el)).not.toContain("Demo history");
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
    id: `o${i}`, supplierName: `Vendor ${i}`, itemsText: "Aavin milk 500 ml × 10", state: "ORDER_PLACED",
    stateLabel: "Order placed", stepIndex: 0, isDiscrepancy: false, vendorReply: null, discrepancyTexts: [], updatedText: "",
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
      id: `a${i}`, timeText: "6:05 pm", actorLabel: "AIRA", actionText: "Sale completed", outcome: "ok", evidenceUri: null,
    }));
    const el = mount(<AuditPage rows={rows} resolveEvidence={async () => null} limit={8} />);
    expect(el.querySelectorAll("tbody tr")).toHaveLength(8);
    const more = el.querySelector<HTMLAnchorElement>("a.more")!;
    expect(more.getAttribute("href")).toBe("#/audit");
    expect(text(more)).toBe("See all 12 actions");
  });

  it("the overview puts orders with a discrepancy first, so a short list never hides one", () => {
    const rows = [order(1), order(2), order(3), { ...order(4), state: "DISCREPANCY", stateLabel: "Discrepancy", isDiscrepancy: true, stepIndex: 2 }];
    const views = { stock: [], audit: [], orders: rows, today: { salesCount: 0, revenue: 0, revenueText: "₹0", topProduct: null, byMethod: { cash: 0, upi: 0 }, byMethodText: { cash: "₹0", upi: "₹0" } } };
    const el = mount(<HomePage views={views} forecast={null} forecastLoading={false} resolveEvidence={async () => null} />);
    const titles = [...el.querySelectorAll("article h3")].map((h) => text(h));
    expect(titles).toEqual(["Vendor 4", "Vendor 1", "Vendor 2"]);
  });

  it("shows no link when everything fits", () => {
    const el = mount(<OrdersPage rows={[order(1)]} limit={3} />);
    expect(el.querySelector("a.more")).toBeNull();
  });
});
