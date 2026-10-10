# AIRA Shop Dashboard Implementation Plan (Kanish · 03)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a read-only, accessible web **Shop Dashboard** on Firebase Hosting that shows Murugan Dairy's live stock, today's sales, orders and deliveries (with discrepancies), the audit trail and reorder suggestions, in real time from Firestore. Judges can open the demo shop without signing in; a blind owner's family signs in with Google.

**Architecture:**
- **Stack:** Vite + TypeScript + Preact SPA in `apps/dashboard`, using the Firebase modular SDK.
- **Data flow:** `onSnapshot` listeners on `shops/{shopId}/…` → `normalize()` turns Firestore Timestamps into millis → pure **selectors** (fully unit-tested) → presentational pages.
- **Forecast:** read from a small aira-live HTTP endpoint (`GET /api/forecast?shop=`).
- **Access:** the pure function `resolveAccess()` decides demo or family mode.
- **Read-only:** the dashboard **never writes** to Firestore.

**Tech Stack:**
- **App:** Preact 10, Vite 5, TypeScript 5, `firebase` v10+ (Auth, Firestore, Storage)
- **Unit tests:** Vitest + jsdom + `@testing-library/preact`
- **End-to-end:** Playwright + `@axe-core/playwright` against the Firestore emulator; `firebase-admin` seeds it
- **Accessibility check:** Lighthouse

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§4 Architecture, Shop Dashboard; §5 Data model; §8 Demo). **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

**Prereq:** `docs/superpowers/plans/2026-10-10-00-foundation.md` is merged: `apps/dashboard` skeleton, `@aira/contracts` with `src/firestore.ts` and `src/enums.ts`, `firebase.json`.

## Schedule

| Day | Tasks |
|---|---|
| D4 (Tue Oct 13) | Tasks 1–3: helpers, selectors, data layer and pages |
| D5 (Wed Oct 14) | Tasks 4–5: end-to-end smoke test, accessibility, hosting deploy, cross-team hand-offs |

## Global Constraints

- **Product name:** **AIRA** in all user-facing copy. The logo comes from `assets/logo/aira-logo-192.png`.
- **Read-only:** no `setDoc` / `updateDoc` / `addDoc` / `deleteDoc` anywhere in `apps/dashboard/src`. A test enforces this.
- **Field names** exactly as in spec §5 and `@aira/contracts` `firestore.ts`: `Product, Supplier, Order, Delivery, InventoryDoc, Sale, Payment, AuditLog`. Doc IDs: `products/{sku}`, `inventory/{sku}`.
- **Order states** come from `@aira/contracts` `ORDER_STATES`. Only `DISCREPANCY` is shown as a branch.
- **Money:** INR as integer rupees, formatted with Indian grouping (`₹1,00,000`).
- **Timezone:** always `Asia/Kolkata` (IST, UTC+05:30, no DST), never the browser's timezone.
- **Demo shop:** ID `demo`, read-only, no sign-in, with a visible "Demo shop" banner.
- **Family access:** Google sign-in. `users/{uid}` must have `role` in `owner|family` and a `shopId`.
- **Accessibility:** WCAG 2.1 AA. A text label on every status (never colour alone), tables with `<caption>` and `scope`, a skip link, visible focus, and axe with **no serious or critical violations**. Lighthouse accessibility ≥ **0.95**.
- **Env vars:**
  - `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_STORAGE_BUCKET`, `VITE_FIREBASE_APP_ID`
  - `VITE_AIRA_LIVE_HTTP` (aira-live base URL)
  - `VITE_USE_EMULATOR` (`"1"` in tests)
  - `VITE_FIRESTORE_EMULATOR_PORT` (default `8085`)
- **Hosting target:** `dashboard`.

## Review Focus

1. **Product with no inventory doc yet** (new SKU): the row shows `0` with a "Low stock" label, not a crash or a blank. *Test in Task 2 (`stockRows` missing inventory).*
2. **A sale at 23:50 IST vs 00:10 IST:** "Today" must use the IST day, whatever timezone the judge's laptop is in. *Tests in Task 1 (`startOfDayIST`) and Task 2 (`todaySummary` boundary).*
3. **Discrepancy orders** must say "Discrepancy" in text, plus each line ("expected 30, got 28"). Colour alone is not enough for colour-blind family members or screen readers. *Tests in Task 2 (`orderRows`) and Task 3 (render).*
4. **Forecast endpoint down or returning malformed JSON:** the page shows "Forecast unavailable" instead of hanging or crashing. *Tests in Task 2 (`parseForecast`, `fetchForecast` failure) and Task 4 (end-to-end, unreachable URL).*
5. **A signed-out visitor with `?shop=someoneElse`:** demo mode ignores the param and only ever reads `shops/demo`. *Test in Task 1 (`resolveAccess`).*

---

## File Structure

```
apps/dashboard/
  package.json            (modify: deps + scripts)
  vite.config.ts          (replace: vitest jsdom config)
  playwright.config.ts    (create)
  index.html              (modify: title, lang, favicon)
  public/aira-logo-192.png (copy from assets/logo)
  src/
    main.tsx              boot: auth → access → listeners → render
    firebase.ts           init app/auth/firestore/storage (+ emulator)
    access.ts             resolveAccess() — pure
    format.ts             formatINR, formatQty, startOfDayIST, daysUntil, formatTimeIST, stateLabel, expiryLabel — pure
    data/normalize.ts     Normalized<T>, normalize() — pure
    data/live.ts          listenShop() — Firestore realtime listeners → ShopData
    selectors/stock.ts    stockRows()
    selectors/today.ts    todaySummary()
    selectors/orders.ts   ORDER_FLOW, orderRows()
    selectors/audit.ts    auditRows()
    forecast.ts           parseForecast(), fetchForecast()
    ui/Layout.tsx         skip link, logo, nav (hash routes), demo banner
    pages/StockPage.tsx  pages/TodayPage.tsx  pages/OrdersPage.tsx  pages/AuditPage.tsx  pages/ForecastPage.tsx
    styles.css            AA-contrast palette, focus rings
  test/
    format.test.ts  access.test.ts  normalize.test.ts
    selectors.test.ts  forecast.test.ts  readonly.test.ts  pages.test.tsx
  e2e/
    seed.ts               firebase-admin seed of shops/demo (emulator)
    smoke.spec.ts         pages + axe
  scripts/a11y-score.mjs  Lighthouse score gate
```

---

### Task 1: Formatting, IST time maths and access resolution (pure)

**Files:**
- Modify: `apps/dashboard/package.json`
- Replace: `apps/dashboard/vite.config.ts`
- Create: `apps/dashboard/src/format.ts`, `apps/dashboard/src/access.ts`
- Test: `apps/dashboard/test/format.test.ts`, `apps/dashboard/test/access.test.ts`

**Interfaces:**
- Consumes: nothing beyond the foundation skeleton.
- Produces:
  - `formatINR(rupees: number): string`
  - `formatQty(qty: number, unit: "L" | "pack" | "box"): string`
  - `IST_OFFSET_MS: number`
  - `startOfDayIST(nowMs: number): number`
  - `daysUntil(isoDate: string, nowMs: number): number`
  - `formatTimeIST(ms: number): string`
  - `stateLabel(state: string): string`
  - `expiryLabel(days: number | null): string | null`
  - `type Access = { mode: "demo"; shopId: "demo" } | { mode: "family"; shopId: string; uid: string } | { mode: "denied"; reason: string }`
  - `resolveAccess(input: { uid: string | null; userDoc: { shopId?: string; role?: string } | null; wantDemo: boolean }): Access`

- [ ] **Step 1: Replace `apps/dashboard/package.json`**

```json
{
  "name": "@aira/dashboard",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@aira/contracts": "*",
    "firebase": "^10.14.0",
    "preact": "^10.24.0"
  },
  "devDependencies": {
    "@axe-core/playwright": "^4.10.0",
    "@playwright/test": "^1.48.0",
    "@preact/preset-vite": "^2.9.0",
    "@testing-library/preact": "^3.2.4",
    "firebase-admin": "^12.6.0",
    "jsdom": "^25.0.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 2: Replace `apps/dashboard/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
  },
} as any);
```

- [ ] **Step 3: Write the failing tests**

```ts
// apps/dashboard/test/format.test.ts
import { describe, it, expect } from "vitest";
import {
  formatINR, formatQty, startOfDayIST, daysUntil, formatTimeIST, stateLabel, expiryLabel,
} from "../src/format";

describe("formatINR", () => {
  it("uses Indian digit grouping and no decimals", () => {
    expect(formatINR(2860)).toBe("₹2,860");
    expect(formatINR(100000)).toBe("₹1,00,000");
    expect(formatINR(0)).toBe("₹0");
  });
});

describe("formatQty", () => {
  it("formats litres, packs and boxes with plurals", () => {
    expect(formatQty(28, "L")).toBe("28 L");
    expect(formatQty(1.5, "L")).toBe("1.5 L");
    expect(formatQty(1, "box")).toBe("1 box");
    expect(formatQty(3, "box")).toBe("3 boxes");
    expect(formatQty(6, "pack")).toBe("6 packs");
    expect(formatQty(1, "pack")).toBe("1 pack");
  });
});

describe("IST day maths", () => {
  it("00:10 IST belongs to the new IST day", () => {
    // 2026-10-10T18:40Z == 2026-10-11 00:10 IST
    expect(startOfDayIST(Date.UTC(2026, 9, 10, 18, 40))).toBe(Date.UTC(2026, 9, 10, 18, 30));
  });
  it("23:50 IST still belongs to the previous IST day", () => {
    // 2026-10-10T18:20Z == 2026-10-10 23:50 IST
    expect(startOfDayIST(Date.UTC(2026, 9, 10, 18, 20))).toBe(Date.UTC(2026, 9, 9, 18, 30));
  });
  it("daysUntil counts IST calendar days", () => {
    const noonIst = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
    expect(daysUntil("2026-10-10", noonIst)).toBe(0);
    expect(daysUntil("2026-10-11", noonIst)).toBe(1);
    expect(daysUntil("2026-10-09", noonIst)).toBe(-1);
  });
  it("formats time in IST regardless of host timezone", () => {
    // 12:35Z == 18:05 IST
    expect(formatTimeIST(Date.UTC(2026, 9, 10, 12, 35))).toMatch(/6:05\s*pm/i);
  });
});

