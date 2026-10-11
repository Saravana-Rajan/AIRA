// frontend/app/src/camera/requests.ts
// Handles server `request_frame` messages: single, burst, continuous and stop.
// Camera problems become one spoken hint (through the throttled hint speaker), never a throw or a retry loop.
import type { ClientMsg, FramePurpose, ServerMsg } from "@aira/contracts";
import { CameraError, type FrameMsg } from "./capture";
import type { HintKey } from "../i18n/camera";

export const CONTINUOUS_FPS = 2;
/** Upper bounds so a malformed server burst can't keep the camera busy for minutes. */
export const MAX_BURST = 10;
export const MAX_INTERVAL_MS = 5000;

export type FrameRequest = Extract<ServerMsg, { t: "request_frame" }>;

export interface RequestDeps {
  capture: (purpose: FramePurpose) => Promise<FrameMsg>;
  startContinuous: (purpose: FramePurpose, fps: number) => () => void;
  send: (m: ClientMsg) => boolean;
  hint: (k: HintKey) => boolean;
  sleep: (ms: number) => Promise<void>;
}

/** An oversize frame means "hold still" (too much detail/noise); anything else means no camera. */
function hintFor(e: unknown): HintKey {
  return e instanceof CameraError && e.code === "too_large" ? "blurry" : "no_camera";
}

function clamp(v: number | undefined, lo: number, hi: number, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, Math.floor(v))) : fallback;
}

export function createFrameRequestHandler(deps: RequestDeps): (m: FrameRequest) => Promise<void> {
  // One continuous stream per purpose: guidance (`guide`) and a one-by-one delivery count (`identify`) can run
  // at the same time, and a stop for one never ends the other.
  const running = new Map<FramePurpose, () => void>();

  return async function handle(m: FrameRequest): Promise<void> {
    if (m.stop) {
      // stop only ends that purpose's continuous stream; a burst in flight finishes on its own
      running.get(m.purpose)?.();
      running.delete(m.purpose);
      return;
    }
    if (m.continuous) {
      running.get(m.purpose)?.();
      running.set(m.purpose, deps.startContinuous(m.purpose, CONTINUOUS_FPS));
      return;
    }
    const count = clamp(m.burst?.count, 1, MAX_BURST, 1);
    const interval = clamp(m.burst?.intervalMs, 0, MAX_INTERVAL_MS, 0);
    for (let i = 0; i < count; i++) {
      try {
        deps.send(await deps.capture(m.purpose));
      } catch (e) {
        deps.hint(hintFor(e));
        return; // one hint, no retry loop
      }
      if (i < count - 1 && interval > 0) await deps.sleep(interval);
    }
  };
}

export const defaultSleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
