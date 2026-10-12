import type { JSX } from "preact";
import type { LiveStatus } from "../lib/live";

/** 20px line icons (stroke = currentColor), one per section. Decorative: the label names the link. */
const ICONS: Record<string, JSX.Element> = {
  "#/": (
    <>
      <rect x="3" y="3" width="7" height="8" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="15" width="7" height="6" rx="1.5" />
    </>
  ),
  "#/today": (
    <>
      <rect x="3" y="4.5" width="18" height="16" rx="2" />
      <path d="M3 9.5h18M8 2.5v4M16 2.5v4" />
      <path d="M8 14.5l2.5 2.5 5-5" />
    </>
  ),
  "#/stock": (
    <>
      <path d="M12 2.8l8.5 4.6v9.2L12 21.2l-8.5-4.6V7.4z" />
      <path d="M3.5 7.4L12 12l8.5-4.6M12 12v9.2" />
    </>
  ),
  "#/prices": (
    <>
      <path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0L3 13V3h10l7.6 7.6a2 2 0 010 2.8z" />
      <circle cx="7.5" cy="7.5" r="1.5" />
    </>
  ),
  "#/orders": (
    <>
      <path d="M1.5 5.5h12.5v10.5H1.5zM14 9h4.5l4 4v3H14" />
      <circle cx="6" cy="18.5" r="2" />
      <circle cx="18" cy="18.5" r="2" />
    </>
  ),
  "#/audit": (
    <>
      <path d="M14 2.5H6a2 2 0 00-2 2v15a2 2 0 002 2h12a2 2 0 002-2V8.5z" />
      <path d="M14 2.5v6h6M8 13h8M8 17h5" />
    </>
  ),
  "#/forecast": (
    <>
      <path d="M3 3v18h18" />
      <path d="M7 15l4-4 3 3 6-6" />
      <path d="M15 8h5v5" />
    </>
  ),
  "#/live": (
    <>
      <circle cx="12" cy="12" r="2.5" />
      <path d="M7.8 7.8a6 6 0 000 8.4M16.2 16.2a6 6 0 000-8.4M4.9 4.9a10 10 0 000 14.2M19.1 19.1a10 10 0 000-14.2" />
    </>
  ),
};

const STATUS_TEXT: Record<LiveStatus, string> = { connecting: "Connecting…", live: "Live data", offline: "Offline" };

/**
 * The left menu: AIRA brand and shop name, one link per section (icon + label, aria-current on the open one)
 * and the data connection at the bottom. The footer repeats the header's status, so it is hidden from screen
 * readers (the header's role="status" announces it once).
 */
export function Sidebar(props: {
  routes: readonly { hash: string; label: string }[];
  route: string;
  shopName: string;
  status: LiveStatus;
  updatedText?: string;
}) {
  return (
    <aside class="sidebar" id="sidebar">
      <div class="sidebar__brand">
        <img class="sidebar__logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="" width={36} height={36} />
        <div class="sidebar__names">
          <span class="sidebar__app">AIRA</span>
          <span class="sidebar__shop">{props.shopName}</span>
        </div>
      </div>
      <nav class="sidebar__nav" aria-label="Dashboard sections">
        <ul class="nav">
          {props.routes.map((r) => (
            <li key={r.hash}>
              <a class="nav__link" href={r.hash} title={r.label} aria-current={props.route === r.hash ? "page" : undefined}>
                <svg
                  class="nav__icon"
                  viewBox="0 0 24 24"
                  width="20"
                  height="20"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                  focusable="false"
                >
                  {ICONS[r.hash]}
                </svg>
                <span class="nav__label">{r.label}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <div class={`sidebar__foot live--${props.status}`} aria-hidden="true">
        <span class="live__dot" />
        <span class="sidebar__status">
          <span class="sidebar__status-text">{STATUS_TEXT[props.status]}</span>
          {props.updatedText && <span class="sidebar__updated">Updated {props.updatedText}</span>}
        </span>
      </div>
    </aside>
  );
}