describe("labels", () => {
  it("maps order states to plain words", () => {
    expect(stateLabel("DISCREPANCY")).toBe("Discrepancy");
    expect(stateLabel("PAYMENT_CONFIRMED")).toBe("Paid");
    expect(stateLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
  it("describes expiry in words", () => {
    expect(expiryLabel(null)).toBeNull();
    expect(expiryLabel(-1)).toBe("Expired");
    expect(expiryLabel(0)).toBe("Expires today");
    expect(expiryLabel(1)).toBe("Expires tomorrow");
    expect(expiryLabel(4)).toBe("Expires in 4 days");
  });
});
```

```ts
// apps/dashboard/test/access.test.ts
import { describe, it, expect } from "vitest";
import { resolveAccess } from "../src/access";

describe("resolveAccess", () => {
  it("signed-out visitors always get the demo shop", () => {
    expect(resolveAccess({ uid: null, userDoc: null, wantDemo: false })).toEqual({ mode: "demo", shopId: "demo" });
  });
  it("?demo=1 forces demo even when signed in", () => {
    expect(resolveAccess({ uid: "u1", userDoc: { shopId: "s1", role: "owner" }, wantDemo: true }))
      .toEqual({ mode: "demo", shopId: "demo" });
  });
  it("owner or family with a shop get family mode for THEIR shop only", () => {
    expect(resolveAccess({ uid: "u1", userDoc: { shopId: "s1", role: "family" }, wantDemo: false }))
      .toEqual({ mode: "family", shopId: "s1", uid: "u1" });
    expect(resolveAccess({ uid: "u2", userDoc: { shopId: "s9", role: "owner" }, wantDemo: false }))
      .toEqual({ mode: "family", shopId: "s9", uid: "u2" });
  });
  it("role demo maps to the demo shop", () => {
    expect(resolveAccess({ uid: "u3", userDoc: { shopId: "x", role: "demo" }, wantDemo: false }))
      .toEqual({ mode: "demo", shopId: "demo" });
  });
  it("signed-in users without a linked shop are denied, not shown another shop", () => {
    expect(resolveAccess({ uid: "u4", userDoc: null, wantDemo: false }))
      .toEqual({ mode: "denied", reason: "No shop is linked to this account." });
    expect(resolveAccess({ uid: "u5", userDoc: { role: "owner" }, wantDemo: false }))
      .toEqual({ mode: "denied", reason: "No shop is linked to this account." });
  });
});
```

- [ ] **Step 4: Run the tests and confirm they fail**

Run: `npm install && npm test -w apps/dashboard`
Expected: FAIL with `Failed to resolve import "../src/format"` and `"../src/access"`.

- [ ] **Step 5: Implement `src/format.ts` and `src/access.ts`**

```ts
// apps/dashboard/src/format.ts
const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
export function formatINR(rupees: number): string {
  return INR.format(Math.round(rupees));
}

export function formatQty(qty: number, unit: "L" | "pack" | "box"): string {
  const n = Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
  if (unit === "L") return `${n} L`;
  if (qty === 1) return `${n} ${unit}`;
  return `${n} ${unit === "box" ? "boxes" : "packs"}`;
}

export const IST_OFFSET_MS = 330 * 60 * 1000; // UTC+05:30, India has no DST
const DAY_MS = 86_400_000;

export function startOfDayIST(nowMs: number): number {
  return Math.floor((nowMs + IST_OFFSET_MS) / DAY_MS) * DAY_MS - IST_OFFSET_MS;
}

export function daysUntil(isoDate: string, nowMs: number): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  const startOfThatDay = Date.UTC(y, m - 1, d) - IST_OFFSET_MS;
  return Math.round((startOfThatDay - startOfDayIST(nowMs)) / DAY_MS);
}

const TIME_IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true,
});
export function formatTimeIST(ms: number): string {
  return TIME_IST.format(ms);
}

const STATE_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  ORDER_PLACED: "Order placed",
  DELIVERY_PENDING: "Delivery pending",
  DELIVERY_RECONCILED: "Delivery checked",
  DISCREPANCY: "Discrepancy",
  PAYMENT_PENDING: "Payment pending",
  PAYMENT_CONFIRMED: "Paid",
  CLOSED: "Closed",
  SALE_PENDING: "Sale pending",
  SALE_COMPLETED: "Sale completed",
  SALE_VOID: "Sale cancelled",
};
export function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

export function expiryLabel(days: number | null): string | null {
  if (days === null) return null;
  if (days < 0) return "Expired";
  if (days === 0) return "Expires today";
  if (days === 1) return "Expires tomorrow";
  return `Expires in ${days} days`;
}
```

```ts
// apps/dashboard/src/access.ts
export type Access =
  | { mode: "demo"; shopId: "demo" }
  | { mode: "family"; shopId: string; uid: string }
  | { mode: "denied"; reason: string };

const DEMO: Access = { mode: "demo", shopId: "demo" };
const NO_SHOP = "No shop is linked to this account.";

