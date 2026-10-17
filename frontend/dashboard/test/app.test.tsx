import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import { cleanup, findAllByText, flush, mount, text } from "./helpers/render";
import { App, type ListenFn } from "../src/components/App";
import type { LiveStatus, ShopData } from "../src/lib/live";
import type { Forecast, ForecastItem } from "../src/lib/forecast";
import type { MonitorDeps } from "../src/lib/useMonitor";
import type { SocketLike } from "../src/lib/monitor";
import type { ViewerAuth, ViewerUser } from "../src/lib/viewerAuth";

/** The Live page's deps for the App tests: a socket that never opens, and a sign-in that starts signed out. */
function liveDeps(user: ViewerUser | null = null) {
  const sockets: Array<SocketLike & { sent: string[] }> = [];
  const auth: ViewerAuth & { user: ViewerUser | null; signIn: ReturnType<typeof vi.fn> } = {
    user,
    subscribe: (cb) => { cb(auth.user); return () => {}; },
    signIn: vi.fn(async () => undefined),
    signOut: async () => undefined,
    idToken: async () => "tok",
  };
  // Hoisted: `deps` is in the hook's effect dependencies and must stay the same object across renders.
  const deps: MonitorDeps = {
    connect: () => {
      const s = { onopen: null, onmessage: null, onclose: null, onerror: null, sent: [] as string[], send(d: string) { s.sent.push(d); }, close() {} };
      sockets.push(s);
      return s;
    },
    setTimer: () => 0, clearTimer: () => {}, auth,
  };
  return { deps, sockets, auth };
}

const NOW = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST

const DATA: ShopData = {
  products: [
    { id: "sakthi_curd_1l", sku: "sakthi_curd_1l", name: "Sakthi Curd 1 L", unit: "L", reorderThreshold: 5 },
    { id: "arun_icecream_box", sku: "arun_icecream_box", name: "Arun Ice Cream Family Box", unit: "box", reorderThreshold: 3 },
  ] as never[],
  inventory: [
    { id: "sakthi_curd_1l", sku: "sakthi_curd_1l", onHand: 28, batches: [{ batchId: "b1", qty: 28, expiryDate: "2026-10-11" }], updatedAt: NOW },
    { id: "arun_icecream_box", sku: "arun_icecream_box", onHand: 2, batches: [], updatedAt: NOW },
  ] as never[],
  sales: [
    { id: "s1", items: [{ sku: "arun_icecream_box", qty: 1, unitPrice: 180, lineTotal: 180 }], total: 180, state: "SALE_COMPLETED", paymentId: "p1", createdAt: NOW - 60_000, completedAt: NOW },
  ] as never[],
  payments: [
    { id: "p1", direction: "in", method: "upi", amount: 180, state: "CONFIRMED", evidence: null, refType: "sale", refId: "s1", createdAt: "2026-10-10T06:29:00+00:00" },
  ] as never[],
  orders: [
    { id: "o1", supplierId: "sakthi_vendor", items: [{ sku: "sakthi_curd_1l", qty: 30 }], state: "DISCREPANCY", vendorReply: "OK, delivery at 6 pm", idemKey: "k", createdAt: NOW, updatedAt: NOW },
  ] as never[],
  deliveries: [
    // the demo delivery as reconcile.py writes it: 30 ordered, 28 counted, the bill says 30
    {
      id: "d1", orderId: "o1", items: [{ sku: "sakthi_curd_1l", expectedQty: 30, countedQty: 28 }], invoice: null, state: "DISCREPANCY",
      discrepancies: [
        { kind: "SHORT_DELIVERY", sku: "sakthi_curd_1l", expected: 30, actual: 28 },
        { kind: "INVOICE_QTY_MISMATCH", sku: "sakthi_curd_1l", expected: 28, actual: 30 },
      ],
    },
  ] as never[],
  suppliers: [{ id: "sakthi_vendor", name: "Sakthi curd vendor", phone: "", channel: "whatsapp", products: [] }] as never[],
  auditLogs: [{ id: "a1", actor: "aira", action: "sale_completed", refType: "sale", refId: "s1", outcome: "ok", at: NOW }] as never[],
};

