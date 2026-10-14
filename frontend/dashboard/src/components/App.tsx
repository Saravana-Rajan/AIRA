import { useEffect, useMemo, useState } from "preact/hooks";
import { resolveAccess } from "../lib/access";
import type { LiveStatus, ShopData } from "../lib/live";
import type { Forecast } from "../lib/forecast";
import { formatTimeIST } from "../lib/format";
import { buildViews } from "../lib/views";
import { publicEvidenceUrl, type ListenFn } from "../lib/connect";
import { Layout, parseRoute, type Route } from "./Layout";
import { shopName } from "../lib/shopName";
import { HomePage } from "../pages/HomePage";
import { TodayPage } from "../pages/TodayPage";
import { StockPage } from "../pages/StockPage";
import { PricesPage } from "../pages/PricesPage";
import { OrdersPage } from "../pages/OrdersPage";
import { AuditPage, type ResolveEvidence } from "../pages/AuditPage";
import { ForecastPage } from "../pages/ForecastPage";
import { LivePage } from "../pages/LivePage";
import { useMonitor } from "../lib/useMonitor";

export type { ListenFn };

export interface AppProps {
  /** Realtime shop data (main.tsx passes lazyListen(): Firestore database "aira"). */
  listen: ListenFn;
  /** Reorder suggestions (with the demo-history flag); null means unavailable. */
  loadForecast: (shopId: string) => Promise<Forecast | null>;
  resolveEvidence?: ResolveEvidence;
  now?: () => number;
}

/** Re-derive time-based text ("Expires today", today's totals) every minute, even with no new data. */
const TICK_MS = 60_000;

export function App({ listen, loadForecast, resolveEvidence = publicEvidenceUrl, now = Date.now }: AppProps) {
  // No sign-in in this build: every visitor gets the one shop, read-only, whatever the URL asks for.
  const [access] = useState(() => resolveAccess());
  const [route, setRoute] = useState<Route>(() => parseRoute(location.hash) ?? "#/");
  const [data, setData] = useState<ShopData | null>(null);
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [clock, setClock] = useState(now);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [forecastLoading, setForecastLoading] = useState(true);
  const shopId = access.shopId;

  useEffect(() => {
    const onHash = () => {
      const next = parseRoute(location.hash);
      if (next === null) return; // an in-page anchor, not a section
      setRoute(next);
      document.getElementById("main")?.focus();
    };
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    return listen(
      shopId,
      (d) => {
        const t = now();
        setData(d);
        setUpdatedAt(t);
        setClock(t);
      },
      setStatus,
    );
  }, [shopId]);

  useEffect(() => {
    let alive = true;
    setForecastLoading(true);
    loadForecast(shopId)
      .catch(() => null)
      .then((f) => {
        if (!alive) return;
        setForecast(f);
        setForecastLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [shopId]);

  useEffect(() => {
    const t = setInterval(() => setClock(now()), TICK_MS);
    return () => clearInterval(t);
  }, []);

  const views = useMemo(() => (data ? buildViews(data, clock) : null), [data, clock]);
  // The Live page listens to aira-live's /monitor channel only while it is open.
  const monitor = useMonitor(route === "#/live", shopId);
  const [record, setRecord] = useState(false);

  const forecastPage = (
    <ForecastPage items={forecast?.items ?? null} demoHistory={forecast?.demoHistory ?? false} loading={forecastLoading} />
  );
  let body;
  if (route === "#/forecast") body = forecastPage;
  else if (route === "#/live") {
    body = <LivePage state={monitor.state} conn={monitor.conn} video={monitor.video} keyNeeded={monitor.keyNeeded} configured={monitor.configured} onKey={monitor.setKey} onFollow={monitor.follow} record={record} onRecord={setRecord} shopName={shopName()} />;
  }
  else if (!views) {
    body =
      status === "offline" ? (
        <p role="alert" class="notice notice--error">
          Can't load the shop's records. Check the internet connection, then reload this page.
        </p>
      ) : (
        <p role="status" class="notice">Loading the shop's records…</p>
      );
  } else if (route === "#/today") body = <TodayPage summary={views.today} />;
  else if (route === "#/stock") body = <StockPage rows={views.stock} />;
  else if (route === "#/prices") body = <PricesPage rows={views.prices} />;
  else if (route === "#/orders") body = <OrdersPage rows={views.orders} />;
  else if (route === "#/audit") body = <AuditPage rows={views.audit} resolveEvidence={resolveEvidence} />;
  else {
    body = <HomePage views={views} forecast={forecast} forecastLoading={forecastLoading} resolveEvidence={resolveEvidence} />;
  }

  return (
    <Layout access={access} route={route} status={status} updatedText={updatedAt !== null ? formatTimeIST(updatedAt) : undefined} bare={route === "#/live" && record}>
      {body}
    </Layout>
  );
}
