// frontend/app/test/walk.start.test.ts — real wiring of the bump guard (camera, detector and app faked)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const video = { readyState: 4, videoWidth: 1280, videoHeight: 720 };
const paused = vi.hoisted(() => ({ on: false }));
vi.mock("../src/camera/capture", () => ({ startCamera: vi.fn(async () => video), detectionPaused: () => paused.on }));
vi.mock("../src/core/app", () => ({ app: { earcon: vi.fn(), speak: vi.fn(), settings: { lang: "auto" }, lang: () => "en" } }));

import { grabFrame, startBumpGuard } from "../src/walk/start";
import { startCamera } from "../src/camera/capture";
import { setFrameSource } from "../src/camera/source";
import { walkingMode } from "../src/walk/mode";

/** Fake motion sensor: the owner is walking. */
const walkingSensor = { available: true, isWalking: () => true, stop: vi.fn() };

const bitmap = { width: 320, height: 180, close: vi.fn() };

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("bump guard frame grab", () => {
  beforeEach(() => {
    vi.mocked(startCamera).mockClear();
    video.readyState = 4;
    vi.stubGlobal("createImageBitmap", vi.fn(async () => bitmap));
  });
  afterEach(() => {
    setFrameSource(null);
    vi.unstubAllGlobals();
  });

  it("grabs a 320-px-wide frame of the shared rear camera, stamped with the frame time", async () => {
    vi.spyOn(performance, "now").mockReturnValue(4321);
    const g = await grabFrame();
    expect(g).toEqual({ frame: bitmap, ts: 4321 });
    expect(createImageBitmap).toHaveBeenCalledWith(video, expect.objectContaining({ resizeWidth: 320, resizeHeight: 180 }));
    vi.mocked(performance.now).mockRestore();
  });

  it("no detector frame while an owner tool photo (ask, count, ...) is being captured", async () => {
    paused.on = true;
    try {
      expect(await grabFrame()).toBeNull();
      expect(createImageBitmap).not.toHaveBeenCalled();
    } finally {
      paused.on = false;
    }
    expect(await grabFrame()).not.toBeNull();
  });

  it("a camera that is not ready yet gives no frame", async () => {
    video.readyState = 1;
    expect(await grabFrame()).toBeNull();
  });

  it("demo mode (/try) grabs nothing and never opens the camera", async () => {
    setFrameSource(document.createElement("canvas"));
    expect(await grabFrame()).toBeNull();
    expect(startCamera).not.toHaveBeenCalled();
  });
});

describe("startBumpGuard", () => {
  afterEach(() => setHidden(false));

  it("pauses while the app is hidden, resumes when visible, and dispose() closes the detector", async () => {
    const detector = { kind: "main" as const, detect: vi.fn(async (_f: unknown, ts: number) => ({ ts, dets: [], floor: null })), close: vi.fn() };
    const g = await startBumpGuard({ detector });
    expect(g.isRunning()).toBe(true);
    setHidden(true);
    expect(g.isRunning()).toBe(false);
    setHidden(false);
    expect(g.isRunning()).toBe(true);
    g.dispose();
    expect(g.isRunning()).toBe(false);
    expect(detector.close).toHaveBeenCalledTimes(1);
    setHidden(false);   // listener removed: stays stopped
    expect(g.isRunning()).toBe(false);
  });

  it("onWarn hears each warning after the vibration; a failing listener never blocks the earcon or phrase", async () => {
    const vibrate = vi.fn();
    vi.stubGlobal("navigator", { vibrate });
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const detector = { kind: "main" as const, detect: vi.fn(async (_f: unknown, ts: number) => ({ ts, dets: [], floor: null })), close: vi.fn() };
    const seen: string[] = [];
    const onWarn = vi.fn((w: { phrase: string }) => {
      seen.push(`warn:${w.phrase}:vibrated=${vibrate.mock.calls.length}`);
      throw new Error("ui broke");
    });
    const g = await startBumpGuard({ detector, onWarn, motion: walkingSensor });
    g.stop();
    const { app } = await import("../src/core/app");
    vi.mocked(app.speak).mockClear();
    g.process({ ts: 0, floor: null, dets: [{ label: "crate", score: 0.9, box: [300, 200, 800, 420] }] });
    expect(seen).toEqual([]);                       // one frame is not enough
    g.process({ ts: 125, floor: null, dets: [{ label: "crate", score: 0.9, box: [300, 200, 800, 420] }] });
    expect(seen).toEqual(["warn:bump.left_right:vibrated=1"]);
    expect(app.earcon).toHaveBeenCalledWith("warn");
    expect(app.speak).toHaveBeenCalledWith("Obstacle left");
    expect(err).toHaveBeenCalled();
    g.dispose();
    err.mockRestore();
    vi.unstubAllGlobals();
  });

  it("without a motion sensor (jsdom) the guard runs approach-only and still starts", async () => {
    const detector = { kind: "main" as const, detect: vi.fn(async (_f: unknown, ts: number) => ({ ts, dets: [], floor: null })), close: vi.fn() };
    const { app } = await import("../src/core/app");
    vi.mocked(app.earcon).mockClear();
    const g = await startBumpGuard({ detector });
    g.stop();
    expect(g.motion.available).toBe(false);
    const big = { label: "crate", score: 0.9, box: [300, 450, 800, 550] as [number, number, number, number] };
    for (let i = 0; i < 6; i++) g.process({ ts: i * 125, floor: null, dets: [big] });
    expect(app.earcon).not.toHaveBeenCalledWith("warn");
    g.dispose();
  });

  it("walking mode: the persisted switch drives the guard, and setWalkingMode on the guard persists it", async () => {
    const store = new Map<string, string>();
    walkingMode.reset({ getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } } as unknown as Storage);
    const detector = { kind: "main" as const, detect: vi.fn(async (_f: unknown, ts: number) => ({ ts, dets: [], floor: null })), close: vi.fn() };
    const g = await startBumpGuard({ detector, motion: walkingSensor });
    g.stop();
    expect(g.isWalkingMode()).toBe(true);
    walkingMode.set(false);
    expect(g.isWalkingMode()).toBe(false);
    g.setWalkingMode(true);
    expect(walkingMode.get()).toBe(true);
    expect(store.get("aira.walkingMode")).toBe("on");
    g.dispose();
    expect(walkingSensor.stop).toHaveBeenCalled();
    walkingMode.set(false);
    expect(g.isWalkingMode()).toBe(true);           // unsubscribed on dispose
    walkingMode.reset(null);
  });
});
