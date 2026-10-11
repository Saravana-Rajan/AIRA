// Tilt hints for the chest mount. The phone sits upright in portrait, screen against the chest,
// back camera facing forward: DeviceOrientation beta ≈ 90°, gamma ≈ 0°. That is the CORRECT pose.
// We only speak when the smoothed reading has been clearly outside the band for a while, and never
// when the frames themselves look fine (the owner cannot see the screen; nagging him is worse than
// a slightly odd angle).
import { frameLooksGood as frameEvidence } from "./quality";

export type TiltHintKey = "tilt_down" | "tilt_up";

/** OK band for beta (pitch): 60°–120° covers slouching and leaning on the counter. */
export const TILT_MIN = 60;
export const TILT_MAX = 120;
/** Beyond this roll (gamma) the pitch reading is unreliable and there is no key to say "straighten". */
export const ROLL_MAX = 25;
/** The smoothed reading must stay out of band this long before we say anything. */
export const TILT_HOLD_MS = 1500;
/** Minimum gap between two tilt hints (matches the other hint loops). */
export const TILT_HINT_GAP_MS = 8000;
/** EMA weight of a new sample; deviceorientation fires ~60 Hz on Android, ~10 Hz in some browsers. */
export const TILT_SMOOTH_ALPHA = 0.3;

function finite(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

/** Pure decision for one (already smoothed) reading. null means "fine" or "do not know". */
export function tiltHint(betaDeg: number | null | undefined, gammaDeg: number | null | undefined = null): TiltHintKey | null {
  if (!finite(betaDeg)) return null; // no sensor, no permission, no reading
  if (finite(gammaDeg) && Math.abs(gammaDeg) > ROLL_MAX) return null;
  if (betaDeg > TILT_MAX) return "tilt_down";
  if (betaDeg < TILT_MIN) return "tilt_up";
  return null;
}

export interface TiltJudgeOptions {
  now?: () => number;
  holdMs?: number;
  gapMs?: number;
  /** Frame evidence: when true, the camera sees fine and no tilt hint is given. */
  frameLooksGood?: (now: number) => boolean;
  alpha?: number;
}

export interface TiltJudge {
  /** Feed one raw sensor sample; returns a hint only when it should be spoken right now. */
  sample(betaDeg: number | null | undefined, gammaDeg?: number | null | undefined): TiltHintKey | null;
  reset(): void;
}

/** Stateful judge: smooths readings, requires the bad pose to be held, throttles, defers to frame evidence. */
export function createTiltJudge(opts: TiltJudgeOptions = {}): TiltJudge {
  const now = opts.now ?? (() => Date.now());
  const holdMs = opts.holdMs ?? TILT_HOLD_MS;
  const gapMs = opts.gapMs ?? TILT_HINT_GAP_MS;
  const frameGood = opts.frameLooksGood ?? frameEvidence;
  const alpha = opts.alpha ?? TILT_SMOOTH_ALPHA;
  let beta: number | null = null;
  let gamma: number | null = null;
  let outSince: number | null = null;
  let lastHintAt = Number.NEGATIVE_INFINITY;

  const reset = () => { beta = null; gamma = null; outSince = null; };

  return {
    reset,
    sample(b, g = null) {
      if (!finite(b)) { reset(); return null; }
      const t = now();
      beta = beta == null ? b : beta + alpha * (b - beta);
      if (finite(g)) gamma = gamma == null ? g : gamma + alpha * (g - gamma);
      else gamma = null;
      const h = tiltHint(beta, gamma);
      if (!h) { outSince = null; return null; }
      if (outSince == null) outSince = t;
      if (t - outSince < holdMs) return null;
      if (t - lastHintAt < gapMs) return null;
      if (frameGood(t)) return null;
      lastHintAt = t;
      outSince = t; // must be held again before the next hint
      return h;
    },
  };
}

/** Listens to deviceorientation and speaks through onHint. No sensor or no permission: no events, no hints. */
export function startTiltHints(onHint: (h: TiltHintKey) => void, judge: TiltJudge = createTiltJudge()): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (e: Event) => {
    const o = e as DeviceOrientationEvent;
    const h = judge.sample(o.beta, o.gamma);
    if (h) onHint(h);
  };
  window.addEventListener("deviceorientation", handler);
  return () => window.removeEventListener("deviceorientation", handler);
}
