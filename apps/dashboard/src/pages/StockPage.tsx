import type { StockRow } from "../lib/selectors/stock";

function expiryClass(days: number | null): string {
  if (days === null) return "expiry";
  if (days < 0) return "expiry expiry--past";
  if (days <= 1) return "expiry expiry--soon";
  return "expiry";
}

/** Stock on hand per product, low stock first. Every status is a word, never colour alone. */
export function StockPage(props: { rows: StockRow[] }) {
  return (
    <section class="card card--stock" aria-labelledby="stock-title">
      <h2 id="stock-title" class="card__title">Stock</h2>
      {props.rows.length === 0 ? (
        <p class="empty">No products yet. They appear here once the shop's catalog is loaded.</p>
      ) : (
        <div class="table-wrap">
          <table class="table">
            <caption>Stock on hand, low stock first</caption>
            <thead>
              <tr>
                <th scope="col">Product</th>
                <th scope="col" class="num">On hand</th>
                <th scope="col">Status</th>
                <th scope="col" class="wide-only">Expiry</th>
              </tr>
            </thead>
            <tbody>
              {props.rows.map((r) => (
                <tr key={r.sku} class={r.lowStock ? "row--low" : undefined}>
                  <th scope="row">
                    <span class="cell__main">{r.name}</span>
                    <span class={`narrow-only ${r.expiryText ? expiryClass(r.daysToExpiry) : "muted"}`}>
                      {r.expiryText ?? "Expiry not recorded"}
                    </span>
                  </th>
                  <td class="num">{r.onHandText}</td>
                  <td>
                    <span class={r.lowStock ? "badge badge--low" : "badge badge--ok"}>{r.lowStock ? "Low stock" : "OK"}</span>
                  </td>
                  <td class="wide-only">
                    {r.expiryText ? <span class={expiryClass(r.daysToExpiry)}>{r.expiryText}</span> : <span class="muted">Not recorded</span>}
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
