import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { wireApp, type WireEnv, type WiredApp, type WireOpts } from "../src/core/wire";
import { memStore } from "../src/net/offline-queue";
import { getFrameSource, setFrameSource } from "../src/camera/source";
import { TryAira } from "../src/demo/TryAira";
import { SCENARIOS } from "../src/demo/scenarios";

class FakeSocket {
  open = true;
  closed = false;
  connects: Array<Extract<ClientMsg, { t: "hello" }>> = [];
  sent: ClientMsg[] = [];
  private hs = new Set<(m: ServerMsg) => void>();
  constructor(public url: string) {}
  connect(h: Extract<ClientMsg, { t: "hello" }>) { this.connects.push(h); this.open = true; this.closed = false; }
  send(m: ClientMsg) { if (!this.open) return false; this.sent.push(m); return true; }
  on(h: (m: ServerMsg) => void) { this.hs.add(h); return () => { this.hs.delete(h); }; }
  close() { this.closed = true; this.open = false; }
  emit(m: ServerMsg) { this.hs.forEach((h) => h(m)); }
}

function fakeEnv() {
  const sockets: FakeSocket[] = [];
  const env: Partial<WireEnv> = {
    createSocket: (url) => { const s = new FakeSocket(url); sockets.push(s); return s as never; },
    createAudioContext: () => ({ resume: vi.fn(() => Promise.resolve()), close: vi.fn(() => Promise.resolve()) }) as unknown as AudioContext,
    createPlayback: () => ({ enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), msSinceAudio: () => Infinity }),
    playEarcon: vi.fn(),
    vibrate: vi.fn(),
    speech: { speak: vi.fn(), cancel: vi.fn() },
    announce: vi.fn(),
    startMic: vi.fn(async () => ({ stop: vi.fn() })),
    queueStore: () => memStore(),
    storage: null,
    win: new EventTarget(),
    doc: Object.assign(new EventTarget(), { hidden: false }),
    requestWakeLock: vi.fn(),
    requestPermissions: vi.fn(),
  };
  return { env, sockets };
}

let root: HTMLDivElement;
beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});
afterEach(async () => {
  await act(() => { render(null, root); });
  root.remove();
  setFrameSource(null);
});

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const $ = <T extends Element = HTMLElement>(sel: string) => root.querySelector(sel) as T;
const $$ = (sel: string) => Array.from(root.querySelectorAll<HTMLElement>(sel));

async function mount(over: { injectAudio?: (...a: never[]) => Promise<number>; onWired?: (w: WiredApp) => void } = {}) {
  const fake = fakeEnv();
  const wires: WiredApp[] = [];
  const optsSeen: WireOpts[] = [];
  // Real wireApp; only the browser adapters are fake. The page's own env (no prompts, no saved data) wins.
  const wire = (o: WireOpts) => {
    optsSeen.push(o);
    const w = wireApp({ ...o, env: { ...fake.env, ...o.env } });
    wires.push(w);
    return w;
  };
  const injectAudio = vi.fn(over.injectAudio ?? (async () => 3));
  await act(() => {
    render(<TryAira wire={wire} wsUrl="ws://test/ws" audioDelayMs={0} injectAudio={injectAudio as never} onWired={over.onWired} />, root);
  });
  const socket = () => fake.sockets[0];
  const emit = async (m: ServerMsg) => { await act(() => { socket().emit(m); }); };
  const click = async (el: HTMLElement) => { await act(() => { el.click(); }); };
  const card = (id: string) => $$(".try-scene").find((b) => b.dataset.scene === id)!;
  return { fake, wires, optsSeen, injectAudio, socket, emit, click, card };
}

