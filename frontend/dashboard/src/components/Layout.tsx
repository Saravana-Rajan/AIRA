import type { ComponentChildren } from "preact";
import type { Access } from "../lib/access";
import type { LiveStatus } from "../lib/live";
import { Header } from "./Header";

export const ROUTES = [
  { hash: "#/", label: "Overview" },
  { hash: "#/today", label: "Today" },
  { hash: "#/stock", label: "Stock" },
  { hash: "#/prices", label: "Prices" },
  { hash: "#/orders", label: "Orders & deliveries" },
  { hash: "#/audit", label: "Audit trail" },
  { hash: "#/forecast", label: "Forecast" },
  { hash: "#/live", label: "Live" },
] as const;

export type Route = (typeof ROUTES)[number]["hash"];

/**
 * The route for a location hash. "#/..." hashes that are unknown fall back to the overview.
 * Returns null for in-page anchors (such as the skip link's "#main"), which must not change the route.
 */
export function parseRoute(hash: string): Route | null {
  if (hash === "" || hash === "#") return "#/";
  if (!hash.startsWith("#/")) return null;
  return ROUTES.find((r) => r.hash === hash)?.hash ?? "#/";
}

const SHOP_NAMES: Record<string, string> = { sunrise_dairy: "Sunrise Dairy" };

export function Layout(props: {
  access: Access;
  route: Route;
  status: LiveStatus;
  updatedText?: string;
  children: ComponentChildren;
}) {
  const shopId = props.access.mode === "denied" ? "" : props.access.shopId;
  const skipToMain = (e: MouseEvent) => {
    e.preventDefault();
    document.getElementById("main")?.focus();
  };
  return (
    <>
      <a class="skip-link" href="#main" onClick={skipToMain}>Skip to content</a>
      <Header shopName={SHOP_NAMES[shopId] ?? (shopId || "AIRA")} status={props.status} updatedText={props.updatedText} />
      {props.access.mode === "demo" && (
        <p class="banner">
          <strong>Demo shop.</strong> Read-only view: records change only through AIRA.
        </p>
      )}
      <nav class="tabs" aria-label="Dashboard sections">
        <ul class="tabs__list">
          {ROUTES.map((r) => (
            <li key={r.hash}>
              <a class="tabs__link" href={r.hash} aria-current={props.route === r.hash ? "page" : undefined}>
                {r.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" tabIndex={-1} class={props.route === "#/" ? "main" : props.route === "#/live" ? "main main--single main--live" : "main main--single"}>
        {props.children}
      </main>
    </>
  );
}
