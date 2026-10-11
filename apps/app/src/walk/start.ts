// apps/app/src/walk/start.ts
// Real wiring of the bump guard: shared rear camera → worker detector → vibrate / earcon / phrase.
// The integrator calls startBumpGuard() once (after the Start tap) and connects the guidance session's
// onNear to guard.setHandNearTarget.
import { app } from "../core/app";
import { startCamera } from "../camera/capture";
import { getFrameSource } from "../camera/source";
import type { BumpWarning } from "./bump";
import { createDetector, type Detector } from "./detector";
import { createBumpGuard, type BumpGuard } from "./guard";

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
  const video = await startCamera();
  if (video.readyState < 2 || !video.videoWidth) return null;
  const ts = performance.now();
  const h = Math.max(1, Math.round((GRAB_W * video.videoHeight) / video.videoWidth));
  const frame = await createImageBitmap(video, { resizeWidth: GRAB_W, resizeHeight: h, resizeQuality: "low" });
  return { frame, ts };
}

export interface RunningBumpGuard extends BumpGuard {
  detector: Detector;
  /** Stops the loop, the detector (worker) and the visibility listener. */
  dispose(): void;
}

export interface StartBumpGuardOptions {
  /** Tests / bench only. */
  detector?: Detector;
  /** Told about each warning right after the vibration (e.g. the red "Stop!" chip). Errors here are ignored. */
  onWarn?: (w: BumpWarning) => void;
}

/** Starts the always-on bump guard. Pauses while the app is hidden. */
export async function startBumpGuard(opts: StartBumpGuardOptions = {}): Promise<RunningBumpGuard> {
  const detector = opts.detector ?? (await createDetector());
  const guard = createBumpGuard({
    grab: grabFrame,
    detector,
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
  if (!document.hidden) guard.start();
  return Object.assign(guard, {
    detector,
    dispose() {
      document.removeEventListener("visibilitychange", onVisibility);
      guard.stop();
      detector.close();
    },
  });
}
