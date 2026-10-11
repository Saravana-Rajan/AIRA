// Tilt hints for the chest mount. The phone sits upright in portrait, screen against the chest,
// back camera facing forward: DeviceOrientation beta ≈ 90°, gamma ≈ 0°. That is the CORRECT pose.
// We only speak when the smoothed reading has been clearly outside the band for a while, and never
// when the frames themselves look fine (the owner cannot see the screen; nagging him is worse than
// a slightly odd angle).
import { frameLooksGood as frameEvidence } from "./quality";
import { clientLog } from "../core/clientLog";

export type TiltHintKey = "tilt_down" | "tilt_up";

/** Why one sample did or did not become a spoken hint (diagnostics only; nothing acts on it). */
export type TiltReason = "no_reading" | "ok" | "hold" | "gap" | "frame_good" | "speak";

export interface TiltDecision {
  reason: TiltReason;
  /** The hint the smoothed reading points at (null in band or without a reading). */
  hint: TiltHintKey | null;
  rawBeta: number | null;
  rawGamma: number | null;
  /** Smoothed (EMA) readings the decision was made on. */
  beta: number | null;
  gamma: number | null;
  /** How long the smoothed reading has been out of band. */
  outMs: number;
  /** Ms since the last spoken tilt hint (Infinity before the first). */
  sinceHintMs: number;
}

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
  /** Told about every sample's verdict (diagnostics). Must be cheap: deviceorientation fires up to 60 Hz. */
  onDecision?: (d: TiltDecision) => void;
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
  const onDecision = opts.onDecision;
  let beta: number | null = null;
  let gamma: number | null = null;
  let outSince: number | null = null;
  let lastHintAt = Number.NEGATIVE_INFINITY;

  const reset = () => { beta = null; gamma = null; outSince = null; };
  // Diagnostics only: the verdict is decided before this is called, and a throwing listener changes nothing.
  const tell = (reason: TiltReason, hint: TiltHintKey | null, rawBeta: number | null, rawGamma: number | null, t: number) => {
    if (!onDecision) return;
    try {
      onDecision({
        reason, hint, rawBeta, rawGamma, beta, gamma,
        outMs: outSince == null ? 0 : t - outSince,
        sinceHintMs: t - lastHintAt,
      });
    } catch { /* never let a log break the judge */ }
  };

  return {
    reset,
    sample(b, g = null) {
      if (!finite(b)) { reset(); tell("no_reading", null, null, finite(g) ? g : null, now()); return null; }
      const t = now();
      beta = beta == null ? b : beta + alpha * (b - beta);
      if (finite(g)) gamma = gamma == null ? g : gamma + alpha * (g - gamma);
      else gamma = null;
      const rawG = finite(g) ? g : null;
      const h = tiltHint(beta, gamma);
      if (!h) { outSince = null; tell("ok", null, b, rawG, t); return null; }
      if (outSince == null) outSince = t;
      if (t - outSince < holdMs) { tell("hold", h, b, rawG, t); return null; }
      if (t - lastHintAt < gapMs) { tell("gap", h, b, rawG, t); return null; }
      if (frameGood(t)) { tell("frame_good", h, b, rawG, t); return null; }
      tell("speak", h, b, rawG, t);
      lastHintAt = t;
      outSince = t; // must be held again before the next hint
      return h;
    },
  };
}

/** The default judge's diagnostics: one "tilt" line a second to the server, plus every spoken hint. */
function logTiltDecision(d: TiltDecision): void {
  const round = (v: number | null) => (v == null ? null : Math.round(v * 10) / 10);
  clientLog("tilt", {
    reason: d.reason, hint: d.hint,
    rawBeta: round(d.rawBeta), rawGamma: round(d.rawGamma), beta: round(d.beta), gamma: round(d.gamma),
    outMs: Math.round(d.outMs), sinceHintMs: Number.isFinite(d.sinceHintMs) ? Math.round(d.sinceHintMs) : null,
  }, { minGapMs: d.reason === "speak" ? 0 : 1000 });
}

/** Listens to deviceorientation and speaks through onHint. No sensor or no permission: no events, no hints. */
export function startTiltHints(
  onHint: (h: TiltHintKey) => void,
  judge: TiltJudge = createTiltJudge({ onDecision: logTiltDecision }),
): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (e: Event) => {
    const o = e as DeviceOrientationEvent;
    const h = judge.sample(o.beta, o.gamma);
    if (h) onHint(h);
  };
  window.addEventListener("deviceorientation", handler);
  return () => window.removeEventListener("deviceorientation", handler);
}
