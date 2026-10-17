// frontend/app/src/camera/capture.ts
// Owns the single rear-camera stream. Everything else (requests, hand tracking,
// bump guard) reads frames through captureFrame / getVideo so only one stream exists.
import type { ClientMsg, FramePurpose } from "@aira/contracts";
import { app } from "../core/app";
import { clientLog, shortText, type LogData } from "../core/clientLog";
import { toGray, qualityHint, noteFrameQuality } from "./quality";
import { boostPixels } from "./lowlight";
import { overrideSource } from "./source";
import { ensureBackCamera, type BackCameraResult, type CameraFacing } from "./backCamera";
import { beginLooking } from "./looking";
import { ENCODE_TIMEOUT_MS, EncodeFailure, sharedWorkerEncoder, type GrabbedFrame, type WorkerEncoder } from "./encode";
import { BACKGROUND_MAX_BUFFERED as BG_CAP, videoYields, waitForDrain } from "../net/sendPriority";

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
 *  are never queued, so at most one sits ahead of an owner frame on a slow uplink. The lowest cap of all
 *  (net/sendPriority): mic audio, tool frames and the live video all go before them. */
export const PREVIEW_MAX_BUFFERED = BG_CAP;
export const BACKGROUND_MAX_BUFFERED = PREVIEW_MAX_BUFFERED;

/** The owner's own camera questions (ask, count, identify, point): full detail, so ER 2 can count packs. */
export const PRIORITY_MAX_SIDE = 1280;
export const PRIORITY_QUALITY = 0.9;
const PRIORITY: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["ask", "count", "identify", "point"]);
/** Count frames: 1024 px long side at quality 0.7 (about 110 KB). A busy Galaxy A22 took 4-13 s to encode a
 *  1280 px count photo on the main thread (2026-10-13); the server reads this one frame twice. */
export const COUNT_MAX_SIDE = 1024;
export const COUNT_QUALITY = 0.7;
/** Tool frame requests that pause live, describe and preview frames (and on-phone MediaPipe) until they are sent. */
const HOLDS: ReadonlySet<FramePurpose> = new Set<FramePurpose>([...PRIORITY, "walk", "invoice", "cash"]);
/** Walk with me (purpose `walk`, about 1 a second): enough detail for ER to see the target and floor obstacles. */
export const WALK_MAX_SIDE = 1024;
export const WALK_QUALITY = 0.75;
/** Background streams that wait while an owner question captures: scene narration and the monitor preview. */
const PAUSABLE: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["describe", "preview"]);

export function isPriorityPurpose(purpose: FramePurpose): boolean {
  return PRIORITY.has(purpose);
}

/** True for a tool frame request (ask, count, identify, point, walk, invoice, cash) that holds background frames. */
export function holdsBackground(purpose: FramePurpose): boolean {
  return HOLDS.has(purpose);
}
/** Gemini Live video (purpose `live`): one frame a second, 640 px long side, quality 0.5. At 768 px / 0.7 an
 *  iPhone sent ~93 KB frames (up to 129 KB) and the owner's speech queued behind them on the uplink; Live only
 *  needs a scene-level view (exact reading and counting use their own sharper tool frames). */
export const LIVE_MAX_SIDE = 640;
export const LIVE_QUALITY = 0.5;

/** How big and how good a frame of this purpose is. */
export function frameSpec(purpose: FramePurpose): { maxSide: number; quality: number } {
  if (purpose === "preview") return { maxSide: PREVIEW_MAX_SIDE, quality: PREVIEW_QUALITY };
  if (purpose === "count") return { maxSide: COUNT_MAX_SIDE, quality: COUNT_QUALITY };
  if (PRIORITY.has(purpose)) return { maxSide: PRIORITY_MAX_SIDE, quality: PRIORITY_QUALITY };
  if (purpose === "live") return { maxSide: LIVE_MAX_SIDE, quality: LIVE_QUALITY };
  if (purpose === "walk") return { maxSide: WALK_MAX_SIDE, quality: WALK_QUALITY };
  return { maxSide: MAX_SIDE, quality: JPEG_QUALITY };
}

// Tool frames (anything but the live video and the monitor preview) beat the live video: while one is being
// captured, and once one started during a live capture, the live frame is skipped.
let toolCaptures = 0;
let heldCaptures = 0;
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
let liveCaptures = 0;

