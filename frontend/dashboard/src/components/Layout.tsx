import type { ComponentChildren } from "preact";
import { useEffect, useState } from "preact/hooks";
import type { Access } from "../lib/access";
import type { LiveStatus } from "../lib/live";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

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

export const SHOP_NAMES: Record<string, string> = { sunrise_dairy: "Sunrise Dairy" };

/**
 * The page frame: a left section menu (a drawer behind the header's menu button on a phone), the header with
 * the shop name and live status, and the main region. The menu stays the same on every section, Live included.
 */
export function Layout(props: {
  access: Access;
  route: Route;
  status: LiveStatus;
  updatedText?: string;
  /** Record mode on the Live page: only the page itself, no header or menu (a clean screen recording). */
  bare?: boolean;
  children: ComponentChildren;
}) {
  const shopId = props.access.mode === "denied" ? "" : props.access.shopId;
  const shopName = SHOP_NAMES[shopId] ?? (shopId || "AIRA");
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setMenuOpen(false), [props.route]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setMenuOpen(false);
      document.querySelector<HTMLElement>(".header__menu")?.focus();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [menuOpen]);
  const skipToMain = (e: MouseEvent) => {
    e.preventDefault();
    document.getElementById("main")?.focus();
  };
  if (props.bare) {
    return (
      <main id="main" tabIndex={-1} class="main main--single main--record">
        {props.children}
      </main>
    );
  }
  const live = props.route === "#/live";
  const shell = ["shell", menuOpen && "shell--menu-open"].filter(Boolean).join(" ");
  return (
    <>
      <a class="skip-link" href="#main" onClick={skipToMain}>Skip to content</a>
      <div class={shell}>
        <Sidebar routes={ROUTES} route={props.route} shopName={shopName} status={props.status} updatedText={props.updatedText} />
        {menuOpen && <div class="scrim" aria-hidden="true" onClick={() => setMenuOpen(false)} />}
        <div class="shell__body">
          <Header
            shopName={shopName}
            title={ROUTES.find((r) => r.hash === props.route)?.label}
            status={props.status}
            updatedText={props.updatedText}
            menuOpen={menuOpen}
            onMenu={() => setMenuOpen((o) => !o)}
          />
          <main id="main" tabIndex={-1} class={props.route === "#/" ? "main" : live ? "main main--single main--live" : "main main--single"}>
            {props.children}
          </main>
        </div>
      </div>
    </>
  );
}
