// The /bench page and the startup hook, against the real `app` singleton and AiraSocket.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { app, initApp } from "../src/core/app";
import { AiraSocket } from "../src/net/socket";
import type { Settings } from "../src/core/types";
import { installBench, installBenchIfEnabled } from "../src/bench/install";
import { KEYS, loadSaved, loadTrials } from "../src/bench/attach";
import { BenchPage } from "../src/bench/BenchPage";

class FakeWS {
  static last: FakeWS | null = null;
  readyState = 0;
  sent: string[] = [];
  onopen?: () => void;
  onmessage?: (e: { data: string }) => void;
  onclose?: () => void;
  constructor(public url: string) { FakeWS.last = this; }
  send(s: string) { this.sent.push(s); }
  close() { this.readyState = 3; this.onclose?.(); }
  open() { this.readyState = 1; this.onopen?.(); }
  push(m: unknown) { this.onmessage?.({ data: JSON.stringify(m) }); }
}

const SETTINGS: Settings = {
  lang: "en", verbosity: "short", screenReaderMode: false, speechRate: 1, shopId: "murugan_dairy", onboarded: true,
};

function wireRealApp(): FakeWS {
  const socket = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as unknown as typeof WebSocket });
  initApp({ socket, settings: SETTINGS, speakImpl: () => {}, earconImpl: () => {}, hapticImpl: () => {} });
  socket.connect({ t: "hello", uid: "owner", shopId: "murugan_dairy", lang: "en", verbosity: "short", appVersion: "0.1.0" });
  const ws = FakeWS.last!;
  ws.open();
  return ws;
}

/** jsdom's Blob has no .text(); FileReader works. */
function blobText(b: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsText(b);
  });
}

beforeEach(() => localStorage.clear());
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("installBench (real app singleton)", () => {
  it("is a safe no-op when called before the app is wired", async () => {
    vi.resetModules();
    localStorage.setItem(KEYS.enabled, "1");
    const fresh = await import("../src/bench/install");
    let off: () => void = () => {};
    expect(() => { off = fresh.installBenchIfEnabled(); }).not.toThrow();
    expect(() => off()).not.toThrow();
  });

  it("records nothing unless bench mode was switched on at /bench", () => {
    const ws = wireRealApp();
    const off = installBenchIfEnabled();
    ws.push({ t: "transcript", role: "user", text: "stock?", final: true });
    ws.push({ t: "audio", pcm24: "AAAA" });
    expect(loadSaved()).toEqual([]);
    off();
  });

  it("captures app.send traffic and incoming messages, then restores the socket", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    vi.setSystemTime(10_000);
    localStorage.setItem(KEYS.enabled, "1");
    const ws = wireRealApp();
    const socket = app.socket;
    const off = installBenchIfEnabled();

    ws.push({ t: "target", frameId: "f1", label: "curd", point: [500, 500], confidence: 0.8 });
    vi.setSystemTime(13_500);
    expect(app.send({ t: "event", name: "fingertip_in_target", data: {} })).toBe(true);
    expect(JSON.parse(ws.sent[ws.sent.length - 1])).toEqual({ t: "event", name: "fingertip_in_target", data: {} });
    vi.setSystemTime(14_000);
    ws.push({ t: "cue", cue: "match" });

    expect(loadSaved()).toEqual([{ metric: "guidance_time_to_touch_ms", value: 3500, ts: 13_500 }]);
    expect(loadTrials()).toEqual([
      { trial: 1, product: "curd", startedAtMs: 10_000, touchedAtMs: 13_500, correctItem: true },
    ]);

    off();
    expect(Object.prototype.hasOwnProperty.call(socket, "send")).toBe(false);
    expect(socket.send).toBe(AiraSocket.prototype.send);
  });

  it("installing twice never double-counts", () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    const ws = wireRealApp();
    const off1 = installBench();
    const off2 = installBench();
    vi.setSystemTime(1000);
    ws.push({ t: "transcript", role: "user", text: "hi", final: true });
    vi.setSystemTime(1400);
    ws.push({ t: "audio", pcm24: "AAAA" });
    expect(loadSaved()).toEqual([{ metric: "first_audio_ms", value: 400, ts: 1400 }]);
    off2();
    off1();
  });
});

