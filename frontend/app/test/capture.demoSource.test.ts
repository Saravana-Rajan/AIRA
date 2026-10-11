// frontend/app/test/capture.demoSource.test.ts
// captureFrame() must draw from the demo frame source (/try) when one is set, without ever
// opening the camera, and fall back to the real camera after setFrameSource(null).
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "auto" }, lang: () => "en" },
}));
vi.mock("../src/core/app", () => ({ app }));
import { captureFrame, __setCaptureDeps, type FrameSource } from "../src/camera/capture";
import { setFrameSource } from "../src/camera/source";

function image(w: number, h: number) {
  const i = document.createElement("img");
  Object.defineProperty(i, "naturalWidth", { value: w });
  Object.defineProperty(i, "naturalHeight", { value: h });
  return i;
}

let drawn: unknown[][];
let made: number[][];
let getUserMedia: ReturnType<typeof vi.fn>;

function camera(w: number, h: number): FrameSource & { draw: ReturnType<typeof vi.fn> } {
  return { width: () => w, height: () => h, draw: vi.fn() };
}

beforeEach(() => {
  drawn = [];
  made = [];
  getUserMedia = vi.fn(async () => {
    throw new Error("camera must not be opened in demo mode");
  });
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  __setCaptureDeps({
    source: null,
    encoder: async () => "AAAA",
    makeCanvas: (w, h) => {
      made.push([w, h]);
      return {
        canvas: {} as HTMLCanvasElement,
        ctx: {
          drawImage: (...a: unknown[]) => drawn.push(a),
          getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4).fill(200) }),
        } as unknown as CanvasRenderingContext2D,
      };
    },
  });
});

afterEach(() => {
  setFrameSource(null);
  __setCaptureDeps({ source: null });
});

describe("captureFrame with a demo frame source", () => {
  it("draws the demo image at <=640 px and never asks for the camera", async () => {
    const img = image(1600, 1200);
    setFrameSource(img);
    const f = await captureFrame("invoice");
    expect(f).toMatchObject({ t: "frame", w: 640, h: 480, purpose: "invoice", jpeg: "AAAA" });
    expect(made).toEqual([[640, 480]]);
    expect(drawn).toEqual([[img, 0, 0, 640, 480]]);
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("wins over a camera that is already open", async () => {
    const cam = camera(1280, 720);
    __setCaptureDeps({ source: cam });
    const img = image(800, 600);
    setFrameSource(img);
    const f = await captureFrame("cash");
    expect(f).toMatchObject({ w: 640, h: 480, purpose: "cash" });
    expect(drawn).toEqual([[img, 0, 0, 640, 480]]);
    expect(cam.draw).not.toHaveBeenCalled();
  });

  it("uses the real camera again after setFrameSource(null) (Review Focus #1)", async () => {
    const cam = camera(1280, 720);
    __setCaptureDeps({ source: cam });
    setFrameSource(image(800, 600));
    setFrameSource(null);
    const f = await captureFrame("count");
    expect(f).toMatchObject({ w: 640, h: 360, purpose: "count" });
    expect(cam.draw).toHaveBeenCalledTimes(1);
    expect(drawn).toEqual([]);
  });

  it("reports no_frame for a demo video whose metadata has not loaded, without opening the camera", async () => {
    const v = document.createElement("video");
    Object.defineProperty(v, "videoWidth", { value: 0 });
    Object.defineProperty(v, "videoHeight", { value: 0 });
    setFrameSource(v);
    await expect(captureFrame("guide")).rejects.toMatchObject({ code: "no_frame" });
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});
