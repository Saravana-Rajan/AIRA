import { describe, it, expect } from "vitest";
import type { InventoryDoc, Product } from "@aira/contracts";
import { toRows } from "../src/lib/stock";

const products = [
  { sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L", unit: "L", reorderThreshold: 5 },
  { sku: "aavin_milk_500ml", name: "Aavin Milk 500 ml", unit: "L", reorderThreshold: 10 },
  { sku: "aavin_ghee_200ml", name: "Aavin Ghee 200 ml", unit: "jar", reorderThreshold: 2 },
] as unknown as Product[];

const inventory: InventoryDoc[] = [
  { sku: "sakthi_curd_1l", onHand: 2, batches: [], updatedAt: "2026-10-10T08:00:00Z" },
  { sku: "aavin_ghee_200ml", onHand: 9, batches: [], updatedAt: "2026-10-10T08:00:00Z" },
];

describe("toRows", () => {
  it("joins products with inventory, defaults missing stock to 0, flags low stock and sorts by name", () => {
    expect(toRows(products, inventory)).toEqual([
      { sku: "aavin_ghee_200ml", name: "Aavin Ghee 200 ml", onHand: 9, unit: "jar", low: false },
      { sku: "aavin_milk_500ml", name: "Aavin Milk 500 ml", onHand: 0, unit: "L", low: true },
      { sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L", onHand: 2, unit: "L", low: true },
    ]);
  });

  it("returns no rows when there are no products", () => {
    expect(toRows([], inventory)).toEqual([]);
  });

  it("hides a product disabled by voice (active === false) and shows one added by voice", () => {
    const withDisabled = [
      ...products,
      { sku: "arun_icecream_box", name: "Arun Ice Cream", unit: "box", reorderThreshold: 3, active: false },
      { sku: "aavin_lassi_200ml", name: "Aavin Lassi 200 ml", unit: "pack", reorderThreshold: 2, active: true },
    ] as unknown as Product[];
    const skus = toRows(withDisabled, inventory).map((r) => r.sku);
    expect(skus).not.toContain("arun_icecream_box");
    expect(skus).toContain("aavin_lassi_200ml");
    expect(skus).toContain("sakthi_curd_1l");
  });
});
