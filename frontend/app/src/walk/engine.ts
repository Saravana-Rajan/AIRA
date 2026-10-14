// frontend/app/src/walk/engine.ts
// MediaPipe Object Detector (EfficientDet-Lite0 int8) + the near-floor edge check, for one frame.
// Used inside the detector worker (static MediaPipe import, so the worker bundle needs no code-splitting)
// or on the main thread as a fallback (MediaPipe loaded lazily by the caller).
import type * as MediaPipeVision from "@mediapipe/tasks-vision";
import type { ObjectDetector } from "@mediapipe/tasks-vision";
import { fromMediaPipe, type RawDet } from "./detections";
import type { DetectFrame, DetectResult } from "./detector";
import { floorEdgeDensity } from "./floor";

export const MP_VERSION = "1.1.0";
export const MP_WASM = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}/wasm`;
// Check the model URL on the Object Detector web guide.
export const OBJECT_MODEL =
  "https://storage.googleapis.com/mediapipe-models/object_detector/efficientdet_lite0/int8/latest/efficientdet_lite0.tflite";
// The detector keeps detections this far down so the per-frame diagnostic (raw count + top score) can show
// near-misses sitting just under MIN_SCORE; the app still acts only on >= MIN_SCORE (detections.ts).
export const DETECT_FLOOR = 0.2;
/** Size of the thumbnail used for the floor check. */
const FLOOR_W = 64;
const FLOOR_H = 48;

export interface Engine {
  readonly delegate: "GPU" | "CPU";
  run(frame: DetectFrame, ts: number): DetectResult;
  close(): void;
}

type Canvas2D = { getContext(id: "2d", o?: unknown): any };

function frameSize(f: DetectFrame): [number, number] {
  const v = f as HTMLVideoElement;
  if (typeof v.videoWidth === "number" && v.videoWidth > 0) return [v.videoWidth, v.videoHeight];
  return [(f as ImageBitmap).width, (f as ImageBitmap).height];
}

function makeFloorCanvas(): Canvas2D | null {
  try {
    if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(FLOOR_W, FLOOR_H) as unknown as Canvas2D;
    if (typeof document !== "undefined") {
      const c = document.createElement("canvas");
      c.width = FLOOR_W; c.height = FLOOR_H;
      return c;
    }
  } catch { /* no canvas: floor check off */ }
  return null;
}

export type MediaPipe = Pick<typeof MediaPipeVision, "FilesetResolver" | "ObjectDetector">;

/**
 * @param mp the `@mediapipe/tasks-vision` module.
 * @param opts.module true inside a module worker (MediaPipe then loads its ES-module wasm loader).
 */
export async function createEngine(mp: MediaPipe, opts: { module: boolean; delegate?: "GPU" | "CPU" }): Promise<Engine> {
  const { FilesetResolver, ObjectDetector } = mp;
  const files = await FilesetResolver.forVisionTasks(MP_WASM, opts.module);
  const make = (delegate: "GPU" | "CPU") =>
    ObjectDetector.createFromOptions(files, {
      baseOptions: { modelAssetPath: OBJECT_MODEL, delegate },
      runningMode: "VIDEO",
      scoreThreshold: DETECT_FLOOR,
      maxResults: 8,
    });
  let delegate: "GPU" | "CPU" = opts.delegate ?? "GPU";
  let detector: ObjectDetector;
  try {
    detector = await make(delegate);
  } catch (e) {
    if (delegate === "CPU") throw e;
    delegate = "CPU";
    detector = await make("CPU");   // WebGL2 unavailable ⇒ CPU
  }

  const canvas = makeFloorCanvas();
  const ctx = canvas?.getContext("2d", { willReadFrequently: true }) ?? null;
  let lastTs = -1;

  return {
    delegate,
    run(frame, ts) {
      const [w, h] = frameSize(frame);
      let t = ts;
      if (t <= lastTs) t = lastTs + 1;   // MediaPipe needs strictly increasing timestamps
      lastTs = t;
      // One detect call, and the raw pre-filter count + top score are kept for the diagnostic: "returns nothing"
      // (raw 0), "sees things but scores them below the bar" (raw > 0, top < MIN_SCORE) and "throws every frame"
      // (err set) are three different bugs, and this is the only place that can tell them apart.
      let raw = 0;
      let top = 0;
      let err: string | undefined;
      let dets: RawDet[] = [];
      if (w && h) {
        try {
          const res = detector.detectForVideo(frame, t);
          const list = res?.detections ?? [];
          raw = list.length;
          for (const d of list) for (const c of d.categories ?? []) if (c.score > top) top = c.score;
          dets = fromMediaPipe(res, w, h);
        } catch (e) {
          err = e instanceof Error ? e.message : String(e);   // a per-frame detect throw: surfaced, not swallowed
        }
      }
      let floor: number | null = null;
      if (ctx && w && h) {
        try {
          ctx.drawImage(frame, 0, 0, FLOOR_W, FLOOR_H);
          floor = floorEdgeDensity(ctx.getImageData(0, 0, FLOOR_W, FLOOR_H).data, FLOOR_W, FLOOR_H);
        } catch {
          floor = null;
        }
      }
      return { ts, dets, floor, raw, top, err };
    },
    close() { detector.close(); },
  };
}
