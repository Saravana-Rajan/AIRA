import type { InventoryDoc, Product } from "@aira/contracts";

export type StockRow = { sku: string; name: string; onHand: number; unit: string; low: boolean };

/** Join products with their inventory docs. A product with no inventory doc shows 0 and is low. */
export function toRows(products: Product[], inventory: InventoryDoc[]): StockRow[] {
  const bySku = new Map(inventory.map((i) => [i.sku, i.onHand]));
  return products
    .map((p) => {
      const onHand = bySku.get(p.sku) ?? 0;
      return { sku: p.sku, name: p.name, onHand, unit: p.unit, low: onHand <= p.reorderThreshold };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}
