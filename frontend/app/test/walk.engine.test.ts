// frontend/app/test/walk.engine.test.ts — the bump-guard engine's per-frame detect diagnostics
import { describe, it, expect, vi } from "vitest";
import { createEngine, DETECT_FLOOR } from "../src/walk/engine";
import { MIN_SCORE } from "../src/walk/detections";

// A fake @mediapipe/tasks-vision: FilesetResolver + an ObjectDetector whose detectForVideo we control.
function fakeMp(detectForVideo: (f: unknown, t: number) => unknown) {
  const detector = { detectForVideo: vi.fn(detectForVideo), close: vi.fn() };
  const mp = {
    FilesetResolver: { forVisionTasks: vi.fn(async () => ({})) },
    ObjectDetector: { createFromOptions: vi.fn(async () => detector) },
  };
  return { mp, detector };
}

const frame = { width: 320, height: 240 } as unknown as ImageBitmap;
const det = (name: string, score: number) =>
  ({ categories: [{ categoryName: name, score }], boundingBox: { originX: 0, originY: 0, width: 32, height: 24 } });

describe("bump-guard engine diagnostics", () => {
  it("keeps the raw pre-filter count and top score, and only acts on >= MIN_SCORE", async () => {
    const { mp } = fakeMp(() => ({ detections: [det("chair", 0.8), det("cup", MIN_SCORE - 0.05)] }));
    const engine = await createEngine(mp as never, { module: false });
    const r = engine.run(frame, 1);
    expect(r.raw).toBe(2);                              // both came back from the model
    expect(r.top).toBeCloseTo(0.8);                     // top score, even of a dropped one
    expect(r.dets.map((d) => d.label)).toEqual(["chair"]);  // only the one at/above MIN_SCORE survives
    expect(r.err).toBeUndefined();
  });

  it("surfaces a per-frame detect throw instead of swallowing it into an empty result", async () => {
    const { mp } = fakeMp(() => { throw new Error("detectForVideo exploded"); });
    const engine = await createEngine(mp as never, { module: false });
    const r = engine.run(frame, 1);
    expect(r.err).toMatch(/exploded/);
    expect(r.dets).toEqual([]);
    expect(r.raw).toBe(0);
  });

  it("runs the detector at a floor below MIN_SCORE so near-misses show up in the diagnostic", async () => {
    const { mp } = fakeMp(() => ({ detections: [] }));
    await createEngine(mp as never, { module: false });
    expect(DETECT_FLOOR).toBeLessThan(MIN_SCORE);
    expect(mp.ObjectDetector.createFromOptions).toHaveBeenCalledWith(
      expect.anything(), expect.objectContaining({ scoreThreshold: DETECT_FLOOR }));
  });
});
