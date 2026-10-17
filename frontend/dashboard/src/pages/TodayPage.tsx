import type { JSX } from "preact";
import type { SaleMethod, TodaySummary } from "../lib/selectors/today";
import { DataTable } from "../components/DataTable";

/** 20px line icons in the menu's style (stroke = currentColor). Decorative: the label names the number. */
const ICONS: Record<string, JSX.Element> = {
  revenue: (
    <>
      <path d="M6 4h12M6 9h12M6 4c5 0 7 2 7 5s-2 5-7 5l7 6" />
    </>
  ),
  sales: (
    <>
      <path d="M4 6h2l2.2 9.5h9.6L20 8H7" />
      <circle cx="9.5" cy="19" r="1.5" />
      <circle cx="17" cy="19" r="1.5" />
    </>
  ),
  average: (
    <>
      <path d="M3 17l5-5 4 4 7-8" />
      <path d="M15 8h4v4" />
    </>
  ),
  top: (
    <>
      <path d="M12 3l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.4l-5.3 2.8 1.1-5.9L3.5 9.2l5.9-.8z" />
    </>
  ),
  cash: (
    <>
      <rect x="2.5" y="6" width="19" height="12" rx="2" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
};

function Icon({ name }: { name: string }) {
  return (
    <svg class="kpi__icon-svg" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
      {ICONS[name]}
    </svg>
  );
}

const METHOD_LABEL: Record<SaleMethod, string> = { cash: "Cash", upi: "UPI", pending: "Pending" };

const pct = (part: number, whole: number) => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : "0%");

/**
 * Today's takings (IST day): four equal key numbers, the cash/UPI split as a stacked bar with a legend, and
 * today's sales as a table (newest first). On the full page the outer card is transparent (CSS), so the
 * tiles, the payments card and the table float on the page like the overview's.
 */
export function TodayPage(props: { summary: TodaySummary }) {
  const s = props.summary;
  const paid = s.byMethod.cash + s.byMethod.upi;
  const recent = s.recent ?? [];
  const tiles: { key: string; label: string; value: string; note?: string | null }[] = [
    { key: "revenue", label: "Revenue", value: s.revenueText },
    { key: "sales", label: "Sales", value: String(s.salesCount) },
    { key: "average", label: "Average sale", value: s.avgText ?? "—" },
    { key: "top", label: "Top product", value: s.topProduct ?? "None yet", note: s.topProduct ? s.topProductQty : null },
  ];
  return (
    <section class="card card--today" aria-labelledby="today-title">
      <h2 id="today-title" class="card__title">Today</h2>
      <dl class="kpis kpis--today">
        {tiles.map((t) => (
          <div key={t.key} class="kpi">
            <span class="kpi__icon" aria-hidden="true"><Icon name={t.key} /></span>
            <dt class="kpi__label">{t.label}</dt>
            <dd class="kpi__value">{t.value}</dd>
            {t.note && <dd class="kpi__note">{t.note} sold</dd>}
          </div>
        ))}
      </dl>
      <div class="today__grid">
        <section class="subcard subcard--pay" aria-labelledby="today-pay-title">
          <h3 id="today-pay-title" class="subcard__title">Payments</h3>
          {paid > 0 ? (
            <div class="split" aria-hidden="true">
              <span class="split__cash" style={{ width: `${(s.byMethod.cash / paid) * 100}%` }} />
              <span class="split__upi" />
            </div>
          ) : (
            <>
              <div class="split-empty" aria-hidden="true" />
              <p class="empty">No payments yet</p>
            </>
          )}
          <dl class="paylegend">
            <div class="paylegend__row paylegend__row--cash">
              <dt>Cash received</dt>{" "}
              <dd>{s.byMethodText.cash}</dd>{" "}
              <dd class="paylegend__pct">{pct(s.byMethod.cash, paid)}</dd>
            </div>
            <div class="paylegend__row paylegend__row--upi">
              <dt>UPI received</dt>{" "}
              <dd>{s.byMethodText.upi}</dd>{" "}
              <dd class="paylegend__pct">{pct(s.byMethod.upi, paid)}</dd>
            </div>
          </dl>
        </section>
        <section class="subcard subcard--sales" aria-labelledby="today-sales-title">
          <h3 id="today-sales-title" class="subcard__title">Today's sales</h3>
          <DataTable
            caption="Newest first, up to 20 sales"
            class="table--sales"
            columns={[{ label: "Time" }, { label: "Items" }, { label: "Amount", num: true }, { label: "Payment" }]}
          >
            {recent.length === 0 ? (
              <tr>
                <td class="empty-cell" colSpan={4}>No sales yet today — sales made through AIRA appear here.</td>
              </tr>
            ) : (
              recent.map((r) => (
                <tr key={r.id}>
                  <th scope="row" class="time">{r.time}</th>
                  <td class="items" title={r.items}>{r.items || "No items listed"}</td>
                  <td class="num">{r.amountText}</td>
                  <td><span class={`badge badge--${r.method}`}>{METHOD_LABEL[r.method]}</span></td>
                </tr>
              ))
            )}
          </DataTable>
        </section>
      </div>
    </section>
  );
}
