// Installable app: service-worker strategy, service-worker handlers and registration.
// sw-strategy.js and sw.js are plain scripts served from public/, so the tests load them
// the way the browser does (a script evaluated against `self`), not as ES modules.
import { describe, it, expect, beforeEach } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, URL } from "node:url"; // Node's URL, also under the jsdom environment
import vm from "node:vm";
import { registerServiceWorker } from "../src/pwa/registerSW";

const ORIGIN = "https://aira-shop.web.app";
const HERE = dirname(fileURLToPath(import.meta.url));
const appPath = (p: string) => resolve(HERE, "..", p);
const repoPath = (p: string) => resolve(HERE, "../../..", p);
const STRATEGY_SRC = readFileSync(appPath("public/sw-strategy.js"), "utf8");
const SW_SRC = readFileSync(appPath("public/sw.js"), "utf8");

type Strategy = {
  VERSION: string;
  PRECACHE: string[];
  strategyFor(url: string, mode: string, origin?: string): "cache-first" | "network-first" | "bypass";
  fallbackFor(url: string): string;
};

function loadStrategy(): Strategy {
  const ctx: any = { URL };
  ctx.self = ctx;
  vm.runInNewContext(STRATEGY_SRC, ctx);
  return ctx.swStrategy;
}

const S = loadStrategy();

describe("service worker strategy (Review Focus #3)", () => {
  it("never caches websockets, Firestore, Google APIs, Cloud Run, API, webhooks or Firebase reserved URLs", () => {
    expect(S.strategyFor("wss://aira-live-x.a.run.app/ws", "websocket")).toBe("bypass");
    expect(S.strategyFor("ws://localhost:8080/ws", "websocket")).toBe("bypass");
    expect(S.strategyFor("https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel", "cors")).toBe("bypass");
    expect(S.strategyFor("https://identitytoolkit.googleapis.com/v1/accounts", "cors")).toBe("bypass");
    expect(S.strategyFor("https://firebaseinstallations.googleapis.com/v1/projects/x", "cors")).toBe("bypass");
    expect(S.strategyFor("https://storage.googleapis.com/some-bucket/photo.jpg", "cors")).toBe("bypass");
    expect(S.strategyFor("https://fonts.googleapis.com/css2?family=Inter", "no-cors")).toBe("bypass");
    expect(S.strategyFor("https://aira-live-abc-el.a.run.app/jobs/expiry", "cors")).toBe("bypass");
    expect(S.strategyFor(`${ORIGIN}/api/x`, "cors")).toBe("bypass");
    expect(S.strategyFor(`${ORIGIN}/webhooks/whatsapp`, "cors")).toBe("bypass");
    expect(S.strategyFor(`${ORIGIN}/__/firebase/init.json`, "cors")).toBe("bypass");
    expect(S.strategyFor(`${ORIGIN}/.well-known/assetlinks.json`, "cors")).toBe("bypass");
  });

  it("bypasses non-http schemes and unparseable URLs", () => {
    expect(S.strategyFor("chrome-extension://abc/script.js", "no-cors")).toBe("bypass");
    expect(S.strategyFor("blob:https://aira-shop.web.app/1234", "cors")).toBe("bypass");
    expect(S.strategyFor("not a url", "cors")).toBe("bypass");
  });

  it("bypasses unknown cross-origin requests when the page origin is known", () => {
    expect(S.strategyFor("https://example.com/data", "cors", ORIGIN)).toBe("bypass");
    expect(S.strategyFor(`${ORIGIN}/version`, "cors", ORIGIN)).toBe("network-first");
  });

  it("caches MediaPipe models, wasm, fonts, demo media and static assets first", () => {
    expect(S.strategyFor("https://storage.googleapis.com/mediapipe-models/hand_landmarker/x/hand_landmarker.task", "cors")).toBe("cache-first");
    expect(S.strategyFor("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/wasm/vision_wasm_internal.wasm", "cors", ORIGIN)).toBe("cache-first");
    expect(S.strategyFor("https://fonts.gstatic.com/s/inter/v13/abc.woff2", "cors", ORIGIN)).toBe("cache-first");
    expect(S.strategyFor(`${ORIGIN}/demo/invoice_ok.jpg`, "no-cors")).toBe("cache-first");
    expect(S.strategyFor(`${ORIGIN}/assets/index-abc.js`, "cors")).toBe("cache-first");
    expect(S.strategyFor(`${ORIGIN}/icons/aira-logo-192.png`, "no-cors")).toBe("cache-first");
  });

  it("uses network-first for page navigations and the manifest", () => {
    expect(S.strategyFor(`${ORIGIN}/try`, "navigate")).toBe("network-first");
    expect(S.strategyFor(`${ORIGIN}/dashboard/`, "navigate", ORIGIN)).toBe("network-first");
    expect(S.strategyFor(`${ORIGIN}/manifest.webmanifest`, "cors")).toBe("network-first");
  });

  it("precaches the app shell, manifest and the logo icons", () => {
    expect(S.VERSION).toBe("aira-v1");
    expect(S.PRECACHE).toEqual(["/", "/index.html", "/manifest.webmanifest", "/icons/aira-logo-192.png", "/icons/aira-logo-512.png"]);
  });

  it("falls back to the right shell for offline navigations", () => {
    expect(S.fallbackFor(`${ORIGIN}/try?x=1`)).toBe("/");
    expect(S.fallbackFor(`${ORIGIN}/dashboard/stock`)).toBe("/dashboard/");
    expect(S.fallbackFor(`${ORIGIN}/dashboard`)).toBe("/dashboard/");
  });

  it("is also usable as a CommonJS module", () => {
    const mod: any = { exports: {} };
    vm.runInNewContext(STRATEGY_SRC, { URL, module: mod });
    expect(mod.exports.strategyFor(`${ORIGIN}/try`, "navigate")).toBe("network-first");
  });
});