describe("Try AIRA landing page", () => {
  it("shows the hero, the dashboard link and one card per scene, grouped by the day", async () => {
    const t = await mount();
    expect($("h1").textContent).toBe("A blind shopkeeper runs his own shop. Alone.");
    expect($<HTMLImageElement>(".try-logo").getAttribute("src")).toBe("/icons/aira-logo-512.png");
    expect($<HTMLImageElement>(".try-logo").getAttribute("alt")).toBe("AIRA");
    expect($(".try-cta").textContent).toBe("Try AIRA");
    expect($<HTMLAnchorElement>(".try-link").getAttribute("href")).toBe("/dashboard/");
    expect($$(".try-scene").map((b) => b.dataset.scene)).toEqual(SCENARIOS.map((s) => s.id));
    expect($$(".try-part h3").map((h) => h.textContent)).toHaveLength(3);
    // Nothing is wired or connected until the judge acts (the tap unlocks audio).
    expect(t.wires).toHaveLength(0);
    expect($(".try-chip").textContent).toBe("Not started");
  });

  it("Try AIRA wires the real app once, without the owner's hello, prompts or saved data", async () => {
    const t = await mount();
    await t.click($(".try-cta"));
    await t.click($(".try-cta"));
    expect(t.wires).toHaveLength(1);
    expect(t.optsSeen[0]).toMatchObject({ connect: false, wsUrl: "ws://test/ws" });
    expect(t.fake.env.requestPermissions).not.toHaveBeenCalled();
    expect(t.socket().connects).toHaveLength(1);
    expect(t.socket().connects[0]).toMatchObject({ t: "hello", shopId: "sunrise_dairy", lang: "en" });
    expect("idToken" in t.socket().connects[0]).toBe(false);
    expect(t.socket().connects[0].uid).toMatch(/^judge-/);
    expect($(".try-chip").textContent).toBe("Connecting…");
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: true });
    expect($(".try-chip").textContent).toBe("Live (mock server)");
  });

  it("passes the wired app to onWired (the camera and request_frame wiring)", async () => {
    const onWired = vi.fn();
    const t = await mount({ onWired });
    await t.click(t.card("summary"));
    expect(onWired).toHaveBeenCalledTimes(1);
    expect(onWired.mock.calls[0][0]).toBe(t.wires[0]);
  });

  it("holds a scene's prompt until the session is ready, then sends it once", async () => {
    const t = await mount();
    await t.click(t.card("order"));
    expect(t.socket().sent).toEqual([]);
    expect(t.card("order").getAttribute("aria-pressed")).toBe("true");
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    expect(t.socket().sent).toEqual([{ t: "text", text: SCENARIOS[0].prompt }]);
    expect($(".try-chip").textContent).toBe("Live");
    await t.emit({ t: "ready", sessionId: "s", resume: "r2", mock: false });
    expect(t.socket().sent).toHaveLength(1);
  });

  it("shows AIRA's lines in the judge panel and answers a confirm through the app's confirm state", async () => {
    const t = await mount();
    await t.click(t.card("order"));
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await t.emit({ t: "transcript", role: "aira", text: "10 litres of Sakthi curd. Send?", final: true });
    await t.emit({ t: "confirm", id: "c1", kind: "order", prompt: "Send 10 L to Sakthi?" });
    expect($(".try-log").textContent).toContain("AIRA: 10 litres of Sakthi curd. Send?");
    expect($(".try-confirm").textContent).toContain("Send 10 L to Sakthi?");
    expect(t.wires[0].confirm.pending?.id).toBe("c1");
    await t.click($(".try-yes"));
    expect(t.socket().sent.at(-1)).toEqual({ t: "confirm", id: "c1", answer: "yes" });
    expect(t.wires[0].confirm.pending).toBeNull();
    expect($(".try-confirm")).toBeNull();
  });

  it("never queues a confirm answer: when offline it says so and keeps the question", async () => {
    const t = await mount();
    await t.click(t.card("order"));
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await t.emit({ t: "confirm", id: "c2", kind: "order", prompt: "Send 10 L to Sakthi?" });
    t.socket().open = false;
    await t.click($(".try-no"));
    expect(t.socket().sent.some((m) => m.t === "confirm")).toBe(false);
    expect($(".try-confirm")).not.toBeNull();
    expect($(".try-log").textContent).toContain("Not sent");
    t.socket().open = true;
    await t.click($(".try-no"));
    expect(t.socket().sent.at(-1)).toEqual({ t: "confirm", id: "c2", answer: "no" });
    expect($(".try-confirm")).toBeNull();
  });

  it("sends typed text (the no-microphone fallback)", async () => {
    const t = await mount();
    const input = $<HTMLInputElement>("#try-typed-input");
    await act(() => { input.value = "How much Sakthi curd is left?"; input.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(() => { $<HTMLFormElement>(".try-typed").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(t.wires).toHaveLength(1);
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    expect(t.socket().sent).toEqual([{ t: "text", text: "How much Sakthi curd is left?" }]);
    expect(input.value).toBe("");
    // Blank input sends nothing.
    await act(() => { $<HTMLFormElement>(".try-typed").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    expect(t.socket().sent).toHaveLength(1);
  });

  it("Talk opens the microphone through the app; Stop sends stop", async () => {
    const t = await mount();
    await t.click($(".try-talk"));
    await flush();
    expect(t.fake.env.startMic).toHaveBeenCalledTimes(1);
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await t.click($(".try-stop"));
    expect(t.socket().sent.at(-1)).toEqual({ t: "stop" });
  });

  it("uses the scene's photo as the camera once it loads, and says so when it is not recorded yet", async () => {
    const t = await mount();
    await t.click(t.card("delivery-crate"));
    const img = $<HTMLImageElement>(".try-stage img");
    expect(img.getAttribute("src")).toBe("/demo/crate_sakthi_curd.jpg");
    expect(getFrameSource()).toBeNull();
    await act(() => { img.dispatchEvent(new Event("load")); });
    expect(getFrameSource()).toBe(img);
    await act(() => { img.dispatchEvent(new Event("error")); });
    expect(getFrameSource()).toBeNull();
    expect($(".try-stage").textContent).toContain("not recorded yet");
    // A voice-only scene clears any demo media.
    await t.click(t.card("summary"));
    expect(getFrameSource()).toBeNull();
    expect($(".try-stage img")).toBeNull();
  });

  it("plays the soundbox clip into the audio stream after the UPI prompt is sent", async () => {
    const t = await mount();
    await t.click(t.card("sale-upi"));
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(t.socket().sent[0]).toEqual({ t: "text", text: SCENARIOS[8].prompt });
    expect(t.injectAudio).toHaveBeenCalledTimes(1);
    const [src, send, opts] = t.injectAudio.mock.calls[0] as unknown as [string, (m: ClientMsg) => boolean, { signal: AbortSignal }];
    expect(src).toBe("/demo/soundbox_upi_60.wav");
    expect(send({ t: "audio", pcm16: "AAAA" })).toBe(true);
    expect(t.socket().sent.at(-1)).toEqual({ t: "audio", pcm16: "AAAA" });
    expect(opts.signal.aborted).toBe(false);
  });

  it("reports a missing soundbox clip instead of failing silently", async () => {
    const t = await mount({ injectAudio: async () => { throw new Error("demo audio 404: /demo/soundbox_upi_60.wav"); } });
    await t.click(t.card("sale-upi"));
    await t.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect($(".try-stage").textContent).toContain("not recorded yet");
    expect($(".try-log").textContent).toContain("demo audio 404");
  });

  it("leaving the page restores the real camera and closes the demo session", async () => {
    const t = await mount();
    await t.click(t.card("delivery-crate"));
    const img = $<HTMLImageElement>(".try-stage img");
    await act(() => { img.dispatchEvent(new Event("load")); });
    expect(getFrameSource()).toBe(img);
    await act(() => { render(null, root); });
    expect(getFrameSource()).toBeNull(); // Review Focus #1
    expect(t.socket().closed).toBe(true);
  });
});
