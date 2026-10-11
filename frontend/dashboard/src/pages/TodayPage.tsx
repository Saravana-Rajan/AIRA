import type { TodaySummary } from "../lib/selectors/today";

/** Today's takings (IST day): revenue, sales, the cash/UPI split and the best seller. */
export function TodayPage(props: { summary: TodaySummary }) {
  const s = props.summary;
  const paid = s.byMethod.cash + s.byMethod.upi;
  return (
    <section class="card card--today" aria-labelledby="today-title">
      <h2 id="today-title" class="card__title">Today</h2>
      {s.salesCount === 0 && <p class="empty">No sales yet today.</p>}
      <dl class="today">
        <div class="today__revenue">
          <dt>Revenue</dt>
          <dd>{s.revenueText}</dd>
        </div>
        <div>
          <dt>Sales</dt>
          <dd>{s.salesCount}</dd>
        </div>
        <div>
          <dt>Top product</dt>
          <dd>{s.topProduct ?? "None yet"}</dd>
        </div>
      </dl>
      {paid > 0 && (
        <div class="split" aria-hidden="true">
          <span class="split__cash" style={{ width: `${(s.byMethod.cash / paid) * 100}%` }} />
          <span class="split__upi" />
        </div>
      )}
      <dl class="methods">
        <div class="methods__cash">
          <dt>Cash received</dt>
          <dd>{s.byMethodText.cash}</dd>
        </div>
        <div class="methods__upi">
          <dt>UPI received</dt>
          <dd>{s.byMethodText.upi}</dd>
        </div>
      </dl>
    </section>
  );
}