// ---------- sw.js handlers, run against fake Cache Storage / fetch ----------

// Minimal header bag: the jsdom environment's Headers drops "range", so it can't be used here.
class FakeHeaders {
  private names: Set<string>;
  constructor(init: Record<string, string> | FakeHeaders = {}) {
    this.names = init instanceof FakeHeaders ? new Set(init.names) : new Set(Object.keys(init).map((k) => k.toLowerCase()));
  }
  has(name: string) { return this.names.has(name.toLowerCase()); }
}

class FakeRequest {
  url: string;
  method: string;
  mode: string;
  cache: string;
  headers: FakeHeaders;
  constructor(input: string | FakeRequest, init: any = {}) {
    const base: any = typeof input === "string" ? { url: input } : input;
    this.url = new URL(base.url, ORIGIN).href;
    this.method = init.method ?? base.method ?? "GET";
    this.mode = init.mode ?? base.mode ?? "cors";
    this.cache = init.cache ?? base.cache ?? "default";
    this.headers = new FakeHeaders(init.headers ?? base.headers);
  }
}

const keyOf = (r: string | { url: string }) => new URL(typeof r === "string" ? r : r.url, ORIGIN).href;
const tick = () => new Promise((r) => setTimeout(r, 0));

function makeSW() {
  const store = new Map<string, Map<string, Response>>();
  const listeners: Record<string, (e: any) => void> = {};
  const fetched: { url: string; cache: string }[] = [];
  let network: (req: FakeRequest) => Promise<Response> = async () => new Response("net", { status: 200 });
  let skipped = false;
  let claimed = false;

  const openSync = (name: string) => {
    if (!store.has(name)) store.set(name, new Map());
    const m = store.get(name)!;
    const cache = {
      add: async (r: string | FakeRequest) => {
        const res = await ctx.fetch(typeof r === "string" ? new FakeRequest(r) : r);
        if (res.status !== 200) throw new TypeError(`bad status ${res.status}`);
        m.set(keyOf(r), res);
      },
      addAll: async (rs: (string | FakeRequest)[]) => { for (const r of rs) await cache.add(r); },
      put: async (r: string | FakeRequest, res: Response) => { m.set(keyOf(r), res); },
      match: async (r: string | FakeRequest) => m.get(keyOf(r))?.clone(),
    };
    return cache;
  };

  const ctx: any = {
    URL, Response, Promise, TypeError, Error,
    Request: FakeRequest,
    location: new URL(`${ORIGIN}/sw.js`),
    importScripts: (p: string) => {
      if (p !== "/sw-strategy.js") throw new Error(`unexpected importScripts(${p})`);
      vm.runInContext(STRATEGY_SRC, ctx);
    },
    addEventListener: (type: string, fn: (e: any) => void) => { listeners[type] = fn; },
    skipWaiting: async () => { skipped = true; },
    clients: { claim: async () => { claimed = true; } },
    fetch: (req: FakeRequest) => { fetched.push({ url: req.url, cache: req.cache }); return network(req); },
    caches: {
      open: async (name: string) => openSync(name),
      keys: async () => [...store.keys()],
      delete: async (name: string) => store.delete(name),
      match: async (r: string | FakeRequest) => {
        for (const m of store.values()) {
          const hit = m.get(keyOf(r));
          if (hit) return hit.clone();
        }
        return undefined;
      },
    },
  };
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(SW_SRC, ctx);

  async function dispatchFetch(url: string, init: any = {}) {
    let responded: Promise<Response> | undefined;
    const waits: Promise<unknown>[] = [];
    listeners.fetch({
      request: new FakeRequest(url, init),
      respondWith: (p: Promise<Response>) => { responded = p; },
      waitUntil: (p: Promise<unknown>) => { waits.push(p); },
    });
    try {
      return responded ? await responded : undefined;
    } finally {
      await Promise.allSettled(waits);
      await tick(); // let background cache writes land
    }
  }

  async function lifecycle(type: "install" | "activate") {
    const waits: Promise<unknown>[] = [];
    listeners[type]({ waitUntil: (p: Promise<unknown>) => { waits.push(p); } });
    await Promise.all(waits);
  }

  return {
    store, fetched, dispatchFetch, lifecycle,
    setNetwork: (fn: typeof network) => { network = fn; },
    get skipped() { return skipped; },
    get claimed() { return claimed; },
  };
}

