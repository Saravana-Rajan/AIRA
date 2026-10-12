// The owner's price board: active products, their price and when it last changed. Read-only; nothing here
// is meant for customers. Prices come from the product docs (Firestore is the source of truth).
import type { Product } from "@aira/contracts";
import type { Normalized } from "../normalize";
import { formatCount, formatPrice, formatWhenIST } from "../format";
import { toMs } from "../normalize";
import { isActiveProduct } from "./stock";

export const PRICE_CATEGORIES = ["milk", "curd", "buttermilk", "ghee", "paneer", "butter", "ice cream", "other"] as const;
export type PriceCategory = (typeof PRICE_CATEGORIES)[number];

const LABELS: Record<PriceCategory, string> = {
  milk: "Milk", curd: "Curd", buttermilk: "Buttermilk", ghee: "Ghee", paneer: "Paneer", butter: "Butter",
  "ice cream": "Ice cream", other: "Other",
};

// Matched in this order against the name and id, so "buttermilk" is neither butter nor milk.
const MATCH: Array<[PriceCategory, string[]]> = [
  ["buttermilk", ["buttermilk", "butter milk"]],
  ["ice cream", ["ice cream", "icecream", "ice-cream"]],
  ["butter", ["butter"]],
  ["paneer", ["paneer"]],
  ["ghee", ["ghee"]],
  ["curd", ["curd"]],
  ["milk", ["milk"]],
];

export interface PriceRow {
  sku: string; name: string; size: string; unit: string;
  price: number; priceText: string;
  category: PriceCategory; categoryLabel: string;
  lastChangedMs: number | null; lastChangedText: string;
}

/** The board group of a product from its name (and id); "other" when none fits. */
export function categoryOf(p: { name?: unknown; id?: unknown }): PriceCategory {
  const text = `${String(p.name ?? "")} ${String(p.id ?? "")}`.toLowerCase().replace(/_/g, " ");
  for (const [cat, words] of MATCH) if (words.some((w) => text.includes(w))) return cat;
  return "other";
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}

/** "500 ml", "1 L", "100 g", "1 kg"; "" when the product has no pack size. */
export function packSizeText(p: { packSizeL?: unknown; packSizeG?: unknown }): string {
  const l = num(p.packSizeL);
  if (l !== null) return l < 1 ? `${formatCount(Math.round(l * 1000))} ml` : `${formatCount(l)} L`;
  const g = num(p.packSizeG);
  if (g !== null) return g >= 1000 ? `${formatCount(g / 1000)} kg` : `${formatCount(g)} g`;
  return "";
}

function sortSize(p: { packSizeL?: unknown; packSizeG?: unknown }): number {
  return num(p.packSizeL) ?? (num(p.packSizeG) ?? 0) / 1000;
}

/**
 * One row per active product that has a price, in board order (milk, curd, buttermilk, ghee, paneer, butter,
 * ice cream, other), smaller packs first, then by name. A product without a price is left out: the board never
 * invents one. "Last changed" is `priceUpdatedAt` (set by product_update on a price change), else `updatedAt`.
 */
export function priceRows(products: Normalized<Product>[], nowMs: number): PriceRow[] {
  const rows: PriceRow[] = [];
  for (const p of products) {
    if (!isActiveProduct(p)) continue;
    const price = num(p.price);
    if (price === null) continue;
    const loose = p as unknown as Record<string, unknown>;
    const category = categoryOf(p);
    const lastChangedMs = toMs(loose.priceUpdatedAt) ?? toMs(loose.updatedAt);
    rows.push({
      sku: p.id,
      name: String(p.name ?? p.id),
      size: packSizeText(p),
      unit: String(p.unit ?? ""),
      price,
      priceText: formatPrice(price),
      category,
      categoryLabel: LABELS[category],
      lastChangedMs,
      lastChangedText: lastChangedMs === null ? "Not changed yet" : formatWhenIST(lastChangedMs, nowMs),
    });
  }
  const order = (c: PriceCategory) => PRICE_CATEGORIES.indexOf(c);
  const bySku = new Map(products.map((p) => [p.id, p]));
  return rows.sort((a, b) =>
    order(a.category) - order(b.category) ||
    sortSize(bySku.get(a.sku) ?? {}) - sortSize(bySku.get(b.sku) ?? {}) ||
    a.name.localeCompare(b.name));
}
