// frontend/app/src/camera/lowlight.ts
// Low light, pure: (1) the sustained light level the phone reports to the server (never one frame: a level
// changes only after MIN_FRAMES samples over at least MIN_MS agree, with hysteresis so a view hovering around a
// threshold does not flicker between levels), and (2) the brightness/contrast boost applied to frames before
// upload when the view is dim (AIRA_LOWLIGHT). The boost touches only the pixels about to be encoded: the quality
// check and the level reported to the server read the RAW luma first, and the bump guard grabs its own frames
// straight from the <video> (walk/start.ts), so nothing here ever reaches it.
import type { LightLevel } from "@aira/contracts";
import { DARK_LUMA } from "./quality";

/** Below this mean luma (0-255) the view is "dark": the same bar as the camera's "too dark" hint. */
export { DARK_LUMA };
/** From DARK_LUMA up to here the view is "dim": usable, worth a boost. At or above: "bright". */
export const DIM_LUMA = 90;
/** Hysteresis: a level is left only once the luma has crossed its threshold by this much. */
export const HYSTERESIS = 12;
/** A new level must hold for this many consecutive samples ... */
export const MIN_FRAMES = 4;
/** ... and for at least this long (ms) before it is reported. */
export const MIN_MS = 3000;

/** The level a single luma reading suggests, given the level in force (hysteresis around both thresholds). */
export function levelFor(luma: number, current: LightLevel): LightLevel {
  const darkUp = current === "dark" ? DARK_LUMA + HYSTERESIS : DARK_LUMA;
  if (luma < darkUp) return "dark";
  const brightDown = current === "bright" ? DIM_LUMA - HYSTERESIS : DIM_LUMA;
  if (luma >= brightDown) return "bright";
  return "dim";
}

export interface DarkDetectorOptions {
  minFrames?: number;
  minMs?: number;
  /** The level before the first sample (a fresh session starts bright: nothing to offer until proven dark). */
  initial?: LightLevel;
}

export interface DarkDetector {
  /** One raw luma sample. The new level when it just changed, else null. */
  feed(luma: number, now: number): LightLevel | null;
  level(): LightLevel;
  reset(): void;
}

/** A level changes only after `minFrames` consecutive samples spanning at least `minMs` agree on the new one. */
export function createDarkDetector(opts: DarkDetectorOptions = {}): DarkDetector {
  const minFrames = opts.minFrames ?? MIN_FRAMES;
  const minMs = opts.minMs ?? MIN_MS;
  let level: LightLevel = opts.initial ?? "bright";
  let candidate: LightLevel | null = null;
  let count = 0;
  let since = 0;
  return {
    feed(luma, now) {
      if (!Number.isFinite(luma)) return null;
      const next = levelFor(luma, level);
      if (next === level) { candidate = null; count = 0; return null; }
      if (next !== candidate) { candidate = next; count = 0; since = now; }
      count++;
      if (count >= minFrames && now - since >= minMs) {
        level = next;
        candidate = null;
        count = 0;
        return level;
      }
      return null;
    },
    level: () => level,
    reset() { candidate = null; count = 0; },
  };
}

// ---------- the boost ----------
/** The boost aims the mean luma here ... */
export const BOOST_TARGET_LUMA = 110;
/** ... with at most this gain, so noise is not amplified into a picture that only looks lit. */
export const MAX_GAIN = 2.5;
/** A little extra contrast around mid-grey so a lifted picture does not go flat. */
export const CONTRAST = 1.15;

/** The gain for a raw mean luma: 1 (no boost) at or above DIM_LUMA, otherwise up to MAX_GAIN toward the target. */
export function boostGainFor(luma: number): number {
  if (!(luma > 0) || luma >= DIM_LUMA) return 1;
  return Math.min(MAX_GAIN, Math.max(1, BOOST_TARGET_LUMA / luma));
}

/** Multiplies RGB by `gain` and stretches contrast around 128 in place (alpha untouched). A gain of 1 is a no-op. */
export function boostPixels(rgba: Uint8ClampedArray, gain: number, contrast: number = CONTRAST): void {
  if (gain <= 1) return;
  for (let p = 0; p < rgba.length; p += 4) {
    for (let c = 0; c < 3; c++) {
      const v = rgba[p + c] * gain;
      rgba[p + c] = (v - 128) * contrast + 128; // the clamped array clips to 0..255
    }
  }
}
