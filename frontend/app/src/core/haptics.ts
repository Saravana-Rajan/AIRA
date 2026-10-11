import type { HapticName } from "./types";

export const HAPTIC_PATTERNS: Record<HapticName, number[]> = {
  ack: [15],
  warn: [80, 60, 80],
  done: [30, 40, 30],
  match: [20, 30, 20],
  mismatch: [200, 100, 200],
};

/** Returns false (no-op) where the Vibration API is missing (e.g. iOS Safari) or refuses. */
export function vibrate(name: HapticName, nav: Navigator | undefined = globalThis.navigator): boolean {
  const v = (nav as (Navigator & { vibrate?: (p: number[]) => boolean }) | undefined)?.vibrate;
  if (typeof v !== "function") return false;
  try {
    return v.call(nav, HAPTIC_PATTERNS[name]) === true;
  } catch {
    return false;
  }
}