export function resolveAccess(input: {
  uid: string | null;
  userDoc: { shopId?: string; role?: string } | null;
  wantDemo: boolean;
}): Access {
  if (input.wantDemo || !input.uid) return DEMO;
  const doc = input.userDoc;
  if (doc?.role === "demo") return DEMO;
  if (doc && (doc.role === "owner" || doc.role === "family") && doc.shopId) {
    return { mode: "family", shopId: doc.shopId, uid: input.uid };
  }
  return { mode: "denied", reason: NO_SHOP };
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `npm test -w apps/dashboard`
Expected: PASS. `format.test.ts` has 9 tests and `access.test.ts` has 5.

- [ ] **Step 7: Commit**

```bash
git add apps/dashboard/package.json apps/dashboard/vite.config.ts apps/dashboard/src/format.ts apps/dashboard/src/access.ts apps/dashboard/test/format.test.ts apps/dashboard/test/access.test.ts package-lock.json
git commit -m "feat(dashboard): IST-safe formatting helpers and demo/family access resolution"
```

---

### Task 2: Pure selectors — stock, today, orders, audit, forecast

**Files:**
- Create: `apps/dashboard/src/data/normalize.ts`, `src/selectors/stock.ts`, `src/selectors/today.ts`, `src/selectors/orders.ts`, `src/selectors/audit.ts`, `src/forecast.ts`
- Test: `apps/dashboard/test/normalize.test.ts`, `test/selectors.test.ts`, `test/forecast.test.ts`

**Interfaces:**
- Consumes:
  - `@aira/contracts`:
    - `Product`: `name, brand, unit, price, reorderThreshold`
    - `InventoryDoc`: `onHand, batches[{batchId, qty, expiryDate}]`
    - `Sale`: `items[{sku, qty, unitPrice, lineTotal}], total, state, completedAt`
    - `Payment`: `direction, method, amount, state, createdAt`
    - `Order`: `supplierId, items[{sku, qty}], state, vendorReply, updatedAt`
    - `Delivery`: `orderId, discrepancies[{sku?, field?, expected, actual}]`
    - `Supplier`: `name`
    - `AuditLog`: `actor, action, refType, refId, outcome, evidenceUri, at`
    - `ORDER_STATES`
  - Task 1 helpers.
  - **Timestamp fields:** `Sale.completedAt`, `Payment.createdAt`, `Order.updatedAt` and `AuditLog.at` must exist as Firestore server timestamps. `Sale.completedAt` and `Payment.createdAt` are **not in spec §5 yet**; see Cross-team notes (Kanish/01 + contracts PR).
- Produces:
  - `type Normalized<T>` and `normalize<T>(id: string, data: Record<string, unknown>): Normalized<T>`. Timestamps become millis, recursively in plain objects and arrays.
  - `stockRows(products: Normalized<Product>[], inventory: Normalized<InventoryDoc>[], nowMs: number): StockRow[]`, with `StockRow { sku: string; name: string; onHand: number; onHandText: string; lowStock: boolean; daysToExpiry: number | null; expiryText: string | null }`
  - `todaySummary(sales: Normalized<Sale>[], payments: Normalized<Payment>[], products: Normalized<Product>[], nowMs: number): TodaySummary`, with `TodaySummary { salesCount: number; revenue: number; revenueText: string; topProduct: string | null; byMethod: { cash: number; upi: number }; byMethodText: { cash: string; upi: string } }`
  - `ORDER_FLOW` and `orderRows(orders: Normalized<Order>[], deliveries: Normalized<Delivery>[], suppliers: Normalized<Supplier>[], products: Normalized<Product>[]): OrderRow[]`, with `OrderRow { id: string; supplierName: string; itemsText: string; state: string; stateLabel: string; stepIndex: number; isDiscrepancy: boolean; vendorReply: string | null; discrepancyTexts: string[]; updatedText: string }`
  - `auditRows(logs: Normalized<AuditLog>[]): AuditRow[]`, with `AuditRow { id: string; timeText: string; actorLabel: string; actionText: string; outcome: string; evidenceUri: string | null }`
  - `parseForecast(json: unknown): ForecastItem[] | null`, with `ForecastItem { sku: string; name: string; dailyDemand: number; onHand: number; suggestedOrder: number }`
  - `fetchForecast(base: string, shopId: string, fetchImpl?: typeof fetch, timeoutMs?: number): Promise<ForecastItem[] | null>`

- [ ] **Step 1: Write the failing tests**

```ts
// apps/dashboard/test/normalize.test.ts
import { describe, it, expect } from "vitest";
import { normalize } from "../src/data/normalize";

const ts = (ms: number) => ({ toMillis: () => ms });

describe("normalize", () => {
  it("adds id and converts Timestamps (top-level and nested) to millis", () => {
    const out: any = normalize("o1", {
      state: "ORDER_PLACED",
      updatedAt: ts(1000),
      items: [{ sku: "a", at: ts(5) }],
      invoice: { readAt: ts(7), total: 10 },
    });
    expect(out).toEqual({
      id: "o1",
      state: "ORDER_PLACED",
      updatedAt: 1000,
      items: [{ sku: "a", at: 5 }],
      invoice: { readAt: 7, total: 10 },
    });
  });
});
```

```ts
// apps/dashboard/test/selectors.test.ts
import { describe, it, expect } from "vitest";
import { ORDER_STATES } from "@aira/contracts";
import { stockRows } from "../src/selectors/stock";
import { todaySummary } from "../src/selectors/today";
import { ORDER_FLOW, orderRows } from "../src/selectors/orders";
import { auditRows } from "../src/selectors/audit";

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
const START = Date.UTC(2026, 9, 9, 18, 30); // 2026-10-10 00:00 IST

const products: any[] = [
  { id: "sakthi_curd_1l", name: "Sakthi curd 1 L", brand: "Sakthi", unit: "L", price: 60, reorderThreshold: 5 },
  { id: "aavin_milk_500ml", name: "Aavin milk 500 ml", brand: "Aavin", unit: "pack", price: 25, reorderThreshold: 10 },
  { id: "arun_icecream_box", name: "Arun ice cream box", brand: "Arun", unit: "box", price: 180, reorderThreshold: 3 },
  { id: "new_sku", name: "Aavin ghee 200 ml", brand: "Aavin", unit: "pack", price: 150, reorderThreshold: 2 },
];
const inventory: any[] = [
  { id: "sakthi_curd_1l", onHand: 28, batches: [{ batchId: "b1", qty: 22, expiryDate: "2026-10-14" }, { batchId: "b2", qty: 6, expiryDate: "2026-10-11" }] },
  { id: "aavin_milk_500ml", onHand: 40, batches: [{ batchId: "m1", qty: 0, expiryDate: "2026-10-09" }, { batchId: "m2", qty: 40, expiryDate: "2026-10-12" }] },
  { id: "arun_icecream_box", onHand: 2, batches: [] },
];

describe("stockRows", () => {
  const rows = stockRows(products, inventory, NOW);
  it("treats a product with no inventory doc as 0 and low stock", () => {
    const r = rows.find((x) => x.sku === "new_sku")!;
    expect(r.onHand).toBe(0);
    expect(r.onHandText).toBe("0 packs");
    expect(r.lowStock).toBe(true);
  });
  it("flags low stock at or below the reorder threshold", () => {
    expect(rows.find((x) => x.sku === "arun_icecream_box")!.lowStock).toBe(true);
    expect(rows.find((x) => x.sku === "sakthi_curd_1l")!.lowStock).toBe(false);
  });
  it("uses the nearest expiry among batches that still have stock", () => {
    const curd = rows.find((x) => x.sku === "sakthi_curd_1l")!;
    expect(curd.daysToExpiry).toBe(1);
    expect(curd.expiryText).toBe("Expires tomorrow");
    const milk = rows.find((x) => x.sku === "aavin_milk_500ml")!;
    expect(milk.daysToExpiry).toBe(2); // the empty, already-expired batch m1 is ignored
  });
  it("sorts low stock first, then soonest expiry, then name", () => {
    expect(rows.map((r) => r.sku)).toEqual(["new_sku", "arun_icecream_box", "sakthi_curd_1l", "aavin_milk_500ml"]);
  });
});

describe("todaySummary", () => {
  const sales: any[] = [
    { id: "s1", state: "SALE_COMPLETED", completedAt: START + 3_600_000, total: 50, items: [{ sku: "aavin_milk_500ml", qty: 2, unitPrice: 25, lineTotal: 50 }] },
    { id: "s2", state: "SALE_COMPLETED", completedAt: START + 7_200_000, total: 180, items: [{ sku: "arun_icecream_box", qty: 1, unitPrice: 180, lineTotal: 180 }] },
    { id: "s3", state: "SALE_PENDING", completedAt: null, total: 60, items: [{ sku: "sakthi_curd_1l", qty: 1, unitPrice: 60, lineTotal: 60 }] },
    { id: "s4", state: "SALE_COMPLETED", completedAt: START - 60_000, total: 999, items: [{ sku: "sakthi_curd_1l", qty: 1, unitPrice: 999, lineTotal: 999 }] }, // 23:59 IST yesterday
  ];
  const payments: any[] = [
    { id: "p1", direction: "in", method: "cash", amount: 50, state: "CONFIRMED", createdAt: START + 3_600_000 },
    { id: "p2", direction: "in", method: "upi", amount: 180, state: "CONFIRMED", createdAt: START + 7_200_000 },
    { id: "p3", direction: "in", method: "upi", amount: 60, state: "PENDING", createdAt: START + 7_300_000 },
    { id: "p4", direction: "out", method: "cash", amount: 1000, state: "CONFIRMED", createdAt: START + 1_000 },
  ];
  it("counts only sales COMPLETED within today's IST day", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.salesCount).toBe(2);
    expect(s.revenue).toBe(230);
    expect(s.revenueText).toBe("₹230");
  });
  it("picks the top product by revenue and names it", () => {
    expect(todaySummary(sales, payments, products, NOW).topProduct).toBe("Arun ice cream box");
  });
  it("sums only confirmed incoming payments by method", () => {
    const s = todaySummary(sales, payments, products, NOW);
    expect(s.byMethod).toEqual({ cash: 50, upi: 180 });
    expect(s.byMethodText).toEqual({ cash: "₹50", upi: "₹180" });
  });
  it("returns zeros on an empty day", () => {
    const s = todaySummary([], [], products, NOW);
    expect(s).toMatchObject({ salesCount: 0, revenue: 0, topProduct: null, byMethod: { cash: 0, upi: 0 } });
  });
});

describe("orderRows", () => {
  const suppliers: any[] = [{ id: "sakthi_vendor", name: "Sakthi vendor" }];
  const orders: any[] = [
    { id: "o1", supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "DISCREPANCY", vendorReply: "OK, delivery at 6 pm", updatedAt: Date.UTC(2026, 9, 10, 12, 35) },
    { id: "o2", supplierId: "missing_vendor", items: [{ sku: "aavin_milk_500ml", qty: 20 }], state: "ORDER_PLACED", updatedAt: Date.UTC(2026, 9, 10, 4, 0) },
  ];
  const deliveries: any[] = [
    { id: "d1", orderId: "o1", discrepancies: [{ sku: "sakthi_curd_1l", expected: 30, actual: 28 }, { field: "total", expected: 1500, actual: 1600 }] },
  ];
  const rows = orderRows(orders, deliveries, suppliers, products);

  it("ORDER_FLOW only uses states defined in the contracts", () => {
    for (const s of ORDER_FLOW) expect(ORDER_STATES).toContain(s);
    expect(ORDER_STATES).toContain("DISCREPANCY");
  });
  it("labels a discrepancy in words and lists every mismatch", () => {
    const r = rows.find((x) => x.id === "o1")!;
    expect(r.isDiscrepancy).toBe(true);
    expect(r.stateLabel).toBe("Discrepancy");
    expect(r.stepIndex).toBe(ORDER_FLOW.indexOf("DELIVERY_RECONCILED"));
    expect(r.discrepancyTexts).toEqual(["Sakthi curd 1 L: expected 30, got 28", "total: expected 1500, got 1600"]);
    expect(r.vendorReply).toBe("OK, delivery at 6 pm");
    expect(r.itemsText).toBe("Sakthi curd 1 L × 30");
  });
  it("sorts newest first and survives an unknown supplier", () => {
    expect(rows.map((x) => x.id)).toEqual(["o1", "o2"]);
    const r = rows.find((x) => x.id === "o2")!;
    expect(r.supplierName).toBe("missing_vendor");
    expect(r.vendorReply).toBeNull();
    expect(r.stepIndex).toBe(0);
  });
});

describe("auditRows", () => {
  it("sorts newest first and humanises actor + action", () => {
    const rows = auditRows([
      { id: "a1", actor: "owner", action: "order_confirmed", refType: "order", refId: "o1", outcome: "ok", at: 1000 } as any,
      { id: "a2", actor: "aira", action: "sale_completed", refType: "sale", refId: "s2", outcome: "ok", evidenceUri: "gs://aira-evidence/s2.jpg", at: 2000 } as any,
    ]);
    expect(rows[0]).toMatchObject({ id: "a2", actorLabel: "AIRA", actionText: "Sale completed", outcome: "ok", evidenceUri: "gs://aira-evidence/s2.jpg" });
    expect(rows[1]).toMatchObject({ id: "a1", actorLabel: "Owner", actionText: "Order confirmed", evidenceUri: null });
  });
});
```

```ts
// apps/dashboard/test/forecast.test.ts
import { describe, it, expect } from "vitest";
import { parseForecast, fetchForecast } from "../src/forecast";

const good = { items: [{ sku: "sakthi_curd_1l", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 }] };

describe("parseForecast", () => {
  it("accepts the documented shape", () => {
    expect(parseForecast(good)).toEqual(good.items);
  });
  it("rejects malformed payloads", () => {
    expect(parseForecast(null)).toBeNull();
    expect(parseForecast({ items: "nope" })).toBeNull();
    expect(parseForecast({ items: [{ sku: "x", name: "X", dailyDemand: "lots", onHand: 1, suggestedOrder: 2 }] })).toBeNull();
  });
});

describe("fetchForecast", () => {
  it("returns items on 200", async () => {
    const f = (async () => new Response(JSON.stringify(good), { status: 200 })) as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", f)).toEqual(good.items);
  });
  it("returns null on HTTP error, network error or bad JSON", async () => {
    const http500 = (async () => new Response("boom", { status: 500 })) as typeof fetch;
    const netErr = (async () => { throw new TypeError("fetch failed"); }) as typeof fetch;
    const badJson = (async () => new Response("<html>", { status: 200 })) as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", http500)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", netErr)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", badJson)).toBeNull();
  });
  it("calls the documented URL with the shop id encoded", async () => {
    let seen = "";
    const f = (async (url: RequestInfo | URL) => { seen = String(url); return new Response(JSON.stringify(good)); }) as typeof fetch;
    await fetchForecast("https://live.example/", "my shop", f);
    expect(seen).toBe("https://live.example/api/forecast?shop=my%20shop");
  });
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `npm test -w apps/dashboard`
Expected: FAIL with `Failed to resolve import "../src/data/normalize"` (and the selectors and forecast modules).

- [ ] **Step 3: Implement `src/data/normalize.ts`**

```ts
// apps/dashboard/src/data/normalize.ts
type TimestampLike = { toMillis(): number };

export type Normalized<T> = {
  [K in keyof T]: T[K] extends TimestampLike ? number
    : T[K] extends TimestampLike | null | undefined ? number | null
    : T[K];
} & { id: string };

function isTimestamp(v: unknown): v is TimestampLike {
  return typeof v === "object" && v !== null && typeof (v as TimestampLike).toMillis === "function";
}

function convert(v: unknown): unknown {
  if (isTimestamp(v)) return v.toMillis();
  if (Array.isArray(v)) return v.map(convert);
  if (typeof v === "object" && v !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, inner] of Object.entries(v)) out[k] = convert(inner);
    return out;
  }
  return v;
}

