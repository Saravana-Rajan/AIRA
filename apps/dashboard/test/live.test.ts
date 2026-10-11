import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Constraint = { kind: string; field?: string; op?: string; value?: unknown; dir?: string; n?: number };
type FakeQuery = { path: string; constraints: Constraint[] };
type Sub = {
  q: FakeQuery; opts: unknown; unsubscribed: boolean;
  next: (snap: unknown) => void; error: (e: unknown) => void;
};

const h = vi.hoisted(() => ({ subs: [] as Sub[] }));

vi.mock("../src/lib/firebase", () => ({ db: { fake: "db" } }));
vi.mock("firebase/firestore", () => ({
  collection: (_db: unknown, ...segs: string[]) => ({ path: segs.join("/") }),
  query: (c: { path: string }, ...constraints: Constraint[]) => ({ path: c.path, constraints }),
  where: (field: string, op: string, value: unknown) => ({ kind: "where", field, op, value }),
  orderBy: (field: string, dir: string) => ({ kind: "orderBy", field, dir }),
  limit: (n: number) => ({ kind: "limit", n }),
  Timestamp: { fromMillis: (ms: number) => ({ kind: "ts", ms }) },
  onSnapshot: (q: FakeQuery, opts: unknown, next: Sub["next"], error: Sub["error"]) => {
    const s: Sub = { q, opts, next, error, unsubscribed: false };
    h.subs.push(s);
    return () => {
      s.unsubscribed = true;
    };
  },
}));

import { listenShop, type ShopData } from "../src/lib/live";

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
const START = Date.UTC(2026, 9, 9, 18, 30); // 2026-10-10 00:00 IST
const DAY = 86_400_000;

const live = () => h.subs.filter((s) => !s.unsubscribed);
const snap = (docs: Array<[string, Record<string, unknown>]>, fromCache = false) => ({
  docs: docs.map(([id, d]) => ({ id, data: () => d })),
  metadata: { fromCache },
});
const subFor = (path: string, pred: (c: Constraint[]) => boolean = () => true) =>
  live().find((s) => s.q.path === path && pred(s.q.constraints))!;
const whereValue = (s: Sub) => s.q.constraints.find((c) => c.kind === "where")?.value;
const isTs = (c: Constraint[]) => (c.find((x) => x.kind === "where")?.value as { kind?: string })?.kind === "ts";
const isIso = (c: Constraint[]) => typeof c.find((x) => x.kind === "where")?.value === "string";

function deliverAll(except: string[] = []) {
  for (const s of live()) {
    const key = `${s.q.path}${isTs(s.q.constraints) ? "@ts" : isIso(s.q.constraints) ? "@iso" : ""}`;
    if (!except.includes(key)) s.next(snap([]));
  }
}

beforeEach(() => {
  h.subs.length = 0;
});
afterEach(() => {
  vi.useRealTimers();
});

