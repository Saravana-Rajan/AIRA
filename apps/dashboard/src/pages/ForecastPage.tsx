import type { ForecastItem } from "../lib/forecast";

/**
 * Reorder suggestions from aira-live (average daily sales vs stock on hand). `demoHistory` means backfilled
 * demo sales feed the numbers: the card says so, and never presents them as real shop sales.
 */
export function ForecastPage(props: { items: ForecastItem[] | null; loading: boolean; demoHistory?: boolean }) {
  const demo = props.demoHistory === true && !props.loading && props.items !== null;
  return (
    <section class="card card--forecast" aria-labelledby="forecast-title">
      <h2 id="forecast-title" class="card__title">Forecast</h2>
      {props.loading && <p role="status" class="empty">Loading forecast…</p>}
      {!props.loading && props.items === null && (
        <p role="status" class="empty">Forecast unavailable</p>
      )}
      {demo && (
        <p role="note" class="notice">Demo history: these numbers come from backfilled demo sales, not real shop sales.</p>
      )}
      {!props.loading && props.items !== null && props.items.length === 0 && (
        <p class="empty">No reorders needed right now.</p>
      )}
      {!props.loading && props.items !== null && props.items.length > 0 && (
        <div class="table-wrap">
          <table class="table">
            <caption>{demo ? "Suggested reorders, from average daily sales (demo history)" : "Suggested reorders, from average daily sales"}</caption>
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col" class="num wide-only">Sold per day</th>
                <th scope="col" class="num wide-only">On hand</th>
                <th scope="col" class="num">Suggested order</th>
              </tr>
            </thead>
            <tbody>
              {props.items.map((f) => (
                <tr key={f.sku}>
                  <th scope="row">
                    <span class="cell__main">{f.name}</span>
                    <span class="narrow-only muted">
                      Sells {f.dailyDemand.toFixed(1)} a day, {f.onHand} on hand
                    </span>
                  </th>
                  <td class="num wide-only">{f.dailyDemand.toFixed(1)}</td>
                  <td class="num wide-only">{f.onHand}</td>
                  <td class="num">
                    <strong>{f.suggestedOrder}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
