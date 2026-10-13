// frontend/app/src/camera/encode.ts
// JPEG encoding off the main thread. Production 2026-10-13 (Galaxy A22, Android 10): a count photo took 4-13 s to
// encode on the main thread (canvas.toBlob) while MediaPipe, the live video and describe frames shared it, so the
// server's wait ran out. Here the camera frame (a VideoFrame, or an ImageBitmap) is handed to a module worker that
// scales it on an OffscreenCanvas and encodes it with convertToBlob. Pure client side: the worker is injected.
// When Worker / OffscreenCanvas are missing (old browsers, jsdom tests) or the worker dies, capture.ts falls back
// to its canvas path.

/** A frame the worker can draw: a VideoFrame (preferred, no main-thread copy) or an ImageBitmap. Transferred. */
export type GrabbedFrame = { close(): void } & Transferable;

/** main -> worker */
export interface EncodeJob {
  type: "encode";
  id: number;
  frame: GrabbedFrame;
  w: number;
  h: number;
  quality: number;
  /** Re-encode at this quality when the first JPEG's base64 is longer than maxB64. */
  retryQuality: number;
  maxB64: number;
  /** Also return the frame's luma (quality check / scene gate). */
  gray: boolean;
}

/** worker -> main */
export type EncodeReply =
  | { type: "done"; id: number; jpeg: string; gray: Float32Array | null }
  | { type: "fail"; id: number; code: "too_large" | "no_canvas" };

/** The parts of a Worker the encoder uses (a fake in tests). */
export interface EncodeWorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void;
  terminate(): void;
  onmessage: ((e: { data: unknown }) => void) | null;
  onerror: ((e: unknown) => void) | null;
}

export interface EncodeOptions {
  quality: number;
  retryQuality: number;
  maxB64: number;
  gray: boolean;
}

export class EncodeFailure extends Error {
  readonly code: "too_large" | "no_canvas" | "worker";
  constructor(code: "too_large" | "no_canvas" | "worker") {
    super(`encode:${code}`);
    this.name = "EncodeFailure";
    this.code = code;
  }
}

export interface WorkerEncoder {
  /** Scales `frame` to w x h and encodes it as JPEG base64 (no `data:` prefix). The frame is transferred/closed. */
  encode(frame: GrabbedFrame, w: number, h: number, o: EncodeOptions): Promise<{ jpeg: string; gray: Float32Array | null }>;
  /** True once the worker crashed or was closed: capture uses the canvas path from then on. */
  readonly dead: boolean;
  close(): void;
}

export function createWorkerEncoder(worker: EncodeWorkerLike): WorkerEncoder {
  const pending = new Map<number, { resolve: (r: { jpeg: string; gray: Float32Array | null }) => void; reject: (e: Error) => void }>();
  let seq = 0;
  let dead = false;

  const die = () => {
    if (dead) return;
    dead = true;
    for (const p of pending.values()) p.reject(new EncodeFailure("worker"));
    pending.clear();
    try { worker.terminate(); } catch { /* already gone */ }
  };

  worker.onmessage = (e) => {
    const m = e.data as EncodeReply | null;
    if (!m || typeof m !== "object") return;
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.type === "done") p.resolve({ jpeg: m.jpeg, gray: m.gray ?? null });
    else p.reject(new EncodeFailure(m.code));
  };
  worker.onerror = () => die();

  return {
    get dead() { return dead; },
    encode(frame, w, h, o) {
      if (dead) {
        try { frame.close(); } catch { /* ignore */ }
        return Promise.reject(new EncodeFailure("worker"));
      }
      const id = ++seq;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        const job: EncodeJob = { type: "encode", id, frame, w, h, ...o };
        try {
          worker.postMessage(job, [frame]);
        } catch {
          pending.delete(id);
          try { frame.close(); } catch { /* ignore */ }
          die();
          reject(new EncodeFailure("worker"));
        }
      });
    },
    close: die,
  };
}

/** True when this browser can encode in a worker: module workers, OffscreenCanvas with convertToBlob. */
export function workerEncodingSupported(g: typeof globalThis = globalThis): boolean {
  const oc = (g as unknown as { OffscreenCanvas?: { prototype?: { convertToBlob?: unknown } } }).OffscreenCanvas as unknown as ({ prototype?: { convertToBlob?: unknown } } | undefined);
  return typeof (g as { Worker?: unknown }).Worker === "function" && typeof (oc as unknown) === "function"
    && typeof oc?.prototype?.convertToBlob === "function";
}

let shared: WorkerEncoder | null = null;
let failed = false;

/** The app's one encode worker, created on first use; null when unsupported or it could not start. */
export function sharedWorkerEncoder(): WorkerEncoder | null {
  if (shared && !shared.dead) return shared;
  if (failed || !workerEncodingSupported()) return null;
  try {
    const w = new Worker(new URL("./encode.worker.ts", import.meta.url), { type: "module" });
    shared = createWorkerEncoder(w as unknown as EncodeWorkerLike);
    return shared;
  } catch {
    failed = true;
    return null;
  }
}
