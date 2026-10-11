// frontend/app/src/camera/capture.ts
// Owns the single rear-camera stream. Everything else (requests, hand tracking,
// bump guard) reads frames through captureFrame / getVideo so only one stream exists.
import type { ClientMsg, FramePurpose } from "@aira/contracts";
import { app } from "../core/app";
import { toGray, qualityHint, noteFrameQuality } from "./quality";
import { overrideSource } from "./source";
import { ensureBackCamera, type CameraFacing } from "./backCamera";

export type FrameMsg = Extract<ClientMsg, { t: "frame" }>;
export type CameraErrorCode = "permission" | "no_frame" | "no_canvas" | "too_large";
export const MAX_SIDE = 640;
export const JPEG_QUALITY = 0.8;
export const JPEG_RETRY_QUALITY = 0.6;
export const MAX_B64 = 1_500_000;

export class CameraError extends Error {
  readonly code: CameraErrorCode;
  constructor(code: CameraErrorCode) {
    super(`camera:${code}`);
    this.name = "CameraError";
    this.code = code;
  }
}

export interface FrameSource {
  width(): number;
  height(): number;
  draw(ctx: CanvasRenderingContext2D, w: number, h: number): void;
}
/** Encodes the canvas as JPEG and returns base64 without the `data:` prefix. */
export type Encoder = (canvas: HTMLCanvasElement, quality: number) => Promise<string>;
export type MakeCanvas = (w: number, h: number) => { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null };

const defaultEncoder: Encoder = (canvas, q) =>
  new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => {
        if (!b) return reject(new CameraError("no_canvas"));
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
        r.onerror = () => reject(r.error ?? new CameraError("no_canvas"));
        r.readAsDataURL(b);
      },
      "image/jpeg",
      q,
    );
  });

const defaultMakeCanvas: MakeCanvas = (w, h) => {
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  return { canvas, ctx: canvas.getContext("2d", { willReadFrequently: true }) };
};

interface CaptureDeps {
  source: FrameSource | null;
  encoder: Encoder;
  makeCanvas: MakeCanvas;
}
const deps: CaptureDeps = { source: null, encoder: defaultEncoder, makeCanvas: defaultMakeCanvas };

/** Tests only: swap the frame source, encoder or canvas factory. */
export function __setCaptureDeps(p: Partial<CaptureDeps>): void {
  Object.assign(deps, p);
}

let stream: MediaStream | null = null;
let video: HTMLVideoElement | null = null;
let starting: Promise<HTMLVideoElement> | null = null;
let qualityListener: ((h: "dark" | "blurry") => void) | null = null;
let qualityCounter = 0;
let seq = 0;
let facing: CameraFacing = "unknown";

/** Which side the open camera faces. "user" means no back camera was found (callers may say so once). */
export function cameraFacing(): CameraFacing {
  return facing;
}

export function setQualityListener(fn: ((h: "dark" | "blurry") => void) | null): void {
  qualityListener = fn;
  qualityCounter = 0;
}

/** The shared camera <video>, or null when the camera is off (used by guidance/hands.ts). */
export function getVideo(): HTMLVideoElement | null {
  return video;
}

/** Scales (w, h) so the longest side is at most `max`. Never upscales. */
export function fitWithin(w: number, h: number, max = MAX_SIDE): { w: number; h: number } {
  const s = Math.min(1, max / Math.max(w, h));
  return { w: Math.round(w * s), h: Math.round(h * s) };
}

const nextId = () => `f${Date.now().toString(36)}${(++seq).toString(36)}`;

