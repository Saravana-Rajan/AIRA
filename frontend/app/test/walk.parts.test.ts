// frontend/app/test/walk.parts.test.ts — pure helpers behind the bump-guard detector loop
import { describe, it, expect } from "vitest";
import { toBox1000, fromMediaPipe, iou } from "../src/walk/detections";
import { createTracker } from "../src/walk/tracker";
import { floorEdgeDensity, createFloorCheck } from "../src/walk/floor";
import { bumpText } from "../src/walk/strings";

type B = [number, number, number, number];
const sq = (s: number, cx = 500, cy = 500): B => [cy - s / 2, cx - s / 2, cy + s / 2, cx + s / 2];
const det = (box: B, label = "chair", score = 0.8) => ({ label, score, box });

describe("detections", () => {
  it("converts a MediaPipe pixel box to [ymin, xmin, ymax, xmax] 0–1000", () => {
    expect(toBox1000({ originX: 64, originY: 120, width: 128, height: 60 }, 320, 240)).toEqual([500, 200, 750, 600]);
  });
  it("clamps boxes that spill outside the frame", () => {
    expect(toBox1000({ originX: -10, originY: 200, width: 400, height: 100 }, 320, 240)).toEqual([833, 0, 1000, 1000]);
  });
  it("keeps the top category, drops low scores and boxless detections", () => {
    const res = {
      detections: [
        { categories: [{ categoryName: "chair", score: 0.7 }], boundingBox: { originX: 0, originY: 0, width: 160, height: 120 }, keypoints: [] },
        { categories: [{ categoryName: "cup", score: 0.2 }], boundingBox: { originX: 0, originY: 0, width: 10, height: 10 }, keypoints: [] },
        { categories: [{ categoryName: "bench", score: 0.9 }], keypoints: [] },
        { categories: [], boundingBox: { originX: 160, originY: 120, width: 160, height: 120 }, keypoints: [] },
      ],
    };
    expect(fromMediaPipe(res as any, 320, 240, 0.4)).toEqual([{ label: "chair", score: 0.7, box: [0, 0, 500, 500] }]);
  });
  it("iou", () => {
    expect(iou([0, 0, 100, 100], [0, 0, 100, 100])).toBe(1);
    expect(iou([0, 0, 100, 100], [200, 200, 300, 300])).toBe(0);
    expect(iou([0, 0, 100, 100], [0, 50, 100, 150])).toBeCloseTo(1 / 3, 5);
  });
});

describe("tracker", () => {
  it("keeps a stable key for the same object across frames and grows its history", () => {
    const tr = createTracker();
    const a = tr.update([det(sq(100))], 0);
    const b = tr.update([det(sq(120))], 125);
    const c = tr.update([det(sq(140), "bench")], 250);     // label flicker does not break the track
    expect(b[0].key).toBe(a[0].key);
    expect(c[0].key).toBe(a[0].key);
    expect(c[0].boxes).toHaveLength(3);
    expect(c[0].ts).toEqual([0, 125, 250]);
  });
  it("gives a separate object its own key", () => {
    const tr = createTracker();
    tr.update([det(sq(100, 200))], 0);
    const out = tr.update([det(sq(100, 200)), det(sq(100, 800), "person")], 125);
    expect(out).toHaveLength(2);
    expect(out[0].key).not.toBe(out[1].key);
  });
  it("keeps only the last second of history", () => {
    const tr = createTracker({ historyMs: 1000 });
    let out: ReturnType<typeof tr.update> = [];
    for (let t = 0; t <= 2000; t += 250) out = tr.update([det(sq(100))], t);
    expect(out[0].ts[0]).toBeGreaterThanOrEqual(1000);
  });
  it("drops a track that was not seen for a while", () => {
    const tr = createTracker({ maxAgeMs: 500 });
    const a = tr.update([det(sq(100))], 0);
    tr.update([], 300);
    const b = tr.update([det(sq(100))], 1000);
    expect(b[0].key).not.toBe(a[0].key);
    expect(b[0].boxes).toHaveLength(1);
  });
});

function rgbaFlat(w: number, h: number, v = 128) {
  const a = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < a.length; i += 4) { a[i] = v; a[i + 1] = v; a[i + 2] = v; a[i + 3] = 255; }
  return a;
}
function paintChecker(a: Uint8ClampedArray, w: number, y0: number, y1: number, x0: number, x1: number) {
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const v = (x + y) % 2 === 0 ? 0 : 255; const p = (y * w + x) * 4;
    a[p] = v; a[p + 1] = v; a[p + 2] = v;
  }
}

describe("near-floor check (lower third, walking path)", () => {
  const W = 60, H = 45;
  it("a plain floor has no edges", () => {
    expect(floorEdgeDensity(rgbaFlat(W, H), W, H)).toBe(0);
  });
  it("a crate edge pattern in the lower third of the path counts; the same pattern up high does not", () => {
    const low = rgbaFlat(W, H); paintChecker(low, W, 30, 45, 18, 42);
    const high = rgbaFlat(W, H); paintChecker(high, W, 0, 15, 18, 42);
    const side = rgbaFlat(W, H); paintChecker(side, W, 30, 45, 0, 12);
    expect(floorEdgeDensity(low, W, H)).toBeGreaterThan(0.5);
    expect(floorEdgeDensity(high, W, H)).toBe(0);
    expect(floorEdgeDensity(side, W, H)).toBe(0);
  });
  it("tiny frames do not crash", () => {
    expect(floorEdgeDensity(new Uint8ClampedArray(4 * 4), 2, 2)).toBe(0);
  });
  it("triggers only after a sustained jump above the learned floor", () => {
    const fc = createFloorCheck({ minDensity: 0.12, ratio: 2.5, frames: 2, warmup: 3 });
    expect(fc.update(0.4)).toBe(false);                    // warm-up never triggers
    fc.reset();
    for (let i = 0; i < 3; i++) expect(fc.update(0.03)).toBe(false);
    expect(fc.update(0.4)).toBe(false);                    // single spike
    expect(fc.update(0.03)).toBe(false);
    expect(fc.update(0.4)).toBe(false);
    expect(fc.update(0.4)).toBe(true);                     // two in a row
    expect(fc.update(0.4)).toBe(true);                     // still there
  });
  it("never triggers below the absolute minimum, however clean the floor was", () => {
    const fc = createFloorCheck({ minDensity: 0.12, ratio: 2.5, frames: 1, warmup: 2 });
    fc.update(0.001); fc.update(0.001);
    expect(fc.update(0.1)).toBe(false);
  });
});

describe("bump phrases", () => {
  it("Stop for an obstacle straight ahead, Obstacle left / right otherwise, in ta/hi/en", () => {
    expect(bumpText("bump.stop", "centre", "en")).toBe("Stop");
    expect(bumpText("bump.left_right", "left", "en")).toBe("Obstacle left");
    expect(bumpText("bump.left_right", "right", "en")).toBe("Obstacle right");
    for (const lang of ["ta", "hi"] as const) {
      for (const [p, s] of [["bump.stop", "centre"], ["bump.left_right", "left"], ["bump.left_right", "right"]] as const) {
        const txt = bumpText(p, s, lang);
        expect(txt.length).toBeGreaterThan(0);
        expect(txt).not.toBe(bumpText(p, s, "en"));
      }
    }
  });
});
