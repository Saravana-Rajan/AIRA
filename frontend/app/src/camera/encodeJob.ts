// frontend/app/src/camera/encodeJob.ts — one encode job as the worker runs it (pure: the canvas factory is
// injected, so tests run it without a worker or a real OffscreenCanvas).
import type { EncodeJob, EncodeReply } from "./encode";
import { toGray } from "./quality";

/** The OffscreenCanvas parts an encode job uses. */
export interface JobCanvas {
  getContext(kind: "2d", opts?: { willReadFrequently?: boolean }): {
    drawImage(img: unknown, x: number, y: number, w: number, h: number): void;
    getImageData(x: number, y: number, w: number, h: number): { data: Uint8ClampedArray };
  } | null;
  convertToBlob(o: { type: string; quality: number }): Promise<{ arrayBuffer(): Promise<ArrayBuffer> }>;
}

/** Base64 of bytes, in chunks (String.fromCharCode on a whole photo would overflow the call stack). */
export function bytesToBase64(bytes: Uint8Array): string {
  let s = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    s += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + CHUNK)));
  }
  return btoa(s);
}

/** Draws, (optionally) reads the luma, encodes, retries once lighter when too big. Always closes the frame. */
export async function runEncodeJob(job: EncodeJob, makeCanvas: (w: number, h: number) => JobCanvas): Promise<EncodeReply> {
  let ctx: ReturnType<JobCanvas["getContext"]> = null;
  let canvas: JobCanvas;
  try {
    canvas = makeCanvas(job.w, job.h);
    ctx = canvas.getContext("2d", { willReadFrequently: job.gray });
    if (!ctx) return { type: "fail", id: job.id, code: "no_canvas" };
    ctx.drawImage(job.frame, 0, 0, job.w, job.h);
  } finally {
    try { job.frame.close(); } catch { /* already closed */ }
  }
  const gray = job.gray ? toGray(ctx.getImageData(0, 0, job.w, job.h).data, job.w, job.h) : null;
  const encode = async (q: number) => {
    const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: q });
    return bytesToBase64(new Uint8Array(await blob.arrayBuffer()));
  };
  let jpeg = await encode(job.quality);
  if (jpeg.length > job.maxB64) jpeg = await encode(Math.min(job.quality, job.retryQuality));
  if (jpeg.length > job.maxB64) return { type: "fail", id: job.id, code: "too_large" };
  return { type: "done", id: job.id, jpeg, gray };
}
