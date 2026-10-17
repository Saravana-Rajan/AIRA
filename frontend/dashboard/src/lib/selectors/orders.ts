import type { Delivery, Discrepancy, Order, Product, Supplier } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatCount, formatLineQty, formatPrice, formatTimeIST, formatWhenIST, isQtyUnit, stateLabel } from "../format";

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

/**
 * A delivery discrepancy as backend/aira_live/business/reconcile.py writes it: the contract fields plus
 * `kind`, and `name` (UNKNOWN_INVOICE_LINE) or `fields` (INVOICE_UNREADABLE). expected/actual mean different
 * things per kind (ordered vs counted, agreed vs billed price, counted vs billed qty), so each kind gets its
 * own sentence; a bare "expected X, got Y" made the demo delivery read as two lines that contradict.
 */
type DiscrepancyEntry = Discrepancy & { kind?: unknown; name?: unknown; fields?: unknown };

/** The count comparisons. A kind-less entry (older shape) is the same comparison. */
const COUNT_KINDS = new Set(["SHORT_DELIVERY", "OVER_DELIVERY"]);
/** Same tolerance as reconcile.py's EPS: counts closer than this are equal. */
const COUNT_EPS = 1e-6;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const shown = (v: unknown): string | null =>
  typeof v === "number" || typeof v === "boolean" ? String(v) : textOrNull(v);

