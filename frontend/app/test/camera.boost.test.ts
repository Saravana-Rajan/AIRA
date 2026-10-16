// frontend/app/test/camera.boost.test.ts — the low-light boost touches only uploaded frames: it is applied on the
// canvas path after the raw quality check, never to the monitor preview, and never to the bump guard's frames.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" }, socket: { bufferedAmount: vi.fn(() => 0) } },
}));
vi.mock("../src/core/app", () => ({ app }));
const boostSpy = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../src/camera/lowlight", async (importOriginal) => {
  const real = await importOriginal<typeof import("../src/camera/lowlight")>();
  return { ...real, boostPixels: (...a: Parameters<typeof real.boostPixels>) => { boostSpy.calls++; return real.boostPixels(...a); } };
});

import { captureFrame, setFrameBoost, frameBoost, setQualityListener, __setCaptureDeps } from "../src/camera/capture";

/** A fake canvas whose image data is the full frame size (so the gray copy is real, not NaN past 4x4). */
function canvasWith(value: number) {
  const state = { data: new Uint8ClampedArray(0) };
  const put = vi.fn();
  const makeCanvas = (w: number, h: number) => {
    if (state.data.length !== w * h * 4) {
      state.data = new Uint8ClampedArray(w * h * 4).fill(value);
      for (let i = 3; i < state.data.length; i += 4) state.data[i] = 255;
    }
    const ctx = { getImageData: () => ({ data: state.data, width: w, height: h }), putImageData: put } as unknown as CanvasRenderingContext2D;
    return { canvas: {} as HTMLCanvasElement, ctx };
  };
  return { get data() { return state.data; }, put, makeCanvas };
}

beforeEach(() => {
  boostSpy.calls = 0;
  setFrameBoost(null);
  __setCaptureDeps({ source: { width: () => 1280, height: () => 720, draw: () => {} }, encoder: async () => "AAAA", worker: () => null });
});
afterEach(() => { setFrameBoost(null); setQualityListener(null); });

describe("upload boost", () => {
  it("off by default: frames are encoded untouched", async () => {
    const c = canvasWith(30);
    __setCaptureDeps({ makeCanvas: c.makeCanvas });
    await captureFrame("ask");
    expect(frameBoost()).toBe(1);
    expect(c.put).not.toHaveBeenCalled();
    expect(boostSpy.calls).toBe(0);
    expect(c.data[0]).toBe(30);
  });

  it("with a gain, tool and live frames are brightened before encoding; the preview is not", async () => {
    setFrameBoost(2);
    expect(frameBoost()).toBe(2);
    const c = canvasWith(30);
    __setCaptureDeps({ makeCanvas: c.makeCanvas });
    await captureFrame("ask");
    expect(c.put).toHaveBeenCalledTimes(1);
    expect(c.data[0]).toBeGreaterThan(30);
    const live = canvasWith(30);
    __setCaptureDeps({ makeCanvas: live.makeCanvas });
    await captureFrame("live");
    expect(live.put).toHaveBeenCalledTimes(1);
    const preview = canvasWith(30);
    __setCaptureDeps({ makeCanvas: preview.makeCanvas });
    await captureFrame("preview");
    expect(preview.put).not.toHaveBeenCalled();
    expect(preview.data[0]).toBe(30);
    setFrameBoost(0.5);           // never a darkening "boost"
    expect(frameBoost()).toBe(1);
  });

  it("the quality hint judges the RAW pixels: a boosted dark frame still says dark", async () => {
    setFrameBoost(2.5);
    const hints: string[] = [];
    setQualityListener((h) => hints.push(h));
    const c = canvasWith(10);   // pitch dark; boosted it would read 25+
    __setCaptureDeps({ makeCanvas: c.makeCanvas });
    for (let i = 0; i < 3; i++) await captureFrame("ask");   // every 3rd tool frame is checked
    expect(hints).toEqual(["dark"]);
  });

  it("a boosted frame takes the canvas path, never the encode worker (which gets raw pixels)", async () => {
    const worker = { dead: false, encode: vi.fn(async () => ({ jpeg: "WORK", gray: null })) };
    __setCaptureDeps({
      source: { width: () => 1280, height: () => 720, draw: () => {}, grab: async () => ({ close() {} } as never) },
      worker: () => worker as never,
      makeCanvas: canvasWith(30).makeCanvas,
    });
    expect((await captureFrame("ask")).jpeg).toBe("WORK");
    setFrameBoost(2);
    expect((await captureFrame("ask")).jpeg).toBe("AAAA");
    expect(worker.encode).toHaveBeenCalledTimes(1);
  });
});

describe("the bump guard's frames are raw", () => {
  it("walk/start.ts grabs the <video> itself and never goes through capture's encode or the boost", () => {
    const root = resolve(__dirname, "../src/walk");
    const start = readFileSync(resolve(root, "start.ts"), "utf8");
    const engine = readFileSync(resolve(root, "engine.ts"), "utf8");
    expect(start).toContain("createImageBitmap(video");
    for (const src of [start, engine]) {
      expect(src).not.toMatch(/lowlight|boostPixels|setFrameBoost|captureFrame|captureFrameIf/);
    }
  });
});
