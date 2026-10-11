import type { Delivery, Order, Product, Supplier } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatTimeIST, stateLabel } from "../format";

/** The happy path shown as a timeline. DISCREPANCY is a branch drawn at DELIVERY_RECONCILED. */
export const ORDER_FLOW = [
  "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED",
] as const;
export type OrderFlowState = (typeof ORDER_FLOW)[number];

export interface OrderRow {
  id: string; supplierName: string; itemsText: string; state: string; stateLabel: string;
  /** Index into ORDER_FLOW; -1 while the order is still a DRAFT (or in an unknown state). */
  stepIndex: number; isDiscrepancy: boolean; vendorReply: string | null; discrepancyTexts: string[]; updatedText: string;
  /** The delivery time the owner asked for, in his words plus the read IST date when there is one; null when none. */
  deliveryByText: string | null;
  /** The owner's free-text note to the vendor; null when none. */
  note: string | null;
}

function textOrNull(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

const DAY_IST = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });

/** "2026-10-12T06:00:00+05:30" → "12 Oct, 6:00 am"; a bare date "2026-10-12" → "12 Oct"; "" when unreadable. */
function deliveryAtText(at: unknown): string {
  if (typeof at !== "string" || !at) return "";
  if (at.length === 10) {
    const [y, m, d] = at.split("-").map(Number);
    return Number.isFinite(d) ? DAY_IST.format(Date.UTC(y, m - 1, d, 12)) : "";
  }
  const ms = Date.parse(at);
  return Number.isNaN(ms) ? "" : `${DAY_IST.format(ms)}, ${formatTimeIST(ms)}`;
}

/** The order's deliveryBy ({text, at}) as one line: "6 am tomorrow (12 Oct, 6:00 am)". */
export function deliveryByText(deliveryBy: unknown): string | null {
  if (typeof deliveryBy !== "object" || deliveryBy === null) return null;
  const { text, at } = deliveryBy as { text?: unknown; at?: unknown };
  const words = textOrNull(text);
  if (!words) return null;
  const when = deliveryAtText(at);
  return when ? `${words} (${when})` : words;
}

export function orderRows(
  orders: Normalized<Order>[], deliveries: Normalized<Delivery>[],
  suppliers: Normalized<Supplier>[], products: Normalized<Product>[],
): OrderRow[] {
  const productNames = new Map(products.map((p) => [p.id, p.name]));
  const supplierNames = new Map(suppliers.map((s) => [s.id, s.name]));
  const productName = (sku: string, fallback?: unknown) =>
    productNames.get(sku) ?? (typeof fallback === "string" && fallback ? fallback : sku);
  const updated = (o: Normalized<Order>) => toMs(o.updatedAt);

  return [...orders]
    .sort((a, b) => (updated(b) ?? 0) - (updated(a) ?? 0))
    .map((o): OrderRow => {
      const state = String(o.state);
      const isDiscrepancy = state === "DISCREPANCY";
      const stepIndex = isDiscrepancy
        ? ORDER_FLOW.indexOf("DELIVERY_RECONCILED")
        : ORDER_FLOW.indexOf(state as OrderFlowState);
      const discrepancyTexts = deliveries
        .filter((d) => d.orderId === o.id)
        .flatMap((d) => d.discrepancies ?? [])
        .map((x) => `${x.sku ? productName(x.sku) : (x.field ?? "item")}: expected ${String(x.expected)}, got ${String(x.actual)}`);
      const updatedMs = updated(o);
      return {
        id: o.id,
        supplierName: supplierNames.get(o.supplierId) ?? textOrNull(o.supplierName) ?? o.supplierId,
        itemsText: (o.items ?? [])
          .map((it) => `${productName(it.sku, it.name)} × ${it.qty}`)
          .join(", "),
        state,
        stateLabel: stateLabel(state),
        stepIndex,
        isDiscrepancy,
        vendorReply: textOrNull(o.vendorReply?.text),
        discrepancyTexts,
        updatedText: updatedMs !== null ? formatTimeIST(updatedMs) : "",
        deliveryByText: deliveryByText(o.deliveryBy),
        note: textOrNull(o.note),
      };
    });
}
