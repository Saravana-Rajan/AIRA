// Ops the owner made while the phone was offline, kept until aira-live is back.
// Rule: never drop an op and never send one twice. Each op is keyed by its idemKey.
import type { ClientMsg } from "@aira/contracts";

export type QueuedOp = { idemKey: string; msg: ClientMsg; at: number };

export interface QueueStore {
  all(): Promise<QueuedOp[]>;
  put(op: QueuedOp): Promise<void>;
  del(idemKey: string): Promise<void>;
}

/** Remembered sent keys per queue, so a late duplicate add is not queued again. */
const SENT_MEMORY = 500;

/**
 * Every add and replay runs one at a time on a single chain. Two replays started together
 * (by `ready` and by the browser's `online` event) therefore cannot send the same op twice,
 * and an add that races a replay is either sent by it or left queued for the next one.
 */
export class OfflineQueue {
  private chain: Promise<unknown> = Promise.resolve();
  private sent = new Set<string>();

  constructor(private store: QueueStore, private now: () => number = () => Date.now()) {}

  private run<T>(fn: () => Promise<T>): Promise<T> {
    const next = this.chain.then(fn, fn);
    this.chain = next.catch(() => undefined);
    return next;
  }

  /** Queues `msg` unless an op with this idemKey is already queued or was sent by this queue. */
  add(msg: ClientMsg, idemKey: string): Promise<void> {
    return this.run(async () => {
      if (this.sent.has(idemKey)) return;
      const existing = await this.store.all();
      if (existing.some((o) => o.idemKey === idemKey)) return;
      // Strictly increasing, so order holds when the clock stalls or jumps back, and after a reload.
      const last = existing.reduce((m, o) => Math.max(m, o.at), -Infinity);
      const at = Math.max(this.now(), last + 1);
      await this.store.put({ idemKey, msg, at });
    });
  }

  /**
   * Sends queued ops oldest first. Stops at the first op that cannot be sent (send returns
   * false or throws), so ordering is kept. An op leaves the store only after it was sent.
   * Returns the number of ops sent.
   */
  replay(send: (m: ClientMsg) => boolean): Promise<number> {
    return this.run(async () => {
      const ops = [...(await this.store.all())].sort((a, b) => a.at - b.at);
      let n = 0;
      for (const op of ops) {
        let ok = false;
        try {
          ok = send(op.msg) === true;
        } catch {
          ok = false;
        }
        if (!ok) break;
        this.remember(op.idemKey);
        await this.store.del(op.idemKey);
        n++;
      }
      return n;
    });
  }

  /** Number of ops waiting. */
  pending(): Promise<number> {
    return this.run(async () => (await this.store.all()).length);
  }

  /** True while the op with this idemKey is still waiting to be sent. */
  has(idemKey: string): Promise<boolean> {
    return this.run(async () => (await this.store.all()).some((o) => o.idemKey === idemKey));
  }

  private remember(key: string): void {
    this.sent.add(key);
    if (this.sent.size > SENT_MEMORY) this.sent.delete(this.sent.values().next().value as string);
  }
}

/** Store kept in memory only (tests, and browsers without IndexedDB). */
export function memStore(): QueueStore {
  const m = new Map<string, QueuedOp>();
  return {
    async all() { return [...m.values()].map((o) => ({ ...o })); },
    async put(op) { m.set(op.idemKey, { ...op }); },
    async del(k) { m.delete(k); },
  };
}

/** Its own database, so no other module's IndexedDB schema can collide with it. */
export const OFFLINE_DB = "aira-offline";
export const OFFLINE_STORE = "pending";

/**
 * IndexedDB-backed store: ops survive a reload or a crash. The connection is opened once,
 * on first use. Rejects if IndexedDB is missing or refuses to open.
 */
export function idbStore(
  dbName = OFFLINE_DB,
  storeName = OFFLINE_STORE,
  idb: IDBFactory | undefined = globalThis.indexedDB,
): QueueStore {
  let db: Promise<IDBDatabase> | null = null;
  const open = (): Promise<IDBDatabase> => {
    if (db) return db;
    db = new Promise<IDBDatabase>((res, rej) => {
      if (!idb) return rej(new Error("IndexedDB unavailable"));
      const r = idb.open(dbName, 1);
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains(storeName)) r.result.createObjectStore(storeName, { keyPath: "idemKey" });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error ?? new Error("IndexedDB open failed"));
    });
    db.catch(() => { db = null; }); // let a later call try again
    return db;
  };
  const tx = async <T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const d = await open();
    return new Promise<T>((res, rej) => {
      const req = fn(d.transaction(storeName, mode).objectStore(storeName));
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  };
  return {
    all: () => tx<QueuedOp[]>("readonly", (s) => s.getAll() as IDBRequest<QueuedOp[]>),
    put: async (op) => { await tx("readwrite", (s) => s.put(op)); },
    del: async (k) => { await tx("readwrite", (s) => s.delete(k)); },
  };
}

/**
 * IndexedDB when it works, otherwise memory. The first call decides; if IndexedDB fails to
 * open, ops live in memory for this page (they still replay on reconnect, but not after a reload).
 */
export function defaultStore(idb: IDBFactory | undefined = globalThis.indexedDB): QueueStore {
  const mem = memStore();
  if (!idb) return mem;
  const disk = idbStore(OFFLINE_DB, OFFLINE_STORE, idb);
  let chosen: Promise<QueueStore> | null = null;
  const pick = (): Promise<QueueStore> =>
    (chosen ??= disk.all().then(() => disk, () => mem));
  return {
    all: async () => (await pick()).all(),
    put: async (op) => (await pick()).put(op),
    del: async (k) => (await pick()).del(k),
  };
}