async function openCamera(): Promise<HTMLVideoElement> {
  let s: MediaStream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("no getUserMedia");
    let storage: Storage | null = null;
    try {
      storage = globalThis.localStorage ?? null;
    } catch {
      storage = null;
    }
    const r = await ensureBackCamera(navigator.mediaDevices, storage);
    s = r.stream;
    facing = r.facing;
  } catch {
    throw new CameraError("permission");
  }
  const v = document.createElement("video");
  v.setAttribute("playsinline", "true");
  v.muted = true;
  v.srcObject = s;
  try {
    await v.play();
  } catch {
    // Autoplay can be refused before a user gesture; the size check in captureFrame reports no_frame.
  }
  stream = s;
  video = v;
  deps.source = { width: () => v.videoWidth, height: () => v.videoHeight, draw: (ctx, w, h) => ctx.drawImage(v, 0, 0, w, h) };
  return v;
}

/** Opens the rear camera once and reuses it. Concurrent callers share the same open. */
export async function startCamera(): Promise<HTMLVideoElement> {
  if (video && stream?.active) return video;
  if (!starting) {
    starting = openCamera().finally(() => {
      starting = null;
    });
  }
  return starting;
}

export function stopCamera(): void {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  video = null;
  facing = "unknown";
  deps.source = null;
}

/** Decides whether a captured frame is sent (describe mode's scene-change gate). Gets the gray frame. */
export type FrameKeep = (gray: Float32Array, w: number, h: number) => boolean;

export async function captureFrame(purpose: FramePurpose): Promise<FrameMsg> {
  return (await captureFrameIf(purpose)) as FrameMsg; // without a keep gate every frame is returned
}

/** Like captureFrame, but returns null (without encoding) when `keep` says the frame is not worth sending. */
export async function captureFrameIf(purpose: FramePurpose, keep?: FrameKeep): Promise<FrameMsg | null> {
  // Demo mode (/try): draw the demo media and never open the camera. null = real camera.
  const demo = overrideSource();
  if (!demo && !deps.source) await startCamera();
  const src = demo ?? deps.source;
  if (!src) throw new CameraError("no_frame");
  const sw = src.width();
  const sh = src.height();
  if (!sw || !sh) throw new CameraError("no_frame");
  const { w, h } = fitWithin(sw, sh);
  const { canvas, ctx } = deps.makeCanvas(w, h);
  if (!ctx) throw new CameraError("no_canvas");
  src.draw(ctx, w, h);
  // Quality check on every 3rd frame (cheap enough at 640 px); the keep gate reads the same gray copy.
  const checkQuality = qualityListener !== null && ++qualityCounter % 3 === 0;
  if (checkQuality || keep) {
    const img = ctx.getImageData(0, 0, w, h);
    const gray = toGray(img.data, w, h);
    if (checkQuality && qualityListener) {
      const hint = qualityHint(gray, w, h);
      noteFrameQuality(hint);
      if (hint) qualityListener(hint);
    }
    if (keep && !keep(gray, w, h)) return null;
  }
  let jpeg = await deps.encoder(canvas, JPEG_QUALITY);
  if (jpeg.length > MAX_B64) jpeg = await deps.encoder(canvas, JPEG_RETRY_QUALITY);
  if (jpeg.length > MAX_B64) throw new CameraError("too_large");
  return { t: "frame", id: nextId(), jpeg, w, h, ts: Date.now(), purpose };
}

/** Sends frames via app.send at `fps` until the returned stop fn is called. Never overlaps captures.
 *  With `keep` (describe mode), a frame the gate refuses is dropped without being encoded or sent. */
export function startContinuous(purpose: FramePurpose, fps: number, keep?: FrameKeep): () => void {
  const period = Math.max(100, Math.round(1000 / Math.max(0.1, fps)));
  let stopped = false;
  let busy = false;
  const tick = async () => {
    if (stopped || busy) return;
    busy = true;
    try {
      const f = await captureFrameIf(purpose, keep);
      if (f && !stopped) app.send(f);
    } catch {
      // Camera errors are surfaced by the request handler and the hint speaker.
    } finally {
      busy = false;
    }
  };
  const id = setInterval(tick, period);
  void tick();
  return () => {
    stopped = true;
    clearInterval(id);
  };
}
