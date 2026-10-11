// frontend/app/test/camera.capture.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
// The real core/app.ts singleton is replaced so frames go to a spy instead of a socket.
const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" } },
}));
vi.mock("../src/core/app", () => ({ app }));
import { captureFrame, startContinuous, fitWithin, CameraError, __setCaptureDeps, setQualityListener, MAX_B64 } from "../src/camera/capture";

const fakeCanvas = () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D });

beforeEach(() => {
  vi.useRealTimers();
  (app.send as any).mockClear();
  __setCaptureDeps({
    source: { width: () => 1280, height: () => 720, draw: () => {} },
    encoder: async () => "AAAA",
    makeCanvas: fakeCanvas,
  });
});

describe("fitWithin", () => {
  it("scales the longest side to 640 and never upscales", () => {
    expect(fitWithin(1280, 720)).toEqual({ w: 640, h: 360 });
    expect(fitWithin(720, 1280)).toEqual({ w: 360, h: 640 });
    expect(fitWithin(320, 240)).toEqual({ w: 320, h: 240 });
  });
});

describe("captureFrame", () => {
  it("builds an exact frame message", async () => {
    const f = await captureFrame("count");
    expect(f).toMatchObject({ t: "frame", jpeg: "AAAA", w: 640, h: 360, purpose: "count" });
    expect(Object.keys(f).sort()).toEqual(["h", "id", "jpeg", "purpose", "t", "ts", "w"]);
    expect(typeof f.id).toBe("string");
    expect(typeof f.ts).toBe("number");
  });
  it("gives every frame a unique id", async () => {
    const a = await captureFrame("ask");
    const b = await captureFrame("ask");
    expect(a.id).not.toBe(b.id);
  });
  it("retries at lower quality then rejects oversize frames", async () => {
    const big = "A".repeat(MAX_B64 + 1);
    const enc = vi.fn(async () => big);
    __setCaptureDeps({ encoder: enc });
    await expect(captureFrame("invoice")).rejects.toMatchObject({ code: "too_large" });
    expect(enc).toHaveBeenCalledTimes(2);
  });
  it("uses the lower-quality retry when it fits", async () => {
    const big = "A".repeat(MAX_B64 + 1);
    const enc = vi.fn(async (_c: HTMLCanvasElement, q: number) => (q >= 0.8 ? big : "BBBB"));
    __setCaptureDeps({ encoder: enc });
    const f = await captureFrame("invoice");
    expect(f.jpeg).toBe("BBBB");
    expect(enc).toHaveBeenCalledTimes(2);
  });
  it("rejects with no_frame when the video has no size yet", async () => {
    __setCaptureDeps({ source: { width: () => 0, height: () => 0, draw: () => {} } });
    await expect(captureFrame("ask")).rejects.toBeInstanceOf(CameraError);
    await expect(captureFrame("ask")).rejects.toMatchObject({ code: "no_frame" });
  });
  it("rejects with no_canvas when no 2d context is available", async () => {
    __setCaptureDeps({ makeCanvas: () => ({ canvas: {} as HTMLCanvasElement, ctx: null }) });
    await expect(captureFrame("ask")).rejects.toMatchObject({ code: "no_canvas" });
  });
});

describe("startContinuous", () => {
  it("sends frames at the requested fps until stopped", async () => {
    vi.useFakeTimers();
    const stop = startContinuous("guide", 2);
    await vi.advanceTimersByTimeAsync(1100);
    stop();
    const n = (app.send as any).mock.calls.length;
    expect(n).toBeGreaterThanOrEqual(2);
    expect((app.send as any).mock.calls[0][0]).toMatchObject({ t: "frame", purpose: "guide" });
    await vi.advanceTimersByTimeAsync(2000);
    expect((app.send as any).mock.calls.length).toBe(n);
  });
  it("keeps running when a capture fails", async () => {
    vi.useFakeTimers();
    let fail = true;
    __setCaptureDeps({ encoder: async () => { if (fail) { fail = false; throw new CameraError("no_canvas"); } return "AAAA"; } });
    const stop = startContinuous("guide", 4);
    await vi.advanceTimersByTimeAsync(600);
    stop();
    expect((app.send as any).mock.calls.length).toBeGreaterThanOrEqual(1);
  });
});

describe("quality listener", () => {
  it("checks every 3rd frame and reports a dark hint", async () => {
    const dark = (w: number, h: number) => ({
      canvas: {} as HTMLCanvasElement,
      ctx: { getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4).fill(5) }) } as unknown as CanvasRenderingContext2D,
    });
    __setCaptureDeps({ makeCanvas: dark });
    const hints: string[] = [];
    setQualityListener((h) => hints.push(h));
    await captureFrame("ask");
    await captureFrame("ask");
    expect(hints).toEqual([]);
    await captureFrame("ask");
    expect(hints).toEqual(["dark"]);
    setQualityListener(null);
    for (let i = 0; i < 3; i++) await captureFrame("ask");
    expect(hints).toEqual(["dark"]);
  });
});
