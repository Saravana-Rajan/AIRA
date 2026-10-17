import type { Payment, Product, Sale } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatCount, formatINR, formatQty, formatTimeIST, isQtyUnit, startOfDayIST } from "../format";

export type SaleMethod = "cash" | "upi" | "pending";

/** One of today's sales for the sales table: newest first. */
export interface RecentSale {
  id: string;
  /** IST clock time, "6:05 pm". */
  time: string;
  /** "2 × Aavin Milk 1 L, 1 × Sakthi curd 1 L", cut short with an ellipsis when long. */
  items: string;
  amountText: string;
  /** The confirmed incoming payment's method, or pending when none is confirmed yet. */
  method: SaleMethod;
}

export interface TodaySummary {
  salesCount: number; revenue: number; revenueText: string; topProduct: string | null;
  byMethod: { cash: number; upi: number }; byMethodText: { cash: string; upi: string };
  /** Average sale (revenue / count), "—" when there were no sales. */
  avgText: string;
  /** Units of the top product sold today ("3 boxes"), null when there is no top product. */
  topProductQty: string | null;
  /** Up to RECENT_MAX of today's completed sales, newest first. */
  recent: RecentSale[];
}

const DAY_MS = 86_400_000;
export const RECENT_MAX = 20;
const ITEMS_MAX_CHARS = 60;

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Today's sales and takings, using the IST calendar day that contains nowMs.
 * A sale counts when it is SALE_COMPLETED and its completedAt (else createdAt) falls today.
 * A payment counts when it is a CONFIRMED incoming payment and either its createdAt falls
 * today or, when it has no createdAt (not in spec §5 yet), it belongs to a sale counted today.
 */
export function todaySummary(
  sales: Normalized<Sale>[], payments: Normalized<Payment>[], products: Normalized<Product>[], nowMs: number,
): TodaySummary {
  const start = startOfDayIST(nowMs);
  const end = start + DAY_MS;
  const isToday = (ms: number | null) => ms !== null && ms >= start && ms < end;
  const saleAt = (s: Normalized<Sale>): number | null => {
    const r = s as Record<string, unknown>;
    return toMs(r.completedAt ?? r.createdAt);
  };

  const done = sales.filter((s) => (s as Record<string, unknown>).state === "SALE_COMPLETED" && isToday(saleAt(s)));
  const revenue = done.reduce((acc, s) => acc + num(s.total), 0);

  const bySku = new Map(products.map((p) => [p.id, p]));
  const name = (sku: string) => bySku.get(sku)?.name ?? sku;

  const revenueBySku = new Map<string, number>();
  const qtyBySku = new Map<string, number>();
  for (const s of done) {
    for (const it of s.items ?? []) {
      revenueBySku.set(it.sku, (revenueBySku.get(it.sku) ?? 0) + num(it.lineTotal));
      qtyBySku.set(it.sku, (qtyBySku.get(it.sku) ?? 0) + num(it.qty));
    }
  }
  let topSku: string | null = null;
  let best = -1;
  for (const [sku, v] of revenueBySku) if (v > best) { best = v; topSku = sku; }
  const topProduct = topSku ? name(topSku) : null;
  let topProductQty: string | null = null;
  if (topSku) {
    const q = qtyBySku.get(topSku) ?? 0;
    const unit = bySku.get(topSku)?.unit;
    topProductQty = isQtyUnit(unit) ? formatQty(q, unit) : formatCount(q);
  }

  const todaySaleIds = new Set(done.map((s) => s.id));
  const todayPaymentIds = new Set(done.map((s) => s.paymentId).filter((x): x is string => typeof x === "string"));
  const confirmedIn = payments.filter((p) => {
    if (p.direction !== "in" || p.state !== "CONFIRMED") return false;
    const created = toMs((p as Record<string, unknown>).createdAt);
    if (created !== null) return isToday(created);
    return todayPaymentIds.has(p.id) || (p.refType === "sale" && todaySaleIds.has(p.refId));
  });
  const sumFor = (m: "cash" | "upi") =>
    confirmedIn.filter((p) => p.method === m).reduce((a, p) => a + num(p.amount), 0);
  const byMethod = { cash: sumFor("cash"), upi: sumFor("upi") };

  // The sales table: newest first. A sale's payment is the one it names (paymentId) or the one that names it.
  const paymentById = new Map(confirmedIn.map((p) => [p.id, p]));
  const paymentBySale = new Map(confirmedIn.filter((p) => p.refType === "sale").map((p) => [p.refId, p]));
  const methodOf = (s: Normalized<Sale>): SaleMethod => {
    const p = (typeof s.paymentId === "string" ? paymentById.get(s.paymentId) : undefined) ?? paymentBySale.get(s.id);
    return p?.method === "cash" || p?.method === "upi" ? p.method : "pending";
  };
  const itemsOf = (s: Normalized<Sale>): string => {
    const t = (s.items ?? []).map((it) => `${formatCount(num(it.qty))} × ${name(it.sku)}`).join(", ");
    return t.length > ITEMS_MAX_CHARS ? `${t.slice(0, ITEMS_MAX_CHARS - 1).trimEnd()}…` : t;
  };
  const recent: RecentSale[] = [...done]
    .sort((a, b) => (saleAt(b) ?? 0) - (saleAt(a) ?? 0))
    .slice(0, RECENT_MAX)
    .map((s) => ({ id: s.id, time: formatTimeIST(saleAt(s) ?? 0), items: itemsOf(s), amountText: formatINR(num(s.total)), method: methodOf(s) }));

  return {
    salesCount: done.length,
    revenue,
    revenueText: formatINR(revenue),
    topProduct,
    byMethod,
    byMethodText: { cash: formatINR(byMethod.cash), upi: formatINR(byMethod.upi) },
    avgText: done.length ? formatINR(revenue / done.length) : "—",
    topProductQty,
    recent,
  };
}
