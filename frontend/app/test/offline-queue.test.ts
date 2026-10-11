import { describe, it, expect } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import { OfflineQueue, memStore, idbStore, defaultStore, type QueuedOp, type QueueStore } from "../src/net/offline-queue";

/** A small in-memory stand-in for the IndexedDB API: enough of open/transaction/objectStore. */
function fakeIndexedDB() {
  const dbs = new Map<string, Map<string, Map<string, unknown>>>();
  const later = (fn: () => void) => setTimeout(fn, 0);
  const request = <T>(run: () => T) => {
    const req: { result?: T; error?: unknown; onsuccess?: (() => void) | null; onerror?: (() => void) | null } = {};
    later(() => {
      try { req.result = run(); req.onsuccess?.(); } catch (e) { req.error = e; req.onerror?.(); }
    });
    return req;
  };
  const factory = {
    opens: 0,
    open(name: string) {
      factory.opens++;
      const req: Record<string, any> = {};
      later(() => {
        const isNew = !dbs.has(name);
        if (isNew) dbs.set(name, new Map());
        const stores = dbs.get(name)!;
        const db = {
          objectStoreNames: { contains: (s: string) => stores.has(s) },
          createObjectStore: (s: string) => { stores.set(s, new Map()); },
          transaction: (s: string) => ({
            objectStore: () => {
              const rows = stores.get(s)!;
              return {
                getAll: () => request(() => [...rows.keys()].sort().map((k) => structuredClone(rows.get(k)))),
                put: (v: { idemKey: string }) => request(() => { rows.set(v.idemKey, structuredClone(v)); }),
                delete: (k: string) => request(() => { rows.delete(k); }),
              };
            },
          }),
          close() {},
        };
        req.result = db;
        if (isNew) req.onupgradeneeded?.();
        req.onsuccess?.();
      });
      return req;
    },
  };
  return factory;
}

const text = (s: string): ClientMsg => ({ t: "text", text: s });

describe("OfflineQueue", () => {
  it("replays in insertion order and removes only ops that were sent", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add(text("sale 1"), "k1");
    await q.add(text("sale 2"), "k2");
    let calls = 0;
    const sent = await q.replay(() => ++calls === 1); // first succeeds, second fails
    expect(sent).toBe(1);
    expect((await store.all()).map((o) => o.idemKey)).toEqual(["k2"]);
  });

  it("never duplicates an op with the same idemKey", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add(text("a"), "same");
    await q.add(text("a"), "same");
    expect(await store.all()).toHaveLength(1);
  });

  it("survives a 'reload' (new queue over the same store)", async () => {
    const store = memStore();
    await new OfflineQueue(store).add({ t: "stop" }, "k9");
    const replayed: unknown[] = [];
    await new OfflineQueue(store).replay((m) => { replayed.push(m); return true; });
    expect(replayed).toEqual([{ t: "stop" }]);
    expect(await store.all()).toHaveLength(0);
  });

  it("keeps insertion order even when the clock stands still or goes backwards", async () => {
    const store = memStore();
    let t = 1000;
    const q = new OfflineQueue(store, () => t);
    await q.add(text("b"), "zz"); // key order differs from insertion order on purpose
    t = 500; // clock moved back
    await q.add(text("a"), "aa");
    await q.add(text("c"), "mm"); // same clock reading
    const order: string[] = [];
    await q.replay((m) => { order.push((m as { text: string }).text); return true; });
    expect(order).toEqual(["b", "a", "c"]);
  });

  it("continues the order after a reload, after ops already in the store", async () => {
    const store = memStore();
    await new OfflineQueue(store, () => 5000).add(text("first"), "k1");
    await new OfflineQueue(store, () => 10).add(text("second"), "k2"); // new tab, earlier clock
    const order: string[] = [];
    await new OfflineQueue(store).replay((m) => { order.push((m as { text: string }).text); return true; });
    expect(order).toEqual(["first", "second"]);
  });

  it("two overlapping replays send each op exactly once", async () => {
    const q = new OfflineQueue(memStore());
    await q.add(text("1"), "k1");
    await q.add(text("2"), "k2");
    const sent: unknown[] = [];
    const send = (m: ClientMsg) => { sent.push(m); return true; };
    const [a, b] = await Promise.all([q.replay(send), q.replay(send)]);
    expect(a + b).toBe(2);
    expect(sent).toEqual([text("1"), text("2")]);
  });

  it("an add racing a replay is neither lost nor sent twice", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add(text("1"), "k1");
    const sent: unknown[] = [];
    const send = (m: ClientMsg) => { sent.push(m); return true; };
    await Promise.all([q.replay(send), q.add(text("2"), "k2"), q.replay(send)]);
    expect(sent).toEqual([text("1"), text("2")]);
    expect(await store.all()).toHaveLength(0);
  });

  it("does not queue again an op this queue already sent", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add(text("x"), "k1");
    await q.replay(() => true);
    await q.add(text("x"), "k1");
    expect(await store.all()).toHaveLength(0);
  });

  it("keeps the op when send throws, and stops there", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add(text("1"), "k1");
    await q.add(text("2"), "k2");
    await expect(q.replay(() => { throw new Error("socket gone"); })).resolves.toBe(0);
    expect((await store.all()).map((o) => o.idemKey).sort()).toEqual(["k1", "k2"]);
  });

  it("reports how many ops are pending", async () => {
    const q = new OfflineQueue(memStore());
    expect(await q.pending()).toBe(0);
    await q.add(text("1"), "k1");
    await q.add(text("2"), "k2");
    expect(await q.pending()).toBe(2);
  });

  it("has() tells whether an op is still waiting", async () => {
    const q = new OfflineQueue(memStore());
    await q.add(text("1"), "k1");
    expect(await q.has("k1")).toBe(true);
    expect(await q.has("nope")).toBe(false);
    await q.replay(() => true);
    expect(await q.has("k1")).toBe(false);
  });

  it("a failing store write rejects add() but the queue keeps working", async () => {
    const inner = memStore();
    let fail = true;
    const store: QueueStore = { ...inner, put: async (op: QueuedOp) => { if (fail) throw new Error("quota"); return inner.put(op); } };
    const q = new OfflineQueue(store);
    await expect(q.add(text("1"), "k1")).rejects.toThrow("quota");
    fail = false;
    await q.add(text("2"), "k2");
    expect((await store.all()).map((o) => o.idemKey)).toEqual(["k2"]);
  });
});

