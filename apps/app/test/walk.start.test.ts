// apps/app/test/walk.start.test.ts — real wiring of the bump guard (camera, detector and app faked)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const video = { readyState: 4, videoWidth: 1280, videoHeight: 720 };
vi.mock("../src/camera/capture", () => ({ startCamera: vi.fn(async () => video) }));
vi.mock("../src/core/app", () => ({ app: { earcon: vi.fn(), speak: vi.fn(), settings: { lang: "auto" }, lang: () => "en" } }));

import { grabFrame, startBumpGuard } from "../src/walk/start";
import { startCamera } from "../src/camera/capture";
import { setFrameSource } from "../src/camera/source";

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
    const g = await startBumpGuard({ detector, onWarn });
    g.stop();
    const { app } = await import("../src/core/app");
    vi.mocked(app.speak).mockClear();
    g.process({ ts: 0, floor: null, dets: [{ label: "crate", score: 0.9, box: [300, 200, 800, 420] }] });
    expect(seen).toEqual(["warn:bump.left_right:vibrated=1"]);
    expect(app.earcon).toHaveBeenCalledWith("warn");
    expect(app.speak).toHaveBeenCalledWith("Obstacle left");
    expect(err).toHaveBeenCalled();
    g.dispose();
    err.mockRestore();
    vi.unstubAllGlobals();
  });
});
