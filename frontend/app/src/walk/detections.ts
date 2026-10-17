// frontend/app/src/walk/detections.ts
// MediaPipe object detections → project boxes [ymin, xmin, ymax, xmax], normalised 0–1000. Pure.
import type { Box } from "../guidance/geometry";

export type RawDet = { label: string; score: number; box: Box };
export const MIN_SCORE = 0.4;

type PixelBox = { originX: number; originY: number; width: number; height: number };
type MpDetection = { categories: { categoryName?: string; displayName?: string; score: number }[]; boundingBox?: PixelBox };

const to1000 = (v: number, size: number) => Math.min(1000, Math.max(0, Math.round((v / size) * 1000)));

/** Pixel box (x, y, width, height) in a w×h image → [ymin, xmin, ymax, xmax] 0–1000. Never swaps x and y. */
export function toBox1000(bb: PixelBox, w: number, h: number): Box {
  return [to1000(bb.originY, h), to1000(bb.originX, w), to1000(bb.originY + bb.height, h), to1000(bb.originX + bb.width, w)];
}

/** Keeps detections with a box and a top category scoring at least `minScore`. */
export function fromMediaPipe(res: { detections?: MpDetection[] } | null | undefined, w: number, h: number, minScore = MIN_SCORE): RawDet[] {
  const out: RawDet[] = [];
  if (!w || !h) return out;
  for (const d of res?.detections ?? []) {
    const top = [...(d.categories ?? [])].sort((a, b) => b.score - a.score)[0];
    if (!top || !d.boundingBox || top.score < minScore) continue;
    out.push({ label: top.categoryName || top.displayName || "object", score: top.score, box: toBox1000(d.boundingBox, w, h) });
  }
  return out;
}

const area = (b: Box) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);

export function iou(a: Box, b: Box): number {
  const inter = area([Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])]);
  const union = area(a) + area(b) - inter;
  return union > 0 ? inter / union : 0;
}
