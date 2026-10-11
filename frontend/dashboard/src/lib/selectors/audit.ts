import type { AuditLog, Delivery, Order, Product, Supplier } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatINR, formatWhenIST } from "../format";
import { lineReader, type LineReader } from "./orders";

export interface AuditRow { id: string; timeText: string; actorLabel: string; actionText: string; outcomeText: string; evidenceUri: string | null }

/** The shop records an audit row can point at (refType/refId), so a row can name the order, product or supplier. */
export interface AuditRefs {
  products?: Normalized<Product>[];
  orders?: Normalized<Order>[];
  deliveries?: Normalized<Delivery>[];
  suppliers?: Normalized<Supplier>[];
}

const ACTORS: Record<string, string> = { owner: "Owner", aira: "AIRA", system: "System" };

/** How the order reached the vendor (tools/orders.py: the messaging channel, or "manual" when the owner sent it). */
const SENT_ON: Record<string, string> = {
  telegram: "Sent on Telegram", whatsapp: "Sent on WhatsApp", demo: "Sent to the demo vendor", manual: "Sent by the owner",
};
/** The proof behind a supplier payment (tools/payments.py: cash notes seen by the camera, or the owner's word for UPI). */
const PAID_WITH: Record<string, string> = {
  cash_count: "Cash, checked by camera", user_confirm: "UPI, owner confirmed", soundbox: "Soundbox confirmed",
};
const BILL: Record<string, string> = { DELIVERY_RECONCILED: "Bill matches", DISCREPANCY: "Bill does not match" };
const ORDERS_GO: Record<string, string> = {
  telegram: "Orders go on Telegram", whatsapp: "Orders go on WhatsApp", call: "Orders by phone call",
};

const NUMBER = String.raw`-?\d+(?:\.\d+)?`;
const PAID = new RegExp(`^([a-z_]+):(${NUMBER})$`);
const COUNTED = new RegExp(`^(.+)=(${NUMBER})$`);
const RECEIVED = new RegExp(`^\\+?(${NUMBER})$`);

