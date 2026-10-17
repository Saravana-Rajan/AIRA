// frontend/app/test/guidance.hands.test.ts — MediaPipe fingertip tracking (MediaPipe itself is faked)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const created: { delegate: string }[] = [];
const detectForVideo = vi.fn();
let failGpu = false;

vi.mock("../src/guidance/mediapipe", () => ({
  FilesetResolver: { forVisionTasks: vi.fn(async () => ({ wasm: true })) },
  HandLandmarker: {
    createFromOptions: vi.fn(async (_files: unknown, o: { baseOptions: { delegate: string } }) => {
      if (o.baseOptions.delegate === "GPU" && failGpu) throw new Error("no WebGL2");
      created.push({ delegate: o.baseOptions.delegate });
      return { detectForVideo };
    }),
  },
}));
const paused = vi.hoisted(() => ({ on: false }));
vi.mock("../src/camera/capture", () => ({
  getVideo: vi.fn(() => null),
  startCamera: vi.fn(async () => { throw new Error("camera must not open"); }),
  detectionPaused: () => paused.on,
}));

import { fingertipFrom, handBoxFrom, trackFingertip, trackHand, __resetHands } from "../src/guidance/hands";
import { setFrameSource } from "../src/camera/source";
import { startCamera } from "../src/camera/capture";

const hand = (x: number, y: number) => ({ landmarks: [Array.from({ length: 21 }, (_, i) => (i === 8 ? { x, y } : { x: 0, y: 0 }))] });

describe("fingertipFrom (MediaPipe result → 0–1000 image coords)", () => {
  it("uses the index fingertip (landmark 8) of the first hand and never swaps x and y", () => {
    expect(fingertipFrom(hand(0.2, 0.75))).toEqual({ x1000: 200, y1000: 750 });
  });
  it("null when there is no hand", () => {
    expect(fingertipFrom({ landmarks: [] })).toBeNull();
    expect(fingertipFrom(null)).toBeNull();
    expect(fingertipFrom(undefined)).toBeNull();
  });
  it("clamps landmarks slightly outside the image", () => {
    expect(fingertipFrom(hand(-0.05, 1.02))).toEqual({ x1000: 0, y1000: 1000 });
  });
});

describe("handBoxFrom (the whole hand as [ymin, xmin, ymax, xmax])", () => {
  it("spans every landmark of the first hand, clamped to the image", () => {
    const lms = Array.from({ length: 21 }, (_, i) => ({ x: 0.3 + i * 0.01, y: 0.5 + i * 0.02 }));
    expect(handBoxFrom({ landmarks: [lms] })).toEqual([500, 300, 900, 500]);
    expect(handBoxFrom({ landmarks: [[{ x: -0.1, y: 0.2 }, { x: 1.2, y: 0.9 }]] })).toEqual([200, 0, 900, 1000]);
  });
  it("null without a hand", () => {
    expect(handBoxFrom({ landmarks: [] })).toBeNull();
    expect(handBoxFrom(null)).toBeNull();
  });
});

describe("trackHand", () => {
  let frames: (() => void)[] = [];
  beforeEach(() => {
    frames = [];
    detectForVideo.mockReset();
    __resetHands();
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { frames.push(cb); return frames.length; });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });
  afterEach(() => { setFrameSource(null); vi.unstubAllGlobals(); });

  it("streams the fingertip and the hand box together, null without a hand", async () => {
    const canvas = document.createElement("canvas");
    canvas.width = 64; canvas.height = 48;
    setFrameSource(canvas);
    const seen: unknown[] = [];
    detectForVideo.mockReturnValueOnce(hand(0.5, 0.5)).mockReturnValue({ landmarks: [] });
    const stop = await trackHand((h) => seen.push(h));
    frames.shift()?.();
    frames.shift()?.();
    stop();
    expect(seen).toEqual([{ tip: { x1000: 500, y1000: 500 }, box: [0, 0, 500, 500] }, null]);
  });
});

describe("trackFingertip", () => {
  let frames: (() => void)[] = [];
  beforeEach(() => {
    frames = [];
    created.length = 0;
    failGpu = false;
    detectForVideo.mockReset();
    __resetHands();
    vi.stubGlobal("requestAnimationFrame", (cb: () => void) => { frames.push(cb); return frames.length; });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });
  afterEach(() => {
    setFrameSource(null);
    vi.unstubAllGlobals();
  });
  const runFrame = () => { const f = frames.shift(); f?.(); };

  it("demo mode tracks the demo element and never opens the camera; stop ends the loop", async () => {
    const demo = document.createElement("canvas");
    demo.width = 640; demo.height = 480;
    setFrameSource(demo);
    detectForVideo.mockReturnValueOnce(hand(0.5, 0.25)).mockReturnValueOnce({ landmarks: [] });
    const tips: unknown[] = [];
    const stop = await trackFingertip((t) => tips.push(t));
    runFrame();
    runFrame();
    expect(tips).toEqual([{ x1000: 500, y1000: 250 }, null]);
    expect(detectForVideo.mock.calls[0][0]).toBe(demo);
    expect(startCamera).not.toHaveBeenCalled();
    stop();
    runFrame();
    expect(tips).toHaveLength(2);
  });

  it("MediaPipe timestamps are strictly increasing even when the clock does not move", async () => {
    setFrameSource(document.createElement("canvas"));
    vi.spyOn(performance, "now").mockReturnValue(1000);
    detectForVideo.mockReturnValue({ landmarks: [] });
    const stop = await trackFingertip(() => {});
    runFrame(); runFrame(); runFrame();
    const ts = detectForVideo.mock.calls.map((c) => c[1] as number);
    expect(ts[1]).toBeGreaterThan(ts[0]);
    expect(ts[2]).toBeGreaterThan(ts[1]);
    stop();
    vi.mocked(performance.now).mockRestore();
  });

  it("skips MediaPipe passes while an owner tool photo is being captured, and resumes after", async () => {
    setFrameSource(document.createElement("canvas"));
    detectForVideo.mockClear();
    detectForVideo.mockReturnValue({ landmarks: [] });
    const stop = await trackFingertip(() => {});
    paused.on = true;
    runFrame(); runFrame();
    expect(detectForVideo).not.toHaveBeenCalled();
    paused.on = false;
    runFrame();
    expect(detectForVideo).toHaveBeenCalledTimes(1);
    stop();
  });

  it("falls back to the CPU when the GPU delegate fails, and loads the model only once", async () => {
    setFrameSource(document.createElement("canvas"));
    failGpu = true;
    const a = await trackFingertip(() => {});
    const b = await trackFingertip(() => {});
    expect(created).toEqual([{ delegate: "CPU" }]);
    a(); b();
  });

  it("a detector error reports 'no hand' instead of breaking the loop", async () => {
    setFrameSource(document.createElement("canvas"));
    detectForVideo.mockImplementationOnce(() => { throw new Error("gl lost"); }).mockReturnValueOnce(hand(0.1, 0.1));
    const tips: unknown[] = [];
    const stop = await trackFingertip((t) => tips.push(t));
    runFrame(); runFrame();
    expect(tips).toEqual([null, { x1000: 100, y1000: 100 }]);
    stop();
  });
});
