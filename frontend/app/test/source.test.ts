import { describe, it, expect, afterEach } from "vitest";
import { setFrameSource, getFrameSource, pickSource, overrideSource } from "../src/camera/source";

function video(w: number, h: number) {
  const v = document.createElement("video");
  Object.defineProperty(v, "videoWidth", { value: w });
  Object.defineProperty(v, "videoHeight", { value: h });
  return v;
}
function image(w: number, h: number) {
  const i = document.createElement("img");
  Object.defineProperty(i, "naturalWidth", { value: w });
  Object.defineProperty(i, "naturalHeight", { value: h });
  return i;
}

describe("frame source override", () => {
  afterEach(() => setFrameSource(null));

  it("uses the camera when no override is set", () => {
    const cam = video(1280, 720);
    expect(pickSource(cam)).toEqual({ el: cam, w: 1280, h: 720 });
  });

  it("uses the demo image when set, with its natural size", () => {
    const img = image(1600, 1200);
    setFrameSource(img);
    expect(getFrameSource()).toBe(img);
    expect(pickSource(video(1280, 720))).toEqual({ el: img, w: 1600, h: 1200 });
  });

  it("setFrameSource(null) restores the real camera (Review Focus #1)", () => {
    setFrameSource(video(640, 480));
    setFrameSource(null);
    const cam = video(1280, 720);
    expect(pickSource(cam).el).toBe(cam);
    expect(getFrameSource()).toBeNull();
  });

  it("uses a canvas override with its pixel size", () => {
    const c = document.createElement("canvas");
    c.width = 320;
    c.height = 240;
    setFrameSource(c);
    expect(pickSource(video(1280, 720))).toEqual({ el: c, w: 320, h: 240 });
  });

  it("reads the demo video's size at capture time, not when it was set", () => {
    const v = document.createElement("video");
    let w = 0;
    Object.defineProperty(v, "videoWidth", { get: () => w });
    Object.defineProperty(v, "videoHeight", { get: () => (w ? 720 : 0) });
    setFrameSource(v);
    w = 1280; // metadata loaded after setFrameSource
    expect(pickSource(video(640, 480))).toEqual({ el: v, w: 1280, h: 720 });
  });
});

describe("overrideSource adapter (for captureFrame's FrameSource deps)", () => {
  afterEach(() => setFrameSource(null));

  it("is null when no demo media is active, so the real camera is used", () => {
    expect(overrideSource()).toBeNull();
  });

  it("reports the override size and draws it scaled", () => {
    const img = image(1600, 1200);
    setFrameSource(img);
    const src = overrideSource()!;
    expect(src.width()).toBe(1600);
    expect(src.height()).toBe(1200);
    const calls: unknown[][] = [];
    const ctx = { drawImage: (...a: unknown[]) => calls.push(a) } as unknown as CanvasRenderingContext2D;
    src.draw(ctx, 640, 480);
    expect(calls).toEqual([[img, 0, 0, 640, 480]]);
  });

  it("is null again after setFrameSource(null) (Review Focus #1)", () => {
    setFrameSource(image(10, 10));
    setFrameSource(null);
    expect(overrideSource()).toBeNull();
  });
});
