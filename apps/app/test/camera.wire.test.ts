// apps/app/test/camera.wire.test.ts — wireCameraAndGuidance() / wireCameraIntoApp() over the real app singleton,
// frame handler, guidance session, count UX and OPEN_URL handler. Only browser-bound modules are faked:
// the camera stream, tilt sensor, MediaPipe hand tracking, Web Audio tones and the bump-guard detector.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";

const h = vi.hoisted(() => ({
  onTip: null as null | ((tip: { x1000: number; y1000: number } | null) => void),
  stopTrack: vi.fn(),
  stopTilt: vi.fn(),
}));

vi.mock("../src/camera/capture", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/camera/capture")>()),
  captureFrame: vi.fn(async (purpose: string) => ({ t: "frame", id: "fr1", jpeg: "AAAA", w: 640, h: 480, ts: 1, purpose })),
  startContinuous: vi.fn(() => () => {}),
  setQualityListener: vi.fn(),
  stopCamera: vi.fn(),
}));
vi.mock("../src/camera/tilt", () => ({ startTiltHints: vi.fn(() => h.stopTilt) }));
vi.mock("../src/guidance/hands", () => ({
  trackFingertip: vi.fn(async (onTip: (tip: { x1000: number; y1000: number } | null) => void) => {
    h.onTip = onTip;
    return h.stopTrack;
  }),
}));
vi.mock("../src/guidance/tones", () => ({ guideTo: vi.fn(), stopTone: vi.fn() }));
vi.mock("../src/walk/start", () => ({ startBumpGuard: vi.fn() }));

import { initApp } from "../src/core/app";
import type { Settings } from "../src/core/types";
import { captureFrame, setQualityListener, stopCamera } from "../src/camera/capture";
import { trackFingertip } from "../src/guidance/hands";
import { guideTo, stopTone } from "../src/guidance/tones";
import { startBumpGuard } from "../src/walk/start";
import { wireCameraAndGuidance, wireCameraIntoApp, BUMP_CHIP_MS, type CameraWiring } from "../src/camera/wire";

type Handler = (m: ServerMsg) => void;
function fakeSocket() {
  const hs = new Set<Handler>();
  const sent: ClientMsg[] = [];
  return {
    hs,
    sent,
    on: (fn: Handler) => { hs.add(fn); return () => { hs.delete(fn); }; },
    send: (m: ClientMsg) => { sent.push(m); return true; },
    emit: (m: ServerMsg) => [...hs].forEach((fn) => fn(m)),
  };
}

const SETTINGS: Settings = {
  lang: "en", verbosity: "short", screenReaderMode: false, speechRate: 1, shopId: "sunrise_dairy", onboarded: true,
};

let socket: ReturnType<typeof fakeSocket>;
let said: string[];
let earcons: string[];
const live: Array<{ unsubscribe(): void }> = [];

function setupApp() {
  socket = fakeSocket();
  said = [];
  earcons = [];
  initApp({
    socket: socket as never,
    settings: { ...SETTINGS },
    speakImpl: (t) => { said.push(t); },
    earconImpl: (n) => { earcons.push(n); },
    hapticImpl: () => {},
  });
  // core/app's `send` reads app.socket, so give it the fake's full surface
  return socket;
}

function wire(opts: Parameters<typeof wireCameraAndGuidance>[0] = {}): CameraWiring {
  const cam = wireCameraAndGuidance({ devtools: false, ...opts });
  live.push(cam);
  return cam;
}

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const target = (point: [number, number], box?: [number, number, number, number]): ServerMsg =>
  ({ t: "target", frameId: "f1", label: "curd", point, box, confidence: 0.9 });

beforeEach(() => {
  vi.clearAllMocks();
  h.onTip = null;
  setupApp();
});
afterEach(() => {
  live.splice(0).forEach((c) => c.unsubscribe());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (globalThis as { __aira?: unknown }).__aira;
});

