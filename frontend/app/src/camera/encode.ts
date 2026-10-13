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

/** One worker job (and the frame grab before it) gets this long. The worker runs jobs one at a time, so a stuck
 *  job would stall every later one, and with it the tool holds that pause live video and MediaPipe: on a timeout
 *  the worker is terminated, every pending job is rejected and capture falls back to its canvas path. */
export const ENCODE_TIMEOUT_MS = 4_000;
/** After this many timed-out workers the app stops making new ones and stays on the canvas path. */
export const MAX_WORKER_TIMEOUTS = 2;

export type EncodeFailureCode = "too_large" | "no_canvas" | "worker" | "timeout";

export class EncodeFailure extends Error {
  readonly code: EncodeFailureCode;
  constructor(code: EncodeFailureCode) {
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
  /** True when it died because a job ran past its timeout. */
  readonly timedOut: boolean;
  close(): void;
}

export function createWorkerEncoder(worker: EncodeWorkerLike, opts: { timeoutMs?: number } = {}): WorkerEncoder {
  const timeoutMs = opts.timeoutMs ?? ENCODE_TIMEOUT_MS;
  type Pending = { resolve: (r: { jpeg: string; gray: Float32Array | null }) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> | null };
  const pending = new Map<number, Pending>();
  let seq = 0;
  let dead = false;
  let timedOut = false;

  const die = (code: EncodeFailureCode = "worker") => {
    if (dead) return;
    dead = true;
    for (const p of pending.values()) {
      if (p.timer !== null) clearTimeout(p.timer);
      p.reject(new EncodeFailure(code));
    }
    pending.clear();
    try { worker.terminate(); } catch { /* already gone */ }
  };

  worker.onmessage = (e) => {
    const m = e.data as EncodeReply | null;
    if (!m || typeof m !== "object") return;
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (p.timer !== null) clearTimeout(p.timer);
    if (m.type === "done") p.resolve({ jpeg: m.jpeg, gray: m.gray ?? null });
    else p.reject(new EncodeFailure(m.code));
  };
  worker.onerror = () => die();

  return {
    get dead() { return dead; },
    get timedOut() { return timedOut; },
    encode(frame, w, h, o) {
      if (dead) {
        try { frame.close(); } catch { /* ignore */ }
        return Promise.reject(new EncodeFailure("worker"));
      }
      const id = ++seq;
      return new Promise((resolve, reject) => {
        // A job the worker never answers: the worker is stuck (its queue is serial), so kill it and fail them all.
        const timer = timeoutMs > 0 && Number.isFinite(timeoutMs)
          ? setTimeout(() => { if (!pending.has(id)) return; timedOut = true; die("timeout"); }, timeoutMs)
          : null;
        pending.set(id, { resolve, reject, timer });
        const job: EncodeJob = { type: "encode", id, frame, w, h, ...o };
        try {
          worker.postMessage(job, [frame]);
        } catch {
          pending.delete(id);
          if (timer !== null) clearTimeout(timer);
          try { frame.close(); } catch { /* ignore */ }
          die();
          reject(new EncodeFailure("worker"));
        }
      });
    },
    close: () => die(),
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
let timeouts = 0;

/** The app's one encode worker, created on first use; null when unsupported or it could not start. A worker that
 *  died is replaced on the next call, until MAX_WORKER_TIMEOUTS of them timed out: then the canvas path for good. */
export function sharedWorkerEncoder(
  make: () => EncodeWorkerLike = () => new Worker(new URL("./encode.worker.ts", import.meta.url), { type: "module" }) as unknown as EncodeWorkerLike,
  supported: () => boolean = () => workerEncodingSupported(),
): WorkerEncoder | null {
  if (shared && !shared.dead) return shared;
  if (shared?.timedOut) {
    timeouts++;
    shared = null;
    if (timeouts >= MAX_WORKER_TIMEOUTS) failed = true;
  }
  if (failed || !supported()) return null;
  try {
    shared = createWorkerEncoder(make());
    return shared;
  } catch {
    failed = true;
    return null;
  }
}

/** Tests only: forget the shared worker and its timeout count. */
export function __resetSharedWorkerEncoder(): void {
  try { shared?.close(); } catch { /* ignore */ }
  shared = null;
  failed = false;
  timeouts = 0;
}
