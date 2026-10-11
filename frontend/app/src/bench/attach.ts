// frontend/app/src/bench/attach.ts
// Wires the bench collector and guidance trial log to a socket, and keeps their results in
// browser storage so /bench can read them after the app ran at "/". No dependency on
// core/app here, so the wiring is testable with a fake socket.
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { BenchCollector, type Sample } from "./collector";
import { startBatteryLogger, type BatterySample } from "./battery";
import { GuidanceTrialLog, type GuidanceTrial } from "./trials";

export interface KV {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface BenchSocket {
  on(handler: (m: ServerMsg) => void): () => unknown;
  send(m: ClientMsg): boolean;
}

export interface BenchTarget { socket: BenchSocket }

export const KEYS = {
  samples: "aira.bench.samples",
  battery: "aira.bench.battery",
  trials: "aira.bench.trials",
  device: "aira.bench.device",
  product: "aira.bench.product",
  enabled: "aira.bench.on",
} as const;

const MAX_SAMPLES = 5000;
const MAX_BATTERY = 2000;

/** In-memory KV, for tests and for browsers where storage is blocked. */
export function memoryStore(): KV {
  const m = new Map<string, string>();
  return {
    getItem: (k) => (m.has(k) ? m.get(k)! : null),
    setItem: (k, v) => { m.set(k, v); },
    removeItem: (k) => { m.delete(k); },
  };
}

let fallback: KV | null = null;

/** localStorage when it works (private mode or blocked site data can throw), else memory. */
export function defaultStore(): KV {
  try {
    const ls = globalThis.localStorage;
    if (ls) {
      const probe = "aira.bench.probe";
      ls.setItem(probe, "1");
      ls.removeItem(probe);
      return ls;
    }
  } catch { /* fall through */ }
  return (fallback ??= memoryStore());
}

function readList<T>(store: KV, key: string): T[] {
  try {
    const v = JSON.parse(store.getItem(key) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

function appendList<T>(store: KV, key: string, items: T[], max: number): void {
  if (items.length === 0) return;
  try {
    store.setItem(key, JSON.stringify([...readList<T>(store, key), ...items].slice(-max)));
  } catch { /* storage full or blocked: drop rather than break the session */ }
}

export function loadSaved(store: KV = defaultStore()): Sample[] { return readList<Sample>(store, KEYS.samples); }
export function loadBattery(store: KV = defaultStore()): BatterySample[] { return readList<BatterySample>(store, KEYS.battery); }
export function loadTrials(store: KV = defaultStore()): GuidanceTrial[] { return readList<GuidanceTrial>(store, KEYS.trials); }

export function clearSaved(store: KV = defaultStore()): void {
  for (const k of [KEYS.samples, KEYS.battery, KEYS.trials]) {
    try { store.removeItem(k); } catch { /* ignore */ }
  }
}

export function getSetting(key: "device" | "product", store: KV = defaultStore()): string {
  try { return store.getItem(KEYS[key]) ?? ""; } catch { return ""; }
}

export function setSetting(key: "device" | "product", value: string, store: KV = defaultStore()): void {
  try { store.setItem(KEYS[key], value); } catch { /* ignore */ }
}

/** Bench recording is opt-in per device, switched on from the /bench page. */
export function benchEnabled(store: KV = defaultStore()): boolean {
  try { return store.getItem(KEYS.enabled) === "1"; } catch { return false; }
}

export function setBenchEnabled(on: boolean, store: KV = defaultStore()): void {
  try { on ? store.setItem(KEYS.enabled, "1") : store.removeItem(KEYS.enabled); } catch { /* ignore */ }
}

export interface AttachOptions {
  store?: KV;
  now?: () => number;
  /** Also log battery level (default true). */
  battery?: boolean;
  nav?: Navigator;
  batteryEveryMs?: number;
}

const attached = new WeakSet<object>();

/**
 * Observes all traffic on `target.socket` without changing it. Outgoing messages count only
 * when the socket actually sent them. Returns a detach function. A second attach on the same
 * socket is a no-op, so nothing is ever counted twice.
 */
export function attachBench(target: BenchTarget, opts: AttachOptions = {}): () => void {
  const socket = target.socket;
  if (attached.has(socket)) return () => {};
  attached.add(socket);

  const store = opts.store ?? defaultStore();
  const now = opts.now ?? Date.now;
  const collector = new BenchCollector();
  const saved = loadTrials(store);
  const trials = new GuidanceTrialLog(saved.reduce((n, t) => Math.max(n, t.trial || 0), 0));

  const flush = () => {
    if (collector.samples.length) appendList(store, KEYS.samples, collector.samples.splice(0), MAX_SAMPLES);
    if (trials.trials.length) appendList(store, KEYS.trials, trials.trials.splice(0), MAX_SAMPLES);
  };

  const off = socket.on((m) => {
    const t = now();
    if (m.t === "target") trials.setProduct(getSetting("product", store));
    collector.onIncoming(m, t);
    trials.onIncoming(m, t);
    flush();
  });

  const hadOwnSend = Object.prototype.hasOwnProperty.call(socket, "send");
  const originalSend = socket.send;
  socket.send = function benchSend(m: ClientMsg): boolean {
    const ok = originalSend.call(socket, m);
    if (ok || m.t === "stop") {
      const t = now();
      collector.onOutgoing(m, t);
      trials.onOutgoing(m, t);
      flush();
    }
    return ok;
  };

  // Close guidance runs that time out while no messages arrive.
  const ticker = setInterval(() => { trials.tick(now()); flush(); }, 1000);

  let detached = false;
  let stopBattery: () => void = () => {};
  if (opts.battery !== false) {
    const nav = opts.nav ?? (globalThis.navigator as Navigator | undefined);
    if (nav) {
      startBatteryLogger(nav, opts.batteryEveryMs ?? 60_000, (s) => appendList(store, KEYS.battery, [s], MAX_BATTERY))
        .then((stop) => { if (detached) stop(); else stopBattery = stop; })
        .catch(() => { /* never break the session over battery logging */ });
    }
  }

  return () => {
    if (detached) return;
    detached = true;
    off();
    clearInterval(ticker);
    stopBattery();
    if (hadOwnSend) socket.send = originalSend;
    else delete (socket as Partial<BenchSocket>).send;
    attached.delete(socket);
    flush();
  };
}
