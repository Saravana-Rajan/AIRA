// apps/app/src/walk/bump.ts
// Shop-floor bump guard: pure decision logic (no DOM, no network).
// Boxes use the project convention [ymin, xmin, ymax, xmax], normalised 0–1000.
import type { Box } from "../guidance/geometry";

export type Side = "left" | "right" | "centre";
export type BumpPhrase = "bump.stop" | "bump.left_right";

/** Walking path = centre 40% of the frame width. */
export const PATH_XMIN = 300;
export const PATH_XMAX = 700;
/** Warn when time-to-contact is below this (seconds). */
export const TTC_WARN_S = 1.2;
/** ...or when the box fills more than 35% of the frame height. */
export const HEIGHT_WARN = 350;
/** Server-raised urgency widens both thresholds (never narrows them). */
export const TTC_WARN_URGENT_S = 2.0;
export const HEIGHT_WARN_URGENT = 250;
/** One warning per object per 1.5 s. */
export const COOLDOWN_MS = 1500;
/** `navigator.vibrate` pattern for a warning. */
export const WARN_VIBRATE: readonly number[] = [80, 40, 80];
/** Frame timestamp → vibrate call must stay under this. */
export const ALERT_BUDGET_MS = 150;
/** Box centre within ±50 of the middle ⇒ "Stop"; otherwise "Obstacle left / right". */
const CENTRE_HALF_WIDTH = 50;

export type BumpDetection = {
  key: string;      // stable per-object id (tracker id or label+slot) for the cooldown
  box: Box;
  ttcS: number;     // from timeToContact(); Infinity when not approaching
  ts: number;       // frame timestamp, ms
};

export type BumpState = {
  lastWarnAt: Record<string, number>;   // per-object time of the last warning, ms
  handNearTarget: boolean;              // hand guidance is active and the hand is near the target
  urgentUntil: number;                  // server-raised urgency deadline, ms (0 = none)
};

export type BumpWarning = { key: string; phrase: BumpPhrase; side: Side; vibrate: number[] };

export const initialBumpState = (): BumpState => ({ lastWarnAt: {}, handNearTarget: false, urgentUntil: 0 });

const validBox = (b: Box) => b[2] > b[0] && b[3] > b[1];
const scale = (b: Box) => Math.sqrt(Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]));

/**
 * Time to contact in seconds from a track of boxes for one object.
 * For an object approaching at constant speed the apparent size s ∝ 1/Z, so
 * TTC = Z / v = s / (ds/dt). ds/dt is a least-squares slope over all samples.
 * Returns Infinity when the object is not getting closer or there is too little data.
 */
export function timeToContact(boxes: Box[], ts: number[]): number {
  const n = Math.min(boxes.length, ts.length);
  if (n < 2) return Infinity;
  let mt = 0, ms = 0;
  const s: number[] = [];
  for (let i = 0; i < n; i++) { s.push(scale(boxes[i])); mt += ts[i] / 1000; ms += s[i]; }
  mt /= n; ms /= n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    const dt = ts[i] / 1000 - mt;
    num += dt * (s[i] - ms);
    den += dt * dt;
  }
  if (den <= 0) return Infinity;
  const slope = num / den;            // size units per second
  const last = s[n - 1];
  if (!(slope > 0) || !(last > 0)) return Infinity;
  return last / slope;
}

/** True when the box overlaps the centre 40% of the frame (x 300..700). */
export function inWalkingPath(box: Box): boolean {
  return validBox(box) && box[3] > PATH_XMIN && box[1] < PATH_XMAX;
}

/** Which way the obstacle is, from the box centre. */
export function obstacleSide(box: Box): Side {
  const cx = (box[1] + box[3]) / 2;
  if (cx < 500 - CENTRE_HALF_WIDTH) return "left";
  if (cx > 500 + CENTRE_HALF_WIDTH) return "right";
  return "centre";
}

/**
 * Decide whether this detection must trigger a warning now. Pure: it never changes
 * `state`; call recordWarn() after acting on a warning.
 */
export function shouldWarn(state: BumpState, det: BumpDetection): BumpWarning | null {
  if (!inWalkingPath(det.box)) return null;
  const urgent = det.ts < state.urgentUntil;
  const ttcLimit = urgent ? TTC_WARN_URGENT_S : TTC_WARN_S;
  const heightLimit = urgent ? HEIGHT_WARN_URGENT : HEIGHT_WARN;
  const height = det.box[2] - det.box[0];
  if (!(det.ttcS < ttcLimit || height > heightLimit)) return null;
  if (state.handNearTarget) return null;
  const last = state.lastWarnAt[det.key];
  if (last !== undefined && det.ts - last < COOLDOWN_MS) return null;
  const side = obstacleSide(det.box);
  return { key: det.key, phrase: side === "centre" ? "bump.stop" : "bump.left_right", side, vibrate: [...WARN_VIBRATE] };
}

/** New state with the warning for `det.key` recorded at `det.ts` (starts its cooldown). */
export function recordWarn(state: BumpState, det: BumpDetection): BumpState {
  return { ...state, lastWarnAt: { ...state.lastWarnAt, [det.key]: det.ts } };
}

/** The server may only RAISE urgency: an earlier deadline never shortens the current one. */
export function raiseUrgency(state: BumpState, untilMs: number): BumpState {
  return untilMs > state.urgentUntil ? { ...state, urgentUntil: untilMs } : state;
}
