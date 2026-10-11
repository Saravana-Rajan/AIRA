// frontend/app/src/walk/floor.ts
// "Near-floor obstacle" check for crates on the floor (the object model does not know crates).
// Looks at the lower third of the frame inside the walking path (centre 40%): a plain floor has few
// strong edges; a crate edge or slats add many. Pure. Thresholds need tuning on the shop floor.
import type { Box } from "../guidance/geometry";

/** Box reported for a floor obstacle: lower part of the walking path (height > 35% ⇒ warns). */
export const FLOOR_BOX: Box = [600, 300, 1000, 700];
/** Luma step (0–255) that counts as a strong edge. */
export const EDGE_T = 40;

/** Fraction (0–1) of strong-edge pixels in the lower third × centre 40% of an RGBA frame. */
export function floorEdgeDensity(rgba: Uint8ClampedArray, w: number, h: number, edge = EDGE_T): number {
  if (w < 3 || h < 3) return 0;
  const y0 = Math.floor((h * 2) / 3);
  const x0 = Math.floor(w * 0.3);
  const x1 = Math.ceil(w * 0.7);
  const luma = (x: number, y: number) => {
    const p = (y * w + x) * 4;
    return 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  };
  let n = 0;
  let strong = 0;
  for (let y = y0; y < h - 1; y++) {
    for (let x = x0; x < x1 - 1; x++) {
      const c = luma(x, y);
      const g = Math.abs(luma(x + 1, y) - c) + Math.abs(luma(x, y + 1) - c);
      n++;
      if (g > edge) strong++;
    }
  }
  return n ? strong / n : 0;
}

export interface FloorCheckOptions {
  minDensity?: number;  // never trigger below this density
  ratio?: number;       // ...and only when density ≥ ratio × the learned floor level
  frames?: number;      // consecutive frames needed
  warmup?: number;      // frames used to learn the floor before any trigger
  alpha?: number;       // learning rate of the floor level
}

/** Learns the usual floor edge level and reports a sustained jump above it. */
export function createFloorCheck(opts: FloorCheckOptions = {}) {
  const minDensity = opts.minDensity ?? 0.12;
  const ratio = opts.ratio ?? 2.5;
  const frames = opts.frames ?? 2;
  const warmup = opts.warmup ?? 3;
  const alpha = opts.alpha ?? 0.1;
  let seen = 0;
  let base = 0;
  let streak = 0;

  return {
    update(density: number): boolean {
      if (!Number.isFinite(density)) return false;
      if (seen < warmup) {
        base = seen === 0 ? density : base + (density - base) / (seen + 1);
        seen++;
        return false;
      }
      const high = density >= minDensity && density >= base * ratio;
      if (!high) {
        streak = 0;
        base += alpha * (density - base);
        return false;
      }
      streak++;
      return streak >= frames;
    },
    reset() { seen = 0; base = 0; streak = 0; },
  };
}