/** label: a product's name for its sku; qty: a quantity in that product's selling unit. */
function discrepancyText(
  x: DiscrepancyEntry, label: (sku: string, fallback?: unknown) => string, qty: (sku: string | null, n: number) => string,
): string {
  const sku = textOrNull(x.sku);
  const who = sku ? label(sku, x.name) : (textOrNull(x.field) ?? textOrNull(x.name) ?? "item");
  const exp = num(x.expected);
  const act = num(x.actual);
  const both = exp !== null && act !== null;
  switch (x.kind) {
    case "SHORT_DELIVERY":
      return both ? `${who}: counted ${qty(sku, act)} of ${qty(sku, exp)} ordered` : `${who}: count does not match the order`;
    case "OVER_DELIVERY":
      return both ? `${who}: counted ${qty(sku, act)}, ordered ${qty(sku, exp)}` : `${who}: count does not match the order`;
    case "UNORDERED_ITEM":
      return act !== null ? `${who}: not ordered, ${qty(sku, act)} delivered` : `${who}: delivered but not ordered`;
    case "INVOICE_UNREADABLE":
      return "Bill unreadable: please check it again";
    case "UNKNOWN_INVOICE_LINE": {
      const line = textOrNull(x.name);
      return line ? `Bill has an item not ordered: ${line}` : "Bill has an item that was not ordered";
    }
    case "PRICE_MISMATCH":
      return both
        ? `${who}: bill price ${formatPrice(act)}, agreed ${formatPrice(exp)}`
        : `${who}: bill price does not match the agreed price`;
    case "LINE_TOTAL_MISMATCH":
      return both
        ? `${who}: bill line total ${formatPrice(act)}, expected ${formatPrice(exp)}`
        : `${who}: bill line total does not add up`;
    case "INVOICE_QTY_MISMATCH":
      // Both numbers are in the bill's units (a crate, say), which may not be the product's selling unit.
      return both ? `${who}: bill says ${formatCount(act)}, counted ${formatCount(exp)}` : `${who}: bill quantity does not match the count`;
    case "TOTAL_MISMATCH":
      return both ? `Bill total ${formatPrice(act)}, lines add up to ${formatPrice(exp)}` : "Bill total does not match its lines";
    default: {
      const e = shown(x.expected);
      const a = shown(x.actual);
      const parts = [e !== null ? `expected ${e}` : null, a !== null ? `got ${a}` : null].filter(Boolean);
      return parts.length ? `${who}: ${parts.join(", ")}` : `${who}: does not match`;
    }
  }
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

/**
 * Names and measures order lines. Quantities are in the product's selling unit, as the backend stores them
 * (litres for milk and curd, else packs/jars/boxes): the line's own unit first, then the catalog's.
 */
export function lineReader(products: Normalized<Product>[]) {
  const bySku = new Map(products.map((p) => [p.id, p]));
  const name = (sku: string, fallback?: unknown): string =>
    textOrNull(bySku.get(sku)?.name) ?? textOrNull(fallback) ?? sku;
  const qty = (sku: string, n: unknown, lineUnit?: unknown): string => {
    const q = typeof n === "number" ? n : Number(n);
    if (!Number.isFinite(q)) return "an unknown amount";
    const p = bySku.get(sku);
    return formatLineQty(q, isQtyUnit(lineUnit) ? lineUnit : p?.unit, p?.packSizeL);
  };
  return { name, qty };
}
export type LineReader = ReturnType<typeof lineReader>;

/** "Aavin Milk 500 ml: 20 L (40 packs)" for each line of an order. */
export function lineText(lines: LineReader, it: { sku: string; qty: unknown; unit?: unknown; name?: unknown }): string {
  return `${lines.name(it.sku, it.name)}: ${lines.qty(it.sku, it.qty, it.unit)}`;
}

/**
 * A DRAFT order was read back to the owner but never sent: he said no, or has not said yes yet. Only sent
 * orders belong in the order list; a draft he later sends becomes ORDER_PLACED and moves across by itself.
 */
export function splitOrders(rows: OrderRow[]): { sent: OrderRow[]; notSent: OrderRow[] } {
  return { sent: rows.filter((r) => r.state !== "DRAFT"), notSent: rows.filter((r) => r.state === "DRAFT") };
}

export function orderRows(
  orders: Normalized<Order>[], deliveries: Normalized<Delivery>[],
  suppliers: Normalized<Supplier>[], products: Normalized<Product>[],
  nowMs: number = Date.now(),
): OrderRow[] {
  const supplierNames = new Map(suppliers.map((s) => [s.id, s.name]));
  const lines = lineReader(products);
  const updated = (o: Normalized<Order>) => toMs(o.updatedAt);

  return [...orders]
    .sort((a, b) => (updated(b) ?? 0) - (updated(a) ?? 0))
    .map((o): OrderRow => {
      const state = String(o.state);
      const isDiscrepancy = state === "DISCREPANCY";
      const stepIndex = isDiscrepancy
        ? ORDER_FLOW.indexOf("DELIVERY_RECONCILED")
        : ORDER_FLOW.indexOf(state as OrderFlowState);
      const orderNames = new Map((o.items ?? []).map((it) => [it.sku, it.name]));
      const orderUnits = new Map((o.items ?? []).map((it) => [it.sku, it.unit]));
      const label = (sku: string, fallback?: unknown) => lines.name(sku, textOrNull(fallback) ?? orderNames.get(sku));
      /** In the order line's selling unit, with packs for pouches: "19 L (38 packs)"; a bare number when unknown. */
      const qty = (sku: string | null, n: number) => (sku ? lines.qty(sku, n, orderUnits.get(sku)) : formatCount(n));
      const texts = deliveries
        .filter((d) => d.orderId === o.id)
        .flatMap((d) => {
          const written = (d.discrepancies ?? []) as DiscrepancyEntry[];
          // A count that differs from the order is shown even before reconcile writes it, but only once.
          const covered = new Set(
            written.filter((x) => typeof x.kind !== "string" || COUNT_KINDS.has(x.kind)).map((x) => x.sku),
          );
          const counts: DiscrepancyEntry[] = (d.items ?? []).flatMap((it) => {
            const want = num(it.expectedQty);
            const got = num(it.countedQty);
            if (want === null || got === null || Math.abs(got - want) < COUNT_EPS || covered.has(it.sku)) return [];
            const kind = got < want ? "SHORT_DELIVERY" : "OVER_DELIVERY";
            return [{ kind, sku: it.sku, name: it.name, expected: want, actual: got }];
          });
          return [...counts, ...written];
        })
        .map((x) => discrepancyText(x, label, qty));
      const discrepancyTexts = [...new Set(texts)];
      const updatedMs = updated(o);
      return {
        id: o.id,
        supplierName: supplierNames.get(o.supplierId) ?? textOrNull(o.supplierName) ?? o.supplierId,
        itemsText: (o.items ?? []).map((it) => lineText(lines, it)).join(", "),
        state,
        stateLabel: stateLabel(state),
        stepIndex,
        isDiscrepancy,
        vendorReply: textOrNull(o.vendorReply?.text),
        discrepancyTexts,
        updatedText: updatedMs !== null ? formatWhenIST(updatedMs, nowMs) : "",
        deliveryByText: deliveryByText(o.deliveryBy),
        note: textOrNull(o.note),
      };
    });
}
