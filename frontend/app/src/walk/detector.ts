// frontend/app/src/walk/detector.ts
// Object detector for the bump guard. Runs MediaPipe EfficientDet-Lite0 (int8) in a Web Worker so the
// UI thread, voice and hand tracking stay smooth; falls back to the main thread if the worker fails.
import type { RawDet } from "./detections";

export type DetectResult = { ts: number; dets: RawDet[]; floor: number | null };
export type DetectFrame = ImageBitmap | HTMLVideoElement;

export interface Detector {
  readonly kind: "worker" | "main";
  detect(frame: DetectFrame, ts: number): Promise<DetectResult>;
  close(): void;
}

/** The parts of a Worker the detector uses (a fake in tests). */
export interface WorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void;
  terminate(): void;
  onmessage: ((e: { data: any }) => void) | null;
  onerror: ((e: unknown) => void) | null;
}

/** Worker protocol. main → worker: init | frame. worker → main: ready | error | result. */
export type ToWorker = { type: "init" } | { type: "frame"; seq: number; ts: number; frame: DetectFrame };
export type FromWorker =
  | { type: "ready"; delegate: "GPU" | "CPU" }
  | { type: "error"; message: string }
  | ({ type: "result"; seq: number } & DetectResult);

export const READY_TIMEOUT_MS = 20_000;   // first run downloads the model (~4.5 MB)

const isBitmap = (f: unknown): f is ImageBitmap => typeof ImageBitmap !== "undefined" && f instanceof ImageBitmap;

export function createWorkerDetector(worker: WorkerLike, readyTimeoutMs = READY_TIMEOUT_MS): Promise<Detector> {
  return new Promise((resolve, reject) => {
    const pending = new Map<number, { resolve: (r: DetectResult) => void; reject: (e: Error) => void }>();
    let seq = 0;
    let ready = false;
    let dead: Error | null = null;

    const fail = (err: Error) => {
      if (dead) return;
      dead = err;
      clearTimeout(timer);
      for (const p of pending.values()) p.reject(err);
      pending.clear();
      worker.terminate();
      if (!ready) reject(err);
    };

    const timer = setTimeout(() => fail(new Error("bump detector: worker ready timeout")), readyTimeoutMs);

    const detector: Detector = {
      kind: "worker",
      detect(frame, ts) {
        if (dead) {
          if (isBitmap(frame)) frame.close();
          return Promise.reject(dead);
        }
        const id = ++seq;
        return new Promise<DetectResult>((res, rej) => {
          pending.set(id, { resolve: res, reject: rej });
          const msg: ToWorker = { type: "frame", seq: id, ts, frame };
          worker.postMessage(msg, isBitmap(frame) ? [frame] : []);
        });
      },
      close() { fail(new Error("bump detector: closed")); },
    };

    worker.onmessage = (e) => {
      const m = e.data as FromWorker;
      if (!m || typeof m !== "object") return;
      if (m.type === "ready") {
        ready = true;
        clearTimeout(timer);
        resolve(detector);
      } else if (m.type === "error") {
        fail(new Error(`bump detector: ${m.message}`));
      } else if (m.type === "result") {
        const p = pending.get(m.seq);
        if (!p) return;
        pending.delete(m.seq);
        p.resolve({ ts: m.ts, dets: m.dets, floor: m.floor });
      }
    };
    worker.onerror = (e) => fail(e instanceof Error ? e : new Error("bump detector: worker crashed"));
    worker.postMessage({ type: "init" } satisfies ToWorker);
  });
}

/** Main-thread detector (fallback when module workers / OffscreenCanvas are unavailable). */
export async function createMainThreadDetector(): Promise<Detector> {
  // MediaPipe only through the lazy ../guidance/mediapipe entry (see that file).
  const [{ createEngine }, mp] = await Promise.all([import("./engine"), import("../guidance/mediapipe")]);
  const engine = await createEngine(mp, { module: false });
  return {
    kind: "main",
    async detect(frame, ts) {
      try {
        return engine.run(frame, ts);
      } finally {
        if (isBitmap(frame)) frame.close();
      }
    },
    close() { engine.close(); },
  };
}

/** Worker first, then main thread. */
export async function createDetector(): Promise<Detector> {
  if (typeof Worker !== "undefined") {
    try {
      const worker = new Worker(new URL("./detector.worker.ts", import.meta.url), { type: "module" });
      return await createWorkerDetector(worker as unknown as WorkerLike);
    } catch {
      /* fall through to the main thread */
    }
  }
  return createMainThreadDetector();
}