describe("listenShop", () => {
  it("listens under shops/{shopId} with single-field queries only (no composite indexes)", () => {
    const stop = listenShop("murugan_dairy", () => {}, () => NOW);
    const paths = live().map((s) => s.q.path).sort();
    expect(paths).toEqual([
      "shops/murugan_dairy/auditLogs",
      "shops/murugan_dairy/deliveries",
      "shops/murugan_dairy/inventory",
      "shops/murugan_dairy/orders",
      "shops/murugan_dairy/payments",
      "shops/murugan_dairy/payments",
      "shops/murugan_dairy/products",
      "shops/murugan_dairy/sales",
      "shops/murugan_dairy/sales",
    ]);
    // suppliers (vendor phones, Telegram chat ids) is server-only in infra/firestore.rules: never subscribed
    expect(paths).not.toContain("shops/murugan_dairy/suppliers");
    for (const s of live()) {
      const fields = new Set(s.q.constraints.filter((c) => c.field).map((c) => c.field));
      expect(fields.size, s.q.path).toBeLessThanOrEqual(1);
      expect(s.opts).toEqual({ includeMetadataChanges: true });
    }
    stop();
  });

  it("filters sales and payments from the start of the IST day, for Timestamp and ISO-string times", () => {
    const stop = listenShop("murugan_dairy", () => {}, () => NOW);
    const salesTs = subFor("shops/murugan_dairy/sales", isTs);
    const salesIso = subFor("shops/murugan_dairy/sales", isIso);
    expect(salesTs.q.constraints).toEqual([{ kind: "where", field: "completedAt", op: ">=", value: { kind: "ts", ms: START } }]);
    // Strings compare as text, so the ISO bound starts a day early to cover any UTC offset; selectors cut the exact IST day.
    expect(salesIso.q.constraints).toEqual([{ kind: "where", field: "completedAt", op: ">=", value: new Date(START - DAY).toISOString() }]);
    expect(whereValue(subFor("shops/murugan_dairy/payments", isTs))).toEqual({ kind: "ts", ms: START });
    expect(subFor("shops/murugan_dairy/payments", isIso).q.constraints[0].field).toBe("createdAt");
    expect(subFor("shops/murugan_dairy/orders").q.constraints).toEqual([
      { kind: "orderBy", field: "updatedAt", dir: "desc" }, { kind: "limit", n: 20 },
    ]);
    expect(subFor("shops/murugan_dairy/auditLogs").q.constraints).toEqual([
      { kind: "orderBy", field: "at", dir: "desc" }, { kind: "limit", n: 100 },
    ]);
    stop();
  });

  it("waits for every first snapshot (no half-loaded 'Low stock' flash), then emits normalised, de-duplicated data", () => {
    const onChange = vi.fn();
    const onStatus = vi.fn();
    const stop = listenShop("murugan_dairy", onChange, () => NOW, onStatus);
    expect(onStatus).toHaveBeenLastCalledWith("connecting");

    subFor("shops/murugan_dairy/products").next(snap([["sakthi_curd_1l", { sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L" }]]));
    const sale = { id: "s1", state: "SALE_COMPLETED", total: 60, completedAt: { toMillis: () => NOW } };
    subFor("shops/murugan_dairy/sales", isTs).next(snap([["s1", sale]]));
    subFor("shops/murugan_dairy/sales", isIso).next(snap([["s1", sale], ["s2", { id: "s2", completedAt: "2026-10-10T05:00:00+00:00" }]]));
    deliverAll(["shops/murugan_dairy/products", "shops/murugan_dairy/sales@ts", "shops/murugan_dairy/sales@iso", "shops/murugan_dairy/inventory"]);
    expect(onChange).not.toHaveBeenCalled();

    subFor("shops/murugan_dairy/inventory").next(snap([["sakthi_curd_1l", { onHand: 28, batches: [] }]]));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onStatus).toHaveBeenLastCalledWith("live");
    const d: ShopData = onChange.mock.calls[0][0];
    expect(d.products).toEqual([{ id: "sakthi_curd_1l", sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L" }]);
    expect(d.inventory[0]).toMatchObject({ id: "sakthi_curd_1l", onHand: 28 });
    expect(d.sales.map((s) => s.id).sort()).toEqual(["s1", "s2"]);
    expect(d.sales.find((s) => s.id === "s1")!.completedAt).toBe(NOW);
    expect(d.orders).toEqual([]);

    // Later changes stream straight through.
    subFor("shops/murugan_dairy/inventory").next(snap([["sakthi_curd_1l", { onHand: 26, batches: [] }]]));
    expect(onChange).toHaveBeenCalledTimes(2);
    expect((onChange.mock.calls[1][0] as ShopData).inventory[0].onHand).toBe(26);
    stop();
  });

  it("reports offline when a listener fails, and still shows the collections that loaded", () => {
    const onChange = vi.fn();
    const onStatus = vi.fn();
    const stop = listenShop("murugan_dairy", onChange, () => NOW, onStatus);
    subFor("shops/murugan_dairy/auditLogs").error(new Error("permission-denied"));
    expect(onStatus).toHaveBeenLastCalledWith("offline");
    deliverAll(["shops/murugan_dairy/auditLogs"]);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect((onChange.mock.calls[0][0] as ShopData).auditLogs).toEqual([]);
    expect(onStatus).toHaveBeenLastCalledWith("offline");
    stop();
  });

  it("reports offline while snapshots come only from the local cache, and live again once the server answers", () => {
    const onStatus = vi.fn();
    const stop = listenShop("murugan_dairy", () => {}, () => NOW, onStatus);
    deliverAll();
    expect(onStatus).toHaveBeenLastCalledWith("live");
    subFor("shops/murugan_dairy/products").next(snap([], true));
    expect(onStatus).toHaveBeenLastCalledWith("offline");
    subFor("shops/murugan_dairy/products").next(snap([], false));
    expect(onStatus).toHaveBeenLastCalledWith("live");
    stop();
  });

  it("stop() unsubscribes every listener and nothing fires afterwards", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const stop = listenShop("murugan_dairy", onChange, () => NOW);
    const all = [...h.subs];
    stop();
    expect(all.every((s) => s.unsubscribed)).toBe(true);
    vi.advanceTimersByTime(2 * DAY);
    expect(h.subs).toHaveLength(all.length); // no re-subscribe after stop
  });

  it("re-subscribes just after the next IST midnight so 'Today' rolls over in an open tab", () => {
    vi.useFakeTimers();
    let now = NOW;
    const onChange = vi.fn();
    const stop = listenShop("murugan_dairy", onChange, () => now);
    deliverAll();
    const first = [...h.subs];
    const untilMidnight = START + DAY - NOW;
    vi.advanceTimersByTime(untilMidnight - 10);
    expect(first.every((s) => !s.unsubscribed)).toBe(true);
    now = START + DAY + 1000;
    vi.advanceTimersByTime(1010);
    expect(first.every((s) => s.unsubscribed)).toBe(true);
    expect(live()).toHaveLength(first.length);
    expect(whereValue(subFor("shops/murugan_dairy/sales", isTs))).toEqual({ kind: "ts", ms: START + DAY });
    // The page keeps showing the last data while the new listeners load.
    const calls = onChange.mock.calls.length;
    subFor("shops/murugan_dairy/sales", isTs).next(snap([]));
    expect(onChange.mock.calls.length).toBe(calls + 1);
    stop();
  });
});
