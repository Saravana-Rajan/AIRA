import type { InventoryDoc, Product } from "@aira/contracts";
import { isActiveProduct } from "./selectors/stock";

export type StockRow = { sku: string; name: string; onHand: number; unit: string; low: boolean };

/** Join products with their inventory docs. A product with no inventory doc shows 0 and is low.
 * Products disabled by voice (`active: false`) are left out; new ones added by voice appear as they arrive. */
export function toRows(products: Product[], inventory: InventoryDoc[]): StockRow[] {
  const bySku = new Map(inventory.map((i) => [i.sku, i.onHand]));
  return products
    .filter(isActiveProduct)
    .map((p) => {
      const onHand = bySku.get(p.sku) ?? 0;
      return { sku: p.sku, name: p.name, onHand, unit: p.unit, low: onHand <= p.reorderThreshold };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}
