import type { Payment, Product, Sale } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatINR, startOfDayIST } from "../format";

export interface TodaySummary {
  salesCount: number; revenue: number; revenueText: string; topProduct: string | null;
  byMethod: { cash: number; upi: number }; byMethodText: { cash: string; upi: string };
}

const DAY_MS = 86_400_000;

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

  const done = sales.filter((s) => {
    const r = s as Record<string, unknown>;
    return r.state === "SALE_COMPLETED" && isToday(toMs(r.completedAt ?? r.createdAt));
  });
  const revenue = done.reduce((acc, s) => acc + num(s.total), 0);

  const revenueBySku = new Map<string, number>();
  for (const s of done) {
    for (const it of s.items ?? []) revenueBySku.set(it.sku, (revenueBySku.get(it.sku) ?? 0) + num(it.lineTotal));
  }
  let topSku: string | null = null;
  let best = -1;
  for (const [sku, v] of revenueBySku) if (v > best) { best = v; topSku = sku; }
  const topProduct = topSku ? (products.find((p) => p.id === topSku)?.name ?? topSku) : null;

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

  return {
    salesCount: done.length,
    revenue,
    revenueText: formatINR(revenue),
    topProduct,
    byMethod,
    byMethodText: { cash: formatINR(byMethod.cash), upi: formatINR(byMethod.upi) },
  };
}
