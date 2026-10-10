// apps/app/src/guidance/geometry.ts
import type { CueWord } from "../i18n/camera";

export type Tip = { x1000: number; y1000: number };
export type Point = [number, number];                    // [y, x] 0–1000 (Gemini convention)
export type Box = [number, number, number, number];      // [ymin, xmin, ymax, xmax]
export type Vec = { dx: number; dy: number; dist: number };
export const STOP_RADIUS = 40;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function vectorTo(target: Point, tip: Tip): Vec {
  const dx = target[1] - tip.x1000;      // + ⇒ move right
  const dy = target[0] - tip.y1000;      // + ⇒ move down (image y grows downward)
  return { dx, dy, dist: Math.hypot(dx, dy) };
}

/** pan: left/right; freqHz: higher when target is above; beepHz: faster when closer. */
export function toneParams(v: Vec): { pan: number; freqHz: number; beepHz: number } {
  return {
    pan: clamp(v.dx / 300, -1, 1),
    freqHz: clamp(660 - v.dy * 0.8, 330, 1100),
    beepHz: clamp(8 - v.dist / 100, 1, 8),
  };
}

export function microCue(v: Vec, inBox: boolean): CueWord {
  if (inBox || v.dist <= STOP_RADIUS) return "stop";
  if (Math.abs(v.dx) >= Math.abs(v.dy)) return v.dx > 0 ? "right" : "left";
  return v.dy > 0 ? "down" : "up";
}

export function insideBox(tip: Tip, box: Box | undefined, point: Point, radius = STOP_RADIUS): boolean {
  if (box) {
    const [ymin, xmin, ymax, xmax] = box;
    return tip.y1000 >= ymin && tip.y1000 <= ymax && tip.x1000 >= xmin && tip.x1000 <= xmax;
  }
  return Math.hypot(point[1] - tip.x1000, point[0] - tip.y1000) <= radius;
}

/** Speak a micro-cue only when it changes and has been stable for minMs (stop is immediate). */
export class DirectionHold {
  private current: CueWord | null = null;
  private candidate: CueWord | null = null;
  private since = 0;
  constructor(private minMs = 400) {}

  update(cue: CueWord, now: number): CueWord | null {
    if (cue === this.current) { this.candidate = null; return null; }
    if (this.current === null || cue === "stop") { this.current = cue; this.candidate = null; return cue; }
    if (cue !== this.candidate) { this.candidate = cue; this.since = now; return null; }
    if (now - this.since >= this.minMs) { this.current = cue; this.candidate = null; return cue; }
    return null;
  }

  reset() { this.current = null; this.candidate = null; this.since = 0; }
}

/**
 * True exactly once, after `dwellMs` inside the target. A loss of the fingertip
 * (an `update(false)` sample) of at most `graceMs`, measured from the last inside
 * sample to the next one, does not restart the window; a longer loss does.
 * The caller must feed `update(false, now)` on frames where the tip is missing.
 */
export class DwellTimer {
  private enteredAt: number | null = null;
  private lastInside = 0;
  private sawOutside = false;
  private fired = false;
  constructor(private dwellMs = 500, private graceMs = 150) {}

  update(inside: boolean, now: number): boolean {
    if (this.fired) return false;
    if (inside) {
      const lostTooLong = this.sawOutside && now - this.lastInside > this.graceMs;
      if (this.enteredAt === null || lostTooLong) this.enteredAt = now;
      this.lastInside = now;
      this.sawOutside = false;
      if (now - this.enteredAt >= this.dwellMs) { this.fired = true; return true; }
      return false;
    }
    this.sawOutside = true;
    if (this.enteredAt !== null && now - this.lastInside > this.graceMs) this.enteredAt = null;
    return false;
  }

  reset() { this.enteredAt = null; this.lastInside = 0; this.sawOutside = false; this.fired = false; }
}
