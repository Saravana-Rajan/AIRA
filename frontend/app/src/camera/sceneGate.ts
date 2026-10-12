// frontend/app/src/camera/sceneGate.ts
// Scene-change gate for the low-rate `describe` stream (describe mode). A frame goes to the server only when
// the picture changed since the last frame SENT (mean absolute luma difference of a coarse gray grid above a
// threshold) or when the accelerometer says the owner is walking. The first frame always goes. Pure: the
// gate gets the gray frame from capture.ts and never touches the camera.

/** Coarse grid the comparison runs on: cheap, and small movements of the phone do not count as a new scene. */
export const GRID_W = 32;
export const GRID_H = 24;
/** Mean absolute luma difference (0-255) over the grid that counts as "the scene changed". */
export const DIFF_THRESHOLD = 12;

export interface SceneGateOptions {
  threshold?: number;
  /** The motion sensor: true while the owner walks (every frame goes then). Absent = frame difference only. */
  isWalking?: () => boolean;
}

export interface SceneGate {
  /** true when this frame should be sent; the reference updates only for sent frames. */
  keep(gray: Float32Array, w: number, h: number): boolean;
  /** Forget the reference: the next frame goes. */
  reset(): void;
}

/** Box-averages a gray frame onto a gw x gh grid. */
export function coarseGray(gray: Float32Array, w: number, h: number, gw = GRID_W, gh = GRID_H): Float32Array {
  const out = new Float32Array(gw * gh);
  if (w <= 0 || h <= 0 || gray.length < w * h) return out;
  for (let gy = 0; gy < gh; gy++) {
    const y0 = Math.floor((gy * h) / gh);
    const y1 = Math.max(y0 + 1, Math.floor(((gy + 1) * h) / gh));
    for (let gx = 0; gx < gw; gx++) {
      const x0 = Math.floor((gx * w) / gw);
      const x1 = Math.max(x0 + 1, Math.floor(((gx + 1) * w) / gw));
      let s = 0;
      let n = 0;
      for (let y = y0; y < y1 && y < h; y++) {
        for (let x = x0; x < x1 && x < w; x++) {
          const v = gray[y * w + x];
          if (Number.isFinite(v)) { s += v; n++; }
        }
      }
      out[gy * gw + gx] = n ? s / n : 0;
    }
  }
  return out;
}

/** Mean absolute difference of two equal-length grids (Infinity when the sizes differ). */
export function meanAbsDiff(a: Float32Array, b: Float32Array): number {
  if (a.length !== b.length || a.length === 0) return Number.POSITIVE_INFINITY;
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / a.length;
}

export function createSceneGate(opts: SceneGateOptions = {}): SceneGate {
  const threshold = opts.threshold ?? DIFF_THRESHOLD;
  let ref: Float32Array | null = null;
  return {
    keep(gray, w, h) {
      const grid = coarseGray(gray, w, h);
      let walking = false;
      try { walking = opts.isWalking?.() === true; } catch { walking = false; }
      const changed = ref === null || walking || meanAbsDiff(grid, ref) >= threshold;
      if (changed) ref = grid;
      return changed;
    },
    reset() { ref = null; },
  };
}