/** A backend code in plain words: "needs_recount" → "Needs recount", "zone=fridge2" → "Zone fridge2"; null when empty. */
function humanise(code: unknown): string | null {
  const words = String(code ?? "").trim().replace(/[_\-=:]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  if (!words) return null;
  if (words === "ok") return "OK";
  return words[0].toUpperCase() + words.slice(1);
}

/** "a", "a and b", "a, b and c". */
function listJoin(parts: string[]): string {
  return parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function textOrNull(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

/** "20 L (40 packs) of Aavin Milk 500 ml and 25 packs of Aavin Paneer 200 g"; past two lines, "and N more items". */
function itemsPhrase(items: Order["items"], lines: LineReader): string {
  const parts = items.map((it) => `${lines.qty(it.sku, it.qty, it.unit)} of ${lines.name(it.sku, it.name)}`);
  if (parts.length <= 2) return listJoin(parts);
  const more = parts.length - 2;
  return `${parts[0]}, ${parts[1]} and ${more} more ${more === 1 ? "item" : "items"}`;
}

interface Explained { action: string | null; outcome: string | null }

function explainer(refs: AuditRefs) {
  const lines = lineReader(refs.products ?? []);
  const orders = new Map((refs.orders ?? []).map((o) => [o.id, o]));
  const deliveries = new Map((refs.deliveries ?? []).map((d) => [d.id, d]));
  const supplierNames = new Map((refs.suppliers ?? []).map((s) => [s.id, s.name]));
  const productNames = new Map((refs.products ?? []).map((p) => [p.id, textOrNull(p.name)]));

  const supplierOf = (o: Normalized<Order> | undefined): string | null =>
    o ? (textOrNull(supplierNames.get(o.supplierId)) ?? textOrNull(o.supplierName)) : null;
  // Suppliers are server-only on the dashboard, so a supplier is named from any order sent to it.
  const supplierById = (id: string): string | null =>
    textOrNull(supplierNames.get(id)) ?? (refs.orders ?? []).map((o) => (o.supplierId === id ? textOrNull(o.supplierName) : null)).find(Boolean) ?? null;
  const from = (who: string | null) => (who ? ` from ${who}` : "");
  const skuWords = (sku: string) => sku.replace(/_/g, " ");

  return (l: Normalized<AuditLog>): Explained => {
    const outcome = String(l.outcome ?? "").trim();
    const refId = String(l.refId ?? "");
    const product = productNames.get(refId) ?? null;
    switch (l.action) {
      case "order_placed": {
        const order = orders.get(refId);
        const who = supplierOf(order);
        let action: string | null = null;
        if (order && order.items?.length) action = `Ordered ${itemsPhrase(order.items, lines)}${from(who)}`;
        else if (who) action = `Placed an order with ${who}`;
        return { action, outcome: SENT_ON[outcome] ?? null };
      }
      case "supplier_paid": {
        const m = PAID.exec(outcome);
        if (!m) return { action: null, outcome: null };
        const who = supplierOf(orders.get(refId)) ?? "a supplier";
        return { action: `Paid ${who} ${formatINR(Number(m[2]))}`, outcome: PAID_WITH[m[1]] ?? humanise(m[1]) };
      }
      case "delivery_counted": {
        const m = COUNTED.exec(outcome);
        if (!m) return { action: null, outcome: null };
        const [, sku, qty] = m;
        const delivery = deliveries.get(refId);
        const line = delivery?.items?.find((i) => i.sku === sku);
        const name = lines.name(sku, line?.name ?? skuWords(sku));
        const who = supplierOf(delivery ? orders.get(delivery.orderId) : undefined);
        return { action: `Counted ${lines.qty(sku, Number(qty), line?.unit)} of ${name}${from(who)}`, outcome: "Added to stock" };
      }
      case "invoice_checked": {
        const delivery = deliveries.get(refId);
        const who = supplierOf(delivery ? orders.get(delivery.orderId) : undefined);
        return { action: `Checked the bill${from(who)}`, outcome: BILL[outcome] ?? null };
      }
      case "product_added":
        return { action: product && `New product: ${product}`, outcome: null };
      case "product_updated": {
        const fields = outcome.split(",").map((f) => f.trim().replace(/_/g, " ")).filter(Boolean);
        const changed = fields.length ? listJoin(fields) : "";
        return {
          action: product && `Changed ${product}`,
          outcome: changed ? `${changed[0].toUpperCase()}${changed.slice(1)} changed` : null,
        };
      }
      case "product_disabled":
        return { action: product && `Stopped selling ${product}`, outcome: outcome === "disabled" ? "Taken off sale" : null };
      case "stock_received": {
        const m = RECEIVED.exec(outcome);
        if (!m) return { action: null, outcome: null };
        const name = product ?? skuWords(refId);
        return { action: `Received ${lines.qty(refId, Number(m[1]))} of ${name} without an order`, outcome: "Added to stock" };
      }
      case "supplier_added": {
        const who = supplierById(refId);
        return { action: who ? `New supplier: ${who}` : "New supplier added", outcome: ORDERS_GO[outcome] ?? null };
      }
      default:
        return { action: null, outcome: null };
    }
  };
}

/**
 * The audit trail, newest first, in plain sentences: known actions name the product, supplier, litres or packs
 * and rupees they refer to (from refs); anything else falls back to the action and outcome codes in plain words.
 * Times carry their day when not today (formatWhenIST against nowMs).
 */
export function auditRows(logs: Normalized<AuditLog>[], nowMs: number = Date.now(), refs: AuditRefs = {}): AuditRow[] {
  const explain = explainer(refs);
  const at = (l: Normalized<AuditLog>) => toMs(l.at);
  return [...logs]
    .sort((a, b) => (at(b) ?? 0) - (at(a) ?? 0))
    .map((l): AuditRow => {
      const ms = at(l);
      const said = explain(l);
      return {
        id: l.id,
        timeText: ms !== null ? formatWhenIST(ms, nowMs) : "",
        actorLabel: ACTORS[String(l.actor)] ?? String(l.actor),
        actionText: said.action ?? humanise(l.action) ?? "Action not recorded",
        outcomeText: said.outcome ?? humanise(l.outcome) ?? "Not recorded",
        evidenceUri: l.evidenceUri ?? null,
      };
    });
}
