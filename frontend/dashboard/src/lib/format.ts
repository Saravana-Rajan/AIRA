// Pure formatting helpers for the Shop Dashboard.
// All dates and times are IST (Asia/Kolkata, UTC+05:30, no DST), never the browser's timezone.
import type { ProductUnit } from "@aira/contracts";

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

/** Integer rupees with Indian digit grouping, e.g. 100000 -> "₹1,00,000". */
export function formatINR(rupees: number): string {
  return INR.format(Math.round(rupees));
}

const INR_PAISE = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** A price from a bill: whole rupees as formatINR, paise only when there are some ("₹24.50"). */
export function formatPrice(rupees: number): string {
  return Number.isInteger(rupees) ? formatINR(rupees) : INR_PAISE.format(rupees);
}

/** Selling units from the catalog: L (milk, curd), pack, jar (ghee), box (ice cream). */
export type QtyUnit = ProductUnit;

const PLURALS: Record<Exclude<QtyUnit, "L">, string> = { pack: "packs", jar: "jars", box: "boxes" };

/** A bare count: whole numbers as they are, anything else to one decimal. */
export function formatCount(qty: number): string {
  return Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
}

export function formatQty(qty: number, unit: QtyUnit): string {
  const n = formatCount(qty);
  if (unit === "L") return `${n} L`;
  if (qty === 1) return `${n} ${unit}`;
  return `${n} ${PLURALS[unit] ?? unit}`;
}

export const IST_OFFSET_MS = 330 * 60 * 1000; // UTC+05:30, India has no DST
const DAY_MS = 86_400_000;

/** Epoch millis of 00:00 IST on the IST calendar day that contains nowMs. */
export function startOfDayIST(nowMs: number): number {
  return Math.floor((nowMs + IST_OFFSET_MS) / DAY_MS) * DAY_MS - IST_OFFSET_MS;
}

/** Whole IST calendar days from today (IST) until isoDate ("YYYY-MM-DD"); negative when past. */
export function daysUntil(isoDate: string, nowMs: number): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  const startOfThatDay = Date.UTC(y, m - 1, d) - IST_OFFSET_MS;
  return Math.round((startOfThatDay - startOfDayIST(nowMs)) / DAY_MS);
}

const TIME_IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
});

/** Clock time in IST, e.g. "6:05 pm". */
export function formatTimeIST(ms: number): string {
  // ICU may put a narrow or plain no-break space before am/pm; use a normal space for stable text.
  return TIME_IST.format(ms).replace(/[  ]/g, " ");
}

const STATE_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  ORDER_PLACED: "Order placed",
  DELIVERY_PENDING: "Delivery pending",
  DELIVERY_RECONCILED: "Delivery checked",
  DISCREPANCY: "Discrepancy",
  PAYMENT_PENDING: "Payment pending",
  PAYMENT_CONFIRMED: "Paid",
  CLOSED: "Closed",
  SALE_PENDING: "Sale pending",
  SALE_COMPLETED: "Sale completed",
  SALE_VOID: "Sale cancelled",
};

/** Plain-word label for an order or sale state; unknown states are shown as-is. */
export function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

/** Expiry in words for a days-left count (from daysUntil); null when the product has no expiry. */
export function expiryLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return "Expired";
  if (days === 0) return "Expires today";
  if (days === 1) return "Expires tomorrow";
  return `Expires in ${days} days`;
}
