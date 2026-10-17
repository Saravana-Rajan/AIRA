import type { DashboardViews } from "../lib/views";
import type { Forecast } from "../lib/forecast";
import { ORDER_FLOW, splitOrders } from "../lib/selectors/orders";
import { TodayPage } from "./TodayPage";
import { StockPage } from "./StockPage";
import { OrdersPage } from "./OrdersPage";
import { ForecastPage } from "./ForecastPage";
import { AuditPage, type ResolveEvidence } from "./AuditPage";

export { DEMO_SHOP_ID, SHOP_NAME } from "../lib/access";

const OVERVIEW_ORDERS = 3;

/** The headline numbers across the top of the overview, each a raised tile. The values are the views' own text. */
function Kpis(props: { views: DashboardViews; forecast: Forecast | null }) {
  const { views } = props;
  const lowStock = views.stock.filter((r) => r.lowStock).length;
  const { sent } = splitOrders(views.orders);
  const open = sent.filter((o) => o.stepIndex < ORDER_FLOW.length - 1).length;
  const flagged = sent.filter((o) => o.isDiscrepancy || o.discrepancyTexts.length > 0).length;
  const tiles: { label: string; value: string; note?: string; tone?: "warn" | "danger" | "ok" }[] = [
    { label: "Revenue today", value: views.today.revenueText, note: `${views.today.salesCount} ${views.today.salesCount === 1 ? "sale" : "sales"}` },
    { label: "Low stock", value: String(lowStock), note: `of ${views.stock.length} products`, tone: lowStock > 0 ? "warn" : "ok" },
    { label: "Open orders", value: String(open), note: flagged > 0 ? `${flagged} with a discrepancy` : "none flagged", tone: flagged > 0 ? "danger" : undefined },
  ];
  return (
    <section class="kpis" aria-label="Key numbers">
      {tiles.map((t) => (
        <div key={t.label} class={t.tone ? `kpi kpi--${t.tone}` : "kpi"}>
          <span class="kpi__label">{t.label}</span>
          <span class="kpi__value">{t.value}</span>
          {t.note && <span class="kpi__note">{t.note}</span>}
        </div>
      ))}
    </section>
  );
}

/** The overview: the key numbers, then every section as a card. The grid places them (1 column on a phone, 2–3 on a laptop). */
export function HomePage(props: {
  views: DashboardViews;
  forecast: Forecast | null;
  forecastLoading: boolean;
  resolveEvidence: ResolveEvidence;
}) {
  // A short list must never hide a delivery that does not match its order (or a short count not yet reconciled).
  const flagged = (o: (typeof props.views.orders)[number]) => o.isDiscrepancy || o.discrepancyTexts.length > 0;
  const orders = [...props.views.orders].sort((a, b) => Number(flagged(b)) - Number(flagged(a)));
  return (
    <>
      <Kpis views={props.views} forecast={props.forecast} />
      <TodayPage summary={props.views.today} />
      <StockPage rows={props.views.stock} />
      <OrdersPage rows={orders} limit={OVERVIEW_ORDERS} />
    </>
  );
}
