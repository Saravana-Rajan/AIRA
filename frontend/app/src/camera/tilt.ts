// DeviceOrientation beta: phone upright portrait ≈ 90° (rear camera looks straight ahead).
// A chest mount should look slightly down at the counter/fridge: 55°–85°.
export const TILT_MIN = 55;
export const TILT_MAX = 85;
export const TILT_HINT_GAP_MS = 3000;

export function tiltHint(betaDeg: number | null): "tilt_down" | "tilt_up" | null {
  if (betaDeg == null || !Number.isFinite(betaDeg)) return null; // null and undefined (no sensor)
  if (betaDeg > TILT_MAX) return "tilt_down";
  if (betaDeg < TILT_MIN) return "tilt_up";
  return null;
}

/** Listens to deviceorientation; calls onHint at most once per 3 s. Returns an unsubscribe fn. */
export function startTiltHints(onHint: (h: "tilt_down" | "tilt_up") => void): () => void {
  if (typeof window === "undefined") return () => {};
  let last = Number.NEGATIVE_INFINITY;
  const handler = (e: Event) => {
    const h = tiltHint((e as DeviceOrientationEvent).beta);
    if (!h) return;
    const now = Date.now();
    if (now - last <= TILT_HINT_GAP_MS) return;
    last = now;
    onHint(h);
  };
  window.addEventListener("deviceorientation", handler);
  return () => window.removeEventListener("deviceorientation", handler);
}
