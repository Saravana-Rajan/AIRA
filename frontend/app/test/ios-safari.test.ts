// iOS Safari (iPhone, browser or Add to Home Screen): the feature-detect paths that keep AIRA working there.
// Motion permission inside the Start tap, no Vibration API, a locked / interrupted AudioContext, speech that needs
// a gesture, wake lock, inline camera video and the home-screen meta tags.
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { ServerMsg } from "@aira/contracts";
import { motionAccess, motionNeedsPermission, requestMotionAccess, __resetMotionAccess } from "../src/core/motionPermission";
import { startTiltHints, type TiltJudge } from "../src/camera/tilt";
import { startMotionSensor } from "../src/walk/motion";
import { vibrate } from "../src/core/haptics";
import { isIOS, shouldAutoStart } from "../src/core/autostart";
import { browserEnv, wireApp, type WireEnv, type WiredApp } from "../src/core/wire";
import { startMic, MIC_RESUME_WAIT_MS, type MicDeps } from "../src/voice/mic";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { memStore } from "../src/net/offline-queue";

const HERE = dirname(fileURLToPath(import.meta.url));
const appPath = (p: string) => resolve(HERE, "..", p);
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

beforeEach(() => __resetMotionAccess());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/* ---------- 1. motion / orientation permission ---------- */

function iosGlobals(answer: () => Promise<string>) {
  const orient = vi.fn(answer);
  const motion = vi.fn(answer);
  const g = {
    DeviceOrientationEvent: { requestPermission: orient },
    DeviceMotionEvent: { requestPermission: motion },
  } as unknown as typeof globalThis;
  return { g, orient, motion };
}

describe("motion permission (iOS needs requestPermission inside the Start tap)", () => {
  it("asks for orientation and motion synchronously, in the same tap, and shares the answer", async () => {
    const { g, orient, motion } = iosGlobals(async () => "granted");
    requestMotionAccess(g);
    expect(orient).toHaveBeenCalledTimes(1); // no await between the tap and the calls
    expect(motion).toHaveBeenCalledTimes(1);
    expect(await motionAccess("orientation", g)).toBe(true);
    expect(await motionAccess("motion", g)).toBe(true);
    expect(orient).toHaveBeenCalledTimes(1); // later callers reuse the tap's answer: no second prompt
  });

  it("denied means false, never an error", async () => {
    const { g } = iosGlobals(async () => "denied");
    requestMotionAccess(g);
    await expect(motionAccess("orientation", g)).resolves.toBe(false);
  });

  it("a prompt that throws (no gesture) is false and is asked again next time", async () => {
    let n = 0;
    const { g, orient } = iosGlobals(async () => { n++; if (n === 1) throw new Error("NotAllowedError"); return "granted"; });
    await expect(motionAccess("orientation", g)).resolves.toBe(false);
    await expect(motionAccess("orientation", g)).resolves.toBe(true);
    expect(orient).toHaveBeenCalledTimes(2);
  });

  it("a synchronous throw is also just false", async () => {
    const g = { DeviceOrientationEvent: { requestPermission: () => { throw new Error("boom"); } } } as unknown as typeof globalThis;
    await expect(motionAccess("orientation", g)).resolves.toBe(false);
  });

  it("Android (no requestPermission) needs no prompt; a phone without the API has no access", async () => {
    const android = { DeviceOrientationEvent: function () {}, DeviceMotionEvent: function () {} } as unknown as typeof globalThis;
    expect(motionNeedsPermission(android)).toBe(false);
    await expect(motionAccess("orientation", android)).resolves.toBe(true);
    await expect(motionAccess("motion", {} as typeof globalThis)).resolves.toBe(false);
    expect(() => requestMotionAccess({} as typeof globalThis)).not.toThrow();
    expect(motionNeedsPermission(iosGlobals(async () => "granted").g)).toBe(true);
  });
});

