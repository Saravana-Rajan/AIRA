import type { OrderRow } from "../lib/selectors/orders";
import { ORDER_FLOW, splitOrders } from "../lib/selectors/orders";
import { stateLabel } from "../lib/format";

/** The colour family of an order card, its pill and its progress track (dashboard.css: .order--<tone>). */
export type OrderTone = "placed" | "delivery" | "checked" | "payment" | "paid" | "closed" | "problem";

const TONE_BY_STATE: Record<string, OrderTone> = {
  ORDER_PLACED: "placed",
  DELIVERY_PENDING: "delivery",
  DELIVERY_RECONCILED: "checked",
  PAYMENT_PENDING: "payment",
  PAYMENT_CONFIRMED: "paid",
  CLOSED: "closed",
  DISCREPANCY: "problem",
};

/** A mismatch (a DISCREPANCY state, or a short count still being checked) is always red; else the state's colour. */
export function orderTone(o: Pick<OrderRow, "state" | "isDiscrepancy" | "discrepancyTexts">): OrderTone {
  if (o.isDiscrepancy || o.discrepancyTexts.length > 0) return "problem";
  return TONE_BY_STATE[o.state] ?? "closed";
}

function stepClass(i: number, o: OrderRow): string {
  if (i < o.stepIndex) return "track__step done";
  if (i === o.stepIndex) return o.isDiscrepancy ? "track__step now problem" : "track__step now";
  return "track__step";
}

const CalendarIcon = () => (
  <svg class="chip__icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <rect x="3" y="4.5" width="18" height="16" rx="2" />
    <path d="M3 9.5h18M8 2.5v4M16 2.5v4" />
  </svg>
);
const NoteIcon = () => (
  <svg class="chip__icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M14 2.5H6a2 2 0 00-2 2v15a2 2 0 002 2h12a2 2 0 002-2V8.5z" />
    <path d="M14 2.5v6h6M8 13h8M8 17h5" />
  </svg>
);
const MessageIcon = () => (
  <svg class="reply__icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M4 5.5h16v10H9l-5 4z" />
  </svg>
);
const WarningIcon = () => (
  <svg class="drafts__icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path d="M12 3.5l9.5 16.5h-19z" />
    <path d="M12 10v4.5M12 17.5v.5" />
  </svg>
);

/** One supplier order: what was ordered, where it is on the order → delivery → payment track, and any mismatch. */
function OrderCard({ o }: { o: OrderRow }) {
  const headingId = `order-${o.id}`;
  const tone = orderTone(o);
  const cls = ["order", `order--${tone}`, o.isDiscrepancy && "order--discrepancy"].filter(Boolean).join(" ");
  return (
    <article class={cls} aria-labelledby={headingId}>
      <div class="order__head">
        <div class="order__who">
          <h3 id={headingId} class="order__title">{o.supplierName}</h3>
          <p class="order__items">{o.itemsText || "No items listed"}</p>
        </div>
        <p class="order__state">
          <span class={`badge badge--${tone}`}>{o.stateLabel}</span>
          {o.updatedText && <span class="order__time">Updated {o.updatedText}</span>}
        </p>
      </div>
      <ol class="track" aria-label="Order progress">
        {ORDER_FLOW.map((s, i) => (
          <li key={s} class={stepClass(i, o)} aria-current={i === o.stepIndex ? "step" : undefined}>
            <span class="track__node" aria-hidden="true" />
            <span class="track__label">{i === o.stepIndex && o.isDiscrepancy ? "Discrepancy found" : stateLabel(s)}</span>
            {i < o.stepIndex && <span class="sr-only">, done</span>}
          </li>
        ))}
      </ol>
      {o.discrepancyTexts.length > 0 && (
        <div class="mismatch">
          <p class="mismatch__title">The delivery does not match the order</p>
          <ul class="mismatch__list" aria-label="Discrepancies">
            {o.discrepancyTexts.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      )}
      {(o.deliveryByText || o.note) && (
        <dl class="order__extras">
          {o.deliveryByText && (
            <div class="order__extra chip chip--deliver">
              <CalendarIcon />
              <dt>Deliver by:</dt>{" "}
              <dd>{o.deliveryByText}</dd>
            </div>
          )}
          {o.note && (
            <div class="order__extra chip chip--note">
              <NoteIcon />
              <dt>Note:</dt>{" "}
              <dd>{o.note}</dd>
            </div>
          )}
        </dl>
      )}
      {o.vendorReply && (
        <div class="reply">
          <MessageIcon />
          <p class="order__reply">
            <span class="reply__who">Vendor replied:</span> <q>{o.vendorReply}</q>
          </p>
        </div>
      )}
    </article>
  );
}

/** Orders read back to the owner that never went to the vendor: a warning card, no progress track. */
function NotSent({ rows }: { rows: OrderRow[] }) {
  return (
    <section class="drafts" aria-labelledby="drafts-title">
      <div class="drafts__head">
        <WarningIcon />
        <h3 id="drafts-title" class="drafts__title">Not sent</h3>
      </div>
      <p class="drafts__note">Read back to the owner but not sent to the supplier.</p>
      <ul class="drafts__list">
        {rows.map((o) => (
          <li key={o.id} class="drafts__row">
            <strong>{o.supplierName}</strong>
            {` · ${[o.itemsText || "No items listed", o.updatedText].filter(Boolean).join(" · ")}`}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** How many sent orders sit at each status, as pills, in track order with mismatches first. */
function StatusStrip({ rows }: { rows: OrderRow[] }) {
  const counts = new Map<OrderTone, { label: string; n: number }>();
  for (const o of rows) {
    const tone = orderTone(o);
    const label = tone === "problem" ? "Discrepancy" : o.stateLabel;
    const cur = counts.get(tone);
    counts.set(tone, { label, n: (cur?.n ?? 0) + 1 });
  }
  const order: OrderTone[] = ["problem", "placed", "delivery", "checked", "payment", "paid", "closed"];
  const shown = order.filter((t) => counts.has(t));
  if (shown.length === 0) return null;
  return (
    <ul class="statusbar" aria-label="Orders by status">
      {shown.map((t) => (
        <li key={t} class={`badge badge--${t}`}>
          {counts.get(t)!.label} <b class="statusbar__n">{counts.get(t)!.n}</b>
        </li>
      ))}
    </ul>
  );
}

/**
 * Sent orders as cards; DRAFT orders (never sent) only in a small "Not sent" group on the full page.
 * limit: on the overview, show only the newest few sent orders and link to the full list.
 */
export function OrdersPage(props: { rows: OrderRow[]; limit?: number }) {
  const { sent, notSent } = splitOrders(props.rows);
  const overview = props.limit !== undefined;
  const shown = overview ? sent.slice(0, props.limit) : sent;
  return (
    <section class="card card--orders" aria-labelledby="orders-title">
      <h2 id="orders-title" class="card__title">Orders &amp; deliveries</h2>
      {!overview && sent.length > 0 && <StatusStrip rows={sent} />}
      {sent.length === 0 ? (
        <p class="empty">No supplier orders yet. When the owner orders from a supplier through AIRA, it shows here.</p>
      ) : (
        <div class="orders">
          {shown.map((o) => (
            <OrderCard key={o.id} o={o} />
          ))}
        </div>
      )}
      {shown.length < sent.length && (
        <a class="more" href="#/orders">See all {sent.length} orders</a>
      )}
      {!overview && notSent.length > 0 && <NotSent rows={notSent} />}
    </section>
  );
}
