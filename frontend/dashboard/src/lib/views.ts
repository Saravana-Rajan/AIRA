// Everything the pages show, derived from the live shop data with the pure selectors.
import type { ShopData } from "./live";
import { stockRows, type StockRow } from "./selectors/stock";
import { todaySummary, type TodaySummary } from "./selectors/today";
import { orderRows, type OrderRow } from "./selectors/orders";
import { auditRows, type AuditRow } from "./selectors/audit";

export interface DashboardViews { stock: StockRow[]; today: TodaySummary; orders: OrderRow[]; audit: AuditRow[] }

export function buildViews(d: ShopData, nowMs: number): DashboardViews {
  return {
    stock: stockRows(d.products, d.inventory, nowMs),
    today: todaySummary(d.sales, d.payments, d.products, nowMs),
    orders: orderRows(d.orders, d.deliveries, d.suppliers, d.products, nowMs),
    audit: auditRows(d.auditLogs, nowMs, { products: d.products, orders: d.orders, deliveries: d.deliveries, suppliers: d.suppliers }),
  };
}