describe("tilt hints wait for the orientation permission", () => {
  const judge = (): TiltJudge & { seen: number } => {
    const j = { seen: 0, sample() { j.seen++; return null; }, reset() {} };
    return j;
  };
  const orient = () => {
    const e = new Event("deviceorientation") as Event & { beta: number; gamma: number };
    Object.defineProperty(e, "beta", { value: 10 });
    Object.defineProperty(e, "gamma", { value: 0 });
    window.dispatchEvent(e);
  };

  it("granted: listens only once the answer is in", async () => {
    const grants: Array<(v: string) => void> = [];
    const { g } = iosGlobals(() => new Promise<string>((r) => { grants.push(r); }));
    requestMotionAccess(g); // the Start tap
    const j = judge();
    const stop = startTiltHints(() => {}, j, g);
    orient();
    expect(j.seen).toBe(0);
    grants.forEach((r) => r("granted"));
    await flush();
    orient();
    expect(j.seen).toBe(1);
    stop();
    orient();
    expect(j.seen).toBe(1);
  });

  it("denied: tilt hints just stay off, no error", async () => {
    const { g } = iosGlobals(async () => "denied");
    const j = judge();
    const stop = startTiltHints(() => {}, j, g);
    await flush();
    orient();
    expect(j.seen).toBe(0);
    expect(() => stop()).not.toThrow();
  });

  it("stopped before the answer: never attaches", async () => {
    const { g } = iosGlobals(async () => "granted");
    const j = judge();
    startTiltHints(() => {}, j, g)();
    await flush();
    orient();
    expect(j.seen).toBe(0);
  });
});

describe("walking detector shares the tap's motion answer", () => {
  it("does not prompt a second time after the Start tap", async () => {
    const req = vi.fn(async () => "granted");
    vi.stubGlobal("DeviceMotionEvent", { requestPermission: req });
    vi.stubGlobal("addEventListener", vi.fn());
    vi.stubGlobal("removeEventListener", vi.fn());
    requestMotionAccess();
    const s = await startMotionSensor();
    expect(s.available).toBe(true);
    expect(req).toHaveBeenCalledTimes(1);
    s.stop();
  });
});

/* ---------- 2. vibration ---------- */

describe("haptics without the Vibration API (iOS)", () => {
  it("vibrate() is a silent no-op when navigator.vibrate is missing or throws", () => {
    expect(vibrate("warn", {} as Navigator)).toBe(false);
    expect(vibrate("warn", undefined)).toBe(false);
    expect(vibrate("warn", { vibrate: () => { throw new Error("x"); } } as unknown as Navigator)).toBe(false);
  });
});

/* ---------- wireApp with an iOS-like environment ---------- */

type FakeCtx = EventTarget & { state: string; resume: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> };

function fakeCtx(state = "suspended"): FakeCtx {
  const c = Object.assign(new EventTarget(), {
    state,
    resume: vi.fn(async () => { c.state = "running"; }),
    close: vi.fn(async () => { c.state = "closed"; }),
  });
  return c;
}

const wired: WiredApp[] = [];
afterEach(() => { while (wired.length) wired.pop()!.dispose(); });

function setup(over: Partial<WireEnv> = {}) {
  const ctx = fakeCtx();
  const win = new EventTarget();
  const doc = Object.assign(new EventTarget(), { hidden: false });
  let handler: ((m: ServerMsg) => void) | null = null;
  const socket = {
    connect() {}, close() {}, send: () => true,
    on: (h: (m: ServerMsg) => void) => { handler = h; return () => {}; },
  };
  const env: WireEnv = {
    createSocket: () => socket as never,
    createAudioContext: () => ctx as unknown as AudioContext,
    createPlayback: () => ({ enqueue() {}, flush() {}, resume() {}, msSinceAudio: () => Infinity }),
    playEarcon: vi.fn(),
    vibrate: vi.fn(() => { /* iOS: nothing happens */ }),
    speech: { speak: vi.fn(), cancel: vi.fn(), prime: vi.fn() },
    announce: vi.fn(),
    startMic: vi.fn(async () => ({ stop() {} })),
    queueStore: () => memStore(),
    storage: null,
    win, doc,
    requestWakeLock: vi.fn(),
    requestPermissions: vi.fn(),
    requestMotionAccess: vi.fn(),
    languages: ["en-IN"],
    buildInfo: async () => null,
    ...over,
  };
  const w = wireApp({ settings: { ...DEFAULT_SETTINGS, onboarded: true }, wsUrl: "ws://t/ws", connect: false, env });
  wired.push(w);
  return { w, env, ctx, win, doc, emit: (m: ServerMsg) => handler?.(m), earcons: () => (env.playEarcon as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[1]) };
}

