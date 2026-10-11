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
}

function textOrNull(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
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
      };
    });
}
