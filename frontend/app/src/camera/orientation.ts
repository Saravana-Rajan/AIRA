// frontend/app/src/camera/orientation.ts
// The phone's screen orientation (screen.orientation.angle), sent to the server so a dashboard watching on a
// laptop can turn the live picture to match the phone it cannot see rotate. camera/wire.ts wires it; the server
// relays it to the monitor (aira_live.monitor.orientation). Pure: the window is injected so tests drive it with
// a fake. Needs no permission (unlike the chest-mount tilt sensor in camera/tilt.ts).
import type { ClientMsg, OrientationAngle } from "@aira/contracts";

/** The four angles we report; any other reading is rounded to the nearest of these. */
const ANGLES: readonly OrientationAngle[] = [0, 90, 180, 270];

type OrientationSource = {
  angle?: number;
  addEventListener?: (type: string, fn: () => void) => void;
  removeEventListener?: (type: string, fn: () => void) => void;
};
type Win = {
  screen?: { orientation?: OrientationSource };
  orientation?: number;                                   // legacy iOS Safari: -90 | 0 | 90 | 180
  matchMedia?: (q: string) => { matches: boolean };
  addEventListener?: (type: string, fn: () => void) => void;
  removeEventListener?: (type: string, fn: () => void) => void;
};

const defaultWin = (): Win | undefined => (typeof window === "undefined" ? undefined : (window as unknown as Win));

/** Round any degrees to the nearest of 0/90/180/270 (so -90 becomes 270). */
export function normalizeAngle(deg: number): OrientationAngle {
  if (!Number.isFinite(deg)) return 0;
  const q = ((Math.round(deg / 90) % 4) + 4) % 4; // 0..3
  return ANGLES[q];
}

/** The phone's current screen orientation, from the best source available (0 when none is). */
export function readAngle(win: Win | undefined = defaultWin()): OrientationAngle {
  const a = win?.screen?.orientation?.angle;
  if (typeof a === "number") return normalizeAngle(a);
  if (typeof win?.orientation === "number") return normalizeAngle(win.orientation);
  try {
    if (win?.matchMedia?.("(orientation: landscape)").matches) return 90;
  } catch {
    /* no matchMedia: fall through to portrait */
  }
  return 0;
}

export interface OrientationDeps {
  /** Sends one `orientation` message (camera/wire.ts passes app.send). */
  send: (m: ClientMsg) => unknown;
  win?: Win;
}

/**
 * Sends the phone's screen orientation whenever it changes, and once at the start when the phone already sits in
 * a rotated orientation. Upright portrait (angle 0) is the dashboard's assumed default, so a portrait start sends
 * nothing (and the initial send would drop anyway if the socket is not open yet); a rotation back to portrait is
 * a real change and is still sent. The same angle is never sent twice in a row. Returns a stop() that removes the
 * listeners.
 */
export function startOrientation(deps: OrientationDeps): () => void {
  const win = deps.win ?? defaultWin();
  if (!win) return () => {};
  // Start from 0 (the dashboard's assumed default): only a real deviation or change ever goes on the wire.
  let last: OrientationAngle = 0;
  const report = () => {
    const angle = readAngle(win);
    if (angle === last) return;
    last = angle;
    deps.send({ t: "orientation", angle });
  };
  const source = win.screen?.orientation;
  source?.addEventListener?.("change", report);
  win.addEventListener?.("orientationchange", report); // fallback where screen.orientation has no events
  report(); // the initial value, for the seed
  return () => {
    source?.removeEventListener?.("change", report);
    win.removeEventListener?.("orientationchange", report);
  };
}
