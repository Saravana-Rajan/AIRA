import type { OrderRow } from "../lib/selectors/orders";
import { ORDER_FLOW } from "../lib/selectors/orders";
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

/** limit: on the overview, show only the newest few orders and link to the full list. */
export function OrdersPage(props: { rows: OrderRow[]; limit?: number }) {
  const shown = props.limit !== undefined ? props.rows.slice(0, props.limit) : props.rows;
  return (
    <section class="card card--orders" aria-labelledby="orders-title">
      <h2 id="orders-title" class="card__title">Orders &amp; deliveries</h2>
      {props.rows.length === 0 ? (
        <p class="empty">No supplier orders yet. When the owner orders from a supplier through AIRA, it shows here.</p>
      ) : (
        <div class="orders">
          {shown.map((o) => (
            <OrderCard key={o.id} o={o} />
          ))}
        </div>
      )}
      {shown.length < props.rows.length && (
        <a class="more" href="#/orders">See all {props.rows.length} orders</a>
      )}
    </section>
  );
}