describe("idbStore", () => {
  it("round-trips ops through IndexedDB and opens the database once", async () => {
    const idb = fakeIndexedDB();
    const store = idbStore("aira-offline-test", "pending", idb as unknown as IDBFactory);
    const q = new OfflineQueue(store);
    await q.add(text("b"), "zz");
    await q.add(text("a"), "aa");
    expect((await store.all()).map((o) => o.idemKey).sort()).toEqual(["aa", "zz"]);
    const order: string[] = [];
    await new OfflineQueue(idbStore("aira-offline-test", "pending", idb as unknown as IDBFactory))
      .replay((m) => { order.push((m as { text: string }).text); return true; });
    expect(order).toEqual(["b", "a"]); // insertion order, not key order
    expect(await store.all()).toHaveLength(0);
    expect(idb.opens).toBe(2); // one per store instance, not one per call
  });
});

describe("defaultStore", () => {
  it("falls back to memory when IndexedDB is missing", async () => {
    const store = defaultStore(undefined);
    await store.put({ idemKey: "k", msg: { t: "stop" }, at: 1 });
    expect(await store.all()).toHaveLength(1);
  });

  it("falls back to memory when IndexedDB fails to open", async () => {
    const broken = {
      open() {
        const req: Record<string, any> = {};
        setTimeout(() => { req.error = new Error("blocked"); req.onerror?.(); }, 0);
        return req;
      },
    };
    const store = defaultStore(broken as unknown as IDBFactory);
    await store.put({ idemKey: "k", msg: { t: "stop" }, at: 1 });
    expect((await store.all()).map((o) => o.idemKey)).toEqual(["k"]);
    await store.del("k");
    expect(await store.all()).toHaveLength(0);
  });

  it("uses IndexedDB when it works", async () => {
    const idb = fakeIndexedDB();
    const store = defaultStore(idb as unknown as IDBFactory);
    await store.put({ idemKey: "k", msg: { t: "stop" }, at: 1 });
    expect(idb.opens).toBe(1);
    expect(await store.all()).toHaveLength(1);
  });
});
