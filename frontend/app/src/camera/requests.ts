// frontend/app/src/camera/requests.ts
// Handles server `request_frame` messages: single, burst, continuous and stop.
// Camera problems become one spoken hint (through the throttled hint speaker), never a throw or a retry loop.
import type { ClientMsg, FramePurpose, ServerMsg } from "@aira/contracts";
import { CameraError, isPriorityPurpose, type FrameMsg } from "./capture";
import type { HintKey } from "../i18n/camera";
import { clientLog } from "../core/clientLog";

export const CONTINUOUS_FPS = 2;
/** Describe mode: one frame every 3 s at most (the capture gate drops frames where nothing changed). */
export const DESCRIBE_FPS = 1 / 3;
/** The dashboard's live preview (while a monitor watches): small frames, 8 a second, each sent at once (never
 *  queued: a frame is dropped while the socket is still busy with the last one), relayed to the monitor only. */
export const PREVIEW_FPS = 8;

/** Walk with me: one frame a second (the server pipelines up to two ER looks). */
export const WALK_FPS = 1;

export function continuousFps(purpose: FramePurpose): number {
  if (purpose === "describe") return DESCRIBE_FPS;
  if (purpose === "preview") return PREVIEW_FPS;
  if (purpose === "walk") return WALK_FPS;
  return CONTINUOUS_FPS;
}
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
  /** Owner questions (ask / count / identify / point) pre-empt background capture: describe and preview frames
   *  pause until the returned release fn runs. Optional: without it frames simply go at once. */
  hold?: () => () => void;
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
      running.set(m.purpose, deps.startContinuous(m.purpose, continuousFps(m.purpose)));
      return;
    }
    const count = clamp(m.burst?.count, 1, MAX_BURST, 1);
    const interval = clamp(m.burst?.intervalMs, 0, MAX_INTERVAL_MS, 0);
    // An owner question never waits behind a describe frame: background capture pauses until it is sent.
    const release = isPriorityPurpose(m.purpose) ? deps.hold?.() : undefined;
    try {
      for (let i = 0; i < count; i++) {
        try {
          deps.send(await deps.capture(m.purpose));
        } catch (e) {
          clientLog("frame", { purpose: m.purpose, error: e instanceof CameraError ? e.code : "unknown" });
          deps.hint(hintFor(e));
          return; // one hint, no retry loop
        }
        if (i < count - 1 && interval > 0) await deps.sleep(interval);
      }
    } finally {
      release?.();
    }
  };
}

export const defaultSleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
