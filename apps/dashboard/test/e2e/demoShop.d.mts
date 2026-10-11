// Types for demoShop.mjs (plain ESM so Node and Playwright load it without a build step).

export declare const DEMO_SHOP_ID: "sunrise_dairy";
export declare const OTHER_SHOP_ID: string;

/** A Firestore document to write: Date values become Timestamps, everything else is stored as is. */
export interface SeedDoc { path: string; data: Record<string, unknown> }

export interface CatalogProduct {
  sku: string; name: string; brand: string; unit: string; price: number; costPrice: number;
  reorderThreshold: number; shelfLifeDays: number; [key: string]: unknown;
}
export interface CatalogSupplier { id: string; name: string; channel: string; products: string[]; [key: string]: unknown }
export interface Catalog {
  shop: { id: string; name: string; city: string; currency: string; timezone: string };
  products: CatalogProduct[];
  suppliers: CatalogSupplier[];
}

/** The texts the dashboard must show for the seeded demo shop. */
export interface DemoExpected {
  shopName: string;
  curd: { name: string; onHandText: string; expiryText: string };
  lowStock: { name: string };
  salesCount: number;
  revenueText: string;
  cashText: string;
  upiText: string;
  topProduct: string;
  supplierName: string;
  discrepancyText: string;
  vendorReply: string;
  auditActions: string[];
  otherShopProduct: string;
}

export declare function loadCatalog(): Catalog;
export declare function curdInventoryDoc(nowMs: number, onHand: number): SeedDoc;
export declare function buildDemoShop(nowMs: number, catalog?: Catalog): { docs: SeedDoc[]; expected: DemoExpected };
