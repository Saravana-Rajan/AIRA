import type { InventoryDoc, Product, ProductUnit } from "@aira/contracts";
import type { Normalized } from "../normalize";
import { daysUntil, expiryLabel, formatQty } from "../format";

export interface StockRow {
  sku: string; name: string; onHand: number; onHandText: string;
  lowStock: boolean; daysToExpiry: number | null; expiryText: string | null;
}

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * One row per product (doc id = sku). A product with no inventory doc yet shows 0 and is low stock.
 * Expiry is the nearest one among batches that still hold stock; empty batches are ignored.
 * Sorted low stock first, then soonest expiry, then name.
 */
export function stockRows(
  products: Normalized<Product>[], inventory: Normalized<InventoryDoc>[], nowMs: number,
): StockRow[] {
  const bySku = new Map(inventory.map((d) => [d.id, d]));
  const rows = products.map((p): StockRow => {
    const inv = bySku.get(p.id);
    const onHand = num(inv?.onHand);
    const live = (inv?.batches ?? []).filter((b) => num(b.qty) > 0 && typeof b.expiryDate === "string");
    const daysToExpiry = live.length ? Math.min(...live.map((b) => daysUntil(b.expiryDate, nowMs))) : null;
    return {
      sku: p.id,
      name: String(p.name ?? p.id),
      onHand,
      onHandText: formatQty(onHand, p.unit as ProductUnit),
      lowStock: onHand <= num(p.reorderThreshold),
      daysToExpiry,
      expiryText: expiryLabel(daysToExpiry),
    };
  });
  const FAR = Number.MAX_SAFE_INTEGER;
  return rows.sort((a, b) =>
    Number(b.lowStock) - Number(a.lowStock) ||
    (a.daysToExpiry ?? FAR) - (b.daysToExpiry ?? FAR) ||
    a.name.localeCompare(b.name));
}
