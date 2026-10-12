// frontend/app/test/clientLog.test.ts — the phone's diagnostic lines: throttle, drop-when-closed, and the tags
// build, camera, tilt, frame, hint and speak reaching the socket as "log" messages. Behaviour is unchanged:
// every assertion on speech, hints and frames here is the same as before the logging existed.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import {
  createClientLogger, clientLog, __resetClientLog, loadBuildInfo, shortText, CLIENT_LOG_MAX_PER_S, CLIENT_LOG_MAX_BYTES,
} from "../src/core/clientLog";
import { initApp, app } from "../src/core/app";
import type { Settings } from "../src/core/types";
import { createTiltJudge, startTiltHints, TILT_HOLD_MS, TILT_HINT_GAP_MS, type TiltDecision } from "../src/camera/tilt";
import { createHintSpeaker, HINT_GAP_MS } from "../src/camera/hints";
import { captureFrame, __setCaptureDeps, setQualityListener, describeCamera } from "../src/camera/capture";
import { createFrameRequestHandler } from "../src/camera/requests";
import { CameraError } from "../src/camera/capture";
import { wireApp, APP_VERSION, type WireEnv, type WiredApp } from "../src/core/wire";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { memStore } from "../src/net/offline-queue";

type LogMsg = Extract<ClientMsg, { t: "log" }>;
const logs = (sent: ClientMsg[], tag?: string): LogMsg[] =>
  sent.filter((m): m is LogMsg => m.t === "log" && (tag === undefined || m.tag === tag));

const SETTINGS: Settings = {
  lang: "en", verbosity: "short", screenReaderMode: false, speechRate: 1, shopId: "sunrise_dairy", onboarded: true, showBoxes: false,
};

function fakeSocket() {
  const sent: ClientMsg[] = [];
  const hs = new Set<(m: ServerMsg) => void>();
  return {
    sent,
    open: true,
    hello: null as Extract<ClientMsg, { t: "hello" }> | null,
    connect(h: Extract<ClientMsg, { t: "hello" }>) { this.hello = h; },
    send(m: ClientMsg) { if (!this.open) return false; sent.push(m); return true; },
    on(h: (m: ServerMsg) => void) { hs.add(h); return () => { hs.delete(h); }; },
    close() { this.open = false; },
    emit(m: ServerMsg) { hs.forEach((h) => h(m)); },
  };
}

beforeEach(() => { __resetClientLog(); });
afterEach(() => { vi.useRealTimers(); });

describe("createClientLogger", () => {
  it("sends a log message with the tag and data, and reports a closed socket as not sent", () => {
    const sent: ClientMsg[] = [];
    let open = true;
    const l = createClientLogger((m) => { if (!open) return false; sent.push(m); return true; }, () => 1000);
    expect(l.log("tilt", { beta: 131.2, reason: "speak" })).toBe(true);
    expect(sent).toEqual([{ t: "log", tag: "tilt", data: { beta: 131.2, reason: "speak" } }]);
    open = false;
    expect(l.log("tilt", { beta: 1 })).toBe(false);
    expect(sent.length).toBe(1);
  });

  it("never throws when the sender throws", () => {
    const l = createClientLogger(() => { throw new Error("no socket"); });
    expect(l.log("x")).toBe(false);
  });

  it("throttles to the per-second budget and resumes in the next second", () => {
    let t = 5000;
    const sent: ClientMsg[] = [];
    const l = createClientLogger((m) => { sent.push(m); return true; }, () => t);
    for (let i = 0; i < CLIENT_LOG_MAX_PER_S + 10; i++) l.log("frame", { i });
    expect(sent.length).toBe(CLIENT_LOG_MAX_PER_S);
    t = 6000;
    expect(l.log("frame", { i: 99 })).toBe(true);
    expect(sent.length).toBe(CLIENT_LOG_MAX_PER_S + 1);
  });

  it("minGapMs drops a repeat of the same tag inside the gap but not other tags", () => {
    let t = 0;
    const sent: ClientMsg[] = [];
    const l = createClientLogger((m) => { sent.push(m); return true; }, () => t);
    expect(l.log("tilt", { a: 1 }, { minGapMs: 1000 })).toBe(true);
    t = 500;
    expect(l.log("tilt", { a: 2 }, { minGapMs: 1000 })).toBe(false);
    expect(l.log("hint", { key: "tilt_up" })).toBe(true);
    t = 1000;
    expect(l.log("tilt", { a: 3 }, { minGapMs: 1000 })).toBe(true);
    expect(logs(sent, "tilt").map((m) => m.data.a)).toEqual([1, 3]);
  });

  it("replaces oversized data with a size marker", () => {
    const sent: ClientMsg[] = [];
    const l = createClientLogger((m) => { sent.push(m); return true; });
    l.log("big", { blob: "z".repeat(CLIENT_LOG_MAX_BYTES * 2) });
    const d = logs(sent)[0].data as { _truncated: boolean; size: number; head: string };
    expect(d._truncated).toBe(true);
    expect(d.size).toBeGreaterThan(CLIENT_LOG_MAX_BYTES);
    expect(JSON.stringify(d).length).toBeLessThan(CLIENT_LOG_MAX_BYTES);
  });

  it("shortText cuts a line to 60 characters", () => {
    expect(shortText("short")).toBe("short");
    expect(shortText("x".repeat(100))).toBe(`${"x".repeat(60)}…`);
  });
});

