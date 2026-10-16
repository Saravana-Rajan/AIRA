// frontend/app/test/camera.lowlight.test.ts — the sustained-dark detector, the boost and the light reporter (pure).
import { describe, it, expect, vi } from "vitest";
import {
  createDarkDetector, levelFor, boostGainFor, boostPixels, DARK_LUMA, DIM_LUMA, HYSTERESIS, MIN_FRAMES, MIN_MS,
  MAX_GAIN,
} from "../src/camera/lowlight";
import { startLightWatch, LUMA_REPORT_DELTA, LUMA_REPORT_GAP_MS, TORCH_HEARTBEAT_MS } from "../src/camera/lightWatch";
import type { ClientMsg, TorchState } from "@aira/contracts";

describe("sustained-dark detector", () => {
  it("one dark frame is not darkness: the level changes only after MIN_FRAMES samples over MIN_MS", () => {
    const d = createDarkDetector();
    expect(d.level()).toBe("bright");
    expect(d.feed(5, 0)).toBeNull();
    expect(d.feed(5, 100)).toBeNull();
    expect(d.feed(5, 200)).toBeNull();
    expect(d.feed(5, 300)).toBeNull();             // 4 frames but only 300 ms: not yet
    expect(d.level()).toBe("bright");
    expect(d.feed(5, MIN_MS)).toBe("dark");          // 5th frame, 3 s after the first: dark
    expect(d.level()).toBe("dark");
    expect(MIN_FRAMES).toBe(4);
    expect(MIN_MS).toBe(3000);
  });

  it("a flicker (one bright frame among dark ones) restarts the count and never reports", () => {
    const d = createDarkDetector();
    d.feed(5, 0); d.feed(5, 1000); d.feed(5, 2000);
    expect(d.feed(200, 2500)).toBeNull();            // the view is lit for a frame: back to bright, count reset
    expect(d.feed(5, 3000)).toBeNull();
    expect(d.feed(5, 4000)).toBeNull();
    expect(d.feed(5, 5000)).toBeNull();
    expect(d.feed(5, 5500)).toBeNull();              // 4 frames since the flicker but only 2.5 s
    expect(d.feed(5, 6000)).toBe("dark");
  });

  it("hysteresis: leaving dark needs the luma to rise past DARK_LUMA + HYSTERESIS, not just DARK_LUMA", () => {
    const d = createDarkDetector({ initial: "dark" });
    const justAbove = DARK_LUMA + 1;
    for (let i = 0; i < 10; i++) expect(d.feed(justAbove, i * 1000)).toBeNull();
    expect(d.level()).toBe("dark");                  // hovering just above the bar stays dark: no flicker
    const clearlyAbove = DARK_LUMA + HYSTERESIS;
    const outs: Array<string | null> = [];
    for (let i = 0; i < 5; i++) outs.push(d.feed(clearlyAbove, 20_000 + i * 1000));
    expect(outs).toEqual([null, null, null, "dim", null]); // committed on the 4th frame, 3 s after the first
    expect(d.level()).toBe("dim");
    // and a bright view does not slip back to dim at DIM_LUMA - 1
    const b = createDarkDetector();
    expect(levelFor(DIM_LUMA - 1, "bright")).toBe("bright");
    expect(levelFor(DIM_LUMA - HYSTERESIS - 1, "bright")).toBe("dim");
    expect(levelFor(DIM_LUMA - 1, "dim")).toBe("dim");
    expect(b.level()).toBe("bright");
  });

  it("dim is between the two bars; a non-finite sample is ignored", () => {
    expect(levelFor(DARK_LUMA, "bright")).toBe("dim");
    expect(levelFor(DIM_LUMA, "dim")).toBe("bright");
    expect(levelFor(DARK_LUMA - 1, "bright")).toBe("dark");
    const d = createDarkDetector();
    expect(d.feed(Number.NaN, 0)).toBeNull();
    expect(d.level()).toBe("bright");
  });
});

