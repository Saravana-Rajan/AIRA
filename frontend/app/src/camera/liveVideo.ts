// frontend/app/src/camera/liveVideo.ts
// The camera as Gemini Live video: while the Live session is up and the camera is on, one `live` frame a second
// (768 px, quality 0.7). Tool frames (ask, count, identify...) always win: a tick is skipped while one is being
// captured, and a live frame is dropped when a tool capture started while it was being made. A tick is also
// skipped while the socket is still busy (stale video is worse than none) and while the page is hidden.
// A cheap change detector marks a frame `changed` (with the phone's own detector labels) when the view settled
// on something new, so the server can ask Live to speak up. Pure: every browser touch comes through deps.
import type { ClientMsg } from "@aira/contracts";
import type { FrameKeep, FrameMsg } from "./capture";
import { coarseGray, meanAbsDiff } from "./sceneGate";

export const LIVE_FPS = 1;
/** Bytes waiting on the socket above which a live tick is skipped. */
export const LIVE_MAX_BUFFERED = 64_000;
/** Mean absolute luma difference (0-255, 32x24 grid) from the last settled view that counts as a new scene. */
export const CHANGE_THRESHOLD = 14;
/** ...and the most two frames a second apart may differ for the view to count as steady (no motion blur). */
export const STEADY_THRESHOLD = 6;
export const MAX_LABELS = 8;

export interface ChangeDetector {
  /** One frame's coarse grid and the labels seen now; true when the view just settled on something new. */
  feed(grid: Float32Array, labels: readonly string[]): boolean;
  reset(): void;
}

const labelSet = (labels: readonly string[]) => new Set(labels.map((l) => l.trim().toLowerCase()).filter(Boolean));

/**
 * Change = the grid moved at least CHANGE_THRESHOLD from the last settled view, or a label appeared that the
 * settled view did not have. It is reported only once the view is steady (this frame within STEADY_THRESHOLD
 * of the previous one), so a frame in mid-motion never counts. The first frame only sets the reference.
 */
export function createChangeDetector(opts: { threshold?: number; steady?: number } = {}): ChangeDetector {
  const threshold = opts.threshold ?? CHANGE_THRESHOLD;
  const steady = opts.steady ?? STEADY_THRESHOLD;
  let settled: Float32Array | null = null;
  let settledLabels = new Set<string>();
  let prev: Float32Array | null = null;
  return {
    feed(grid, labels) {
      const now = labelSet(labels);
      const last = prev;
      prev = grid;
      if (settled === null) {
        settled = grid;
        settledLabels = now;
        return false;
      }
      const moved = meanAbsDiff(grid, settled) >= threshold;
      const newLabel = [...now].some((l) => !settledLabels.has(l));
      if (!moved && !newLabel) return false;
      const isSteady = last !== null && meanAbsDiff(grid, last) < steady;
      if (!isSteady) return false;
      settled = grid;
      settledLabels = now;
      return true;
    },
    reset() {
      settled = null;
      prev = null;
      settledLabels = new Set();
    },
  };
}

export interface LiveVideoDeps {
  /** captureFrameIf("live", keep): the gate sees the gray frame and always keeps it. */
  capture: (keep: FrameKeep) => Promise<FrameMsg | null>;
  send: (m: ClientMsg) => boolean;
  /** The Live session is up (ready received, socket open). */
  active: () => boolean;
  cameraOn: () => boolean;
  hidden: () => boolean;
  /** Bytes waiting on the socket. */
  buffered: () => number;
  toolBusy: () => boolean;
  toolCount: () => number;
  /** The phone's current detector labels (MediaPipe), newest first. */
  labels: () => readonly string[];
  detector?: ChangeDetector;
  fps?: number;
}

/** Starts the 1 fps live sender; returns its stop function. Never throws. */
export function startLiveVideo(deps: LiveVideoDeps): () => void {
  const det = deps.detector ?? createChangeDetector();
  const period = Math.round(1000 / (deps.fps ?? LIVE_FPS));
  let stopped = false;
  let inFlight = false;
  let wasHidden = false;

  const skip = (): boolean => {
    const hidden = deps.hidden();
    if (hidden) wasHidden = true;
    return hidden || !deps.active() || !deps.cameraOn() || deps.toolBusy() || deps.buffered() > LIVE_MAX_BUFFERED;
  };

  const tick = async () => {
    if (stopped || inFlight) return;
    try {
      if (skip()) return;
    } catch {
      return;
    }
    if (wasHidden) { det.reset(); wasHidden = false; } // back from hidden: the first view is a reference only
    inFlight = true;
    const seq = deps.toolCount();
    let grid: Float32Array | null = null;
    try {
      const f = await deps.capture((gray, w, h) => { grid = coarseGray(gray, w, h); return true; });
      if (!f || stopped) return;
      // A tool frame started meanwhile, or the socket filled up: the tool frame goes first, this one never.
      if (deps.toolCount() !== seq || deps.toolBusy() || deps.hidden() || deps.buffered() > LIVE_MAX_BUFFERED) return;
      const labels = deps.labels().slice(0, MAX_LABELS);
      const changed = grid !== null && det.feed(grid, labels);
      deps.send(changed ? { ...f, changed: true, labels: [...labels] } : f);
    } catch {
      // camera hiccup: the next tick tries again
    } finally {
      inFlight = false;
    }
  };

  const id = setInterval(() => { void tick(); }, period);
  void tick();
  return () => {
    stopped = true;
    clearInterval(id);
  };
}