function fakeLive() {
  const h: { onChange?: (d: ShopData) => void; onStatus?: (s: LiveStatus) => void; stop: ReturnType<typeof vi.fn>; shopId?: string } = { stop: vi.fn() };
  const listen: ListenFn = (shopId, onChange, onStatus) => {
    h.shopId = shopId;
    h.onChange = onChange;
    h.onStatus = onStatus;
    onStatus("connecting");
    return h.stop;
  };
  const push = (d: ShopData = DATA) =>
    act(() => {
      h.onStatus!("live");
      h.onChange!(d);
    });
  return { h, listen, push };
}

const h2s = (el: HTMLElement) => [...el.querySelectorAll("h2")].map((x) => text(x));

function go(hash: string) {
  act(() => {
    location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

beforeEach(() => {
  history.replaceState(null, "", "/dashboard/");
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs(); // App reads import.meta.env: the sign-in test below turns VITE_MONITOR_AUTH on
});

describe("App", () => {
  it("shows a loading state until the shop's records arrive, then the overview of every section", async () => {
    const { listen, push, h } = fakeLive();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    expect(findAllByText(el, "[role=status]", "Loading the shop's records…")).toHaveLength(1);
    expect(findAllByText(el, "[role=status]", "Connecting…")).toHaveLength(1);
    expect(h.shopId).toBe("sunrise_dairy");

    push();
    await flush();
    expect(h2s(el)).toEqual(["Today", "Stock", "Orders & deliveries"]);
    expect(findAllByText(el, "[role=status]", "Live")).toHaveLength(1);
    expect(text(el)).toContain("Arun Ice Cream Family Box");
    expect(findAllByText(el, "li", "Sakthi Curd 1 L: counted 28 L of 30 L ordered")).toHaveLength(1);
    expect(findAllByText(el, "li", "Sakthi Curd 1 L: bill says 30, counted 28")).toHaveLength(1);
    expect(text(el)).not.toMatch(/expected 28, got 30|undefined/);
    expect(findAllByText(el, ".order__items", "Sakthi Curd 1 L: 30 L")).toHaveLength(1);
    expect(findAllByText(el, "dd", "₹180").length).toBeGreaterThan(0);
    expect(text(el)).not.toMatch(/Forecast|Audit trail|Reorders suggested/);
    expect(text(el)).not.toMatch(/Demo shop/);
  });

  it("shows one section per hash route and moves focus to it", async () => {
    const { listen, push } = fakeLive();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    push();
    go("#/orders");
    expect(h2s(el)).toEqual(["Orders & deliveries"]);
    expect(document.activeElement).toBe(el.querySelector("main"));
    go("#/stock");
    expect(h2s(el)).toEqual(["Stock"]);
    const curd = [...el.querySelectorAll("tbody tr")].find((r) => text(r).includes("Sakthi Curd 1 L"))!;
    expect(text(curd)).toContain("28 L");
    expect(text(curd)).toContain("Expires tomorrow");
    go("#main"); // the skip link target is not a route
    expect(h2s(el)).toEqual(["Stock"]);
    go("#/");
    expect(h2s(el)).toHaveLength(3);
  });

  it("only ever reads the demo shop, whatever ?shop= says", () => {
    history.replaceState(null, "", "/dashboard/?shop=someone_else");
    const { listen, h } = fakeLive();
    mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    expect(h.shopId).toBe("sunrise_dairy");
  });

  it("updates in place when Firestore changes", () => {
    const { listen, push } = fakeLive();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    push();
    go("#/stock");
    push({ ...DATA, inventory: [{ ...(DATA.inventory[0] as object), onHand: 26 }, DATA.inventory[1]] as never[] });
    const curd = [...el.querySelectorAll("tbody tr")].find((r) => text(r).includes("Sakthi Curd 1 L"))!;
    expect(text(curd)).toContain("26 L");
  });

  it("explains what to do when the records cannot be loaded", () => {
    const { listen, h } = fakeLive();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    act(() => h.onStatus!("offline"));
    expect(findAllByText(el, "[role=alert]", /Can't load the shop's records/)).toHaveLength(1);
    expect(findAllByText(el, "[role=status]", "Offline")).toHaveLength(1);
  });

  it("has no Forecast or Audit trail pages any more: their old links open the overview", async () => {
    const { listen, push } = fakeLive();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    push();
    await flush();
    for (const gone of ["#/forecast", "#/audit"]) {
      go(gone);
      expect(h2s(el)).toEqual(["Today", "Stock", "Orders & deliveries"]);
    }
    expect([...el.querySelectorAll("nav a")].map((a) => text(a))).not.toContain("Forecast");
  });

  it("by default (no VITE_MONITOR_AUTH) the Live page opens for anyone: no sign-in, no key, a socket at once with nothing sent, and the empty people panel", () => {
    vi.stubEnv("VITE_AIRA_LIVE_WS", "wss://live.example.test/ws"); // CI has no .env.local: the URL must not come from it
    sessionStorage.clear();
    history.replaceState(null, "", "/dashboard/#/live");
    const { listen } = fakeLive();
    const live = liveDeps(); // the fake sign-in is injected but does not count: the flag is off
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} monitorDeps={live.deps} />);
    expect(el.querySelector(".livepage")).not.toBeNull();
    expect(el.querySelector("input, form, #monitor-key, .keyprompt, .signin, .signin__btn, .lv-user")).toBeNull();
    expect(text(el)).not.toMatch(/Sign in|sign-in|Monitor key/);
    expect(live.sockets).toHaveLength(1);
    expect(live.sockets[0].sent).toEqual([]);
    expect(live.auth.signIn).not.toHaveBeenCalled();
    expect(text(el.querySelector(".people__title"))).toBe("People using AIRA now (0)");
    expect(text(el.querySelector(".people__empty"))).toBe("No one is using AIRA right now.");
    act(() => { live.sockets[0].onopen!({}); });
    expect(live.sockets[0].sent).toEqual([]); // live on open: nothing to hand over
    expect(findAllByText(el, ".lv-conn__text", "Connected")).toHaveLength(1);
    expect(sessionStorage.length).toBe(0);
  });

  it("opens the Live page behind Google sign-in (VITE_MONITOR_AUTH=1): a sign-in button, no key screen, nothing read from the fragment or storage, no socket until signed in", () => {
    vi.stubEnv("VITE_MONITOR_AUTH", "1");
    sessionStorage.clear();
    history.replaceState(null, "", "/dashboard/#/live");
    const { listen } = fakeLive();
    const live = liveDeps();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} monitorDeps={live.deps} />);
    expect(el.querySelector(".livepage")).not.toBeNull();
    expect(el.querySelector("input, form, #monitor-key, .keyprompt")).toBeNull();
    expect(text(el)).not.toContain("Monitor key");
    const btn = findAllByText(el, "button.signin__btn", "Sign in with Google to watch AIRA Live");
    expect(btn).toHaveLength(1);
    expect(text(el)).not.toContain("Waiting for a phone");
    expect(live.sockets).toHaveLength(0);
    act(() => { btn[0].click(); });
    expect(live.auth.signIn).toHaveBeenCalledTimes(1);
    expect(sessionStorage.length).toBe(0);
  });

  it("the other pages are untouched by the Live sign-in", async () => {
    const { listen, push } = fakeLive();
    const live = liveDeps();
    const el = mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} monitorDeps={live.deps} />);
    push();
    await flush();
    for (const hash of ["#/", "#/today", "#/stock", "#/prices", "#/orders", "#/audit", "#/forecast"]) {
      go(hash);
      expect(text(el)).not.toMatch(/Sign in|sign-in/);
      expect(el.querySelector(".signin, .livepage")).toBeNull();
    }
    go("#/live");
    expect(el.querySelector(".livepage")).not.toBeNull();
    go("#/stock");
    expect(h2s(el)).toEqual(["Stock"]);
  });

  it("stops listening when it unmounts", () => {
    const { listen, h } = fakeLive();
    mount(<App listen={listen} loadForecast={async () => null} now={() => NOW} />);
    cleanup();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });
});
