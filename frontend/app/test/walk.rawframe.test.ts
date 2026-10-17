// frontend/app/test/walk.rawframe.test.ts — with a low-light boost active, the bump guard still detects on the raw
// camera picture: its frame comes straight from the <video>, and the boost code never runs for it.
import { describe, it, expect, vi, afterEach } from "vitest";

const video = { readyState: 4, videoWidth: 1280, videoHeight: 720 };
const boostSpy = vi.hoisted(() => ({ calls: 0 }));
vi.mock("../src/camera/lowlight", async (importOriginal) => {
  const real = await importOriginal<typeof import("../src/camera/lowlight")>();
  return { ...real, boostPixels: (...a: Parameters<typeof real.boostPixels>) => { boostSpy.calls++; return real.boostPixels(...a); } };
});
vi.mock("../src/core/app", () => ({ app: { earcon: vi.fn(), speak: vi.fn(), speakUrgent: vi.fn(), send: vi.fn(), settings: { lang: "auto" }, lang: () => "en" } }));
vi.mock("../src/camera/capture", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/camera/capture")>()),
  startCamera: vi.fn(async () => video),
  detectionPaused: () => false,
}));

import { grabFrame } from "../src/walk/start";
import { setFrameBoost, frameBoost } from "../src/camera/capture";

const bitmap = { width: 320, height: 180, close: vi.fn() };

afterEach(() => { setFrameBoost(null); vi.unstubAllGlobals(); });

describe("bump guard input with the boost on", () => {
  it("grabs the raw <video> frame; the boost is never applied to it", async () => {
    setFrameBoost(2.5);
    expect(frameBoost()).toBe(2.5);
    vi.stubGlobal("createImageBitmap", vi.fn(async () => bitmap));
    const g = await grabFrame();
    expect(g?.frame).toBe(bitmap);
    expect(createImageBitmap).toHaveBeenCalledWith(video, expect.objectContaining({ resizeWidth: 320 }));
    expect(boostSpy.calls).toBe(0);
  });
});
