import type { OrderRow } from "../lib/selectors/orders";
import { ORDER_FLOW, splitOrders } from "../lib/selectors/orders";
import { stateLabel } from "../lib/format";

function stepClass(i: number, o: OrderRow): string {
  if (i < o.stepIndex) return "track__step done";
  if (i === o.stepIndex) return o.isDiscrepancy ? "track__step now problem" : "track__step now";
  return "track__step";
}

/** One supplier order: what was ordered, where it is on the order → delivery → payment track, and any mismatch. */
function OrderCard({ o }: { o: OrderRow }) {
  const headingId = `order-${o.id}`;
  return (
    <article class={o.isDiscrepancy ? "order order--discrepancy" : "order"} aria-labelledby={headingId}>
      <div class="order__head">
        <div class="order__who">
          <h3 id={headingId} class="order__title">{o.supplierName}</h3>
          <p class="order__items">{o.itemsText || "No items listed"}</p>
        </div>
        <p class="order__state">
          <span class={o.isDiscrepancy ? "badge badge--low" : "badge badge--state"}>{o.stateLabel}</span>
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
            <div class="order__extra">
              <dt>Deliver by:</dt>{" "}
              <dd>{o.deliveryByText}</dd>
            </div>
          )}
          {o.note && (
            <div class="order__extra">
              <dt>Note:</dt>{" "}
              <dd>{o.note}</dd>
            </div>
          )}
        </dl>
      )}
      {o.vendorReply && (
        <p class="order__reply">
          Vendor replied: <q>{o.vendorReply}</q>
        </p>
      )}
    </article>
  );
}

/** Orders read back to the owner that never went to the vendor: plain text, no progress track. */
function NotSent({ rows }: { rows: OrderRow[] }) {
  return (
    <section class="drafts" aria-labelledby="drafts-title">
      <h3 id="drafts-title" class="drafts__title">Not sent</h3>
      <p class="drafts__note">Read back to the owner but not sent to the supplier.</p>
      <ul class="drafts__list">
        {rows.map((o) => (
          <li key={o.id}>{[o.supplierName, o.itemsText || "No items listed", o.updatedText].filter(Boolean).join(" · ")}</li>
        ))}
      </ul>
    </section>
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