describe("sw.js handlers", () => {
  let sw: ReturnType<typeof makeSW>;
  beforeEach(() => { sw = makeSW(); });

  it("precaches the shell on install, bypassing the HTTP cache, and activates immediately", async () => {
    await sw.lifecycle("install");
    const cache = sw.store.get("aira-v1")!;
    expect([...cache.keys()]).toEqual(S.PRECACHE.map((p) => new URL(p, ORIGIN).href));
    expect(sw.fetched.every((f) => f.cache === "reload")).toBe(true);
    expect(sw.skipped).toBe(true);
  });

  it("still installs when one precache URL fails", async () => {
    sw.setNetwork(async (req) =>
      req.url.endsWith("aira-logo-512.png") ? new Response("missing", { status: 404 }) : new Response("ok"));
    await sw.lifecycle("install");
    expect(sw.store.get("aira-v1")!.size).toBe(S.PRECACHE.length - 1);
    expect(sw.skipped).toBe(true);
  });

  it("deletes old AIRA caches on activate, keeps other caches, and claims clients", async () => {
    sw.store.set("aira-v0", new Map());
    sw.store.set("other-lib-cache", new Map());
    sw.store.set("aira-v1", new Map());
    await sw.lifecycle("activate");
    expect([...sw.store.keys()].sort()).toEqual(["aira-v1", "other-lib-cache"]);
    expect(sw.claimed).toBe(true);
  });

  it("ignores non-GET, bypassed, range and only-if-cached requests", async () => {
    expect(await sw.dispatchFetch(`${ORIGIN}/assets/a.js`, { method: "POST" })).toBeUndefined();
    expect(await sw.dispatchFetch("https://firestore.googleapis.com/google.firestore.v1.Firestore/Write/channel")).toBeUndefined();
    expect(await sw.dispatchFetch(`${ORIGIN}/api/x`)).toBeUndefined();
    expect(await sw.dispatchFetch("https://example.com/feed.json")).toBeUndefined();
    expect(await sw.dispatchFetch(`${ORIGIN}/demo/shelf.mp4`, { headers: { range: "bytes=0-" } })).toBeUndefined();
    expect(await sw.dispatchFetch(`${ORIGIN}/assets/b.js`, { cache: "only-if-cached", mode: "no-cors" })).toBeUndefined();
    expect(sw.fetched).toEqual([]);
  });

  it("cache-first: stores a miss, then serves the hit without the network", async () => {
    const first = await sw.dispatchFetch(`${ORIGIN}/assets/index-abc.js`);
    expect(await first!.text()).toBe("net");
    expect(sw.fetched.map((f) => f.url)).toEqual([`${ORIGIN}/assets/index-abc.js`]);
    sw.setNetwork(async () => { throw new TypeError("offline"); });
    const second = await sw.dispatchFetch(`${ORIGIN}/assets/index-abc.js`);
    expect(await second!.text()).toBe("net");
    expect(sw.fetched.length).toBe(1);
  });

  it("does not store error, redirected or opaque responses", async () => {
    sw.setNetwork(async () => new Response("nope", { status: 500 }));
    await sw.dispatchFetch(`${ORIGIN}/assets/err.js`);
    sw.setNetwork(async () => {
      const r = new Response("moved", { status: 200 });
      Object.defineProperty(r, "redirected", { value: true });
      return r;
    });
    await sw.dispatchFetch(`${ORIGIN}/assets/redirected.js`);
    sw.setNetwork(async () => {
      const r = new Response(null, { status: 200 });
      Object.defineProperty(r, "status", { value: 0 });
      Object.defineProperty(r, "type", { value: "opaque" });
      return r;
    });
    await sw.dispatchFetch(`${ORIGIN}/assets/opaque.png`, { mode: "no-cors" });
    expect(sw.store.get("aira-v1")?.size ?? 0).toBe(0);
  });

  it("network-first: fresh page online, cached page offline, precached shell as last resort", async () => {
    sw.setNetwork(async (req) => new Response(req.url.endsWith("/try") ? "try-online" : "shell"));
    expect(await (await sw.dispatchFetch(`${ORIGIN}/try`, { mode: "navigate" }))!.text()).toBe("try-online");
    await sw.lifecycle("install");

    sw.setNetwork(async () => { throw new TypeError("offline"); });
    expect(await (await sw.dispatchFetch(`${ORIGIN}/try`, { mode: "navigate" }))!.text()).toBe("try-online");

    const fallback = await sw.dispatchFetch(`${ORIGIN}/bench?x=1`, { mode: "navigate" });
    expect(await fallback!.text()).toBe("shell");
    expect(sw.store.get("aira-v1")!.has(`${ORIGIN}/bench?x=1`)).toBe(false);
  });

  it("network-first: an offline miss with no fallback fails instead of serving the wrong shell", async () => {
    await sw.lifecycle("install"); // "/" is cached, but the dashboard must not get the phone app
    sw.setNetwork(async () => { throw new TypeError("offline"); });
    await expect(sw.dispatchFetch(`${ORIGIN}/dashboard/stock`, { mode: "navigate" })).rejects.toThrow("offline");
    await expect(sw.dispatchFetch(`${ORIGIN}/version`)).rejects.toThrow("offline");
  });
});

