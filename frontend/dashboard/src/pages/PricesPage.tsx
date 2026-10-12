import type { JSX } from "preact";
import type { PriceRow } from "../lib/selectors/prices";
import { DataTable } from "../components/DataTable";

/**
 * The owner's price board: every active product with its price and when it last changed, grouped by category.
 * Read-only and for the shop owner's own reference (large type, light theme); it is not a customer display.
 */
export function PricesPage(props: { rows: PriceRow[] }) {
  const body: JSX.Element[] = [];
  let lastGroup: string | null = null;
  for (const r of props.rows) {
    if (r.category !== lastGroup) {
      lastGroup = r.category;
      body.push(
        <tr key={`g-${r.category}`} class="row--group">
          <th scope="colgroup" colSpan={5} class="price-group">{r.categoryLabel}</th>
        </tr>,
      );
    }
    body.push(
      <tr key={r.sku}>
        <th scope="row">
          <span class="cell__main">{r.name}</span>
          <span class="narrow-only muted">{[r.size, r.unit].filter(Boolean).join(" · ")}</span>
        </th>
        <td class="wide-only">{r.size || <span class="muted">—</span>}</td>
        <td class="wide-only">{r.unit}</td>
        <td class="num price">{r.priceText}</td>
        <td class="time">{r.lastChangedText}</td>
      </tr>,
    );
  }
  return (
    <section class="card card--prices" aria-labelledby="prices-title">
      <h2 id="prices-title" class="card__title">Prices</h2>
      <p class="card__lead">Today's selling prices, for the shop owner. They change only through AIRA ("curd is now 65 rupees").</p>
      {props.rows.length === 0 ? (
        <p class="empty">No prices yet. They appear here once the shop's catalog is loaded.</p>
      ) : (
        <DataTable
          caption="Price list by category, smaller packs first"
          class="table--prices"
          columns={[
            { label: "Product" },
            { label: "Size", wideOnly: true },
            { label: "Unit", wideOnly: true },
            { label: "Price", num: true },
            { label: "Last changed" },
          ]}
        >
          {body}
        </DataTable>
      )}
    </section>
  );
}
