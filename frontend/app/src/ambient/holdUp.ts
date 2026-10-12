// frontend/app/src/ambient/holdUp.ts
// Hold-up detection, pure: "a hand holding an object near the centre and close, steady for a moment".
// Fed by the MediaPipe hand landmarker (the hand's box) and the bump guard's object detector (object boxes),
// all in [ymin, xmin, ymax, xmax] 0–1000. It decides WHEN one `identify_held` frame goes to the server:
// - the hand box overlaps a detected object (that object is the candidate), or the hand alone sits in the
//   central half of the frame and is large enough (close to the chest camera);
// - the candidate has been there for at least `stableMs` (600 ms);
// - at most one frame every `minGapMs` (3 s);
// - never again for the same object (same label and overlapping box) until it leaves the view for `leaveMs`
//   and comes back, or a different object replaces it.
import type { Box } from "../guidance/geometry";
import { iou } from "../walk/detections";

export type HoldUpObject = { label: string; box: Box };
export type HoldUpInput = { hand: Box | null; objects: readonly HoldUpObject[]; ts: number };
export type HoldUp = { label: string; box: Box; ts: number };

export interface HoldUpOptions {
  /** The candidate must be seen continuously this long before a frame goes. */
  stableMs?: number;
  /** At most one frame per this many ms. */
  minGapMs?: number;
  /** Central region as a fraction of each side (0.5 ⇒ the middle 50 % in x and y). */
  centre?: number;
  /** A hand alone counts only when its box covers at least this many 0–1000² units (60 000 ⇒ 6 % of the frame). */
  minHandArea?: number;
  /** Same object: equal label and at least this much overlap with the box it was sent with. */
  sameIou?: number;
  /** Nothing held for this long ⇒ the remembered object is forgotten (it may be announced again). */
  leaveMs?: number;
}

export const STABLE_MS = 600;
export const MIN_GAP_MS = 3000;
export const CENTRE = 0.5;
export const MIN_HAND_AREA = 60_000;
export const SAME_IOU = 0.3;
export const LEAVE_MS = 1500;
/** Label of a candidate made of the hand alone (no detector box under it). */
export const HAND_LABEL = "hand";

const area = (b: Box) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);

export function intersection(a: Box, b: Box): number {
  return area([Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])]);
}

/** The box's centre lies in the central `centre` fraction of the frame (both axes). */
export function isCentral(b: Box, centre = CENTRE): boolean {
  const lo = 500 - centre * 500;
  const hi = 500 + centre * 500;
  const cy = (b[0] + b[2]) / 2;
  const cx = (b[1] + b[3]) / 2;
  return cy >= lo && cy <= hi && cx >= lo && cx <= hi;
}

/** The object the hand holds (largest overlap with the hand box), or the hand itself when it is central and
 *  close, or null. Pure. */
export function candidateOf(input: Omit<HoldUpInput, "ts">, o: Required<Pick<HoldUpOptions, "centre" | "minHandArea">>): HoldUpObject | null {
  const hand = input.hand;
  if (!hand) return null;
  let best: HoldUpObject | null = null;
  let bestOverlap = 0;
  for (const obj of input.objects) {
    const overlap = intersection(hand, obj.box);
    if (overlap > bestOverlap) { best = obj; bestOverlap = overlap; }
  }
  if (best) return { label: best.label, box: best.box };
  if (isCentral(hand, o.centre) && area(hand) >= o.minHandArea) return { label: HAND_LABEL, box: hand };
  return null;
}

export function createHoldUpDetector(opts: HoldUpOptions = {}) {
  const o = {
    stableMs: opts.stableMs ?? STABLE_MS,
    minGapMs: opts.minGapMs ?? MIN_GAP_MS,
    centre: opts.centre ?? CENTRE,
    minHandArea: opts.minHandArea ?? MIN_HAND_AREA,
    sameIou: opts.sameIou ?? SAME_IOU,
    leaveMs: opts.leaveMs ?? LEAVE_MS,
  };
  let current: HoldUpObject | null = null;   // what is held right now
  let since = 0;                             // when it first appeared
  let lastSeenAt = -Infinity;                // when anything was last held
  let sent: HoldUpObject | null = null;      // the object the last frame was sent for
  let sentAt = -Infinity;

  const same = (a: HoldUpObject, b: HoldUpObject) =>
    a.label === b.label && (a.label === HAND_LABEL || iou(a.box, b.box) >= o.sameIou);

  return {
    /** One detector tick. Returns the hold-up to send a frame for, or null. */
    update(input: HoldUpInput): HoldUp | null {
      const cand = candidateOf(input, o);
      if (!cand) {
        current = null;
        if (input.ts - lastSeenAt > o.leaveMs) sent = null;   // it left: coming back counts as new
        return null;
      }
      lastSeenAt = input.ts;
      if (!current || current.label !== cand.label) {
        current = cand;
        since = input.ts;
      } else {
        current = cand;                                       // follow the box; the clock keeps running
      }
      if (input.ts - since < o.stableMs) return null;
      if (sent && same(sent, cand)) return null;               // already announced, still in hand
      if (input.ts - sentAt < o.minGapMs) return null;
      sent = cand;
      sentAt = input.ts;
      return { label: cand.label, box: cand.box, ts: input.ts };
    },
    /** What is held right now (for the overlay / tests). */
    current: () => current,
    reset(): void {
      current = null;
      sent = null;
      since = 0;
      lastSeenAt = -Infinity;
      sentAt = -Infinity;
    },
  };
}

export type HoldUpDetector = ReturnType<typeof createHoldUpDetector>;
