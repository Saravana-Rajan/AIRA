// frontend/app/src/guidance/hands.ts
// MediaPipe Hand Landmarker: streams the index fingertip (landmark 8) and the whole hand's box in 0–1000
// image coords. MediaPipe is loaded lazily (./mediapipe), so it stays out of the start-up bundle.
import type { HandLandmarker } from "@mediapipe/tasks-vision";
import { detectionPaused, startCamera } from "../camera/capture";
import { getFrameSource, type FrameSourceEl } from "../camera/source";
import type { Box } from "./geometry";

export const MP_VERSION = "1.1.0";
export const MP_WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}/wasm`;
// Check the current model URL on the Hand Landmarker web guide; "latest" tracks the newest float16 model.
export const HAND_MODEL =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task";
const DELEGATE_KEY = "aira.handsDelegate";   // A/B switch: localStorage "GPU" | "CPU"
const INDEX_TIP = 8;

type Tip = { x1000: number; y1000: number };
type Landmarks = { landmarks?: { x: number; y: number }[][] } | null | undefined;

let landmarker: Promise<HandLandmarker> | null = null;

/** Tests only: forget the loaded model. */
export function __resetHands(): void {
  landmarker = null;
}

function preferredDelegate(): "GPU" | "CPU" {
  try {
    return localStorage.getItem(DELEGATE_KEY) === "CPU" ? "CPU" : "GPU";
  } catch {
    return "GPU";
  }
}

async function create(): Promise<HandLandmarker> {
  const { FilesetResolver, HandLandmarker } = await import("./mediapipe");
  const files = await FilesetResolver.forVisionTasks(MP_WASM);
  const make = (delegate: "GPU" | "CPU") =>
    HandLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: HAND_MODEL, delegate },
      runningMode: "VIDEO",
      numHands: 1,
    });
  const preferred = preferredDelegate();
  try {
    return await make(preferred);
  } catch (e) {
    if (preferred === "CPU") throw e;
    return make("CPU");   // WebGL2 unavailable ⇒ CPU fallback
  }
}

function load(): Promise<HandLandmarker> {
  landmarker ??= create().catch((e) => { landmarker = null; throw e; });
  return landmarker;
}

const clamp1000 = (v: number) => Math.min(1000, Math.max(0, Math.round(v * 1000)));

/** Index fingertip of the first hand in 0–1000 image coords, or null when no hand is visible. */
export function fingertipFrom(res: Landmarks): Tip | null {
  const lm = res?.landmarks?.[0]?.[INDEX_TIP];
  if (!lm) return null;
  return { x1000: clamp1000(lm.x), y1000: clamp1000(lm.y) };
}

/** Bounding box [ymin, xmin, ymax, xmax] (0–1000) of all landmarks of the first hand, or null without a hand. */
export function handBoxFrom(res: Landmarks): Box | null {
  const lms = res?.landmarks?.[0];
  if (!lms || lms.length === 0) return null;
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const lm of lms) {
    if (lm.x < xmin) xmin = lm.x;
    if (lm.x > xmax) xmax = lm.x;
    if (lm.y < ymin) ymin = lm.y;
    if (lm.y > ymax) ymax = lm.y;
  }
  return [clamp1000(ymin), clamp1000(xmin), clamp1000(ymax), clamp1000(xmax)];
}

export type Hand = { tip: Tip; box: Box };

/** The latest hand any tracking loop saw (ambient hold-up watch, hand guidance), for readers that must not start
 *  a landmarker of their own (the live video's show detector). */
let latestHand: { box: Box | null; at: number } = { box: null, at: -Infinity };
export const HAND_TTL_MS = 500;

/** The hand box seen within the last HAND_TTL_MS by a running tracker, or null (no hand, or no tracker running). */
export function freshHand(now: number = performance.now()): Box | null {
  return now - latestHand.at <= HAND_TTL_MS ? latestHand.box : null;
}

/** Tests / the tracker loop: record what the landmarker saw now. */
export function noteHand(box: Box | null, at: number = performance.now()): void {
  latestHand = { box, at };
}

type VideoWithRvfc = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: () => void) => number;
  cancelVideoFrameCallback?: (id: number) => void;
};

function hasPixels(el: FrameSourceEl): boolean {
  if (el instanceof HTMLVideoElement) return el.readyState >= 2 && el.videoWidth > 0;
  if (el instanceof HTMLImageElement) return el.complete && el.naturalWidth > 0;
  return el.width > 0 && el.height > 0;
}

/**
 * Streams the index fingertip on every new frame; null when no hand. Resolves to a stop function.
 * Demo mode (/try): tracks the demo media instead and never opens the camera.
 */
export function trackFingertip(onTip: (tip: Tip | null) => void): Promise<() => void> {
  return trackHand((hand) => onTip(hand ? hand.tip : null));
}

/**
 * Streams the first hand (fingertip and box) on every new frame; null when no hand. Resolves to a stop
 * function. Each caller gets its own loop; MediaPipe itself is loaded once.
 */
export async function trackHand(onHand: (hand: Hand | null) => void): Promise<() => void> {
  const source: FrameSourceEl = getFrameSource() ?? (await startCamera());
  const hl = await load();
  let running = true;
  let lastTs = -1;
  let frameId = 0;
  const v = source as VideoWithRvfc;
  const useRvfc = source instanceof HTMLVideoElement && typeof v.requestVideoFrameCallback === "function";

  const next = () => {
    if (!running) return;
    frameId = useRvfc ? v.requestVideoFrameCallback!(loop) : requestAnimationFrame(loop);
  };
  const loop = () => {
    if (!running) return;
    // An owner tool photo (ask, count, ...) is being taken: skip this pass, the phone's CPU goes to it.
    if (hasPixels(source) && !detectionPaused()) {
      let ts = performance.now();
      if (ts <= lastTs) ts = lastTs + 1;   // MediaPipe needs strictly increasing timestamps
      lastTs = ts;
      let hand: Hand | null;
      try {
        const res = hl.detectForVideo(source, ts);
        const tip = fingertipFrom(res);
        const box = handBoxFrom(res);
        hand = tip && box ? { tip, box } : null;
      } catch {
        hand = null;   // a lost GL context or a bad frame reads as "no hand"; the loop goes on
      }
      noteHand(hand ? hand.box : null, ts);
      onHand(hand);
    }
    next();
  };
  next();
  return () => {
    running = false;
    if (useRvfc) v.cancelVideoFrameCallback?.(frameId);
    else cancelAnimationFrame(frameId);
  };
}