export function normalize<T>(id: string, data: Record<string, unknown>): Normalized<T> {
  return { id, ...(convert(data) as Record<string, unknown>) } as Normalized<T>;
}
```

- [ ] **Step 4: Implement the selectors**

```ts
// apps/dashboard/src/selectors/stock.ts
import type { Product, InventoryDoc } from "@aira/contracts";
import type { Normalized } from "../data/normalize";
import { daysUntil, expiryLabel, formatQty } from "../format";

export interface StockRow {
  sku: string; name: string; onHand: number; onHandText: string;
  lowStock: boolean; daysToExpiry: number | null; expiryText: string | null;
}

export function stockRows(products: Normalized<Product>[], inventory: Normalized<InventoryDoc>[], nowMs: number): StockRow[] {
  const bySku = new Map(inventory.map((d) => [d.id, d]));
  const rows = products.map((p): StockRow => {
    const inv = bySku.get(p.id);
    const onHand = Number(inv?.onHand ?? 0);
    const live = (inv?.batches ?? []).filter((b: any) => Number(b.qty) > 0 && typeof b.expiryDate === "string");
    const daysToExpiry = live.length ? Math.min(...live.map((b: any) => daysUntil(b.expiryDate, nowMs))) : null;
    return {
      sku: p.id,
      name: p.name,
      onHand,
      onHandText: formatQty(onHand, p.unit as "L" | "pack" | "box"),
      lowStock: onHand <= Number(p.reorderThreshold),
      daysToExpiry,
      expiryText: expiryLabel(daysToExpiry),
    };
  });
  const FAR = Number.MAX_SAFE_INTEGER;
  return rows.sort((a, b) =>
    Number(b.lowStock) - Number(a.lowStock) ||
    (a.daysToExpiry ?? FAR) - (b.daysToExpiry ?? FAR) ||
    a.name.localeCompare(b.name));
}
```

```ts
// apps/dashboard/src/selectors/today.ts
import type { Product, Sale, Payment } from "@aira/contracts";
import type { Normalized } from "../data/normalize";
import { formatINR, startOfDayIST } from "../format";

export interface TodaySummary {
  salesCount: number; revenue: number; revenueText: string; topProduct: string | null;
  byMethod: { cash: number; upi: number }; byMethodText: { cash: string; upi: string };
}

export function todaySummary(
  sales: Normalized<Sale>[], payments: Normalized<Payment>[], products: Normalized<Product>[], nowMs: number,
): TodaySummary {
  const start = startOfDayIST(nowMs);
  const done = sales.filter((s: any) => s.state === "SALE_COMPLETED" && Number(s.completedAt ?? 0) >= start);
  const revenue = done.reduce((acc, s: any) => acc + Number(s.total), 0);

  const revenueBySku = new Map<string, number>();
  for (const s of done as any[]) {
    for (const it of s.items) revenueBySku.set(it.sku, (revenueBySku.get(it.sku) ?? 0) + Number(it.lineTotal));
  }
  let topSku: string | null = null;
  let best = -1;
  for (const [sku, v] of revenueBySku) if (v > best) { best = v; topSku = sku; }
  const topProduct = topSku ? (products.find((p) => p.id === topSku)?.name ?? topSku) : null;

  const confirmedIn = payments.filter((p: any) => p.direction === "in" && p.state === "CONFIRMED" && Number(p.createdAt ?? 0) >= start);
  const sumFor = (m: "cash" | "upi") => confirmedIn.filter((p: any) => p.method === m).reduce((a, p: any) => a + Number(p.amount), 0);
  const byMethod = { cash: sumFor("cash"), upi: sumFor("upi") };

  return {
    salesCount: done.length,
    revenue,
    revenueText: formatINR(revenue),
    topProduct,
    byMethod,
    byMethodText: { cash: formatINR(byMethod.cash), upi: formatINR(byMethod.upi) },
  };
}
```

```ts
// apps/dashboard/src/selectors/orders.ts
import type { Order, Delivery, Supplier, Product } from "@aira/contracts";
import type { Normalized } from "../data/normalize";
import { formatTimeIST, stateLabel } from "../format";

export const ORDER_FLOW = [
  "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED",
] as const;

export interface OrderRow {
  id: string; supplierName: string; itemsText: string; state: string; stateLabel: string;
  stepIndex: number; isDiscrepancy: boolean; vendorReply: string | null; discrepancyTexts: string[]; updatedText: string;
}

export function orderRows(
  orders: Normalized<Order>[], deliveries: Normalized<Delivery>[],
  suppliers: Normalized<Supplier>[], products: Normalized<Product>[],
): OrderRow[] {
  const productName = (sku: string) => products.find((p) => p.id === sku)?.name ?? sku;
  const supplierName = (id: string) => suppliers.find((s) => s.id === id)?.name ?? id;
  return [...orders]
    .sort((a: any, b: any) => Number(b.updatedAt ?? 0) - Number(a.updatedAt ?? 0))
    .map((o: any): OrderRow => {
      const isDiscrepancy = o.state === "DISCREPANCY";
      const stepIndex = isDiscrepancy
        ? ORDER_FLOW.indexOf("DELIVERY_RECONCILED")
        : ORDER_FLOW.indexOf(o.state as (typeof ORDER_FLOW)[number]);
      const discrepancyTexts = (deliveries as any[])
        .filter((d) => d.orderId === o.id)
        .flatMap((d) => (d.discrepancies ?? []) as any[])
        .map((x) => `${x.sku ? productName(x.sku) : x.field}: expected ${x.expected}, got ${x.actual}`);
      return {
        id: o.id,
        supplierName: supplierName(o.supplierId),
        itemsText: (o.items ?? []).map((it: any) => `${productName(it.sku)} × ${it.qty}`).join(", "),
        state: o.state,
        stateLabel: stateLabel(o.state),
        stepIndex,
        isDiscrepancy,
        vendorReply: o.vendorReply ?? null,
        discrepancyTexts,
        updatedText: o.updatedAt ? formatTimeIST(Number(o.updatedAt)) : "",
      };
    });
}
```

```ts
// apps/dashboard/src/selectors/audit.ts
import type { AuditLog } from "@aira/contracts";
import type { Normalized } from "../data/normalize";
import { formatTimeIST } from "../format";

export interface AuditRow { id: string; timeText: string; actorLabel: string; actionText: string; outcome: string; evidenceUri: string | null }

const ACTORS: Record<string, string> = { owner: "Owner", aira: "AIRA", system: "System" };

export function auditRows(logs: Normalized<AuditLog>[]): AuditRow[] {
  return [...logs]
    .sort((a: any, b: any) => Number(b.at ?? 0) - Number(a.at ?? 0))
    .map((l: any) => {
      const words = String(l.action).replace(/_/g, " ");
      return {
        id: l.id,
        timeText: l.at ? formatTimeIST(Number(l.at)) : "",
        actorLabel: ACTORS[l.actor] ?? String(l.actor),
        actionText: words.charAt(0).toUpperCase() + words.slice(1),
        outcome: String(l.outcome),
        evidenceUri: l.evidenceUri ?? null,
      };
    });
}
```

```ts
// apps/dashboard/src/forecast.ts
export interface ForecastItem { sku: string; name: string; dailyDemand: number; onHand: number; suggestedOrder: number }

const isNum = (v: unknown) => typeof v === "number" && Number.isFinite(v);

export function parseForecast(json: unknown): ForecastItem[] | null {
  if (typeof json !== "object" || json === null) return null;
  const items = (json as { items?: unknown }).items;
  if (!Array.isArray(items)) return null;
  for (const it of items) {
    if (typeof it !== "object" || it === null) return null;
    const x = it as Record<string, unknown>;
    if (typeof x.sku !== "string" || typeof x.name !== "string") return null;
    if (!isNum(x.dailyDemand) || !isNum(x.onHand) || !isNum(x.suggestedOrder)) return null;
  }
  return items as ForecastItem[];
}

