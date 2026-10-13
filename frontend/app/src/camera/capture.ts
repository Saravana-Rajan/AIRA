// frontend/app/src/camera/capture.ts
// Owns the single rear-camera stream. Everything else (requests, hand tracking,
// bump guard) reads frames through captureFrame / getVideo so only one stream exists.
import type { ClientMsg, FramePurpose } from "@aira/contracts";
import { app } from "../core/app";
import { clientLog, shortText, type LogData } from "../core/clientLog";
import { toGray, qualityHint, noteFrameQuality } from "./quality";
import { overrideSource } from "./source";
import { ensureBackCamera, type BackCameraResult, type CameraFacing } from "./backCamera";
import { beginLooking } from "./looking";

export type FrameMsg = Extract<ClientMsg, { t: "frame" }>;
export type CameraErrorCode = "permission" | "no_frame" | "no_canvas" | "too_large";
export const MAX_SIDE = 640;
export const JPEG_QUALITY = 0.8;
export const JPEG_RETRY_QUALITY = 0.6;
export const MAX_B64 = 1_500_000;
/** The monitor preview (purpose `preview`): a small, light picture 8 times a second, never seen by Gemini. */
export const PREVIEW_MAX_SIDE = 360;
export const PREVIEW_QUALITY = 0.45;
/** A preview or describe tick is skipped while more than this many bytes wait on the socket: background frames
 *  are never queued, so at most one sits ahead of an owner frame on a slow uplink. */
export const PREVIEW_MAX_BUFFERED = 32_000;
export const BACKGROUND_MAX_BUFFERED = PREVIEW_MAX_BUFFERED;

/** The owner's own camera questions (ask, count, identify, point): full detail, so ER 2 can count packs. */
export const PRIORITY_MAX_SIDE = 1280;
export const PRIORITY_QUALITY = 0.9;
const PRIORITY: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["ask", "count", "identify", "point"]);
/** Count frames keep the 1280 px detail but encode lighter (about 120 KB instead of 230 KB): two of them must
 *  cross a congested phone uplink inside the server's wait. */
export const COUNT_QUALITY = 0.75;
/** Tool frame requests that pause live, describe and preview frames until they are sent. */
const HOLDS: ReadonlySet<FramePurpose> = new Set<FramePurpose>([...PRIORITY, "walk"]);
/** Walk with me (purpose `walk`, about 1 a second): enough detail for ER to see the target and floor obstacles. */
export const WALK_MAX_SIDE = 1024;
export const WALK_QUALITY = 0.75;
/** Background streams that wait while an owner question captures: scene narration and the monitor preview. */
const PAUSABLE: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["describe", "preview"]);

export function isPriorityPurpose(purpose: FramePurpose): boolean {
  return PRIORITY.has(purpose);
}

/** True for a tool frame request (ask, count, identify, point, walk) that holds background frames. */
export function holdsBackground(purpose: FramePurpose): boolean {
  return HOLDS.has(purpose);
}
/** Gemini Live video (purpose `live`): one frame a second, 768 px long side, quality 0.7 (Live API guidance). */
export const LIVE_MAX_SIDE = 768;
export const LIVE_QUALITY = 0.7;

/** How big and how good a frame of this purpose is. */
export function frameSpec(purpose: FramePurpose): { maxSide: number; quality: number } {
  if (purpose === "preview") return { maxSide: PREVIEW_MAX_SIDE, quality: PREVIEW_QUALITY };
  if (purpose === "count") return { maxSide: PRIORITY_MAX_SIDE, quality: COUNT_QUALITY };
  if (PRIORITY.has(purpose)) return { maxSide: PRIORITY_MAX_SIDE, quality: PRIORITY_QUALITY };
  if (purpose === "live") return { maxSide: LIVE_MAX_SIDE, quality: LIVE_QUALITY };
  if (purpose === "walk") return { maxSide: WALK_MAX_SIDE, quality: WALK_QUALITY };
  return { maxSide: MAX_SIDE, quality: JPEG_QUALITY };
}

// Tool frames (anything but the live video and the monitor preview) beat the live video: while one is being
// captured, and once one started during a live capture, the live frame is skipped.
let toolCaptures = 0;
let toolCaptureSeq = 0;
const isToolPurpose = (p: FramePurpose) => p !== "live" && p !== "preview";
/** True while a tool-requested frame (ask, count, identify, ...) is being captured. */
export function toolCaptureBusy(): boolean {
  return toolCaptures > 0;
}
/** Grows by one each time a tool-requested capture starts. */
export function toolCaptureCount(): number {
  return toolCaptureSeq;
}

let backgroundHolds = 0;

/** An owner question is capturing: describe and preview frames pause (and one mid-encode is dropped) until
 *  the returned release fn is called. Holds nest; releasing twice is harmless. */
export function holdBackground(): () => void {
  backgroundHolds++;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    backgroundHolds = Math.max(0, backgroundHolds - 1);
  };
}

export function backgroundHeld(): boolean {
  return backgroundHolds > 0;
}

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
let denied = false;

