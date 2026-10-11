// frontend/app/src/walk/motion.ts
// Walking detection from the phone's accelerometer (DeviceMotionEvent.accelerationIncludingGravity).
// Steps are peaks of the acceleration magnitude above the gravity baseline; the owner is "walking" when
// at least 2 steps landed in the last 2.5 s. The counter is pure (injected samples); startMotionSensor()
// attaches it to the browser and copes with no sensor and the iOS permission prompt.

export type MotionSample = { t: number; x: number; y: number; z: number };

/** Steps must be at least this far apart: cadence capped at 2.5 Hz. */
export const STEP_MIN_INTERVAL_MS = 400;
/** Walking = at least WALK_MIN_STEPS steps in the last WALK_WINDOW_MS. */
export const WALK_WINDOW_MS = 2500;
export const WALK_MIN_STEPS = 2;
/** Magnitude must rise this much (m/s²) above the slow baseline to start a peak... */
export const PEAK_RISE = 1.5;
/** ...and fall back below this much above it before the next peak counts (hysteresis). */
export const PEAK_RESET = 0.6;
/** Baseline (gravity + slow tilt) follows the magnitude with this rate per sample. */
const BASE_ALPHA = 0.02;

export interface StepCounter {
  push(s: MotionSample): void;
  /** Steps in the last WALK_WINDOW_MS before `at`. */
  steps(at: number): number;
  isWalking(at: number): boolean;
  reset(): void;
}

export function createStepCounter(): StepCounter {
  let base: number | null = null;
  let armed = true;          // true once the magnitude has dropped back near the baseline
  let lastStep = -Infinity;
  let stepTimes: number[] = [];

  const prune = (at: number) => { stepTimes = stepTimes.filter((t) => at - t <= WALK_WINDOW_MS && t <= at); };

  return {
    push(s) {
      const mag = Math.sqrt(s.x * s.x + s.y * s.y + s.z * s.z);
      if (!Number.isFinite(mag) || !Number.isFinite(s.t)) return;
      if (base === null) { base = mag; return; }
      const rise = mag - base;
      if (armed && rise > PEAK_RISE) {
        armed = false;
        if (s.t - lastStep >= STEP_MIN_INTERVAL_MS) {
          lastStep = s.t;
          stepTimes.push(s.t);
          prune(s.t);
        }
      } else if (!armed && rise < PEAK_RESET) {
        armed = true;
      }
      // Learn the baseline slowly, and only from quiet samples so a stride does not drag it up.
      if (Math.abs(rise) < PEAK_RISE) base += BASE_ALPHA * (mag - base);
    },
    steps(at) { prune(at); return stepTimes.length; },
    isWalking(at) { prune(at); return stepTimes.length >= WALK_MIN_STEPS; },
    reset() { base = null; armed = true; lastStep = -Infinity; stepTimes = []; },
  };
}

export interface MotionSensor {
  /** false when the phone has no accelerometer or the owner refused the permission (iOS). */
  available: boolean;
  isWalking(at: number): boolean;
  stop(): void;
}

type DME = { requestPermission?: () => Promise<string> };
type MotionEvt = { timeStamp?: number; accelerationIncludingGravity?: { x: number | null; y: number | null; z: number | null } | null };

/**
 * Attaches a step counter to `devicemotion`. On iOS this must run inside a user gesture (the Start tap)
 * for the permission prompt to show; a refused or failed prompt just means `available: false`.
 * Never throws.
 */
export async function startMotionSensor(counter: StepCounter = createStepCounter()): Promise<MotionSensor> {
  const g = globalThis as unknown as {
    DeviceMotionEvent?: DME;
    addEventListener?: (n: string, f: (e: MotionEvt) => void, o?: unknown) => void;
    removeEventListener?: (n: string, f: (e: MotionEvt) => void, o?: unknown) => void;
    performance?: { now(): number };
  };
  const off: MotionSensor = { available: false, isWalking: () => false, stop() { /* nothing to stop */ } };
  if (!g.DeviceMotionEvent || typeof g.addEventListener !== "function") return off;
  if (typeof g.DeviceMotionEvent.requestPermission === "function") {
    try {
      if ((await g.DeviceMotionEvent.requestPermission()) !== "granted") return off;
    } catch {
      return off;
    }
  }
  const handler = (e: MotionEvt) => {
    const a = e.accelerationIncludingGravity;
    if (!a) return;
    const t = typeof e.timeStamp === "number" && Number.isFinite(e.timeStamp) ? e.timeStamp : (g.performance?.now() ?? Date.now());
    counter.push({ t, x: a.x ?? 0, y: a.y ?? 0, z: a.z ?? 0 });
  };
  g.addEventListener("devicemotion", handler, { passive: true });
  return {
    available: true,
    isWalking: (at) => counter.isWalking(at),
    stop() { g.removeEventListener?.("devicemotion", handler, { passive: true } as unknown); },
  };
}