describe("clientLog (the app singleton)", () => {
  it("drops silently before the app is wired", () => {
    expect(clientLog("build", { build: "x" })).toBe(false);
  });

  it("sends through app.send once wired, and drops when the socket is closed", () => {
    const socket = fakeSocket();
    initApp({ socket: socket as never, settings: { ...SETTINGS }, speakImpl: () => {}, earconImpl: () => {}, hapticImpl: () => {} });
    expect(clientLog("build", { build: "a1b2c3d" })).toBe(true);
    expect(logs(socket.sent, "build")[0].data).toEqual({ build: "a1b2c3d" });
    socket.open = false;
    expect(clientLog("build", { build: "a1b2c3d" })).toBe(false);
  });
});

describe("loadBuildInfo", () => {
  it("reads /version.json without caching", async () => {
    const f = vi.fn(async () => ({ ok: true, json: async () => ({ build: "a1b2c3d", updated: "2026-10-11T00:00:00Z" }) }));
    expect(await loadBuildInfo(f)).toEqual({ build: "a1b2c3d", updated: "2026-10-11T00:00:00Z" });
    expect(f).toHaveBeenCalledWith("/version.json", { cache: "no-store" });
  });
  it("is null when the file is missing, malformed or fetch fails", async () => {
    expect(await loadBuildInfo(async () => ({ ok: false, json: async () => ({}) }))).toBeNull();
    expect(await loadBuildInfo(async () => ({ ok: true, json: async () => ({ nope: 1 }) }))).toBeNull();
    expect(await loadBuildInfo(async () => { throw new Error("offline"); })).toBeNull();
    expect(await loadBuildInfo(undefined)).toBeNull();
  });
});