/** True once opening the camera failed (permission refused or no camera); cleared when it opens. */
export function cameraDenied(): boolean {
  return denied;
}

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

/** What the diagnostics say about an open camera: facing, the track's label and settings. Never pixels. */
export function describeCamera(r: BackCameraResult): LogData {
  let label = "";
  let settings: MediaTrackSettings = {};
  try {
    const track = r.stream.getVideoTracks?.()[0];
    label = track?.label ?? "";
    settings = track?.getSettings?.() ?? {};
  } catch {
    /* a stream without tracks: report what we know */
  }
  return {
    facing: r.facing,
    label: shortText(label),
    width: settings.width ?? null,
    height: settings.height ?? null,
    facingMode: settings.facingMode ?? null,
    deviceId: r.deviceId ? r.deviceId.slice(0, 8) : null,
  };
}

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
    clientLog("camera", describeCamera(r));
  } catch (e) {
    const err = e as { name?: string; message?: string } | undefined;
    clientLog("camera", { error: shortText(String(err?.name ?? "Error")), message: shortText(String(err?.message ?? "")) });
    denied = true;
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
  denied = false;
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
  if (!isToolPurpose(purpose)) return captureFrameRaw(purpose, keep);
  toolCaptures++;
  toolCaptureSeq++;
  const endLooking = beginLooking(purpose); // the preview's "AIRA is looking" badge (display only)
  try {
    return await captureFrameRaw(purpose, keep);
  } finally {
    toolCaptures--;
    endLooking();
  }
}

async function captureFrameRaw(purpose: FramePurpose, keep?: FrameKeep): Promise<FrameMsg | null> {
  // Demo mode (/try): draw the demo media and never open the camera. null = real camera.
  const demo = overrideSource();
  if (!demo && !deps.source) await startCamera();
  const src = demo ?? deps.source;
  if (!src) throw new CameraError("no_frame");
  const sw = src.width();
  const sh = src.height();
  if (!sw || !sh) throw new CameraError("no_frame");
  const spec = frameSpec(purpose);
  const { w, h } = fitWithin(sw, sh, spec.maxSide);
  const { canvas, ctx } = deps.makeCanvas(w, h);
  if (!ctx) throw new CameraError("no_canvas");
  src.draw(ctx, w, h);
  // Quality check on every 3rd frame (cheap enough at 640 px); the keep gate reads the same gray copy.
  // Preview frames are for the dashboard only: they never earn the owner a "hold still" hint.
  let quality: "ok" | "dark" | "blurry" | "skipped" = "skipped";
  const checkQuality = isToolPurpose(purpose) && qualityListener !== null && ++qualityCounter % 3 === 0;
  if (checkQuality || keep) {
    const img = ctx.getImageData(0, 0, w, h);
    const gray = toGray(img.data, w, h);
    if (checkQuality && qualityListener) {
      const hint = qualityHint(gray, w, h);
      noteFrameQuality(hint);
      quality = hint ?? "ok";
      if (hint) qualityListener(hint);
    }
    if (keep && !keep(gray, w, h)) return null;
  }
  let jpeg = await deps.encoder(canvas, spec.quality);
  if (jpeg.length > MAX_B64) jpeg = await deps.encoder(canvas, Math.min(spec.quality, JPEG_RETRY_QUALITY));
  if (jpeg.length > MAX_B64) throw new CameraError("too_large");
  // Diagnostics: the frame about to go out, by size only (never the jpeg). At most a few lines a second.
  clientLog("frame", { purpose, bytes: jpeg.length, w, h, quality, demo: demo !== null }, { minGapMs: 250 });
  return { t: "frame", id: nextId(), jpeg, w, h, ts: Date.now(), purpose };
}

/** True while the socket still holds more than BACKGROUND_MAX_BUFFERED bytes it could not hand to the network. */
export function socketBusy(): boolean {
  try {
    return (app.socket?.bufferedAmount?.() ?? 0) > BACKGROUND_MAX_BUFFERED;
  } catch {
    return false;
  }
}

/** Sends frames via app.send at `fps` until the returned stop fn is called. Never overlaps captures.
 *  With `keep` (describe mode), a frame the gate refuses is dropped without being encoded or sent.
 *  A preview or describe frame is sent the moment it is encoded, and dropped (never queued) while the socket is
 *  busy: on a slow uplink a queued background frame would sit ahead of the owner's next tool frame. */
export function startContinuous(purpose: FramePurpose, fps: number, keep?: FrameKeep): () => void {
  const period = Math.max(100, Math.round(1000 / Math.max(0.1, fps)));
  const pausable = PAUSABLE.has(purpose);
  const dropWhenBusy = pausable;
  let stopped = false;
  let busy = false;
  const tick = async () => {
    if (stopped || busy) return;
    if (pausable && backgroundHeld()) return; // the owner's question has the camera and the socket
    if (dropWhenBusy && socketBusy()) return; // the last picture is still on its way: this one is not worth encoding
    busy = true;
    try {
      const f = await captureFrameIf(purpose, keep);
      if (f && !stopped && !(pausable && backgroundHeld()) && !(dropWhenBusy && socketBusy())) app.send(f);
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
