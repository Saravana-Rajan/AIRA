// frontend/app/test/camera.capture.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
// The real core/app.ts singleton is replaced so frames go to a spy instead of a socket.
const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" }, socket: { bufferedAmount: vi.fn(() => 0) } },
}));
vi.mock("../src/core/app", () => ({ app }));
import { captureFrame, captureFrameIf, startContinuous, fitWithin, frameSpec, CameraError, __setCaptureDeps, setQualityListener, MAX_B64, PREVIEW_MAX_SIDE, PREVIEW_QUALITY, PREVIEW_MAX_BUFFERED, socketBusy, PRIORITY_MAX_SIDE, PRIORITY_QUALITY, isPriorityPurpose, holdBackground, backgroundHeld, holdsBackground, COUNT_QUALITY, BACKGROUND_MAX_BUFFERED } from "../src/camera/capture";

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
    const f = await captureFrame("guide");
    expect(f).toMatchObject({ t: "frame", jpeg: "AAAA", w: 640, h: 360, purpose: "guide" });
    expect(Object.keys(f).sort()).toEqual(["captureTs", "h", "id", "jpeg", "purpose", "t", "ts", "w"]);
    expect(typeof f.id).toBe("string");
    expect(typeof f.ts).toBe("number");
  });
  it("stamps captureTs when the picture is grabbed, before encoding; ts after", async () => {
    let clock = 1_000;
    const spy = vi.spyOn(Date, "now").mockImplementation(() => clock);
    try {
      __setCaptureDeps({ encoder: async () => { clock += 450; return "AAAA"; } }); // a slow encode
      const f = await captureFrame("count");
      expect(f.captureTs).toBe(1_000);
      expect(f.ts).toBe(1_450);
    } finally {
      spy.mockRestore();
    }
  });
  it("a monitor preview frame is small (360 px) and light (quality 0.45); other purposes keep 640 px / 0.8", async () => {
    expect(PREVIEW_MAX_SIDE).toBe(360);
    expect(PREVIEW_QUALITY).toBe(0.45);
    expect(frameSpec("preview")).toEqual({ maxSide: 360, quality: 0.45 });
    expect(frameSpec("guide")).toEqual({ maxSide: 640, quality: 0.8 });
    expect(frameSpec("walk")).toEqual({ maxSide: 1024, quality: 0.75 });
    const enc = vi.fn(async (_c: HTMLCanvasElement, _q: number) => "AAAA");
    __setCaptureDeps({ encoder: enc });
    const p = await captureFrame("preview");
    expect(p).toMatchObject({ t: "frame", w: 360, h: 203, purpose: "preview" });
    expect(enc).toHaveBeenLastCalledWith(expect.anything(), 0.45);
    const g = await captureFrame("guide");
    expect(g).toMatchObject({ w: 640, h: 360, purpose: "guide" });
    expect(enc).toHaveBeenLastCalledWith(expect.anything(), 0.8);
  });
  it("preview frames never trigger a camera quality hint", async () => {
    const hints: string[] = [];
    setQualityListener((h) => hints.push(h));
    __setCaptureDeps({ makeCanvas: () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(0) }) } as unknown as CanvasRenderingContext2D }) });
    for (let i = 0; i < 6; i++) await captureFrame("preview");
    expect(hints).toEqual([]);
    setQualityListener(null);
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
  it("sends a preview frame at once and drops it, unencoded, while the socket is still busy with the last one", async () => {
    vi.useFakeTimers();
    const enc = vi.fn(async () => "AAAA");
    __setCaptureDeps({ encoder: enc });
    const buffered = app.socket.bufferedAmount as ReturnType<typeof vi.fn>;
    buffered.mockReturnValue(0);
    const frames = (purpose = "preview") => (app.send as any).mock.calls.map((c: any[]) => c[0]).filter((m: any) => m.t === "frame" && m.purpose === purpose);
    expect(socketBusy()).toBe(false);
    const stop = startContinuous("preview", 8);
    await vi.advanceTimersByTimeAsync(10);
    expect(frames()).toHaveLength(1);
    buffered.mockReturnValue(PREVIEW_MAX_BUFFERED + 1);   // the network has not taken the last frame yet
    expect(socketBusy()).toBe(true);
    const encodes = enc.mock.calls.length;
    await vi.advanceTimersByTimeAsync(500);                 // four ticks: nothing encoded, nothing queued
    expect(enc.mock.calls.length).toBe(encodes);
    expect(frames()).toHaveLength(1);
    buffered.mockReturnValue(0);
    await vi.advanceTimersByTimeAsync(130);
    expect(frames()).toHaveLength(2);
    stop();
    // other purposes never drop: Gemini's frames are not a preview
    buffered.mockReturnValue(PREVIEW_MAX_BUFFERED + 1);
    const stopGuide = startContinuous("guide", 4);
    await vi.advanceTimersByTimeAsync(10);
    expect(frames("guide")).toHaveLength(1);
    stopGuide();
    buffered.mockReturnValue(0);
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

describe("owner questions come first", () => {
  it("ask / identify / point frames go at 1280 px and quality 0.9", async () => {
    expect(PRIORITY_MAX_SIDE).toBe(1280);
    expect(PRIORITY_QUALITY).toBe(0.9);
    for (const p of ["ask", "identify", "point"] as const) {
      expect(isPriorityPurpose(p)).toBe(true);
      expect(frameSpec(p)).toEqual({ maxSide: 1280, quality: 0.9 });
    }
    expect(isPriorityPurpose("count")).toBe(true);
    expect(isPriorityPurpose("describe")).toBe(false);
  });

  it("count frames are 1024 px at quality 0.7 (about 110 KB: a busy phone encodes and sends them fast)", async () => {
    expect(COUNT_QUALITY).toBe(0.7);
    expect(frameSpec("count")).toEqual({ maxSide: 1024, quality: 0.7 });
    const enc = vi.fn(async (_c: HTMLCanvasElement, _q: number) => "AAAA");
    __setCaptureDeps({ encoder: enc, source: { width: () => 1080, height: () => 1920, draw: () => {} } });
    const f = await captureFrame("count");
    expect(f).toMatchObject({ t: "frame", w: 576, h: 1024, purpose: "count" });
    expect(enc).toHaveBeenCalledTimes(1);
    expect(enc).toHaveBeenLastCalledWith(expect.anything(), 0.7);
  });

  it("every tool frame request (ask / count / identify / point / walk / invoice / cash) holds background frames; others do not", () => {
    for (const p of ["ask", "count", "identify", "point", "walk", "invoice", "cash"] as const) expect(holdsBackground(p)).toBe(true);
    for (const p of ["live", "describe", "preview", "guide"] as const) expect(holdsBackground(p)).toBe(false);
  });

  it("describe frames are never queued on a congested socket (they would sit ahead of an owner frame)", async () => {
    vi.useFakeTimers();
    const buffered = app.socket.bufferedAmount as any;
    const frames = () => (app.send as any).mock.calls.map((c: any[]) => c[0]).filter((m: any) => m.t === "frame" && m.purpose === "describe");
    buffered.mockReturnValue(BACKGROUND_MAX_BUFFERED + 1);
    const stop = startContinuous("describe", 4);
    await vi.advanceTimersByTimeAsync(600);
    expect(frames()).toHaveLength(0);
    buffered.mockReturnValue(0);
    await vi.advanceTimersByTimeAsync(300);
    expect(frames().length).toBeGreaterThanOrEqual(1);
    stop();
  });

  it("describe and preview frames pause while a priority capture holds the camera", async () => {
    vi.useFakeTimers();
    const frames = (purpose: string) => (app.send as any).mock.calls.map((c: any[]) => c[0]).filter((m: any) => m.t === "frame" && m.purpose === purpose);
    const release = holdBackground();
    expect(backgroundHeld()).toBe(true);
    const stopD = startContinuous("describe", 4);
    const stopG = startContinuous("guide", 4);
    await vi.advanceTimersByTimeAsync(600);
    expect(frames("describe")).toHaveLength(0);
    expect(frames("guide").length).toBeGreaterThanOrEqual(1);   // hand guidance is never paused
    release();
    release();                                                  // a second release is harmless
    expect(backgroundHeld()).toBe(false);
    await vi.advanceTimersByTimeAsync(300);
    expect(frames("describe").length).toBeGreaterThanOrEqual(1);
    stopD();
    stopG();
  });

  it("a describe frame that was being encoded when the hold began is dropped, never sent after the ask", async () => {
    vi.useFakeTimers();
    let finish: (v: string) => void = () => {};
    __setCaptureDeps({ encoder: () => new Promise<string>((r) => { finish = r; }) });
    const frames = () => (app.send as any).mock.calls.map((c: any[]) => c[0]).filter((m: any) => m.t === "frame" && m.purpose === "describe");
    const stop = startContinuous("describe", 1 / 3);
    await vi.advanceTimersByTimeAsync(5);
    const release = holdBackground();
    finish("AAAA");
    await vi.advanceTimersByTimeAsync(5);
    expect(frames()).toHaveLength(0);
    release();
    stop();
  });
});

describe("keep gate (describe mode)", () => {
  const sized = (w: number, h: number) => ({
    canvas: {} as HTMLCanvasElement,
    ctx: { getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4).fill(120) }) } as unknown as CanvasRenderingContext2D,
  });

  it("captureFrameIf hands the gray frame to keep and returns null without encoding when refused", async () => {
    const enc = vi.fn(async () => "AAAA");
    __setCaptureDeps({ makeCanvas: sized, encoder: enc });
    const seen: Array<[number, number, number]> = [];
    const refuse = (gray: Float32Array, w: number, h: number) => { seen.push([gray.length, w, h]); return false; };
    expect(await captureFrameIf("describe", refuse)).toBeNull();
    expect(seen).toEqual([[640 * 360, 640, 360]]);
    expect(enc).not.toHaveBeenCalled();
    const f = await captureFrameIf("describe", () => true);
    expect(f).toMatchObject({ t: "frame", purpose: "describe", jpeg: "AAAA" });
    expect(await captureFrameIf("ask")).toMatchObject({ purpose: "ask" });
  });

  it("startContinuous at 1/3 fps sends only the frames the gate keeps", async () => {
    vi.useFakeTimers();
    __setCaptureDeps({ makeCanvas: sized });
    // app.send also carries the "log" diagnostics line for each frame; count the frames alone.
    const frames = () => (app.send as any).mock.calls.map((c: any[]) => c[0]).filter((m: any) => m.t === "frame");
    let allow = true;
    const stop = startContinuous("describe", 1 / 3, () => allow);
    await vi.advanceTimersByTimeAsync(50);
    expect(frames().length).toBe(1);
    allow = false;
    await vi.advanceTimersByTimeAsync(6100);                 // two more ticks, both refused
    expect(frames().length).toBe(1);
    allow = true;
    await vi.advanceTimersByTimeAsync(3000);
    expect(frames().length).toBe(2);
    expect(frames()[1]).toMatchObject({ t: "frame", purpose: "describe" });
    stop();
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