describe("warnings still sound on a phone that cannot vibrate", () => {
  it("confirm, error and mismatch play their earcon whether or not vibration exists", () => {
    const a = setup({ vibrate: () => { throw new Error("no vibrate"); } });
    a.emit({ t: "confirm", id: "c1", kind: "sale", prompt: "Sell?" });
    a.emit({ t: "error", code: "network", say: "Network problem." });
    a.emit({ t: "cue", cue: "mismatch" });
    expect(a.earcons()).toEqual(expect.arrayContaining(["warn", "mismatch"]));
    expect(a.earcons().filter((n) => n === "warn").length).toBeGreaterThanOrEqual(2);
  });
});

/* ---------- 3. audio ---------- */

describe("audio unlock and Safari's interrupted state", () => {
  it("inside the Start tap: resumes audio, primes speech, asks for motion access", () => {
    const a = setup();
    expect(a.ctx.resume).toHaveBeenCalled();
    expect(a.env.speech!.prime).toHaveBeenCalledTimes(1);
    expect(a.env.requestMotionAccess).toHaveBeenCalledTimes(1);
  });

  it("resumes an interrupted context when the page comes back, on pageshow and on statechange", async () => {
    const a = setup();
    await flush();
    a.ctx.resume.mockClear();
    a.ctx.state = "interrupted"; // a phone call
    a.ctx.dispatchEvent(new Event("statechange"));
    expect(a.ctx.resume).toHaveBeenCalledTimes(1);
    a.ctx.state = "interrupted";
    a.win.dispatchEvent(new Event("pageshow"));
    expect(a.ctx.resume).toHaveBeenCalledTimes(2);
    a.ctx.state = "suspended";
    a.doc.dispatchEvent(new Event("visibilitychange"));
    expect(a.ctx.resume).toHaveBeenCalledTimes(3);
  });

  it("leaves a running or closed context alone, and does nothing while hidden", async () => {
    const a = setup();
    await flush();
    a.ctx.resume.mockClear();
    a.ctx.state = "running";
    a.win.dispatchEvent(new Event("pageshow"));
    a.ctx.state = "suspended";
    a.doc.hidden = true;
    a.doc.dispatchEvent(new Event("visibilitychange"));
    expect(a.ctx.resume).not.toHaveBeenCalled();
  });

  it("the owner's next touch resumes a locked context and primes speech once", async () => {
    const a = setup();
    await flush();
    a.ctx.resume.mockClear();
    (a.env.speech!.prime as ReturnType<typeof vi.fn>).mockClear();
    a.ctx.state = "suspended";
    a.doc.dispatchEvent(new Event("touchend"));
    a.ctx.state = "suspended";
    a.doc.dispatchEvent(new Event("pointerup"));
    expect(a.ctx.resume).toHaveBeenCalledTimes(2);
    expect(a.env.speech!.prime).toHaveBeenCalledTimes(1);
  });

  it("nothing fires after dispose", async () => {
    const a = setup();
    await flush();
    a.w.dispose();
    a.ctx.resume.mockClear();
    a.ctx.state = "suspended";
    a.doc.dispatchEvent(new Event("touchend"));
    a.win.dispatchEvent(new Event("pageshow"));
    expect(a.ctx.resume).not.toHaveBeenCalled();
  });

  it("startMic never hangs on a resume() that Safari leaves pending", async () => {
    vi.useFakeTimers();
    const node = { port: { onmessage: null }, connect: vi.fn(), disconnect: vi.fn() };
    const ctx = {
      state: "suspended", destination: {},
      audioWorklet: { addModule: vi.fn(async () => {}) },
      createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
      resume: vi.fn(() => new Promise<void>(() => {})), // never settles
      close: vi.fn(async () => {}),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    };
    const track = { stop: vi.fn() };
    const deps: MicDeps = {
      mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) as unknown as MediaStream },
      createContext: () => ctx as unknown as AudioContext,
      createWorkletNode: () => node as unknown as AudioWorkletNode,
      loadWorkletUrl: async () => "blob:w",
    };
    let done = false;
    const p = startMic({ onChunk: () => {}, deps }).then((h) => { done = true; return h; });
    await vi.advanceTimersByTimeAsync(MIC_RESUME_WAIT_MS + 10);
    expect(done).toBe(true);
    const h = await p;
    expect(ctx.addEventListener).toHaveBeenCalledWith("statechange", expect.any(Function));
    h.stop();
    expect(ctx.removeEventListener).toHaveBeenCalledWith("statechange", expect.any(Function));
  });
});

/* ---------- 4. camera and wake lock ---------- */