// ---------- registration ----------

describe("registerServiceWorker", () => {
  it("does nothing in development", async () => {
    const nav = { serviceWorker: { register: () => { throw new Error("no"); } } } as any;
    expect(await registerServiceWorker(nav, false)).toBeNull();
  });

  it("registers /sw.js at scope / in production, always re-checking imported scripts", async () => {
    const calls: any[] = [];
    const nav = { serviceWorker: { register: async (...a: any[]) => { calls.push(a); return { scope: "/" }; } } } as any;
    expect(await registerServiceWorker(nav, true)).toEqual({ scope: "/" });
    expect(calls).toEqual([["/sw.js", { scope: "/", updateViaCache: "none" }]]);
  });

  it("returns null when service workers are unsupported", async () => {
    expect(await registerServiceWorker({} as any, true)).toBeNull();
    expect(await registerServiceWorker(undefined, true)).toBeNull();
  });

  it("never throws when registration fails", async () => {
    const syncThrow = { serviceWorker: { register: () => { throw new Error("SecurityError"); } } } as any;
    const asyncReject = { serviceWorker: { register: async () => { throw new Error("network"); } } } as any;
    expect(await registerServiceWorker(syncThrow, true)).toBeNull();
    expect(await registerServiceWorker(asyncReject, true)).toBeNull();
  });
});

// ---------- manifest (a foundation file; checked once it is merged) ----------

const MANIFEST = appPath("public/manifest.webmanifest");

describe.skipIf(!existsSync(MANIFEST))("manifest (foundation file)", () => {
  const m = existsSync(MANIFEST) ? JSON.parse(readFileSync(MANIFEST, "utf8")) : {};
  it("is installable as AIRA", () => {
    expect(m.name).toBe("AIRA");
    expect(m.short_name).toBe("AIRA");
    expect(m.start_url).toBe("/");
    expect(m.display).toBe("standalone");
  });
  it("has a 'Talk to AIRA' shortcut to /?start=1 (Android app shortcuts, Assistant)", () => {
    expect(m.shortcuts).toEqual([
      expect.objectContaining({ name: "Talk to AIRA", url: "/?start=1" }),
    ]);
    for (const i of m.shortcuts[0].icons ?? []) expect(existsSync(appPath(`public${i.src}`))).toBe(true);
  });
  it("has 192 and 512 icons that the service worker precaches and the logo folder provides", () => {
    const sizes = m.icons.map((i: any) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    for (const i of m.icons) {
      expect(S.PRECACHE).toContain(i.src);
      const name = i.src.split("/").pop();
      expect(existsSync(appPath(`public${i.src}`)) || existsSync(repoPath(`shared/assets/logo/${name}`))).toBe(true);
    }
  });
});
