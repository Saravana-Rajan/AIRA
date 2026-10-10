import { Header } from "../components/Header";
import { StockTable } from "../components/StockTable";

export const DEMO_SHOP_ID = "murugan_dairy";

/** The dashboard's single page for now. Today, Orders & Deliveries and Audit cards land in kanish/03. */
export function HomePage() {
  return (
    <>
      <a class="skip-link" href="#main">Skip to content</a>
      <Header shopName="Murugan Dairy" />
      <main id="main" class="main">
        <section class="card card--wide" aria-labelledby="stock-title">
          <h2 id="stock-title" class="card__title">Stock</h2>
          <StockTable shopId={DEMO_SHOP_ID} />
        </section>
      </main>
    </>
  );
}