/** True while a Gemini Live video frame is being captured: preview and describe frames yield to it (they skip
 *  their tick, and one finished meanwhile is dropped), so the live frame is the next thing on the socket. */
export function liveCaptureBusy(): boolean {
  return liveCaptures > 0;
}

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

/** True while an owner tool frame (ask, count, identify, point, walk, invoice, cash) is requested or capturing:
 *  on-phone MediaPipe (bump guard, room boxes, hand tracking) skips its passes so the phone's CPU goes to it. */
export function detectionPaused(): boolean {
  return backgroundHolds > 0 || heldCaptures > 0;
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
  /** The current camera frame as a transferable VideoFrame / ImageBitmap for the encode worker. Optional: a
   *  source without it (demo media, tests) always encodes on the canvas path. */
  grab?(): Promise<GrabbedFrame>;
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
  /** The off-main-thread JPEG encoder; null = canvas path (unsupported browser, jsdom). */
  worker: () => WorkerEncoder | null;
}
const deps: CaptureDeps = { source: null, encoder: defaultEncoder, makeCanvas: defaultMakeCanvas, worker: sharedWorkerEncoder };

/** Frames whose JPEG was encoded in the worker (for the frame_req diagnostics). */
const viaWorker = new WeakSet<object>();
/** True when this frame's JPEG was made by the encode worker, false when on the main-thread canvas. */
export function encodedInWorker(f: FrameMsg): boolean {
  return viaWorker.has(f);
}

/** The camera frame for the worker: a VideoFrame when the browser has one (no copy on this thread), else an
 *  ImageBitmap of the <video>. */
export async function grabVideoFrame(v: HTMLVideoElement): Promise<GrabbedFrame> {
  const VF = (globalThis as { VideoFrame?: new (src: HTMLVideoElement) => GrabbedFrame }).VideoFrame;
  if (typeof VF === "function") {
    try {
      return new VF(v);
    } catch {
      /* a video without a decoded frame yet: try createImageBitmap */
    }
  }
  return createImageBitmap(v);
}

/** Tests only: swap the frame source, encoder or canvas factory. */
export function __setCaptureDeps(p: Partial<CaptureDeps>): void {
  Object.assign(deps, p);
  probeCanvas = null;
}

let stream: MediaStream | null = null;
let video: HTMLVideoElement | null = null;
let starting: Promise<HTMLVideoElement> | null = null;
let qualityListener: ((h: "dark" | "blurry") => void) | null = null;
let qualityCounter = 0;
// Low-light boost (AIRA_LOWLIGHT): a gain above 1 brightens the pixels of UPLOADED frames (tool, live, describe)
// right before they are encoded, on the canvas path only (the encode worker gets raw pixels, so a boosted frame
// skips it). The quality check and the reported luma read the raw pixels first. The monitor preview and the bump
// guard are never boosted: the preview is for the dashboard, and the guard grabs the <video> itself (walk/start.ts).
let boostGain = 1;

/** The upload boost gain (1 = off). Set by the camera wiring from the light watch; null clears it. */
export function setFrameBoost(gain: number | null): void {
  boostGain = gain !== null && Number.isFinite(gain) && gain > 1 ? gain : 1;
}

export function frameBoost(): number {
  return boostGain;
}
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
  deps.source = {
    width: () => v.videoWidth,
    height: () => v.videoHeight,
    draw: (ctx, w, h) => ctx.drawImage(v, 0, 0, w, h),
    grab: () => grabVideoFrame(v),
  };
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

/**
 * A frame the server asked for (single or burst request), ready to hand to the socket: captured at once, then held
 * until the socket backlog is below TOOL_SEND_BUFFERED, at most TOOL_DRAIN_MAX_MS (net/sendPriority). The owner's
 * mic audio keeps flowing during the wait, and the frame never lands on top of a backlog it would only lengthen.
 */
export async function captureToolFrame(purpose: FramePurpose): Promise<FrameMsg> {
  const f = await captureFrame(purpose);
  const r = await waitForDrain(socketBuffered);
  if (r.waitedMs > 0) clientLog("tool_drain", { purpose, waitedMs: Math.round(r.waitedMs), drained: r.drained }, { minGapMs: 1000 });
  return f;
}