describe("wake lock (feature-checked, re-acquired)", () => {
  it("is skipped quietly where navigator.wakeLock is missing", () => {
    vi.stubGlobal("navigator", { ...navigator, wakeLock: undefined });
    expect(() => browserEnv().requestWakeLock()).not.toThrow();
  });

  it("asks for a screen lock where it exists, and a refusal is swallowed", async () => {
    const request = vi.fn(() => Promise.reject(new Error("NotAllowedError")));
    vi.stubGlobal("navigator", { ...navigator, wakeLock: { request } });
    browserEnv().requestWakeLock();
    await flush();
    expect(request).toHaveBeenCalledWith("screen");
  });

  it("is taken again when the page becomes visible and on a bfcache restore", () => {
    const a = setup();
    expect(a.env.requestWakeLock).toHaveBeenCalledTimes(1);
    a.doc.dispatchEvent(new Event("visibilitychange"));
    a.win.dispatchEvent(new Event("pageshow"));
    expect(a.env.requestWakeLock).toHaveBeenCalledTimes(3);
  });
});

describe("camera <video> elements play inline (iOS needs playsinline + muted)", () => {
  const read = (p: string) => readFileSync(appPath(p), "utf8");
  it("every JSX <video> has muted and playsInline", () => {
    for (const f of ["src/ui/CameraPreview.tsx", "src/ui/BoxOverlay.tsx", "src/demo/TryAira.tsx"]) {
      const tags = read(f).match(/<video\s[^>]*>/g) ?? [];
      expect(tags.length, f).toBeGreaterThan(0);
      for (const t of tags) {
        expect(t, f).toMatch(/\bmuted\b/);
        expect(t, f).toMatch(/\bplaysInline\b/);
      }
    }
  });
  it("every script-made <video> sets playsinline and muted", () => {
    for (const f of ["src/camera/capture.ts", "src/check/DeviceCheck.tsx"]) {
      const src = read(f);
      expect(src, f).toMatch(/createElement\("video"\)/);
      expect(src, f).toMatch(/setAttribute\("playsinline"/);
      expect(src, f).toMatch(/\.muted = true/);
    }
  });
  it("the camera asks for the back camera by facingMode environment", () => {
    expect(read("src/camera/backCamera.ts")).toMatch(/facingMode/);
  });
});

/* ---------- autostart ---------- */

describe("iOS keeps the Start tap", () => {
  it("detects iPhone, iPad and touch Macs (iPadOS)", () => {
    expect(isIOS({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15", maxTouchPoints: 5 })).toBe(true);
    expect(isIOS({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", maxTouchPoints: 5 })).toBe(true);
    expect(isIOS({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", maxTouchPoints: 0 })).toBe(false);
    expect(isIOS({ userAgent: "Mozilla/5.0 (Linux; Android 10; SM-A225F)", maxTouchPoints: 5 })).toBe(false);
    expect(isIOS(undefined)).toBe(false);
  });
  it("never auto-starts on iOS: audio, speech and the motion prompt need the tap", () => {
    expect(shouldAutoStart({ onboarded: true, search: "?start=1", installed: true, ios: true })).toBe(false);
    expect(shouldAutoStart({ onboarded: true, search: "", installed: true, ios: false })).toBe(true);
  });
});

/* ---------- 6. Add to Home Screen ---------- */

describe("iOS home-screen meta", () => {
  const html = readFileSync(appPath("index.html"), "utf8");
  it("runs full screen with a status-bar style and a title", () => {
    expect(html).toMatch(/<meta name="apple-mobile-web-app-capable" content="yes"/);
    expect(html).toMatch(/<meta name="apple-mobile-web-app-status-bar-style" content="(default|black|black-translucent)"/);
    expect(html).toMatch(/<meta name="apple-mobile-web-app-title" content="AIRA"/);
    expect(html).toMatch(/viewport-fit=cover/);
  });
  it("has a 180x180 opaque apple-touch-icon made from the AIRA logo", () => {
    const m = html.match(/<link rel="apple-touch-icon"[^>]*href="([^"]+)"/);
    expect(m).not.toBeNull();
    const file = appPath(`public${m![1]}`);
    expect(existsSync(file)).toBe(true);
    const png = readFileSync(file);
    expect(png.readUInt32BE(16)).toBe(180); // IHDR width
    expect(png.readUInt32BE(20)).toBe(180); // IHDR height
    expect(png[25]).toBe(2); // colour type 2: RGB, no alpha (iOS paints transparency black)
  });
});
