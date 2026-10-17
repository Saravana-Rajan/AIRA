// frontend/app/src/ambient/watch.ts
// The hold-up watcher behind the server's `request_frame {purpose: "identify_held", continuous: true}`
// (ambient mode): hand box (MediaPipe hand landmarker) + the bump guard's object boxes (walk/boxes.ts store)
// → createHoldUpDetector → ONE `identify_held` frame per held-up object, through the shared camera.
// The bump guard beats everything: for BUMP_MUTE_MS after a "Stop!" warning nothing is sent.
import type { ClientMsg, FramePurpose } from "@aira/contracts";
import { app } from "../core/app";
import { captureFrame, type FrameMsg } from "../camera/capture";
import type { Box } from "../guidance/geometry";
import { trackHand, type Hand } from "../guidance/hands";
import { boxStore, MEDIAPIPE_TTL_MS } from "../walk/boxes";
import { createHoldUpDetector, type HoldUpDetector, type HoldUpObject } from "./holdUp";

export const PURPOSE: FramePurpose = "identify_held";
/** No hold-up frame for this long after a bump warning. */
export const BUMP_MUTE_MS = 2500;

let lastBumpWarnAt = -Infinity;

/** Called by the camera wiring on every bump warning (walk guard `onWarn`). */
export function noteBumpWarning(at: number = performance.now()): void {
  lastBumpWarnAt = at;
}

/** True while the bump guard just warned: danger beats a product name. */
export function bumpMuted(now: number = performance.now()): boolean {
  return now - lastBumpWarnAt < BUMP_MUTE_MS;
}

/** Tests only. */
export function __resetBumpMute(): void {
  lastBumpWarnAt = -Infinity;
}

export interface WatchDeps {
  trackHand: (onHand: (hand: Hand | null) => void) => Promise<() => void>;
  /** The latest on-device object boxes (empty when stale or when the detector is off). */
  objects: () => readonly HoldUpObject[];
  capture: (purpose: FramePurpose) => Promise<FrameMsg>;
  send: (m: ClientMsg) => boolean;
  now: () => number;
  /** True ⇒ nothing is sent and the detector forgets what it saw (bump warning, hidden page). */
  suppressed?: () => boolean;
  detector?: HoldUpDetector;
  /** Told about each frame sent (tests, the dev panel). */
  onHoldUp?: (label: string, box: Box) => void;
}

/** Starts the watcher; returns a stop function. Never throws: a camera or model failure just means no frames. */
export function startHoldUpWatcher(deps: WatchDeps): () => void {
  const det = deps.detector ?? createHoldUpDetector();
  let running = true;
  let stopTrack: (() => void) | null = null;
  let capturing = false;

  const onHand = (hand: Hand | null) => {
    if (!running) return;
    if (deps.suppressed?.()) { det.reset(); return; }
    const hit = det.update({ hand: hand ? hand.box : null, objects: deps.objects(), ts: deps.now() });
    if (!hit || capturing) return;
    capturing = true;
    deps.capture(PURPOSE).then(
      (f) => { if (running) { deps.send(f); deps.onHoldUp?.(hit.label, hit.box); } },
      () => { /* camera hiccup: the next hold-up tries again */ },
    ).finally(() => { capturing = false; });
  };

  deps.trackHand(onHand).then(
    (stop) => { if (running) stopTrack = stop; else stop(); },
    () => { /* MediaPipe or camera unavailable: no hold-up frames this session */ },
  );

  return () => {
    running = false;
    stopTrack?.();
    stopTrack = null;
  };
}

/** The latest MediaPipe boxes from the bump guard, when fresh. */
export function freshObjects(now: number = performance.now()): HoldUpObject[] {
  const mp = boxStore.get().mediapipe;
  if (now - mp.at > MEDIAPIPE_TTL_MS) return [];
  return mp.reports.map((r) => ({ label: r.label, box: r.box }));
}

/** Real wiring (camera/wire.ts): hand landmarker, the bump guard's boxes, the shared camera and app.send. */
export function startHoldUpWatch(): () => void {
  return startHoldUpWatcher({
    trackHand,
    objects: () => freshObjects(),
    capture: captureFrame,
    send: (m) => app.send(m),
    now: () => performance.now(),
    suppressed: () => bumpMuted() || (typeof document !== "undefined" && document.hidden),
  });
}