describe("wireCameraAndGuidance", () => {
  it("subscribes once; a second call reuses the live wiring", () => {
    const cam = wire();
    expect(wire()).toBe(cam);
    expect(socket.hs.size).toBe(1);
    expect(setQualityListener).toHaveBeenCalledWith(expect.any(Function));
  });

  it("answers request_frame with a captured frame through app.send", async () => {
    wire();
    socket.emit({ t: "request_frame", purpose: "invoice" });
    await flush();
    expect(captureFrame).toHaveBeenCalledWith("invoice");
    expect(socket.sent).toEqual([expect.objectContaining({ t: "frame", id: "fr1", purpose: "invoice" })]);
  });

  it("a camera failure speaks one hint and sends nothing", async () => {
    vi.mocked(captureFrame).mockRejectedValueOnce(new Error("NotAllowedError"));
    wire();
    socket.emit({ t: "request_frame", purpose: "ask" });
    await flush();
    expect(socket.sent).toEqual([]);
    expect(said).toEqual(["Camera is not available."]);
  });

  it("target → fingertip tracking → tones; [y, x] is never swapped; near-target is reported", async () => {
    const cam = wire();
    const near: boolean[] = [];
    cam.onHandNear((n) => near.push(n));
    socket.emit(target([200, 800], [150, 750, 250, 850]));
    await flush();
    expect(trackFingertip).toHaveBeenCalledTimes(1);
    h.onTip!({ x1000: 200, y1000: 200 });          // far left of the target
    expect(guideTo).toHaveBeenLastCalledWith([200, 800], { x1000: 200, y1000: 200 });
    expect(cam.isHandNearTarget()).toBe(false);
    h.onTip!({ x1000: 800, y1000: 200 });          // x=800, y=200: on the target
    expect(cam.isHandNearTarget()).toBe(true);
    h.onTip!({ x1000: 200, y1000: 800 });          // the swapped point is far away
    expect(cam.isHandNearTarget()).toBe(false);
    expect(near).toEqual([true, false]);
  });

  it("a done cue ends guidance: tone stops and the hand is no longer near", async () => {
    const cam = wire();
    socket.emit(target([500, 500], [450, 450, 550, 550]));
    await flush();
    h.onTip!({ x1000: 500, y1000: 500 });
    expect(cam.isHandNearTarget()).toBe(true);
    socket.emit({ t: "cue", cue: "done" });
    expect(stopTone).toHaveBeenCalled();
    expect(h.stopTrack).toHaveBeenCalled();
    expect(cam.isHandNearTarget()).toBe(false);
  });

  it("speaks live counts once each and reports progress", () => {
    const progress = vi.fn();
    wire({ onCountProgress: progress });
    const count = (counted: number, final = false): ServerMsg =>
      ({ t: "count", sku: "sakthi-curd-1l", counted, expected: 3, final, confidence: 0.9 });
    socket.emit(count(1)); socket.emit(count(1)); socket.emit(count(2)); socket.emit(count(3, true)); socket.emit(count(3, true));
    expect(said).toEqual(["1", "2", "3 counted, expected 3."]);
    expect(earcons).toEqual(["ack", "ack", "done"]);
    expect(progress).toHaveBeenCalledTimes(3);
  });

  it("OPEN_URL: says it, then opens only allowlisted links", () => {
    const open = vi.fn(() => ({ opener: null }));
    vi.stubGlobal("open", open);
    wire();
    socket.emit({ t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "OPEN_URL https://wa.me/919876543210?text=hi" });
    socket.emit({ t: "state", entity: "order", id: "o2", state: "ORDER_PLACED", summary: "OPEN_URL https://evil.example/pay" });
    expect(said).toEqual(["Opening WhatsApp."]);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith("https://wa.me/919876543210?text=hi", "_blank");
  });

  it("extra handlers get every message; one failing handler does not block the rest", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const extra = vi.fn();
    wire({ extraHandlers: [() => { throw new Error("boom"); }, extra] });
    socket.emit({ t: "count", sku: "s", counted: 4, expected: null, final: false, confidence: 0.9 });
    expect(extra).toHaveBeenCalledTimes(1);
    expect(said).toEqual(["4"]);
    err.mockRestore();
  });

  it("unsubscribe detaches and cleans up; wiring again afterwards gives a fresh wiring", async () => {
    const cam = wire();
    socket.emit(target([500, 500]));
    await flush();
    cam.unsubscribe();
    expect(socket.hs.size).toBe(0);
    expect(h.stopTilt).toHaveBeenCalledTimes(1);
    expect(setQualityListener).toHaveBeenLastCalledWith(null);
    expect(stopTone).toHaveBeenCalled();
    socket.emit({ t: "count", sku: "s", counted: 9, expected: null, final: false, confidence: 0.9 });
    expect(said).toEqual([]);
    const again = wire();
    expect(again).not.toBe(cam);
    expect(socket.hs.size).toBe(1);
  });

  it("follows a new app socket: re-wires instead of listening to a dead one", () => {
    const first = wire();
    const old = socket;
    setupApp();                                  // a new WiredApp filled the app singleton
    const second = wire();
    expect(second).not.toBe(first);
    expect(old.hs.size).toBe(0);
    expect(socket.hs.size).toBe(1);
  });

  it("devtools installs window.__aira.dispatch on the same path, and removes it on unsubscribe", async () => {
    const cam = wire({ devtools: true });
    await vi.waitFor(() => expect((globalThis as { __aira?: unknown }).__aira).toBeDefined());
    (globalThis as unknown as { __aira: { dispatch: Handler } }).__aira.dispatch(
      { t: "count", sku: "s", counted: 1, expected: 3, final: false, confidence: 0.9 },
    );
    expect(said).toEqual(["1"]);
    cam.unsubscribe();
    expect((globalThis as { __aira?: unknown }).__aira).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------------------------------------------

function fakeGuard() {
  return {
    setHandNearTarget: vi.fn(),
    onServerMsg: vi.fn(),
    dispose: vi.fn(),
  };
}
type FakeGuard = ReturnType<typeof fakeGuard>;

function fakeWired(confirmPending = false) {
  const calls: string[] = [];
  return {
    calls,
    w: {
      onTalk: vi.fn(() => { calls.push("talk"); }),
      onStop: vi.fn(() => { calls.push("stop"); }),
      dispose: vi.fn(() => { calls.push("dispose"); }),
      local: vi.fn((e: string) => { calls.push(`local:${e}`); }),
      confirm: { pending: confirmPending ? { id: "c1", kind: "order" } : null },
    },
  };
}

function guardStarter(g: FakeGuard = fakeGuard()) {
  let onWarn: ((w: { phrase: string }) => void) | undefined;
  vi.mocked(startBumpGuard).mockImplementation((async (opts: { onWarn?: (w: { phrase: string }) => void } = {}) => {
    onWarn = opts.onWarn;
    return g;
  }) as never);
  return { g, warn: (phrase = "bump.stop") => onWarn!({ phrase }) };
}

describe("wireCameraIntoApp", () => {
  it("starts the bump guard; server messages reach it (it may only raise urgency)", async () => {
    const { g } = guardStarter();
    const { w } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    await p.ready;
    expect(startBumpGuard).toHaveBeenCalledTimes(1);
    socket.emit({ t: "cue", cue: "warn" });
    expect(g.onServerMsg).toHaveBeenCalledWith({ t: "cue", cue: "warn" });
    p.dispose();
  });

  it("mutes the guard while the hand is near the guidance target, and syncs a guard that starts late", async () => {
    let resolve!: (g: FakeGuard) => void;
    const g = fakeGuard();
    vi.mocked(startBumpGuard).mockImplementation((() => new Promise((r) => { resolve = r; })) as never);
    const { w } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    socket.emit(target([500, 500], [450, 450, 550, 550]));
    await flush();
    h.onTip!({ x1000: 500, y1000: 500 });         // near before the guard is ready
    resolve(g);
    await p.ready;
    expect(g.setHandNearTarget).toHaveBeenLastCalledWith(true);
    h.onTip!({ x1000: 100, y1000: 900 });
    expect(g.setHandNearTarget).toHaveBeenLastCalledWith(false);
    p.dispose();
  });

  it("a bump warning shows the red Stop! chip, then clears it", async () => {
    vi.useFakeTimers();
    const s = guardStarter();
    const { w, calls } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    await p.ready;
    s.warn();
    expect(calls).toEqual(["local:bump"]);
    vi.advanceTimersByTime(BUMP_CHIP_MS - 100);
    s.warn("bump.left_right");                    // a second warning keeps the chip up
    vi.advanceTimersByTime(BUMP_CHIP_MS - 100);
    expect(calls).toEqual(["local:bump", "local:bump"]);
    vi.advanceTimersByTime(200);
    expect(calls).toEqual(["local:bump", "local:bump", "local:bump_clear"]);
    p.dispose();
  });

  it("long-press also ends hand guidance, then runs the app's own stop", async () => {
    guardStarter();
    const { w, calls } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    socket.emit(target([500, 500]));
    await flush();
    w.onStop();
    expect(stopTone).toHaveBeenCalled();
    expect(h.stopTrack).toHaveBeenCalled();
    expect(calls).toEqual(["stop"]);
    p.dispose();
  });

  it("double-tap opens a link the browser blocked; otherwise it talks", async () => {
    guardStarter();
    const open = vi.fn().mockReturnValueOnce(null).mockReturnValue({ opener: null });
    vi.stubGlobal("open", open);
    const { w, calls } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    socket.emit({ t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "OPEN_URL https://wa.me/91?text=a" });
    expect(said).toEqual(["Opening WhatsApp.", "Could not open the link. Double-tap to open it."]);
    w.onTalk();                                   // user gesture: the kept link opens, no talk turn
    expect(open).toHaveBeenCalledTimes(2);
    expect(calls).toEqual([]);
    w.onTalk();                                   // nothing pending: a normal talk
    expect(calls).toEqual(["talk"]);
    p.dispose();
  });

  it("a pending confirm wins the double-tap over a kept link", async () => {
    guardStarter();
    vi.stubGlobal("open", vi.fn(() => null));
    const { w, calls } = fakeWired(true);
    const p = wireCameraIntoApp(w as never, { devtools: false });
    socket.emit({ t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "OPEN_URL https://wa.me/91" });
    w.onTalk();
    expect(calls).toEqual(["talk"]);
    p.dispose();
  });

  it("dispose (from the app's own dispose) stops the camera wiring and the guard once", async () => {
    const { g } = guardStarter();
    const { w, calls } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    await p.ready;
    w.dispose();
    w.dispose();
    expect(calls).toEqual(["dispose", "dispose"]);
    expect(g.dispose).toHaveBeenCalledTimes(1);
    expect(stopCamera).toHaveBeenCalledTimes(1);
    expect(socket.hs.size).toBe(0);
  });

  it("a guard that finishes starting after dispose is shut down at once", async () => {
    let resolve!: (g: FakeGuard) => void;
    const g = fakeGuard();
    vi.mocked(startBumpGuard).mockImplementation((() => new Promise((r) => { resolve = r; })) as never);
    const { w } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    p.dispose();
    resolve(g);
    await p.ready;
    expect(g.dispose).toHaveBeenCalledTimes(1);
  });

  it("bumpGuard: false (demo page) never starts the detector", async () => {
    const { w } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false, bumpGuard: false });
    await p.ready;
    expect(startBumpGuard).not.toHaveBeenCalled();
    socket.emit({ t: "count", sku: "s", counted: 2, expected: null, final: false, confidence: 0.9 });
    expect(said).toEqual(["2"]);
    p.dispose();
  });

  it("a guard that fails to start is logged; camera and guidance keep working", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startBumpGuard).mockRejectedValueOnce(new Error("no WebGL"));
    const { w } = fakeWired();
    const p = wireCameraIntoApp(w as never, { devtools: false });
    await expect(p.ready).resolves.toBeUndefined();
    expect(err).toHaveBeenCalled();
    socket.emit({ t: "count", sku: "s", counted: 5, expected: null, final: false, confidence: 0.9 });
    expect(said).toEqual(["5"]);
    p.dispose();
    err.mockRestore();
  });
});