describe("boost", () => {
  it("no gain at or above DIM_LUMA; up to MAX_GAIN toward the target below it", () => {
    expect(boostGainFor(DIM_LUMA)).toBe(1);
    expect(boostGainFor(200)).toBe(1);
    expect(boostGainFor(0)).toBe(1);
    expect(boostGainFor(55)).toBe(2);
    expect(boostGainFor(10)).toBe(MAX_GAIN);
  });

  it("brightens RGB in place, clips at 255, leaves alpha alone, and a gain of 1 is a no-op", () => {
    const px = new Uint8ClampedArray([40, 40, 40, 255, 250, 250, 250, 128]);
    boostPixels(px, 2, 1);
    expect(Array.from(px)).toEqual([80, 80, 80, 255, 255, 255, 255, 128]);
    const same = new Uint8ClampedArray([40, 40, 40, 255]);
    boostPixels(same, 1);
    expect(Array.from(same)).toEqual([40, 40, 40, 255]);
  });
});

function grid(luma: number): Float32Array {
  return new Float32Array(32 * 24).fill(luma);
}

describe("light watch", () => {
  function setup(torch: () => TorchState = () => "off") {
    const sent: ClientMsg[] = [];
    let now = 0;
    let probe: Float32Array | null = grid(150);
    const samples: Array<[number, string]> = [];
    const w = startLightWatch({
      probe: () => probe,
      send: (m) => { sent.push(m); return true; },
      torch,
      now: () => now,
      onSample: (l, level) => samples.push([l, level]),
      setInterval: () => 1,
      clearInterval: vi.fn(),
    });
    return { w, sent, samples, set: (g: Float32Array | null, t: number) => { probe = g; now = t; w.tick(); } };
  }

  it("the first sample is reported; then only a level change, a clear luma change or the torch heartbeat", () => {
    const s = setup();
    s.set(grid(150), 0);
    expect(s.sent).toEqual([{ t: "light", level: "bright", luma: 150, torch: "off" }]);
    s.set(grid(155), 500);                                   // a small change: nothing
    expect(s.sent).toHaveLength(1);
    s.set(grid(150 - LUMA_REPORT_DELTA), 600);               // a clear change, but inside the gap: nothing
    expect(s.sent).toHaveLength(1);
    s.set(grid(150 - LUMA_REPORT_DELTA), LUMA_REPORT_GAP_MS); // and after the gap: reported, same level
    expect(s.sent[1]).toEqual({ t: "light", level: "bright", luma: 130, torch: "off" });
    // darkness, sustained: the level change is reported the moment the detector commits it
    for (let i = 0; i < 4; i++) s.set(grid(5), 2000 + i * 500);
    expect(s.sent.at(-1)).toEqual({ t: "light", level: "bright", luma: 5, torch: "off" }); // the luma jump
    s.set(grid(5), 5001);
    expect(s.sent.at(-1)).toEqual({ t: "light", level: "dark", luma: 5, torch: "off" });
    expect(s.w.level()).toBe("dark");
    expect(s.samples.at(-1)).toEqual([5, "dark"]);
  });

  it("no picture, no report; a torch change reports at once; the heartbeat runs while the torch is on", () => {
    let torch: TorchState = "off";
    const s = setup(() => torch);
    s.set(null, 0);
    expect(s.sent).toEqual([]);
    expect(s.w.lastLuma()).toBeNull();
    s.set(grid(100), 100);
    expect(s.sent).toHaveLength(1);
    torch = "on";
    s.w.noteTorch("on");
    expect(s.sent.at(-1)).toEqual({ t: "light", level: "bright", luma: 100, torch: "on" });
    s.set(grid(100), 100 + TORCH_HEARTBEAT_MS - 1);
    expect(s.sent).toHaveLength(2);
    s.set(grid(100), 100 + TORCH_HEARTBEAT_MS);
    expect(s.sent).toHaveLength(3);
    torch = "off";
    s.set(grid(100), 100 + 2 * TORCH_HEARTBEAT_MS);          // no heartbeat with the torch off
    expect(s.sent).toHaveLength(3);
    s.w.stop();
    s.set(grid(5), 99_999);
    expect(s.sent).toHaveLength(3);
  });

  it("the reported luma is clamped to 0-255 with one decimal", () => {
    const s = setup();
    s.set(grid(300), 0);
    expect(s.sent[0]).toMatchObject({ luma: 255 });
    s.set(grid(12.345), LUMA_REPORT_GAP_MS);
    expect(s.sent[1]).toMatchObject({ luma: 12.3 });
  });
});
