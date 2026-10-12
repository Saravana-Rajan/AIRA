import { afterEach, describe, expect, it } from "vitest";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { PricesPage } from "../src/pages/PricesPage";
import { categoryOf, priceRows, PRICE_CATEGORIES, type PriceRow } from "../src/lib/selectors/prices";
import { buildViews } from "../src/lib/views";

afterEach(cleanup);

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
const DAY = 86_400_000;

const products: any[] = [
  { id: "arun_icecream_box", sku: "arun_icecream_box", name: "Arun Ice Cream Family Box", brand: "Arun", unit: "box", price: 180 },
  { id: "aavin_milk_1l", sku: "aavin_milk_1l", name: "Aavin Milk 1 L", brand: "Aavin", unit: "L", packSizeL: 1, price: 44, updatedAt: "2026-10-01T04:00:00+00:00" },
  { id: "aavin_milk_500ml", sku: "aavin_milk_500ml", name: "Aavin Milk 500 ml", brand: "Aavin", unit: "L", packSizeL: 0.5, price: 44,
    updatedAt: NOW - 3 * DAY, priceUpdatedAt: NOW - 2 * DAY },
  { id: "aavin_buttermilk_200ml", sku: "aavin_buttermilk_200ml", name: "Aavin Buttermilk 200 ml", unit: "pack", packSizeL: 0.2, price: 10 },
  { id: "aavin_butter_100g", sku: "aavin_butter_100g", name: "Aavin Butter 100 g", unit: "pack", packSizeG: 100, price: 55 },
  { id: "aavin_paneer_200g", sku: "aavin_paneer_200g", name: "Aavin Paneer 200 g", unit: "pack", packSizeG: 200, price: 100 },
  { id: "aavin_ghee_500ml", sku: "aavin_ghee_500ml", name: "Aavin Ghee 500 ml", unit: "jar", packSizeL: 0.5, price: 380, priceUpdatedAt: NOW - 3600_000 },
  { id: "sakthi_curd_1l", sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L", unit: "L", packSizeL: 1, price: 60 },
  { id: "old_lassi", sku: "old_lassi", name: "Masala Lassi 200 ml", unit: "pack", packSizeL: 0.2, price: 20, active: false },
  { id: "no_price", sku: "no_price", name: "Mystery Pack", unit: "pack" },
];

describe("categoryOf", () => {
  it("puts each product in a board group, buttermilk before butter and milk", () => {
    expect(categoryOf({ name: "Aavin Buttermilk 200 ml" })).toBe("buttermilk");
    expect(categoryOf({ name: "Aavin Butter 100 g" })).toBe("butter");
    expect(categoryOf({ name: "Aavin Milk 1 L" })).toBe("milk");
    expect(categoryOf({ name: "Sakthi Curd 1 L" })).toBe("curd");
    expect(categoryOf({ name: "Arun Ice Cream Family Box" })).toBe("ice cream");
    expect(categoryOf({ name: "Hatsun Icecream Cup", id: "x" })).toBe("ice cream");
    expect(categoryOf({ name: "Aavin Ghee 500 ml" })).toBe("ghee");
    expect(categoryOf({ name: "Aavin Paneer 200 g" })).toBe("paneer");
    expect(categoryOf({ name: "Masala Lassi" })).toBe("other");
    expect(PRICE_CATEGORIES).toEqual(["milk", "curd", "buttermilk", "ghee", "paneer", "butter", "ice cream", "other"]);
  });
});

describe("priceRows", () => {
  const rows = priceRows(products, NOW);

  it("lists active products only, grouped in board order, small packs first", () => {
    expect(rows.map((r) => r.sku)).toEqual([
      "aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_1l", "aavin_buttermilk_200ml", "aavin_ghee_500ml",
      "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box",
    ]);
    expect(rows.find((r) => r.sku === "old_lassi")).toBeUndefined();
  });

  it("keeps a product without a price, saying so, never inventing one", () => {
    const r = rows.find((x) => x.sku === "no_price");
    expect(r).toBeUndefined();
    const all = priceRows(products.filter((p) => p.id === "no_price"), NOW);
    expect(all).toEqual([]);
  });

  it("formats name, size, unit and price", () => {
    const milk = rows.find((r) => r.sku === "aavin_milk_500ml")!;
    expect(milk).toMatchObject({ name: "Aavin Milk 500 ml", size: "500 ml", unit: "L", price: 44, priceText: "₹44", category: "milk", categoryLabel: "Milk" });
    expect(rows.find((r) => r.sku === "aavin_butter_100g")!.size).toBe("100 g");
    expect(rows.find((r) => r.sku === "aavin_milk_1l")!.size).toBe("1 L");
    expect(rows.find((r) => r.sku === "arun_icecream_box")!.size).toBe("");
    expect(rows.find((r) => r.sku === "arun_icecream_box")!.categoryLabel).toBe("Ice cream");
  });

  it("takes last changed from priceUpdatedAt, else updatedAt, else says it was not changed", () => {
    const milk500 = rows.find((r) => r.sku === "aavin_milk_500ml")!;
    expect(milk500.lastChangedMs).toBe(NOW - 2 * DAY);
    expect(milk500.lastChangedText).toBe("8 Oct, 12:00 pm");
    const milk1 = rows.find((r) => r.sku === "aavin_milk_1l")!;
    expect(milk1.lastChangedMs).toBe(Date.parse("2026-10-01T04:00:00+00:00"));
    expect(milk1.lastChangedText).toBe("1 Oct, 9:30 am");
    const ghee = rows.find((r) => r.sku === "aavin_ghee_500ml")!;
    expect(ghee.lastChangedText).toBe("11:00 am");
    const curd = rows.find((r) => r.sku === "sakthi_curd_1l")!;
    expect(curd.lastChangedMs).toBeNull();
    expect(curd.lastChangedText).toBe("Not changed yet");
  });

  it("is part of the dashboard views", () => {
    const views = buildViews(
      { products, inventory: [], sales: [], payments: [], orders: [], deliveries: [], suppliers: [], auditLogs: [] } as any,
      NOW,
    );
    expect(views.prices.map((r) => r.sku)).toEqual(rows.map((r) => r.sku));
  });
});

describe("PricesPage", () => {
  const rows: PriceRow[] = [
    { sku: "a", name: "Aavin Milk 500 ml", size: "500 ml", unit: "L", price: 44, priceText: "₹44", category: "milk", categoryLabel: "Milk", lastChangedMs: 1, lastChangedText: "8 Oct, 12:00 pm" },
    { sku: "b", name: "Aavin Milk 1 L", size: "1 L", unit: "L", price: 44, priceText: "₹44", category: "milk", categoryLabel: "Milk", lastChangedMs: null, lastChangedText: "Not changed yet" },
    { sku: "c", name: "Arun Ice Cream Family Box", size: "", unit: "box", price: 180, priceText: "₹180", category: "ice cream", categoryLabel: "Ice cream", lastChangedMs: 2, lastChangedText: "11:00 am" },
  ];

  it("renders a read-only table with one group heading row per category and large price cells", () => {
    const el = mount(<PricesPage rows={rows} />);
    expect(text(el.querySelector("h2"))).toBe("Prices");
    const table = el.querySelector("table")!;
    expect(text(table.querySelector("caption"))).toMatch(/price/i);
    expect([...table.querySelectorAll("thead th")].map((h) => text(h))).toEqual(["Product", "Size", "Unit", "Price", "Last changed"]);
    expect([...table.querySelectorAll("thead th")].map((h) => h.getAttribute("scope"))).toEqual(["col", "col", "col", "col", "col"]);
    const groups = [...table.querySelectorAll("tbody th.price-group")].map((h) => text(h));
    expect(groups).toEqual(["Milk", "Ice cream"]);
    const body = table.querySelectorAll("tbody tr");
    expect(body).toHaveLength(5); // 2 group rows + 3 products
    expect(text(body[1].querySelector("th[scope=row] .cell__main"))).toBe("Aavin Milk 500 ml");
    expect(text(body[1])).toContain("500 ml");
    expect(findAllByText(body[1], ".price", "₹44")).toHaveLength(1);
    expect(text(body[2])).toContain("Not changed yet");
    expect(text(body[4])).toContain("₹180");
    expect(el.querySelector("input, button, select, textarea, form")).toBeNull();
    expect(text(el)).toMatch(/for the shop owner/i);
  });

  it("says when there are no prices yet", () => {
    const el = mount(<PricesPage rows={[]} />);
    expect(text(el)).toContain("No prices yet");
  });
});
