import type { DashboardViews } from "../lib/views";
import type { ForecastItem } from "../lib/forecast";
import { TodayPage } from "./TodayPage";
import { StockPage } from "./StockPage";
import { OrdersPage } from "./OrdersPage";
import { ForecastPage } from "./ForecastPage";
import { AuditPage, type ResolveEvidence } from "./AuditPage";

export { DEMO_SHOP_ID } from "../lib/access";

const OVERVIEW_ORDERS = 3;
const OVERVIEW_ACTIONS = 8;

/** The overview: every section as a card. The grid places them (1 column on a phone, 2–3 on a laptop). */
export function HomePage(props: {
  views: DashboardViews;
  forecast: ForecastItem[] | null;
  forecastLoading: boolean;
  resolveEvidence: ResolveEvidence;
}) {
  // A short list must never hide a delivery that does not match its order.
  const orders = [...props.views.orders].sort((a, b) => Number(b.isDiscrepancy) - Number(a.isDiscrepancy));
  return (
    <>
      <TodayPage summary={props.views.today} />
      <StockPage rows={props.views.stock} />
      <OrdersPage rows={orders} limit={OVERVIEW_ORDERS} />
      <ForecastPage items={props.forecast} loading={props.forecastLoading} />
      <AuditPage rows={props.views.audit} resolveEvidence={props.resolveEvidence} limit={OVERVIEW_ACTIONS} />
    </>
  );
}
