// frontend/app/src/ui/boxDraw.ts
// Draws boxes over a camera preview (the phone's "Show boxes" setting). Pure: takes a 2D context-like
// object, so the drawing is tested without a real canvas. Colours per the monitor spec: danger red, object
// blue, found green, hand orange. MediaPipe boxes are thin, ER 2 boxes thick with a label tag.
import type { Box } from "@aira/contracts";

export const BOX_COLOURS = Object.freeze({
  danger: "#e53935",   // the bump guard flagged it in the walking path
  object: "#1e88e5",   // MediaPipe (on-device) or an ER 2 object
  found: "#43a047",    // the target ER 2 found
  hand: "#fb8c00",     // the fingertip (hand guidance)
} as const);
export type BoxKind = keyof typeof BOX_COLOURS;

export const THIN_STROKE = 2;
export const THICK_STROKE = 3;
export const FOUND_STROKE = 4;
export const TAG_FONT = "bold 12px system-ui, sans-serif";
const TAG_PAD = 4;
const TAG_H = 16;

export type DrawBox = {
  label: string;
  box: Box;
  kind: BoxKind;
  /** MediaPipe: thin, no tag unless `score`; ER 2: thick with a tag. */
  source: "mediapipe" | "er2";
  score?: number;
};

/** The parts of CanvasRenderingContext2D the drawing uses (a recorder in tests). */
export interface Ctx2D {
  clearRect(x: number, y: number, w: number, h: number): void;
  strokeRect(x: number, y: number, w: number, h: number): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  fillText(text: string, x: number, y: number): void;
  measureText(text: string): { width: number };
  strokeStyle: string | CanvasGradient | CanvasPattern;
  fillStyle: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  font: string;
  textBaseline: CanvasTextBaseline;
}

/** [ymin, xmin, ymax, xmax] 0-1000 → canvas pixels {x, y, w, h}. */
export function toPixels(box: Box, w: number, h: number): { x: number; y: number; w: number; h: number } {
  const x = (box[1] / 1000) * w;
  const y = (box[0] / 1000) * h;
  return { x, y, w: Math.max(0, ((box[3] - box[1]) / 1000) * w), h: Math.max(0, ((box[2] - box[0]) / 1000) * h) };
}

/** "Aavin milk 92%" when a score is known, with a check mark once found. */
export function tagText(b: Pick<DrawBox, "label" | "kind" | "score">): string {
  const pct = typeof b.score === "number" && Number.isFinite(b.score) ? ` ${Math.round(b.score * 100)}%` : "";
  return `${b.kind === "found" ? "✓ " : ""}${b.label}${pct}`;
}

/** Clears the canvas and draws every box: MediaPipe first (thin), then ER 2 on top (thick, tagged). */
export function drawBoxes(ctx: Ctx2D, w: number, h: number, boxes: readonly DrawBox[], opts: { tagAll?: boolean } = {}): void {
  ctx.clearRect(0, 0, w, h);
  const ordered = [...boxes].sort((a, b) => (a.source === "er2" ? 1 : 0) - (b.source === "er2" ? 1 : 0));
  for (const b of ordered) {
    const colour = BOX_COLOURS[b.kind];
    const r = toPixels(b.box, w, h);
    ctx.strokeStyle = colour;
    ctx.lineWidth = b.kind === "found" ? FOUND_STROKE : b.source === "er2" ? THICK_STROKE : THIN_STROKE;
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    // thin boxes stay quiet unless they are a danger (or the big phone preview asks for every label)
    if (!opts.tagAll && b.source !== "er2" && b.kind !== "danger") continue;
    const text = tagText(b);
    ctx.font = TAG_FONT;
    ctx.textBaseline = "top";
    const tw = ctx.measureText(text).width + TAG_PAD * 2;
    const ty = r.y - TAG_H >= 0 ? r.y - TAG_H : r.y;   // tag above the box, or inside its top-left corner
    ctx.fillStyle = colour;
    ctx.fillRect(r.x, ty, tw, TAG_H);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, r.x + TAG_PAD, ty + 2);
  }
}
