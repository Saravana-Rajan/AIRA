/** Dashboard header: logo, shop name and a live indicator. */
export function Header({ shopName }: { shopName: string }) {
  return (
    <header class="header">
      <img class="header__logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="AIRA" width={40} height={40} />
      <h1 class="header__title">
        {shopName}
        <span class="header__sub">AIRA Shop Dashboard</span>
      </h1>
      <span class="live">
        <span class="live__dot" aria-hidden="true" />
        Live
      </span>
    </header>
  );
}
