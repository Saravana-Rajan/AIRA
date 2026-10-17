import type { ForecastItem } from "../lib/forecast";
import { DataTable } from "../components/DataTable";

/**
 * Reorder suggestions from aira-live (average daily sales vs stock on hand). `demoHistory` means backfilled
 * demo sales feed the numbers: the card says so, and never presents them as real shop sales.
 * An empty list means no product has enough sales history yet (aira-live leaves out products with fewer
 * than 3 days of sales), not that nothing needs reordering.
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
        <p class="empty">Not enough sales yet to forecast.</p>
      )}
      {!props.loading && props.items !== null && props.items.length > 0 && props.items.every((f) => f.suggestedOrder <= 0) && (
        <p class="empty">No reorders needed right now.</p>
      )}
      {!props.loading && props.items !== null && props.items.length > 0 && (
        <DataTable
          caption={demo ? "Suggested reorders, from average daily sales (demo history)" : "Suggested reorders, from average daily sales"}
          columns={[
            { label: "Product" },
            { label: "Sold per day", num: true, wideOnly: true },
            { label: "On hand", num: true, wideOnly: true },
            { label: "Suggested order", num: true },
          ]}
        >
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
        </DataTable>
      )}
    </section>
  );
}