/** Like captureFrame, but returns null (without encoding) when `keep` says the frame is not worth sending. */
export async function captureFrameIf(purpose: FramePurpose, keep?: FrameKeep): Promise<FrameMsg | null> {
  if (purpose === "live") {
    liveCaptures++;
    try {
      return await captureFrameRaw(purpose, keep);
    } finally {
      liveCaptures--;
    }
  }
  if (!isToolPurpose(purpose)) return captureFrameRaw(purpose, keep);
  const held = HOLDS.has(purpose);
  toolCaptures++;
  if (held) heldCaptures++;
  toolCaptureSeq++;
  const endLooking = beginLooking(purpose); // the preview's "AIRA is looking" badge (display only)
  try {
    return await captureFrameRaw(purpose, keep);
  } finally {
    toolCaptures--;
    if (held) heldCaptures--;
    endLooking();
  }
}

async function captureFrameRaw(purpose: FramePurpose, keep?: FrameKeep): Promise<FrameMsg | null> {
  // Demo mode (/try): draw the demo media and never open the camera. null = real camera.
  const demo = overrideSource();
  if (!demo && !deps.source) await startCamera();
  const src: FrameSource | null = demo ?? deps.source;
  if (!src) throw new CameraError("no_frame");
  const sw = src.width();
  const sh = src.height();
  if (!sw || !sh) throw new CameraError("no_frame");
  const spec = frameSpec(purpose);
  const { w, h } = fitWithin(sw, sh, spec.maxSide);
  // Quality check on every 3rd frame; the keep gate reads the same gray copy.
  // Preview frames are for the dashboard only: they never earn the owner a "hold still" hint.
  const checkQuality = isToolPurpose(purpose) && qualityListener !== null && ++qualityCounter % 3 === 0;
  const retryQuality = Math.min(spec.quality, JPEG_RETRY_QUALITY);
  // Low light: boost the pixels Gemini sees (never the monitor preview). A boosted frame takes the canvas path.
  const boost = boostGain > 1 && purpose !== "preview" ? boostGain : 1;
  // The worker path: the frame is scaled and encoded off the main thread. Any worker trouble (other than a frame
  // that is too big at both qualities) falls back to the canvas path below, for this frame and, once the worker
  // died, for good.
  const worker = !demo && src.grab && boost === 1 ? deps.worker() : null;
  if (worker && !worker.dead) {
    let out: { jpeg: string; gray: Float32Array | null } | null = null;
    let grabbedAt = Date.now();
    try {
      grabbedAt = Date.now(); // the picture's moment: before the grab, long before the encode
      // The grab is bounded too: a camera that never hands over a frame must not hold the capture (and with it
      // the tool holds that pause live video and MediaPipe) forever. The encode itself times out in the encoder.
      const frame = await grabWithin(src.grab!, ENCODE_TIMEOUT_MS);
      out = await worker.encode(frame, w, h, { quality: spec.quality, retryQuality, maxB64: MAX_B64, gray: checkQuality || !!keep });
    } catch (e) {
      if (e instanceof EncodeFailure && e.code === "too_large") throw new CameraError("too_large");
      if (e instanceof EncodeFailure && e.code === "timeout") clientLog("encode_timeout", { purpose, w, h }, { minGapMs: 2000 });
      out = null; // this frame goes the canvas way below
    }
    if (out) {
      const quality = out.gray ? checkFrame(out.gray, w, h, checkQuality) : "skipped";
      if (keep && out.gray && !keep(out.gray, w, h)) return null;
      return finish(purpose, out.jpeg, w, h, quality, false, true, grabbedAt);
    }
  }
  const { canvas, ctx } = deps.makeCanvas(w, h);
  if (!ctx) throw new CameraError("no_canvas");
  const drawnAt = Date.now(); // the picture's moment: the draw copies the current video frame
  src.draw(ctx, w, h);
  let quality: "ok" | "dark" | "blurry" | "skipped" = "skipped";
  if (checkQuality || keep || boost > 1) {
    const img = ctx.getImageData(0, 0, w, h);
    if (checkQuality || keep) { // the raw picture is judged BEFORE any boost: a boosted frame never hides "dark"
      const gray = toGray(img.data, w, h);
      quality = checkFrame(gray, w, h, checkQuality);
      if (keep && !keep(gray, w, h)) return null;
    }
    if (boost > 1) {
      boostPixels(img.data, boost);
      ctx.putImageData(img, 0, 0);
    }
  }
  let jpeg = await deps.encoder(canvas, spec.quality);
  if (jpeg.length > MAX_B64) jpeg = await deps.encoder(canvas, retryQuality);
  if (jpeg.length > MAX_B64) throw new CameraError("too_large");
  return finish(purpose, jpeg, w, h, quality, demo !== null, false, drawnAt);
}