describe("BenchPage", () => {
  let root: HTMLElement;
  beforeEach(() => {
    root = document.createElement("div");
    document.body.appendChild(root);
  });
  afterEach(() => {
    render(null, root);
    root.remove();
  });

  function seed() {
    const samples = [
      ...[600, 700, 800].map((v, i) => ({ metric: "first_audio_ms", value: v, ts: Date.UTC(2026, 9, 15, 10, 0, i) })),
      { metric: "sale_e2e_ms", value: 21_000, ts: Date.UTC(2026, 9, 15, 10, 1, 0) },
    ];
    localStorage.setItem(KEYS.samples, JSON.stringify(samples));
    localStorage.setItem(KEYS.trials, JSON.stringify([
      { trial: 1, product: "sakthi_curd_1l", startedAtMs: 0, touchedAtMs: 4200, correctItem: true },
      { trial: 2, product: "sakthi_curd_1l", startedAtMs: 0, touchedAtMs: null, correctItem: false },
    ]));
    localStorage.setItem(KEYS.device, "Redmi Note 13");
  }

  it("shows n, p50 and p95 per metric, and the trial tally, from saved results", () => {
    seed();
    act(() => render(<BenchPage />, root));
    const row = (id: string) => [...root.querySelectorAll("tbody tr")].find((r) => r.textContent!.includes(id))!;
    const cells = (id: string) => [...row(id).querySelectorAll("td")].map((c) => c.textContent);
    expect(cells("first_audio_ms")).toEqual(["3", "700 ms", "800 ms"]);
    expect(cells("sale_e2e_ms")).toEqual(["1", "21.0 s", "21.0 s"]);
    expect(cells("count_update_ms")).toEqual(["0", "–", "–"]);
    expect(root.textContent).toContain("1 of 2 touched the right item");
    expect((root.querySelector("input") as HTMLInputElement).value).toBe("Redmi Note 13");
  });

  it("Start recording switches bench mode on for the app at /", () => {
    act(() => render(<BenchPage />, root));
    const btn = [...root.querySelectorAll("button")].find((b) => b.textContent === "Start recording")!;
    act(() => btn.click());
    expect(localStorage.getItem(KEYS.enabled)).toBe("1");
    expect(root.querySelector("[role=status]")!.textContent).toBe("Recording on this phone");
    const stop = [...root.querySelectorAll("button")].find((b) => b.textContent === "Stop recording")!;
    act(() => stop.click());
    expect(localStorage.getItem(KEYS.enabled)).toBeNull();
  });

  it("exports NDJSON rows in the latency_metrics shape and trials.json in the scorer shape", async () => {
    seed();
    const blobs: Blob[] = [];
    const names: string[] = [];
    Object.assign(URL, {
      createObjectURL: vi.fn((b: Blob) => { blobs.push(b); return "blob:x"; }),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    act(() => render(<BenchPage />, root));
    const button = (label: string) => [...root.querySelectorAll("button")].find((b) => b.textContent!.startsWith(label))!;

    act(() => button("Export latency").click());
    const rows = (await blobText(blobs[0])).trim().split("\n").map((l) => JSON.parse(l));
    expect(rows).toHaveLength(4);
    expect(rows[0]).toEqual({
      metric: "first_audio_ms", value: 600, unit: "ms", device: "Redmi Note 13", build: "0.1.0", ts: "2026-10-15T10:00:00.000Z",
    });
    expect(names[0]).toMatch(/^aira-bench-Redmi-Note-13-\d+\.ndjson$/);

    act(() => button("Export trials.json").click());
    expect(JSON.parse(await blobText(blobs[1])).trials).toHaveLength(2);
    expect(names[1]).toBe("trials.json");
  });

  it("uses only design-token colours (light theme)", async () => {
    const css = (await import("../src/bench/bench.css?raw")).default as string;
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/prefers-color-scheme/);
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});