describe("tags", () => {
  let socket: ReturnType<typeof fakeSocket>;
  let said: string[];
  beforeEach(() => {
    socket = fakeSocket();
    said = [];
    initApp({
      socket: socket as never, settings: { ...SETTINGS },
      speakImpl: (t) => { said.push(t); }, earconImpl: () => {}, hapticImpl: () => {},
    });
  });

  it("tilt: the judge reports every decision with the raw and smoothed readings and a reason", () => {
    let t = 0;
    const seen: TiltDecision[] = [];
    const j = createTiltJudge({ now: () => t, frameLooksGood: () => false, onDecision: (d) => seen.push(d) });
    j.sample(90, 5);
    expect(seen.at(-1)).toMatchObject({ reason: "ok", hint: null, rawBeta: 90, rawGamma: 5, beta: 90, gamma: 5 });
    const hints: string[] = [];
    for (t = 100; t <= 2000; t += 100) { const h = j.sample(10, 0); if (h) hints.push(h); }
    expect(hints).toEqual(["tilt_up"]);                       // behaviour unchanged
    const reasons = new Set(seen.map((d) => d.reason));
    expect(reasons.has("hold")).toBe(true);
    const spoke = seen.filter((d) => d.reason === "speak");
    expect(spoke.length).toBe(1);
    expect(spoke[0]).toMatchObject({ hint: "tilt_up", rawBeta: 10 });
    expect(spoke[0].beta).toBeLessThan(60);
    expect(spoke[0].outMs).toBeGreaterThanOrEqual(TILT_HOLD_MS);
    j.sample(10, 0);
    expect(seen.at(-1)!.reason).toBe("hold");                // must be held again after a hint
    t += TILT_HOLD_MS;
    j.sample(10, 0);
    expect(seen.at(-1)!.reason).toBe("gap");                 // held, but inside the 8 s gap
    expect(seen.at(-1)!.sinceHintMs).toBeGreaterThanOrEqual(TILT_HOLD_MS);
    expect(seen.at(-1)!.sinceHintMs).toBeLessThan(TILT_HINT_GAP_MS);
    const k = createTiltJudge({ now: () => t, frameLooksGood: () => true, onDecision: (d) => seen.push(d) });
    for (t = 0; t <= 2000; t += 100) k.sample(10, 0);
    expect(seen.at(-1)!.reason).toBe("frame_good");
    k.sample(null, null);
    expect(seen.at(-1)).toMatchObject({ reason: "no_reading", rawBeta: null });
  });

  it("tilt: startTiltHints logs decisions over the socket at most once a second, and every spoken hint", () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const orient = (beta: number, gamma = 0) => {
      const e = new Event("deviceorientation") as Event & { beta: number; gamma: number };
      Object.defineProperty(e, "beta", { value: beta });
      Object.defineProperty(e, "gamma", { value: gamma });
      window.dispatchEvent(e);
    };
    const got: string[] = [];
    const stop = startTiltHints((h) => got.push(h));
    for (let t = 0; t <= 2000; t += 50) { vi.setSystemTime(t); orient(10); }
    stop();
    expect(got).toEqual(["tilt_up"]);                         // behaviour unchanged
    const lines = logs(socket.sent, "tilt");
    const spoke = lines.filter((m) => m.data.reason === "speak");
    expect(spoke.length).toBe(1);
    expect(spoke[0].data).toMatchObject({ hint: "tilt_up", rawBeta: 10, rawGamma: 0 });
    expect(lines.length).toBeLessThanOrEqual(4);              // ~1/s over 2 s, plus the speak line
    expect(lines.length).toBeGreaterThanOrEqual(2);
  });

  it("hint: a spoken hint logs its key; one swallowed by the gap logs that it was dropped", () => {
    let t = 0;
    const hint = createHintSpeaker((s) => app.speak(s), () => "en", () => t);
    expect(hint("tilt_up")).toBe(true);
    expect(said).toEqual(["Tilt the phone up a little."]);  // behaviour unchanged
    expect(logs(socket.sent, "hint")[0].data).toMatchObject({ key: "tilt_up", lang: "en" });
    t = 3000;
    expect(hint("tilt_up")).toBe(false);
    expect(said.length).toBe(1);
    const dropped = logs(socket.sent, "hint").at(-1)!.data;
    expect(dropped).toMatchObject({ key: "tilt_up", dropped: "gap" });
    expect(dropped.sinceMs).toBe(3000);
    t = HINT_GAP_MS;
    expect(hint("tilt_up")).toBe(true);
    expect(said.length).toBe(2);
  });

  it("frame: a captured frame logs purpose, bytes, size and the quality verdict, never the jpeg", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const fakeCanvas = () => ({
      canvas: {} as HTMLCanvasElement,
      ctx: { getImageData: () => ({ data: new Uint8ClampedArray(640 * 360 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D,
    });
    __setCaptureDeps({ source: { width: () => 1280, height: () => 720, draw: () => {} }, encoder: async () => "AAAA", makeCanvas: fakeCanvas });
    setQualityListener(() => {});
    const f = await captureFrame("ask");
    expect(f).toMatchObject({ t: "frame", jpeg: "AAAA", w: 640, h: 360, purpose: "ask" });
    const line = logs(socket.sent, "frame")[0].data;
    expect(line).toMatchObject({ purpose: "ask", bytes: 4, w: 640, h: 360, quality: "skipped" });
    expect(JSON.stringify(line)).not.toContain("AAAA");
    vi.setSystemTime(10_300);
    await captureFrame("ask");
    vi.setSystemTime(10_600);
    await captureFrame("ask");                                // every 3rd frame is quality-checked
    // a flat grey frame has no edges, so the Laplacian test calls it blurry: the real verdict is logged
    expect(logs(socket.sent, "frame").at(-1)!.data.quality).toBe("blurry");
    vi.setSystemTime(10_700);
    await captureFrame("ask");                                // inside the 250 ms gap: no line for this one
    expect(logs(socket.sent, "frame")).toHaveLength(3);
    setQualityListener(null);
  });

  it("frame: a capture failure in a request logs the camera error code (one hint, no retry)", async () => {
    const hint = vi.fn(() => true);
    const handle = createFrameRequestHandler({
      capture: vi.fn(async () => { throw new CameraError("no_frame"); }),
      startContinuous: () => () => {},
      send: () => true,
      hint,
      sleep: async () => {},
    });
    await handle({ t: "request_frame", purpose: "ask" });
    expect(hint).toHaveBeenCalledWith("no_camera");         // behaviour unchanged
    expect(logs(socket.sent, "frame")[0].data).toEqual({ purpose: "ask", error: "no_frame" });
  });

  it("camera: describes the open camera by facing, label and track settings only", () => {
    const stream = {
      getVideoTracks: () => [{ label: "camera2 0, facing back", getSettings: () => ({ width: 1280, height: 720, facingMode: "environment", deviceId: "abcdef0123456789" }) }],
    } as unknown as MediaStream;
    expect(describeCamera({ stream, facing: "environment", deviceId: "abcdef0123456789" })).toEqual({
      facing: "environment", label: "camera2 0, facing back", width: 1280, height: 720, facingMode: "environment", deviceId: "abcdef01",
    });
    const bare = { getVideoTracks: () => [] } as unknown as MediaStream;
    expect(describeCamera({ stream: bare, facing: "unknown", deviceId: null })).toEqual({
      facing: "unknown", label: "", width: null, height: null, facingMode: null, deviceId: null,
    });
  });
});

describe("wireApp tags: build and speak", () => {
  const wired: WiredApp[] = [];
  afterEach(() => { while (wired.length) wired.pop()!.dispose(); });
  const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

  function setup(buildInfo: WireEnv["buildInfo"]) {
    let socket!: ReturnType<typeof fakeSocket>;
    const speech = { speak: vi.fn(), cancel: vi.fn() };
    const env: Partial<WireEnv> = {
      createSocket: () => (socket = fakeSocket()) as never,
      createAudioContext: () => null,
      speech,
      announce: vi.fn(),
      startMic: vi.fn(async () => ({ stop() {} })),
      queueStore: () => memStore(),
      storage: null,
      win: new EventTarget(),
      doc: Object.assign(new EventTarget(), { hidden: false }),
      requestWakeLock: () => {},
      requestPermissions: () => {},
      languages: ["en-IN"],
      buildInfo,
    };
    const w = wireApp({ settings: { ...DEFAULT_SETTINGS, lang: "en", onboarded: true }, wsUrl: "ws://test/ws", env });
    wired.push(w);
    return { w, speech, get socket() { return socket; } };
  }

  it("build: after the server's ready, the deployed build and app version go out once per connection", async () => {
    const a = setup(async () => ({ build: "a1b2c3d", updated: "2026-10-11T00:00:00Z" }));
    expect(logs(a.socket.sent, "build")).toEqual([]);
    a.socket.emit({ t: "ready", sessionId: "s1", resume: "s1", mock: true });
    await flush();
    const b = logs(a.socket.sent, "build");
    expect(b.length).toBe(1);
    expect(b[0].data).toMatchObject({ build: "a1b2c3d", updated: "2026-10-11T00:00:00Z", appVersion: APP_VERSION, mock: true, lang: "en" });
  });

  it("build: an unreadable version.json is reported as unknown, never as an error", async () => {
    const a = setup(async () => null);
    a.socket.emit({ t: "ready", sessionId: "s1", resume: "s1", mock: false });
    await flush();
    expect(logs(a.socket.sent, "build")[0].data).toMatchObject({ build: "unknown", mock: false });
  });

  it("speak: a local line logs its first 60 characters and how it was spoken; a duplicate logs the drop", () => {
    const a = setup(async () => null);
    const line = "Microphone is blocked. Allow it in the phone settings and try again, please.";
    app.speak(line);
    expect(a.speech.speak).toHaveBeenCalledTimes(1);          // behaviour unchanged
    expect(logs(a.socket.sent, "speak")[0].data).toEqual({ text: shortText(line), mode: "tts", lang: "en" });
    app.speak(line);
    expect(a.speech.speak).toHaveBeenCalledTimes(1);
    expect(logs(a.socket.sent, "speak").at(-1)!.data).toEqual({ text: shortText(line), dropped: "dedupe" });
  });
});