/** Resolves the grabbed frame, or rejects with EncodeFailure("timeout") after `ms`; a frame arriving late is closed. */
function grabWithin(grab: () => Promise<GrabbedFrame>, ms: number): Promise<GrabbedFrame> {
  return new Promise((resolve, reject) => {
    let done = false;
    const t = setTimeout(() => { done = true; reject(new EncodeFailure("timeout")); }, ms);
    grab().then(
      (f) => {
        if (done) { try { f.close(); } catch { /* ignore */ } return; }
        done = true;
        clearTimeout(t);
        resolve(f);
      },
      (e: unknown) => {
        if (done) return;
        done = true;
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

/** The quality hint for a tool frame's gray copy (every 3rd frame), told to the hint listener. */
function checkFrame(gray: Float32Array, w: number, h: number, check: boolean): "ok" | "dark" | "blurry" | "skipped" {
  if (!check || !qualityListener) return "skipped";
  const hint = qualityHint(gray, w, h);
  noteFrameQuality(hint);
  if (hint) qualityListener(hint);
  return hint ?? "ok";
}

/** `captureTs` is when the picture was grabbed (phone clock, before encoding); `ts` is stamped now, after it. */
function finish(
  purpose: FramePurpose, jpeg: string, w: number, h: number, quality: string, demo: boolean, worker: boolean, captureTs: number,
): FrameMsg {
  // Diagnostics: the frame about to go out, by size only (never the jpeg). At most a few lines a second.
  clientLog("frame", { purpose, bytes: jpeg.length, w, h, quality, demo, worker }, { minGapMs: 250 });
  const f: FrameMsg = { t: "frame", id: nextId(), jpeg, w, h, ts: Date.now(), purpose, captureTs };
  if (worker) viaWorker.add(f);
  return f;
}

let probeCanvas: { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null; w: number; h: number } | null = null;

/**
 * A cheap gray grid (gw x gh, default 32x24) of the camera right now: one tiny canvas draw, no encode. The live
 * video's show detector samples it a few times a second. null while the camera is not ready, while an owner
 * tool photo has the CPU, or on any canvas trouble. Never opens the camera.
 */
export function probeGrid(gw = 32, gh = 24): Float32Array | null {
  try {
    if (detectionPaused()) return null;
    const src: FrameSource | null = overrideSource() ?? deps.source;
    if (!src || !src.width() || !src.height()) return null;
    if (!probeCanvas || probeCanvas.w !== gw || probeCanvas.h !== gh) probeCanvas = { ...deps.makeCanvas(gw, gh), w: gw, h: gh };
    const ctx = probeCanvas.ctx;
    if (!ctx) return null;
    src.draw(ctx, gw, gh);
    return toGray(ctx.getImageData(0, 0, gw, gh).data, gw, gh);
  } catch {
    return null;
  }
}

const socketBuffered = (): number => {
  try { return app.socket?.bufferedAmount?.() ?? 0; } catch { return 0; }
};

/** True while the socket still holds more than BACKGROUND_MAX_BUFFERED bytes it could not hand to the network,
 *  or while the owner is speaking or his mic audio is backed up on it (background frames yield to speech). */
export function socketBusy(): boolean {
  try {
    if (videoYields()) return true;
    return socketBuffered() > BACKGROUND_MAX_BUFFERED;
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
    if (pausable && detectionPaused()) return; // the owner's question has the camera and the socket
    if (dropWhenBusy && socketBusy()) return; // the last picture is still on its way: this one is not worth encoding
    if (pausable && liveCaptureBusy()) return; // a live video frame goes first: Gemini sees it, the dashboard can wait
    busy = true;
    try {
      const f = await captureFrameIf(purpose, keep);
      // A continuous tool stream (walk, guide, identify...) never piles frames onto a backed-up socket: it waits,
      // bounded by TOOL_DRAIN_MAX_MS, for the buffer to drain (mic audio keeps flowing meanwhile), then sends.
      // `busy` stays set, so no second capture starts while it waits.
      if (f && !stopped && !pausable && isToolPurpose(purpose)) await waitForDrain(socketBuffered);
      if (f && !stopped && !(pausable && (detectionPaused() || liveCaptureBusy())) && !(dropWhenBusy && socketBusy())) app.send(f);
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
