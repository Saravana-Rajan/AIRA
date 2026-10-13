// frontend/app/src/walk/start.ts
// Real wiring of the bump guard: shared rear camera → worker detector → vibrate / earcon / phrase.
// The integrator calls startBumpGuard() once (after the Start tap) and connects the guidance session's
// onNear to guard.setHandNearTarget.
import { app } from "../core/app";
import { detectionPaused, startCamera } from "../camera/capture";
import { getFrameSource } from "../camera/source";
import type { BumpWarning } from "./bump";
import { boxStore, createBoxSender } from "./boxes";
import { createDetector, type Detector } from "./detector";
import { createBumpGuard, type BumpGuard } from "./guard";
import { walkingMode } from "./mode";
import { startMotionSensor, type MotionSensor } from "./motion";

/** Detector input width; EfficientDet-Lite0 works at 320 px. */
export const GRAB_W = 320;
const LATENCY_KEEP = 500;

export type BumpLatencyLog = { atMs: number; latencyMs: number; phrase: BumpWarning["phrase"] }[];
const latencyLog: BumpLatencyLog = [];

/** Bench: frame timestamp → vibrate call, one row per warning (most recent 500). */
export const bumpLatencyLog = (): BumpLatencyLog => latencyLog.slice();

/**
 * One small frame of the shared rear camera, stamped with performance.now() at grab time.
 * null while the camera is not ready, and always in demo mode (/try): the guard is for walking in the
 * real shop, so demo media never triggers it and the camera is never opened there.
 */
export async function grabFrame(): Promise<{ frame: ImageBitmap; ts: number } | null> {
  if (getFrameSource()) return null;
  // An owner tool photo (ask, count, ...) is being taken: this detector pass waits, the phone's CPU goes to it.
  if (detectionPaused()) return null;
  const video = await startCamera();
  if (video.readyState < 2 || !video.videoWidth) return null;
  const ts = performance.now();
  const h = Math.max(1, Math.round((GRAB_W * video.videoHeight) / video.videoWidth));
  const frame = await createImageBitmap(video, { resizeWidth: GRAB_W, resizeHeight: h, resizeQuality: "low" });
  return { frame, ts };
}

export interface RunningBumpGuard extends BumpGuard {
  detector: Detector;
  /** The accelerometer; `available: false` means approach-only warnings. */
  motion: MotionSensor;
  /** Stops the loop, the detector (worker), the motion sensor and the visibility listener. */
  dispose(): void;
}

export interface StartBumpGuardOptions {
  /** Tests / bench only. */
  detector?: Detector;
  /** Tests / bench only: a fake accelerometer. */
  motion?: MotionSensor;
  /** Told about each warning right after the vibration (e.g. the red "Stop!" chip). Errors here are ignored. */
  onWarn?: (w: BumpWarning) => void;
  /** false: no `boxes` messages to the server (tests). The overlay store is always fed. */
  sendBoxes?: boolean;
}

/**
 * Starts the always-on bump guard. Pauses while the app is hidden. Call it inside the Start tap: on iOS the
 * motion permission prompt only shows from a user gesture (without it, size warnings stay off).
 * The persisted walking-mode switch (walk/mode.ts) drives the guard; `setWalkingMode` on the result persists it.
 */
export async function startBumpGuard(opts: StartBumpGuardOptions = {}): Promise<RunningBumpGuard> {
  // The permission request goes first, while the user gesture is still fresh.
  const motionP = opts.motion ? Promise.resolve(opts.motion) : startMotionSensor();
  const detector = opts.detector ?? (await createDetector());
  const motion = await motionP;
  // The detector's boxes go to the live monitor at up to 5 a second (walk/boxes.ts) and to the overlay store.
  const boxes = opts.sendBoxes === false ? null : createBoxSender({ send: (m) => app.send(m), now: () => performance.now() });
  const guard = createBumpGuard({
    grab: grabFrame,
    detector,
    motion,
    walkingMode: walkingMode.get(),
    onDetections: (reports) => {
      boxStore.setMediapipe(reports);
      boxes?.push(reports);
    },
    vibrate: (p) => { try { navigator.vibrate?.(p); } catch { /* not supported */ } },
    earcon: () => app.earcon("warn"),
    speak: (s) => app.speak(s),
    lang: () => app.lang(),
    now: () => performance.now(),
    onLatency: (ms, w) => {
      latencyLog.push({ atMs: Date.now(), latencyMs: Math.round(ms), phrase: w.phrase });
      if (latencyLog.length > LATENCY_KEEP) latencyLog.shift();
      // runs after the vibration; a UI problem must never block the earcon and the phrase
      try { opts.onWarn?.(w); } catch (e) { console.error("[aira] bump onWarn failed", e); }
    },
  });
  const onVisibility = () => (document.hidden ? guard.stop() : guard.start());
  document.addEventListener("visibilitychange", onVisibility);
  const setMode = guard.setWalkingMode;   // the inner one: Object.assign below replaces it on `guard`
  const offMode = walkingMode.subscribe(setMode);
  if (!document.hidden) guard.start();
  return Object.assign(guard, {
    detector,
    motion,
    /** Persists the switch (which also tells the guard through the subscription above). */
    setWalkingMode(on: boolean) {
      walkingMode.set(on);
      setMode(on);
    },
    dispose() {
      document.removeEventListener("visibilitychange", onVisibility);
      offMode();
      guard.stop();
      try { motion.stop(); } catch { /* already gone */ }
      detector.close();
    },
  });
}
