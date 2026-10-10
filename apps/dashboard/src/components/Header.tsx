import type { LiveStatus } from "../lib/live";

const STATUS_TEXT: Record<LiveStatus, string> = { connecting: "Connecting…", live: "Live", offline: "Offline" };

/** Dashboard header: logo, shop name and a live indicator that says its state in words. */
export function Header({ shopName, status, updatedText }: { shopName: string; status: LiveStatus; updatedText?: string }) {
  return (
    <header class="header">
      <img class="header__logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="AIRA" width={40} height={40} />
      <div class="header__id">
        <h1 class="header__title">{shopName}</h1>
        <p class="header__sub">AIRA shop dashboard</p>
      </div>
      <div class={`live live--${status}`}>
        <span class="live__dot" aria-hidden="true" />
        <span role="status" class="live__label">{STATUS_TEXT[status]}</span>
        {updatedText && <span class="live__time">Updated {updatedText}</span>}
      </div>
    </header>
  );
}
