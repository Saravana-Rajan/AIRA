// frontend/app/test/camera.sceneGate.test.ts — describe mode sends a frame only when the scene changed or the owner walks.
import { describe, it, expect } from "vitest";
import { coarseGray, createSceneGate, meanAbsDiff, DIFF_THRESHOLD, GRID_W, GRID_H } from "../src/camera/sceneGate";

const W = 64;
const H = 48;
const flat = (v: number) => new Float32Array(W * H).fill(v);
/** Left half `a`, right half `b`. */
const halves = (a: number, b: number) => {
  const g = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) g[y * W + x] = x < W / 2 ? a : b;
  return g;
};

describe("coarseGray / meanAbsDiff", () => {
  it("box-averages onto the grid and ignores non-finite pixels", () => {
    const g = coarseGray(halves(0, 200), W, H);
    expect(g.length).toBe(GRID_W * GRID_H);
    expect(g[0]).toBe(0);
    expect(g[GRID_W - 1]).toBe(200);
    const nan = flat(100);
    nan[0] = Number.NaN;
    expect(coarseGray(nan, W, H)[0]).toBe(100);
    expect(coarseGray(new Float32Array(3), W, H).every((v) => v === 0)).toBe(true); // too short: all zero
  });

  it("mean absolute difference, infinite when the grids differ in size", () => {
    expect(meanAbsDiff(new Float32Array([0, 10]), new Float32Array([5, 20]))).toBe(7.5);
    expect(meanAbsDiff(new Float32Array(2), new Float32Array(3))).toBe(Number.POSITIVE_INFINITY);
  });
});

describe("scene gate", () => {
  it("sends the first frame, blocks an unchanged scene, sends a changed one", () => {
    const gate = createSceneGate();
    expect(gate.keep(flat(100), W, H)).toBe(true);
    expect(gate.keep(flat(100), W, H)).toBe(false);
    expect(gate.keep(flat(100 + DIFF_THRESHOLD - 1), W, H)).toBe(false);   // small change: same scene
    expect(gate.keep(halves(100, 160), W, H)).toBe(true);                  // half the view changed
    expect(gate.keep(halves(100, 160), W, H)).toBe(false);                 // now that is the reference
  });

  it("compares against the last SENT frame, so a slow drift is eventually sent", () => {
    const gate = createSceneGate({ threshold: 10 });
    expect(gate.keep(flat(100), W, H)).toBe(true);
    expect(gate.keep(flat(106), W, H)).toBe(false);
    expect(gate.keep(flat(112), W, H)).toBe(true);                         // 12 from the sent one, not 6 from the last
  });

  it("sends every frame while the owner walks, and the reference follows", () => {
    let walking = true;
    const gate = createSceneGate({ isWalking: () => walking });
    expect(gate.keep(flat(100), W, H)).toBe(true);
    expect(gate.keep(flat(100), W, H)).toBe(true);
    walking = false;
    expect(gate.keep(flat(100), W, H)).toBe(false);
  });

  it("reset forgets the reference; a throwing motion probe counts as not walking", () => {
    const gate = createSceneGate({ isWalking: () => { throw new Error("no sensor"); } });
    expect(gate.keep(flat(50), W, H)).toBe(true);
    expect(gate.keep(flat(50), W, H)).toBe(false);
    gate.reset();
    expect(gate.keep(flat(50), W, H)).toBe(true);
  });
});
