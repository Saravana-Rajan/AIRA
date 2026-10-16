// frontend/app/src/camera/orientation.ts
// The phone's physical orientation (portrait / the two landscapes / upside down), sent to the server so a
// dashboard watching on a laptop can turn the live picture to match the phone it cannot see rotate.
// camera/wire.ts wires it; the server relays it to the monitor (aira_live.monitor.orientation).
//
// Why not screen.orientation.angle: the app is LOCKED to portrait (public/manifest.webmanifest and
// twa/twa-manifest.json both "orientation": "portrait"), so the screen never turns and screen.orientation.angle
// stays 0 forever on the installed app. We read the real pose from the deviceorientation motion sensor instead
// (the same sensor camera/tilt.ts uses for the chest-mount hints), which reports the physical tilt even while the
// screen is locked. That sensor is gated behind the iOS motion permission, asked once inside the Start tap by
// core/motionPermission; a refusal just leaves orientation reporting off, silently.
import type { ClientMsg, OrientationAngle } from "@aira/contracts";
import { motionAccess } from "../core/motionPermission";

/**
 * The one DIRECTION knob. deviceorientation.gamma is the roll (left/right tilt, -90..90); its sign for a given
 * physical landscape is device- and reference-frame-dependent and cannot be verified without a real phone, so it
 * lives here as a single pair. The portrait / landscape / upside-down *detection* below is correct regardless of
 * this choice; only which of the two landscapes is "90" vs "270" depends on it. If a real phone flipped to
 * landscape shows the dashboard turned the wrong way, swap these two values (one line) — that, together with the
 * dashboard's own angle→360-angle knob in LivePage, covers both possible mismatches.
 */
export const ROLL_POS_ANGLE: OrientationAngle = 90; // gamma >= 0 (right edge down) -> this landscape
export const ROLL_NEG_ANGLE: OrientationAngle = 270; // gamma < 0 (left edge down)  -> the other landscape

/** Below this tilt (deg) on BOTH axes the phone is lying flat and its orientation cannot be told: keep the last. */
export const FLAT_DEG = 25;
/** A new angle must hold this long before it is reported, so jitter while turning never flickers the dashboard. */
export const ORIENT_HOLD_MS = 400;

function finite(v: number | null | undefined): v is number {
  return v != null && Number.isFinite(v);
}

/**
 * The screen orientation the phone's physical tilt implies, from beta (pitch, -180..180) and gamma (roll,
 * -90..90). Whichever axis is tilted further decides: a dominant roll means the phone is on its side (landscape),
 * otherwise it is upright/upside-down by the sign of the pitch. When it is lying flat (both axes near level) the
 * orientation is genuinely ambiguous, so `last` is kept. Pure and total; `last` is returned for any non-reading.
 */
export function angleFromTilt(
  beta: number | null | undefined,
  gamma: number | null | undefined,
  last: OrientationAngle = 0,
): OrientationAngle {
  if (!finite(beta) || !finite(gamma)) return last;
  const pitch = Math.abs(beta);
  const roll = Math.abs(gamma);
  if (pitch < FLAT_DEG && roll < FLAT_DEG) return last; // lying flat: cannot tell
  if (roll >= pitch) return gamma >= 0 ? ROLL_POS_ANGLE : ROLL_NEG_ANGLE; // on its side -> landscape
  return beta >= 0 ? 0 : 180; // upright portrait / upside-down portrait
}

export interface OrientationJudgeOptions {
  now?: () => number;
  holdMs?: number;
}

export interface OrientationJudge {
  /** The angle currently reported (0 until the first change commits). */
  readonly current: OrientationAngle;
  /** Feed one raw (beta, gamma); returns the new angle only when it has changed and held `holdMs`, else null. */
  sample(beta: number | null | undefined, gamma: number | null | undefined): OrientationAngle | null;
  reset(): void;
}

/**
 * Debounces the raw classification: a changed angle must stay put for `holdMs` before it is accepted, so the brief
 * diagonal readings a phone passes through while being turned (and sensor noise near the portrait/landscape
 * boundary) never flicker the dashboard. Starts committed to 0 (the dashboard's assumed default), so a phone that
 * is already rotated when wiring starts reports its real angle after one hold, and a return to portrait is a real
 * change that is reported too.
 */
export function createOrientationJudge(opts: OrientationJudgeOptions = {}): OrientationJudge {
  const now = opts.now ?? (() => Date.now());
  const holdMs = opts.holdMs ?? ORIENT_HOLD_MS;
  let current: OrientationAngle = 0;
  let candidate: OrientationAngle = 0;
  let since: number | null = null;
  return {
    get current() {
      return current;
    },
    reset() {
      candidate = current;
      since = null;
    },
    sample(beta, gamma) {
      const a = angleFromTilt(beta, gamma, current);
      if (a === current) {
        candidate = current;
        since = null;
        return null;
      }
      const t = now();
      if (a !== candidate || since == null) {
        candidate = a;
        since = t;
        return null;
      }
      if (t - since < holdMs) return null; // not held long enough yet
      current = a;
      candidate = a;
      since = null;
      return a;
    },
  };
}

type WinLike = {
  addEventListener?: (type: string, fn: (e: unknown) => void) => void;
  removeEventListener?: (type: string, fn: (e: unknown) => void) => void;
};

export interface OrientationDeps {
  /** Sends one `orientation` message (camera/wire.ts passes app.send). */
  send: (m: ClientMsg) => unknown;
  /** The event target to listen on (tests inject a fake). Defaults to the real window. */
  win?: WinLike;
  /** The global carrying DeviceOrientationEvent.requestPermission on iOS. Defaults to globalThis. */
  g?: typeof globalThis;
  /** The debounce judge (tests inject one with a fake clock). */
  judge?: OrientationJudge;
}

/**
 * Listens to the deviceorientation sensor and sends the phone's orientation whenever it changes (after the short
 * hold in the judge). No sensor, no permission, or a refusal on iOS: no events, nothing sent. On iOS the listener
 * attaches only after the orientation permission (asked inside the Start tap by core/motionPermission) comes back
 * "granted". Returns a stop() that removes the listener. Mirrors camera/tilt.ts's attach/permission shape.
 */
export function startOrientation(deps: OrientationDeps): () => void {
  const win = deps.win ?? (typeof window === "undefined" ? undefined : (window as unknown as WinLike));
  if (!win) return () => {};
  const g = deps.g ?? globalThis;
  const judge = deps.judge ?? createOrientationJudge();

  const handler = (e: unknown) => {
    const o = e as { beta?: number | null; gamma?: number | null };
    const angle = judge.sample(o.beta ?? null, o.gamma ?? null);
    if (angle != null) deps.send({ t: "orientation", angle });
  };

  let stopped = false;
  let attached = false;
  const attach = () => {
    if (stopped || attached) return;
    win.addEventListener?.("deviceorientation", handler);
    attached = true;
  };

  const DOE = (g as unknown as { DeviceOrientationEvent?: { requestPermission?: unknown } }).DeviceOrientationEvent;
  if (typeof DOE?.requestPermission === "function") {
    void motionAccess("orientation", g).then((ok) => {
      if (ok) attach();
    });
  } else {
    attach();
  }

  return () => {
    stopped = true;
    if (attached) win.removeEventListener?.("deviceorientation", handler);
    attached = false;
  };
}
