// Frame quality checks (pure). Run on a small downscaled copy of the frame.
export const DARK_LUMA = 40; // tune with bench/ clips on the team's test phone
export const BLUR_VAR = 60;

/** RGBA bytes to Rec.601 luma, one float per pixel. */
export function toGray(rgba: Uint8ClampedArray, w: number, h: number): Float32Array {
  const g = new Float32Array(w * h);
  for (let i = 0, p = 0; i < g.length; i++, p += 4) {
    g[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  }
  return g;
}

export function meanLuma(gray: Float32Array): number {
  if (gray.length === 0) return 0;
  let s = 0;
  for (let i = 0; i < gray.length; i++) s += gray[i];
  return s / gray.length;
}

/** Variance of the 4-neighbour Laplacian over interior pixels; low = blurry. */
export function laplacianVariance(gray: Float32Array, w: number, h: number): number {
  if (w < 3 || h < 3 || gray.length < w * h) return 0;
  let n = 0;
  let sum = 0;
  let sumSq = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const l = gray[i - w] + gray[i + w] + gray[i - 1] + gray[i + 1] - 4 * gray[i];
      n++;
      sum += l;
      sumSq += l * l;
    }
  }
  const mean = sum / n;
  return Math.max(0, sumSq / n - mean * mean);
}

/** "dark" wins over "blurry": a dark frame is always low-contrast too. */
export function qualityHint(gray: Float32Array, w: number, h: number): "dark" | "blurry" | null {
  if (meanLuma(gray) < DARK_LUMA) return "dark";
  if (laplacianVariance(gray, w, h) < BLUR_VAR) return "blurry";
  return null;
}