export async function fetchForecast(
  base: string, shopId: string, fetchImpl: typeof fetch = fetch, timeoutMs = 5000,
): Promise<ForecastItem[] | null> {
  const url = `${base.replace(/\/+$/, "")}/api/forecast?shop=${encodeURIComponent(shopId)}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetchImpl(url, { signal: ctrl.signal });
    if (!res.ok) return null;
    return parseForecast(await res.json());
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `npm test -w apps/dashboard`
Expected: PASS. Task 1's 14 tests, plus 1 normalize test, 13 selector tests and 6 forecast tests.

- [ ] **Step 6: Commit**

```bash
git add apps/dashboard/src/data/normalize.ts apps/dashboard/src/selectors apps/dashboard/src/forecast.ts apps/dashboard/test/normalize.test.ts apps/dashboard/test/selectors.test.ts apps/dashboard/test/forecast.test.ts
git commit -m "feat(dashboard): pure selectors for stock, today, orders, audit and forecast parsing"
```

---

### Task 3: Firebase wiring, realtime listeners, layout and pages

**Files:**
- Create: `apps/dashboard/src/firebase.ts`, `src/data/live.ts`, `src/ui/Layout.tsx`, `src/pages/StockPage.tsx`, `src/pages/TodayPage.tsx`, `src/pages/OrdersPage.tsx`, `src/pages/AuditPage.tsx`, `src/pages/ForecastPage.tsx`, `src/styles.css`
- Replace: `apps/dashboard/src/main.tsx`
- Modify: `apps/dashboard/index.html`
- Copy: `assets/logo/aira-logo-192.png` → `apps/dashboard/public/aira-logo-192.png`
- Test: `apps/dashboard/test/pages.test.tsx`, `apps/dashboard/test/readonly.test.ts`

**Interfaces:**
- Consumes:
  - Task 1: `resolveAccess`, `Access`, and the formatters.
  - Task 2: the selectors, `normalize`, `fetchForecast`.
  - Firebase modular SDK.
- Produces:
  - `firebaseApp`, `auth`, `db`, `storage` (from `src/firebase.ts`)
  - `interface ShopData { products; inventory; sales; payments; orders; deliveries; suppliers; auditLogs }`. Each field is a `Normalized<…>[]` of the matching contracts type.
  - `listenShop(shopId: string, onChange: (d: ShopData) => void, nowMs?: () => number): () => void`
  - Page components:
    - `StockPage({ rows })`
    - `TodayPage({ summary })`
    - `OrdersPage({ rows })`
    - `AuditPage({ rows, resolveEvidence })`
    - `ForecastPage({ items, loading })`
  - `Layout({ access, route, children })`
  - Hash routes: `#/stock` (default), `#/today`, `#/orders`, `#/audit`, `#/forecast`

- [ ] **Step 1: Write the failing tests**

```tsx
// apps/dashboard/test/pages.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/preact";
import { StockPage } from "../src/pages/StockPage";
import { OrdersPage } from "../src/pages/OrdersPage";
import { ForecastPage } from "../src/pages/ForecastPage";
import { TodayPage } from "../src/pages/TodayPage";

describe("StockPage", () => {
  it("renders an accessible table with text (not colour-only) status labels", () => {
    render(<StockPage rows={[
      { sku: "a", name: "Arun ice cream box", onHand: 2, onHandText: "2 boxes", lowStock: true, daysToExpiry: null, expiryText: null },
      { sku: "b", name: "Sakthi curd 1 L", onHand: 28, onHandText: "28 L", lowStock: false, daysToExpiry: 1, expiryText: "Expires tomorrow" },
    ]} />);
    const table = screen.getByRole("table", { name: /stock/i });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3); // header + 2
    expect(within(rows[1]).getByText("Low stock")).toBeTruthy();
    expect(within(rows[2]).getByText("Expires tomorrow")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((h) => h.getAttribute("scope"))).toEqual(["col", "col", "col", "col"]);
  });
});

describe("OrdersPage", () => {
  it("announces discrepancies in words with each mismatch listed", () => {
    render(<OrdersPage rows={[{
      id: "o1", supplierName: "Sakthi vendor", itemsText: "Sakthi curd 1 L × 30", state: "DISCREPANCY",
      stateLabel: "Discrepancy", stepIndex: 2, isDiscrepancy: true, vendorReply: "OK, delivery at 6 pm",
      discrepancyTexts: ["Sakthi curd 1 L: expected 30, got 28"], updatedText: "6:05 pm",
    }]} />);
    expect(screen.getByText("Discrepancy")).toBeTruthy();
    expect(screen.getByText("Sakthi curd 1 L: expected 30, got 28")).toBeTruthy();
    expect(screen.getByText(/OK, delivery at 6 pm/)).toBeTruthy();
    expect(screen.getByRole("list", { name: /order progress for o1/i })).toBeTruthy();
  });
});

describe("ForecastPage", () => {
  it("shows a clear message when the forecast is unavailable", () => {
    render(<ForecastPage items={null} loading={false} />);
    expect(screen.getByText("Forecast unavailable")).toBeTruthy();
  });
  it("shows suggested orders when available", () => {
    render(<ForecastPage loading={false} items={[{ sku: "s", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 }]} />);
    expect(screen.getByText("Sakthi curd 1 L")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
  });
});

describe("TodayPage", () => {
  it("shows revenue and payments by method", () => {
    render(<TodayPage summary={{ salesCount: 2, revenue: 230, revenueText: "₹230", topProduct: "Arun ice cream box", byMethod: { cash: 50, upi: 180 }, byMethodText: { cash: "₹50", upi: "₹180" } }} />);
    expect(screen.getByText("₹230")).toBeTruthy();
    expect(screen.getByText("₹180")).toBeTruthy();
    expect(screen.getByText("Arun ice cream box")).toBeTruthy();
  });
});
```

```ts
// apps/dashboard/test/readonly.test.ts
import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

describe("dashboard is read-only", () => {
  it("never imports Firestore write APIs", () => {
    const src = files(join(__dirname, "..", "src")).filter((f) => /\.(ts|tsx)$/.test(f));
    const offenders = src.filter((f) => /\b(setDoc|updateDoc|addDoc|deleteDoc|writeBatch|runTransaction)\b/.test(readFileSync(f, "utf8")));
    expect(offenders).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `npm test -w apps/dashboard`
Expected: FAIL with `Failed to resolve import "../src/pages/StockPage"`. The readonly test passes already.

- [ ] **Step 3: Implement `src/firebase.ts` and `src/data/live.ts`**

```ts
// apps/dashboard/src/firebase.ts
import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { getFirestore, connectFirestoreEmulator } from "firebase/firestore";
import { getStorage } from "firebase/storage";

const env = (import.meta as any).env ?? {};

export const firebaseApp = initializeApp({
  apiKey: env.VITE_FIREBASE_API_KEY,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: env.VITE_FIREBASE_STORAGE_BUCKET,
  appId: env.VITE_FIREBASE_APP_ID,
});
export const auth = getAuth(firebaseApp);
export const db = getFirestore(firebaseApp);
export const storage = getStorage(firebaseApp);

if (env.VITE_USE_EMULATOR === "1") {
  connectFirestoreEmulator(db, "127.0.0.1", Number(env.VITE_FIRESTORE_EMULATOR_PORT ?? 8085));
}
```

```ts
// apps/dashboard/src/data/live.ts
import {
  collection, limit, onSnapshot, orderBy, query, Timestamp, where, type Query,
} from "firebase/firestore";
import type { Product, InventoryDoc, Sale, Payment, Order, Delivery, Supplier, AuditLog } from "@aira/contracts";
import { db } from "../firebase";
import { normalize, type Normalized } from "./normalize";
import { startOfDayIST } from "../format";

export interface ShopData {
  products: Normalized<Product>[];
  inventory: Normalized<InventoryDoc>[];
  sales: Normalized<Sale>[];
  payments: Normalized<Payment>[];
  orders: Normalized<Order>[];
  deliveries: Normalized<Delivery>[];
  suppliers: Normalized<Supplier>[];
  auditLogs: Normalized<AuditLog>[];
}

const EMPTY: ShopData = { products: [], inventory: [], sales: [], payments: [], orders: [], deliveries: [], suppliers: [], auditLogs: [] };
const DAY_MS = 86_400_000;

export function listenShop(shopId: string, onChange: (d: ShopData) => void, nowMs: () => number = Date.now): () => void {
  let data: ShopData = { ...EMPTY };
  let unsubs: Array<() => void> = [];
  let midnightTimer: ReturnType<typeof setTimeout> | undefined;

  const col = (name: string) => collection(db, "shops", shopId, name);
  const watch = <K extends keyof ShopData>(key: K, q: Query) =>
    onSnapshot(q, (snap) => {
      data = { ...data, [key]: snap.docs.map((d) => normalize(d.id, d.data())) as ShopData[K] };
      onChange(data);
    });

  const subscribe = () => {
    const start = Timestamp.fromMillis(startOfDayIST(nowMs()));
    // Single-field range filters only: no composite indexes needed. State is filtered in selectors.
    unsubs = [
      watch("products", query(col("products"))),
      watch("inventory", query(col("inventory"))),
      watch("sales", query(col("sales"), where("completedAt", ">=", start))),
      watch("payments", query(col("payments"), where("createdAt", ">=", start))),
      watch("orders", query(col("orders"), orderBy("updatedAt", "desc"), limit(20))),
      watch("deliveries", query(col("deliveries"), limit(50))),
      watch("suppliers", query(col("suppliers"))),
      watch("auditLogs", query(col("auditLogs"), orderBy("at", "desc"), limit(100))),
    ];
    // Re-subscribe just after the next IST midnight so "Today" rolls over while the tab stays open.
    const next = startOfDayIST(nowMs()) + DAY_MS + 1000 - nowMs();
    midnightTimer = setTimeout(() => { stop(); data = { ...EMPTY }; subscribe(); }, next);
  };

  const stop = () => {
    unsubs.forEach((u) => u());
    unsubs = [];
    if (midnightTimer) clearTimeout(midnightTimer);
  };

  subscribe();
  return stop;
}
```

- [ ] **Step 4: Implement the layout, pages and styles**

```tsx
// apps/dashboard/src/ui/Layout.tsx
import type { ComponentChildren } from "preact";
import type { Access } from "../access";

export const ROUTES = [
  { hash: "#/stock", label: "Stock" },
  { hash: "#/today", label: "Today" },
  { hash: "#/orders", label: "Orders & deliveries" },
  { hash: "#/audit", label: "Audit trail" },
  { hash: "#/forecast", label: "Forecast" },
] as const;

export function Layout(props: { access: Access; route: string; onSignIn(): void; children: ComponentChildren }) {
  return (
    <div>
      <a class="skip" href="#main">Skip to content</a>
      <header class="top">
        <img src="/aira-logo-192.png" alt="" width={40} height={40} />
        <span class="brand">AIRA — Shop Dashboard</span>
        {props.access.mode === "demo" && (
          <button type="button" class="signin" onClick={props.onSignIn}>Family sign-in</button>
        )}
      </header>
      {props.access.mode === "demo" && (
        <p role="status" class="banner">Demo shop (read-only): Murugan Dairy sample data.</p>
      )}
      <nav aria-label="Dashboard sections">
        <ul class="nav">
          {ROUTES.map((r) => (
            <li key={r.hash}>
              <a href={r.hash} aria-current={props.route === r.hash ? "page" : undefined}>{r.label}</a>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" tabIndex={-1}>{props.children}</main>
    </div>
  );
}
```

```tsx
// apps/dashboard/src/pages/StockPage.tsx
import type { StockRow } from "../selectors/stock";

export function StockPage(props: { rows: StockRow[] }) {
  return (
    <section>
      <h1>Stock</h1>
      <table>
        <caption>Stock on hand</caption>
        <thead>
          <tr><th scope="col">Product</th><th scope="col">On hand</th><th scope="col">Status</th><th scope="col">Expiry</th></tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr key={r.sku}>
              <td>{r.name}</td>
              <td>{r.onHandText}</td>
              <td>{r.lowStock ? <span class="badge warn">Low stock</span> : <span class="badge ok">OK</span>}</td>
              <td>{r.expiryText ? <span class={r.daysToExpiry !== null && r.daysToExpiry <= 1 ? "badge warn" : ""}>{r.expiryText}</span> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

```tsx
// apps/dashboard/src/pages/TodayPage.tsx
import type { TodaySummary } from "../selectors/today";

export function TodayPage(props: { summary: TodaySummary }) {
  const s = props.summary;
  return (
    <section>
      <h1>Today</h1>
      <dl class="stats">
        <div><dt>Sales</dt><dd>{s.salesCount}</dd></div>
        <div><dt>Revenue</dt><dd>{s.revenueText}</dd></div>
        <div><dt>Top product</dt><dd>{s.topProduct ?? "—"}</dd></div>
        <div><dt>Cash received</dt><dd>{s.byMethodText.cash}</dd></div>
        <div><dt>UPI received</dt><dd>{s.byMethodText.upi}</dd></div>
      </dl>
    </section>
  );
}
```

```tsx
// apps/dashboard/src/pages/OrdersPage.tsx
import type { OrderRow } from "../selectors/orders";
import { ORDER_FLOW } from "../selectors/orders";
import { stateLabel } from "../format";

export function OrdersPage(props: { rows: OrderRow[] }) {
  return (
    <section>
      <h1>Orders &amp; deliveries</h1>
      {props.rows.length === 0 && <p>No orders yet.</p>}
      {props.rows.map((o) => (
        <article key={o.id} class={o.isDiscrepancy ? "order discrepancy" : "order"} aria-labelledby={`h-${o.id}`}>
          <h2 id={`h-${o.id}`}>{o.supplierName}: {o.itemsText}</h2>
          <p>
            Status: <strong>{o.stateLabel}</strong>
            {o.updatedText && <> · updated {o.updatedText}</>}
          </p>
          <ol class="steps" aria-label={`Order progress for ${o.id}`}>
            {ORDER_FLOW.map((s, i) => (
              <li key={s} aria-current={i === o.stepIndex ? "step" : undefined}
                  class={i < o.stepIndex ? "done" : i === o.stepIndex ? "now" : ""}>
                {i === o.stepIndex && o.isDiscrepancy ? "Discrepancy found" : stateLabel(s)}
              </li>
            ))}
          </ol>
          {o.vendorReply && <p>Vendor reply: “{o.vendorReply}”</p>}
          {o.discrepancyTexts.length > 0 && (
            <ul aria-label="Discrepancies">
              {o.discrepancyTexts.map((t) => <li key={t}>{t}</li>)}
            </ul>
          )}
        </article>
      ))}
    </section>
  );
}
```

```tsx
// apps/dashboard/src/pages/AuditPage.tsx
import { useEffect, useState } from "preact/hooks";
import type { AuditRow } from "../selectors/audit";

function Evidence(props: { uri: string; resolve(uri: string): Promise<string | null> }) {
  const [href, setHref] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    props.resolve(props.uri).then((h) => (h ? setHref(h) : setFailed(true))).catch(() => setFailed(true));
  }, [props.uri]);
  if (href) return <a href={href} target="_blank" rel="noopener noreferrer">View evidence</a>;
  return <span>{failed ? "Evidence restricted" : "Loading…"}</span>;
}

export function AuditPage(props: { rows: AuditRow[]; resolveEvidence(uri: string): Promise<string | null> }) {
  return (
    <section>
      <h1>Audit trail</h1>
      <table>
        <caption>Recent actions by the owner, AIRA and the system</caption>
        <thead>
          <tr><th scope="col">Time</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Outcome</th><th scope="col">Evidence</th></tr>
        </thead>
        <tbody>
          {props.rows.map((r) => (
            <tr key={r.id}>
              <td>{r.timeText}</td><td>{r.actorLabel}</td><td>{r.actionText}</td><td>{r.outcome}</td>
              <td>{r.evidenceUri ? <Evidence uri={r.evidenceUri} resolve={props.resolveEvidence} /> : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

```tsx
// apps/dashboard/src/pages/ForecastPage.tsx
import type { ForecastItem } from "../forecast";

export function ForecastPage(props: { items: ForecastItem[] | null; loading: boolean }) {
  return (
    <section>
      <h1>Forecast</h1>
      {props.loading && <p role="status">Loading forecast…</p>}
      {!props.loading && props.items === null && <p role="status">Forecast unavailable</p>}
      {!props.loading && props.items && (
        <table>
          <caption>Reorder suggestions (BigQuery demand forecast)</caption>
          <thead>
            <tr><th scope="col">Product</th><th scope="col">Daily demand</th><th scope="col">On hand</th><th scope="col">Suggested order</th></tr>
          </thead>
          <tbody>
            {props.items.map((f) => (
              <tr key={f.sku}>
                <td>{f.name}</td><td>{f.dailyDemand.toFixed(1)}</td><td>{f.onHand}</td><td>{f.suggestedOrder}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
```

```css
/* apps/dashboard/src/styles.css — palette chosen for WCAG AA contrast on white */
:root { --fg: #0b1b3f; --muted: #3d4a66; --accent: #0b57d0; --warn-bg: #fde7e9; --warn-fg: #8a1020; --ok-bg: #e6f4ea; --ok-fg: #0d5c2e; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; color: var(--fg); background: #fff; line-height: 1.5; }
.skip { position: absolute; left: -999px; } .skip:focus { left: 8px; top: 8px; background: #fff; padding: 8px; z-index: 10; }
.top { display: flex; gap: 12px; align-items: center; padding: 12px 16px; border-bottom: 1px solid #d0d7e2; }
.brand { font-weight: 700; font-size: 1.1rem; flex: 1; }
.banner { margin: 0; padding: 8px 16px; background: #eef3fd; color: var(--fg); }
.nav { display: flex; flex-wrap: wrap; gap: 4px; list-style: none; margin: 0; padding: 8px 16px; }
.nav a { color: var(--accent); padding: 6px 10px; border-radius: 6px; }
.nav a[aria-current="page"] { background: var(--accent); color: #fff; }
a:focus-visible, button:focus-visible, main:focus-visible { outline: 3px solid #f29900; outline-offset: 2px; }
main { padding: 16px; }
table { border-collapse: collapse; width: 100%; } caption { text-align: left; font-weight: 600; padding: 4px 0; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid #d0d7e2; }
.badge { padding: 2px 8px; border-radius: 999px; font-weight: 600; }
.badge.warn { background: var(--warn-bg); color: var(--warn-fg); } .badge.ok { background: var(--ok-bg); color: var(--ok-fg); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.stats div { border: 1px solid #d0d7e2; border-radius: 8px; padding: 12px; } dt { color: var(--muted); } dd { margin: 0; font-size: 1.4rem; font-weight: 700; }
.order { border: 1px solid #d0d7e2; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.order.discrepancy { border: 3px solid var(--warn-fg); }
.steps { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; padding: 0; }
.steps li { padding: 2px 8px; border-radius: 6px; border: 1px solid #d0d7e2; color: var(--muted); }
.steps li.done { color: var(--ok-fg); } .steps li.now { border-color: var(--accent); color: var(--fg); font-weight: 700; }
.signin { background: var(--accent); color: #fff; border: 0; border-radius: 6px; padding: 8px 12px; cursor: pointer; }
```

- [ ] **Step 5: Implement `src/main.tsx` and update `index.html`**

```tsx
// apps/dashboard/src/main.tsx
import { render } from "preact";
import { useEffect, useMemo, useState } from "preact/hooks";
import { GoogleAuthProvider, onAuthStateChanged, signInWithPopup } from "firebase/auth";
import { doc, getDoc } from "firebase/firestore";
import { getDownloadURL, ref } from "firebase/storage";
import { auth, db, storage } from "./firebase";
import { resolveAccess, type Access } from "./access";
import { listenShop, type ShopData } from "./data/live";
import { stockRows } from "./selectors/stock";
import { todaySummary } from "./selectors/today";
import { orderRows } from "./selectors/orders";
import { auditRows } from "./selectors/audit";
import { fetchForecast, type ForecastItem } from "./forecast";
import { Layout, ROUTES } from "./ui/Layout";
import { StockPage } from "./pages/StockPage";
import { TodayPage } from "./pages/TodayPage";
import { OrdersPage } from "./pages/OrdersPage";
import { AuditPage } from "./pages/AuditPage";
import { ForecastPage } from "./pages/ForecastPage";
import "./styles.css";

const env = (import.meta as any).env ?? {};
const wantDemo = new URLSearchParams(location.search).get("demo") === "1";
const currentRoute = () => (ROUTES.some((r) => r.hash === location.hash) ? location.hash : "#/stock");

async function resolveEvidence(uri: string): Promise<string | null> {
  if (uri.startsWith("https://")) return uri;
  if (!uri.startsWith("gs://")) return null;
  try { return await getDownloadURL(ref(storage, uri)); } catch { return null; }
}

function App() {
  const [access, setAccess] = useState<Access | null>(null);
  const [data, setData] = useState<ShopData | null>(null);
  const [route, setRoute] = useState(currentRoute());
  const [forecast, setForecast] = useState<ForecastItem[] | null>(null);
  const [forecastLoading, setForecastLoading] = useState(true);

  useEffect(() => {
    const onHash = () => { setRoute(currentRoute()); document.getElementById("main")?.focus(); };
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => onAuthStateChanged(auth, async (user) => {
    let userDoc: { shopId?: string; role?: string } | null = null;
    if (user && !wantDemo) {
      const snap = await getDoc(doc(db, "users", user.uid)).catch(() => null);
      userDoc = snap?.exists() ? (snap.data() as { shopId?: string; role?: string }) : null;
    }
    setAccess(resolveAccess({ uid: user?.uid ?? null, userDoc, wantDemo }));
  }), []);

  useEffect(() => {
    if (!access || access.mode === "denied") return;
    const stop = listenShop(access.shopId, setData);
    setForecastLoading(true);
    fetchForecast(env.VITE_AIRA_LIVE_HTTP ?? "", access.shopId).then((f) => { setForecast(f); setForecastLoading(false); });
    return stop;
  }, [access?.mode, access && access.mode !== "denied" ? access.shopId : ""]);

  const now = Date.now();
  const views = useMemo(() => data && {
    stock: stockRows(data.products, data.inventory, now),
    today: todaySummary(data.sales, data.payments, data.products, now),
    orders: orderRows(data.orders, data.deliveries, data.suppliers, data.products),
    audit: auditRows(data.auditLogs),
  }, [data]);

  if (!access) return <p role="status">Loading AIRA dashboard…</p>;
  const signIn = () => { signInWithPopup(auth, new GoogleAuthProvider()).catch(() => undefined); };
  if (access.mode === "denied") {
    return (
      <main id="main">
        <h1>AIRA</h1>
        <p role="alert">{access.reason}</p>
        <p><a href="/?demo=1">Open the demo shop instead</a></p>
      </main>
    );
  }
  return (
    <Layout access={access} route={route} onSignIn={signIn}>
      {!views && <p role="status">Loading shop data…</p>}
      {views && route === "#/stock" && <StockPage rows={views.stock} />}
      {views && route === "#/today" && <TodayPage summary={views.today} />}
      {views && route === "#/orders" && <OrdersPage rows={views.orders} />}
      {views && route === "#/audit" && <AuditPage rows={views.audit} resolveEvidence={resolveEvidence} />}
      {route === "#/forecast" && <ForecastPage items={forecast} loading={forecastLoading} />}
    </Layout>
  );
}

render(<App />, document.getElementById("root")!);
```

`apps/dashboard/index.html`. Replace the `<head>` title and icon, and keep the `#root` div:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="icon" href="/aira-logo-192.png" />
    <title>AIRA — Shop Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Then copy the logo:

```bash
mkdir -p apps/dashboard/public && cp assets/logo/aira-logo-192.png apps/dashboard/public/aira-logo-192.png
```

- [ ] **Step 6: Run the tests, the type check and the build**

Run: `npm test -w apps/dashboard && npm run build -w apps/dashboard`
Expected: all unit tests PASS (Task 1 + Task 2 + 5 page tests + 1 read-only test), `tsc` reports no errors, and `apps/dashboard/dist/index.html` exists.

- [ ] **Step 7: Commit**

```bash
git add apps/dashboard/src apps/dashboard/index.html apps/dashboard/public/aira-logo-192.png apps/dashboard/test/pages.test.tsx apps/dashboard/test/readonly.test.ts
git commit -m "feat(dashboard): realtime Firestore listeners, accessible pages, demo banner and family sign-in"
```

---

### Task 4: End-to-end smoke test against the Firestore emulator, with axe

**Files:**
- Create: `apps/dashboard/playwright.config.ts`, `apps/dashboard/e2e/seed.ts`, `apps/dashboard/e2e/smoke.spec.ts`

**Interfaces:**
- Consumes:
  - Task 3 app.
  - `firebase.json` emulator config: `emulators.firestore.port = 8085` (cross-team note 3).
  - Firestore rules allowing public read of `shops/demo/**` (cross-team note 2).
- Produces:
  - `npm run e2e -w apps/dashboard`, run inside `firebase emulators:exec`.
  - A seeded demo shop that matches the demo story: 28 L curd, 2 L short on the Sakthi order, and a UPI sale.

- [ ] **Step 1: Create `playwright.config.ts`**

```ts
// apps/dashboard/playwright.config.ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/seed.ts",
  use: { baseURL: "http://localhost:5175" },
  webServer: {
    command: "npx vite --port 5175 --strictPort",
    url: "http://localhost:5175",
    reuseExistingServer: false,
    env: {
      VITE_USE_EMULATOR: "1",
      VITE_FIRESTORE_EMULATOR_PORT: "8085",
      VITE_FIREBASE_PROJECT_ID: "demo-aira",
      VITE_FIREBASE_API_KEY: "demo-key",
      VITE_FIREBASE_AUTH_DOMAIN: "demo-aira.firebaseapp.com",
      VITE_FIREBASE_STORAGE_BUCKET: "demo-aira.appspot.com",
      VITE_FIREBASE_APP_ID: "1:0:web:demo",
      VITE_AIRA_LIVE_HTTP: "http://127.0.0.1:9", // unreachable on purpose → "Forecast unavailable"
    },
  },
});
```

- [ ] **Step 2: Create the seed (runs once before the tests; `emulators:exec` sets `FIRESTORE_EMULATOR_HOST`)**

```ts
// apps/dashboard/e2e/seed.ts
import { initializeApp, getApps } from "firebase-admin/app";
import { getFirestore } from "firebase-admin/firestore";

export default async function seed() {
  if (!process.env.FIRESTORE_EMULATOR_HOST) throw new Error("Run inside `firebase emulators:exec` (FIRESTORE_EMULATOR_HOST missing)");
  if (!getApps().length) initializeApp({ projectId: "demo-aira" });
  const db = getFirestore();
  const shop = db.collection("shops").doc("demo");
  const now = new Date();
  const ymd = (offsetDays: number) => {
    const d = new Date(now.getTime() + 330 * 60_000 + offsetDays * 86_400_000); // IST calendar date
    return d.toISOString().slice(0, 10);
  };
  const batch = db.batch();
  batch.set(shop, { name: "Murugan Dairy (demo)", ownerUid: "demo-owner", city: "Chennai", currency: "INR", timezone: "Asia/Kolkata" });
  // Demo prices only — not real MRP.
  batch.set(shop.collection("products").doc("sakthi_curd_1l"), { name: "Sakthi curd 1 L", brand: "Sakthi", unit: "L", packSizeL: 1, price: 60, costPrice: 50, reorderThreshold: 5, shelfLifeDays: 7, aliases: { ta: ["தயிர்"], hi: ["दही"], en: ["curd"] } });
  batch.set(shop.collection("products").doc("aavin_milk_500ml"), { name: "Aavin milk 500 ml", brand: "Aavin", unit: "pack", packSizeL: 0.5, price: 25, costPrice: 22, reorderThreshold: 10, shelfLifeDays: 2, aliases: { ta: ["பால்"], hi: ["दूध"], en: ["milk"] } });
  batch.set(shop.collection("products").doc("arun_icecream_box"), { name: "Arun ice cream box", brand: "Arun", unit: "box", price: 180, costPrice: 150, reorderThreshold: 3, shelfLifeDays: 180, aliases: { ta: ["ஐஸ்கிரீம்"], hi: ["आइसक्रीम"], en: ["ice cream"] } });
  batch.set(shop.collection("inventory").doc("sakthi_curd_1l"), { onHand: 28, batches: [{ batchId: "b1", qty: 28, expiryDate: ymd(1) }], updatedAt: now });
  batch.set(shop.collection("inventory").doc("aavin_milk_500ml"), { onHand: 40, batches: [{ batchId: "m1", qty: 40, expiryDate: ymd(2) }], updatedAt: now });
  batch.set(shop.collection("inventory").doc("arun_icecream_box"), { onHand: 2, batches: [], updatedAt: now });
  batch.set(shop.collection("suppliers").doc("sakthi_vendor"), { name: "Sakthi vendor", phone: "+910000000000", channel: "whatsapp", products: ["sakthi_curd_1l"] });
  batch.set(shop.collection("orders").doc("o1"), { supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "DISCREPANCY", vendorReply: "OK, delivery at 6 pm", idemKey: "seed-o1", createdAt: now, updatedAt: now });
  batch.set(shop.collection("deliveries").doc("d1"), { orderId: "o1", items: [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28, method: "one_by_one", confidence: 0.93 }], discrepancies: [{ sku: "sakthi_curd_1l", expected: 30, actual: 28 }], state: "DISCREPANCY" });
  batch.set(shop.collection("sales").doc("s1"), { items: [{ sku: "aavin_milk_500ml", qty: 2, unitPrice: 25, lineTotal: 50 }], total: 50, state: "SALE_COMPLETED", paymentId: "p1", idemKey: "seed-s1", createdAt: now, completedAt: now });
  batch.set(shop.collection("sales").doc("s2"), { items: [{ sku: "arun_icecream_box", qty: 1, unitPrice: 180, lineTotal: 180 }], total: 180, state: "SALE_COMPLETED", paymentId: "p2", idemKey: "seed-s2", createdAt: now, completedAt: now });
  batch.set(shop.collection("payments").doc("p1"), { direction: "in", method: "cash", amount: 50, state: "CONFIRMED", evidence: { type: "cash_count", detail: "₹50 note" }, refType: "sale", refId: "s1", createdAt: now });
  batch.set(shop.collection("payments").doc("p2"), { direction: "in", method: "upi", amount: 180, state: "CONFIRMED", evidence: { type: "soundbox", detail: "received rupees 180" }, refType: "sale", refId: "s2", createdAt: now });
  batch.set(shop.collection("auditLogs").doc("a1"), { actor: "aira", action: "sale_completed", refType: "sale", refId: "s2", outcome: "ok", at: now });
  batch.set(shop.collection("auditLogs").doc("a2"), { actor: "owner", action: "order_confirmed", refType: "order", refId: "o1", outcome: "ok", at: new Date(now.getTime() - 60_000) });
  await batch.commit();
}
```

- [ ] **Step 3: Write the smoke spec**

```ts
// apps/dashboard/e2e/smoke.spec.ts
import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function noSeriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => v.id)).toEqual([]);
}

test("demo shop: stock, today, orders, audit, forecast — realtime + accessible", async ({ page }) => {
  await page.goto("/?demo=1#/stock");
  await expect(page.getByRole("status").filter({ hasText: "Demo shop (read-only)" })).toBeVisible();
  const stock = page.getByRole("table", { name: /stock on hand/i });
  await expect(stock.getByRole("row", { name: /Arun ice cream box/ })).toContainText("Low stock");
  await expect(stock.getByRole("row", { name: /Sakthi curd 1 L/ })).toContainText("28 L");
  await expect(stock.getByRole("row", { name: /Sakthi curd 1 L/ })).toContainText("Expires tomorrow");
  await noSeriousA11yIssues(page);

  await page.getByRole("link", { name: "Today" }).click();
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByText("₹230")).toBeVisible();
  await expect(page.getByText("₹180").first()).toBeVisible();
  await noSeriousA11yIssues(page);

  await page.getByRole("link", { name: "Orders & deliveries" }).click();
  await expect(page.getByText("Sakthi curd 1 L: expected 30, got 28")).toBeVisible();
  await expect(page.getByText(/OK, delivery at 6 pm/)).toBeVisible();
  await expect(page.getByText("Discrepancy", { exact: true })).toBeVisible();
  await noSeriousA11yIssues(page);

  await page.getByRole("link", { name: "Audit trail" }).click();
  await expect(page.getByRole("cell", { name: "Sale completed" })).toBeVisible();
  await noSeriousA11yIssues(page);

  await page.getByRole("link", { name: "Forecast" }).click();
  await expect(page.getByText("Forecast unavailable")).toBeVisible();
});

test("signed-out visitor cannot pick another shop via ?shop=", async ({ page }) => {
  await page.goto("/?shop=someone-else#/stock");
  await expect(page.getByRole("status").filter({ hasText: "Demo shop (read-only)" })).toBeVisible();
  await expect(page.getByRole("table", { name: /stock on hand/i })).toContainText("Sakthi curd 1 L");
});
```

- [ ] **Step 4: Install the browser and run the smoke test (expect it to pass once the rules allow demo reads)**

Run:
```bash
npx playwright install --with-deps chromium
npx firebase-tools emulators:exec --only firestore --project demo-aira "npm run e2e -w apps/dashboard"
```
Expected: `2 passed`.

If it fails with `Missing or insufficient permissions`, the Firestore rules don't yet allow public read of `shops/demo/**`. Apply cross-team note 2 (Saravana) and re-run.

- [ ] **Step 5: Commit**

```bash
git add apps/dashboard/playwright.config.ts apps/dashboard/e2e
git commit -m "test(dashboard): Playwright + axe smoke test against Firestore emulator with demo shop seed"
```

---

### Task 5: Lighthouse gate, Firebase Hosting target `dashboard`, deploy

**Files:**
- Create: `apps/dashboard/scripts/a11y-score.mjs`, `apps/dashboard/test/a11y-score.test.ts`, `apps/dashboard/.env.production.example`
- Modify (small PR, coordinate with Saravana): `firebase.json`, `.firebaserc`

**Interfaces:**
- Consumes:
  - the built `dist/`
  - the Firebase project ID
  - aira-live `GET /api/forecast?shop=` (cross-team note 1)
- Produces:
  - `https://<project>-dashboard.web.app` (demo: `/?demo=1`)
  - `checkScore(report: unknown, min: number): { ok: boolean; score: number | null }`

- [ ] **Step 1: Write the failing test for the score gate**

```ts
// apps/dashboard/test/a11y-score.test.ts
import { describe, it, expect } from "vitest";
// @ts-expect-error — plain ESM script without types
import { checkScore } from "../scripts/a11y-score.mjs";

describe("Lighthouse accessibility gate", () => {
  it("passes at or above the minimum", () => {
    expect(checkScore({ categories: { accessibility: { score: 0.97 } } }, 0.95)).toEqual({ ok: true, score: 0.97 });
  });
  it("fails below the minimum or on a broken report", () => {
    expect(checkScore({ categories: { accessibility: { score: 0.9 } } }, 0.95)).toEqual({ ok: false, score: 0.9 });
    expect(checkScore({}, 0.95)).toEqual({ ok: false, score: null });
  });
});
```

Run: `npm test -w apps/dashboard`
Expected: FAIL with `Failed to resolve import "../scripts/a11y-score.mjs"`.

- [ ] **Step 2: Implement `scripts/a11y-score.mjs`**

```js
// apps/dashboard/scripts/a11y-score.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export function checkScore(report, min) {
  const score = report?.categories?.accessibility?.score;
  if (typeof score !== "number") return { ok: false, score: null };
  return { ok: score >= min, score };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [, , file, min = "0.95"] = process.argv;
  const result = checkScore(JSON.parse(readFileSync(file, "utf8")), Number(min));
  console.log(`Lighthouse accessibility score: ${result.score} (min ${min}) → ${result.ok ? "PASS" : "FAIL"}`);
  process.exit(result.ok ? 0 : 1);
}
```

Run: `npm test -w apps/dashboard`
Expected: PASS, including the 2 new tests.

- [ ] **Step 3: Run Lighthouse against the emulator-backed preview**

```bash
cat > /tmp/aira-dash-lh.sh <<'EOF'
set -e
VITE_USE_EMULATOR=1 VITE_FIRESTORE_EMULATOR_PORT=8085 VITE_FIREBASE_PROJECT_ID=demo-aira VITE_FIREBASE_API_KEY=demo-key \
VITE_FIREBASE_AUTH_DOMAIN=demo-aira.firebaseapp.com VITE_FIREBASE_STORAGE_BUCKET=demo-aira.appspot.com \
VITE_FIREBASE_APP_ID=1:0:web:demo VITE_AIRA_LIVE_HTTP=http://127.0.0.1:9 npm run build -w apps/dashboard
npx tsx apps/dashboard/e2e/seed.ts
(cd apps/dashboard && npx vite preview --port 4173 --strictPort) & PREVIEW=$!
sleep 3
npx lighthouse "http://localhost:4173/?demo=1#/stock" --only-categories=accessibility --output=json \
  --output-path=apps/dashboard/lh.json --chrome-flags="--headless=new" --quiet
kill $PREVIEW
node apps/dashboard/scripts/a11y-score.mjs apps/dashboard/lh.json 0.95
EOF
npx firebase-tools emulators:exec --only firestore --project demo-aira "bash /tmp/aira-dash-lh.sh"
```

`seed.ts` exports a default function. For `npx tsx` to run it directly, append `if (process.argv[1]?.endsWith("seed.ts")) seed().then(() => process.exit(0));` to the bottom of `e2e/seed.ts`.

Expected: `Lighthouse accessibility score: 0.9x (min 0.95) → PASS` (score ≥ 0.95). If it fails, fix the reported audits (usually contrast or a missing label) and re-run.

- [ ] **Step 4: Add the hosting target (small PR to `firebase.json` / `.firebaserc`; Saravana reviews)**

Add this entry to the `"hosting"` array in `firebase.json`:

```json
{ "target": "dashboard", "public": "apps/dashboard/dist", "ignore": ["**/.*"],
  "rewrites": [{ "source": "**", "destination": "/index.html" }] }
```

Add `"emulators": { "firestore": { "port": 8085 } }` at the top level if it isn't already there. Then create the site and map the target:

```bash
npx firebase-tools hosting:sites:create <project>-dashboard --project <project>
npx firebase-tools target:apply hosting dashboard <project>-dashboard --project <project>
```

- [ ] **Step 5: Production env and deploy**

`apps/dashboard/.env.production.example` (copy to `.env.production`, which is git-ignored, and fill it in from Firebase console → Project settings → Web app):

```
VITE_FIREBASE_API_KEY=
VITE_FIREBASE_AUTH_DOMAIN=<project>.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=<project>
VITE_FIREBASE_STORAGE_BUCKET=<project>.appspot.com
VITE_FIREBASE_APP_ID=
VITE_AIRA_LIVE_HTTP=https://<aira-live-url>
```

```bash
npm run build -w apps/dashboard
npx firebase-tools deploy --only hosting:dashboard --project <project>
curl -s -o /dev/null -w "%{http_code}\n" "https://<project>-dashboard.web.app/?demo=1"
```

Expected: the deploy prints `Hosting URL: https://<project>-dashboard.web.app`, and `curl` prints `200`. Opening `/?demo=1` shows the Murugan Dairy demo stock (once Saravana's demo shop seed exists in production).

- [ ] **Step 6: Commit**

```bash
git add apps/dashboard/scripts/a11y-score.mjs apps/dashboard/test/a11y-score.test.ts apps/dashboard/.env.production.example apps/dashboard/e2e/seed.ts firebase.json .firebaserc
git commit -m "feat(dashboard): Lighthouse a11y gate and Firebase Hosting target 'dashboard'"
```

---

## Cross-team notes (send these to the owners on D4 morning)

1. **Saravana (aira-live):** add `GET /api/forecast?shop=<shopId>` → `200 {"items":[{"sku","name","dailyDemand","onHand","suggestedOrder"}]}`.
   - Built from `analytics.forecast.daily_demand` + `business.ledger.on_hand`, with `suggestedOrder = max(0, ceil(dailyDemand × 1.2 − onHand))`.
   - CORS allows the `https://<project>-dashboard.web.app` origin.
   - No auth for `shop=demo`. For a real shop, require a Firebase ID token whose `users/{uid}.shopId` matches.
2. **Saravana (Firestore rules):**
   - `match /shops/demo/{document=**} { allow read: if true; allow write: if false; }`
   - `match /shops/{shopId}/{document=**} { allow read: if request.auth != null && get(/databases/$(database)/documents/users/$(request.auth.uid)).data.shopId == shopId && get(/databases/$(database)/documents/users/$(request.auth.uid)).data.role in ['owner','family']; }`
   - `match /users/{uid} { allow read: if request.auth.uid == uid; }`
   - Storage rules: allow the same owner/family read for evidence images.
3. **Saravana (`firebase.json`):** hosting target `dashboard` + `emulators.firestore.port: 8085`. Port 8080 is used by aira-live locally.
4. **Kanish/01 + contracts PR (Saravana reviews):**
   - `business.sales.create` / `complete` must write `createdAt` / `completedAt`.
   - `business.payments.record` / `confirm` must write `createdAt` / `confirmedAt` (Firestore server timestamps).
   - Add these as fields on `Sale` and `Payment` in `packages/contracts/src/firestore.ts` and `aira_live/contracts.py`. Spec §5 doesn't list them yet, and the dashboard's "Today" view depends on them.
5. **Saravana (production demo data):** seed `shops/demo` in the real project with the same shape as `e2e/seed.ts`, so judges see live data at `/?demo=1`.
