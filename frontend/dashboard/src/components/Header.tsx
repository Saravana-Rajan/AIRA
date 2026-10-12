import type { LiveStatus } from "../lib/live";

const STATUS_TEXT: Record<LiveStatus, string> = { connecting: "Connecting…", live: "Live", offline: "Offline" };

/**
 * Dashboard top bar: shop name (the page's only h1) and a live indicator that says its state in words.
 * On a phone it also carries the logo and, when onMenu is given, the button that opens the section menu.
 */
export function Header(props: {
  shopName: string;
  /** The open section (Overview, Stock, …); the shop name is in the menu, so the header names the page. */
  title?: string;
  status: LiveStatus;
  updatedText?: string;
  menuOpen?: boolean;
  onMenu?: () => void;
}) {
  const { shopName, status, updatedText } = props;
  return (
    <header class="header">
      {props.onMenu && (
        <button
          type="button"
          class="header__menu"
          aria-controls="sidebar"
          aria-expanded={props.menuOpen ? "true" : "false"}
          onClick={props.onMenu}
        >
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false">
            {props.menuOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
          </svg>
          <span class="sr-only">Sections menu</span>
        </button>
      )}
      <img class="header__logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="AIRA" width={40} height={40} />
      <div class="header__id">
        {props.title ? (
          <h1 class="header__title">
            {props.title}
            <span class="sr-only">, {shopName}</span>
          </h1>
        ) : (
          <>
            <h1 class="header__title">{shopName}</h1>
            <p class="header__sub">AIRA shop dashboard</p>
          </>
        )}
      </div>
      <div class={`live live--${status}`}>
        <span class="live__dot" aria-hidden="true" />
        <span role="status" class="live__label">{STATUS_TEXT[status]}</span>
        {updatedText && <span class="live__time">Updated {updatedText}</span>}
      </div>
    </header>
  );
}
