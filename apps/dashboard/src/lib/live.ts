// Realtime Firestore listeners for one shop (database "aira", read-only).
// Every query filters or orders on a single field, so the automatic single-field indexes serve them
// all and no composite index is needed. The exact "today in IST" cut happens in the selectors.
import {
  collection, limit, onSnapshot, orderBy, query, Timestamp, where, type Query,
} from "firebase/firestore";
import type { AuditLog, Delivery, InventoryDoc, Order, Payment, Product, Sale, Supplier } from "@aira/contracts";
import { db } from "./firebase";
import { normalize, type Normalized } from "./normalize";
import { startOfDayIST } from "./format";

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

/** connecting: first load · live: every listener is answered by the server · offline: a listener failed or only the cache answers. */
export type LiveStatus = "connecting" | "live" | "offline";

const KEYS: Array<keyof ShopData> = [
  "products", "inventory", "sales", "payments", "orders", "deliveries", "suppliers", "auditLogs",
];
const DAY_MS = 86_400_000;

interface Source { name: string; key: keyof ShopData; q: Query }

/**
 * Time fields hold Firestore Timestamps (server timestamps) or ISO strings (now_iso(), seeds), and Firestore
 * compares values only within one type. So sales and payments get one query per type. The string bound
 * starts a day early to cover any UTC offset written in the string.
 */
function shopSources(shopId: string, nowMs: number): Source[] {
  const col = (name: string) => collection(db, "shops", shopId, name);
  const start = startOfDayIST(nowMs);
  const startTs = Timestamp.fromMillis(start);
  const startIso = new Date(start - DAY_MS).toISOString();
  const all = (key: keyof ShopData): Source => ({ name: key, key, q: query(col(key)) });
  const since = (key: "sales" | "payments", field: string): Source[] => [
    { name: `${key}@ts`, key, q: query(col(key), where(field, ">=", startTs)) },
    { name: `${key}@iso`, key, q: query(col(key), where(field, ">=", startIso)) },
  ];
  return [
    all("products"),
    all("inventory"),
    all("suppliers"),
    ...since("sales", "completedAt"),
    ...since("payments", "createdAt"),
    { name: "orders", key: "orders", q: query(col("orders"), orderBy("updatedAt", "desc"), limit(20)) },
    { name: "deliveries", key: "deliveries", q: query(col("deliveries"), limit(50)) },
    { name: "auditLogs", key: "auditLogs", q: query(col("auditLogs"), orderBy("at", "desc"), limit(100)) },
  ];
}

type Docs = Normalized<Record<string, unknown>>[];

/**
 * Listens to everything the dashboard shows for one shop. onChange fires once every listener has
 * answered (so a half-loaded page never shows wrong "Low stock" badges), then on every change.
 * Just after each IST midnight it re-subscribes so "Today" starts fresh in a tab left open.
 * Returns a function that stops every listener.
 */
export function listenShop(
  shopId: string,
  onChange: (d: ShopData) => void,
  nowMs: () => number = Date.now,
  onStatus: (s: LiveStatus) => void = () => {},
): () => void {
  const parts = new Map<string, Docs>();
  const fromCache = new Map<string, boolean>();
  const failed = new Set<string>();
  let sources: Source[] = [];
  let unsubs: Array<() => void> = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ready = false;
  let stopped = false;
  let lastStatus: LiveStatus | null = null;

  const setStatus = (s: LiveStatus) => {
    if (s !== lastStatus) {
      lastStatus = s;
      onStatus(s);
    }
  };

  const build = (): ShopData => {
    const out = Object.fromEntries(KEYS.map((k) => [k, new Map<string, unknown>()])) as Record<keyof ShopData, Map<string, unknown>>;
    for (const s of sources) for (const d of parts.get(s.name) ?? []) out[s.key].set(d.id, d);
    return Object.fromEntries(KEYS.map((k) => [k, [...out[k].values()]])) as unknown as ShopData;
  };

  const update = () => {
    if (stopped) return;
    if (!ready) ready = sources.every((s) => parts.has(s.name));
    const cached = sources.some((s) => fromCache.get(s.name));
    setStatus(failed.size > 0 || (ready && cached) ? "offline" : ready ? "live" : "connecting");
    if (ready) onChange(build());
  };

  const unsubscribeAll = () => {
    unsubs.forEach((u) => u());
    unsubs = [];
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };

  const subscribe = () => {
    sources = shopSources(shopId, nowMs());
    failed.clear();
    unsubs = sources.map((s) =>
      onSnapshot(
        s.q,
        { includeMetadataChanges: true },
        (snap) => {
          parts.set(s.name, snap.docs.map((d) => normalize<Record<string, unknown>>(d.id, d.data())));
          fromCache.set(s.name, snap.metadata.fromCache);
          update();
        },
        () => {
          // A failed listener is closed by the SDK. Keep what it last showed (or nothing) and say so.
          failed.add(s.name);
          if (!parts.has(s.name)) parts.set(s.name, []);
          update();
        },
      ),
    );
    const now = nowMs();
    timer = setTimeout(() => {
      unsubscribeAll();
      if (!stopped) subscribe();
    }, startOfDayIST(now) + DAY_MS + 1000 - now);
  };

  setStatus("connecting");
  subscribe();
  return () => {
    stopped = true;
    unsubscribeAll();
  };
}
