// frontend/app/test/camera.encode.test.ts — JPEG encoding off the main thread (production 2026-10-13: a Galaxy A22
// took 4-13 s to encode one count photo on a busy main thread). The worker, OffscreenCanvas and VideoFrame are faked.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" }, socket: { bufferedAmount: vi.fn(() => 0) } },
}));
vi.mock("../src/core/app", () => ({ app }));

import {
  createWorkerEncoder, EncodeFailure, workerEncodingSupported, type EncodeJob, type EncodeWorkerLike, type WorkerEncoder,
} from "../src/camera/encode";
import { bytesToBase64, runEncodeJob, type JobCanvas } from "../src/camera/encodeJob";
import {
  __setCaptureDeps, captureFrame, captureFrameIf, CameraError, COUNT_MAX_SIDE, COUNT_QUALITY, detectionPaused, encodedInWorker,
  grabVideoFrame, MAX_B64, setQualityListener,
} from "../src/camera/capture";

const fakeCanvas = () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D });

function fakeWorker() {
  const posted: Array<{ msg: EncodeJob; transfer?: Transferable[] }> = [];
  const w: EncodeWorkerLike & { posted: typeof posted; terminated: boolean } = {
    posted,
    terminated: false,
    onmessage: null,
    onerror: null,
    postMessage(msg, transfer) { posted.push({ msg: msg as EncodeJob, transfer }); },
    terminate() { this.terminated = true; },
  };
  return w;
}

const frame = () => ({ close: vi.fn() }) as unknown as { close: () => void } & Transferable;
const OPTS = { quality: 0.7, retryQuality: 0.6, maxB64: 1000, gray: false };

describe("createWorkerEncoder (main side of the encode worker)", () => {
  it("posts the frame (transferred) with size and quality, and resolves with the worker's JPEG", async () => {
    const w = fakeWorker();
    const enc = createWorkerEncoder(w);
    const f = frame();
    const p = enc.encode(f, 576, 1024, OPTS);
    expect(w.posted).toHaveLength(1);
    expect(w.posted[0].msg).toMatchObject({ type: "encode", w: 576, h: 1024, quality: 0.7, retryQuality: 0.6, maxB64: 1000, gray: false });
    expect(w.posted[0].transfer).toEqual([f]);
    w.onmessage!({ data: { type: "done", id: w.posted[0].msg.id, jpeg: "JPEG", gray: null } });
    await expect(p).resolves.toEqual({ jpeg: "JPEG", gray: null });
  });

  it("matches replies to jobs by id (two encodes in flight)", async () => {
    const w = fakeWorker();
    const enc = createWorkerEncoder(w);
    const a = enc.encode(frame(), 10, 10, OPTS);
    const b = enc.encode(frame(), 10, 10, OPTS);
    w.onmessage!({ data: { type: "done", id: w.posted[1].msg.id, jpeg: "B", gray: null } });
    w.onmessage!({ data: { type: "done", id: w.posted[0].msg.id, jpeg: "A", gray: null } });
    expect((await a).jpeg).toBe("A");
    expect((await b).jpeg).toBe("B");
  });

  it("a too_large reply rejects with that code; the worker stays usable", async () => {
    const w = fakeWorker();
    const enc = createWorkerEncoder(w);
    const p = enc.encode(frame(), 10, 10, OPTS);
    w.onmessage!({ data: { type: "fail", id: w.posted[0].msg.id, code: "too_large" } });
    await expect(p).rejects.toMatchObject({ code: "too_large" });
    expect(enc.dead).toBe(false);
  });

  it("a crashed worker rejects every pending job, is terminated and refuses (closing) new frames", async () => {
    const w = fakeWorker();
    const enc = createWorkerEncoder(w);
    const p = enc.encode(frame(), 10, 10, OPTS);
    w.onerror!(new Error("boom"));
    await expect(p).rejects.toBeInstanceOf(EncodeFailure);
    expect(enc.dead).toBe(true);
    expect(w.terminated).toBe(true);
    const f = frame();
    await expect(enc.encode(f, 10, 10, OPTS)).rejects.toMatchObject({ code: "worker" });
    expect(f.close).toHaveBeenCalled();
  });

  it("is supported only with Worker and OffscreenCanvas.convertToBlob (jsdom has neither)", () => {
    expect(workerEncodingSupported()).toBe(false);
    class OC { convertToBlob() { return null; } }
    expect(workerEncodingSupported({ Worker: function W() {}, OffscreenCanvas: OC } as unknown as typeof globalThis)).toBe(true);
    expect(workerEncodingSupported({ OffscreenCanvas: OC } as unknown as typeof globalThis)).toBe(false);
  });
});

describe("runEncodeJob (what the worker does)", () => {
  function jobCanvas(sizes: number[]) {
    const calls: Array<{ type: string; quality: number }> = [];
    const draws: unknown[][] = [];
    const c: JobCanvas = {
      getContext: () => ({
        drawImage: (...a: unknown[]) => { draws.push(a); },
        getImageData: (_x, _y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4).fill(100) }),
      }),
      convertToBlob: async (o) => {
        calls.push(o);
        const n = sizes[Math.min(calls.length - 1, sizes.length - 1)];
        return { arrayBuffer: async () => new Uint8Array(n).fill(65).buffer };
      },
    };
    return { c, calls, draws };
  }
  const job = (over: Partial<EncodeJob> = {}): EncodeJob =>
    ({ type: "encode", id: 7, frame: frame(), w: 4, h: 2, quality: 0.7, retryQuality: 0.6, maxB64: 100, gray: false, ...over });

  it("scales the frame onto the canvas, encodes JPEG at the quality and closes the frame", async () => {
    const { c, calls, draws } = jobCanvas([30]);
    const j = job();
    const r = await runEncodeJob(j, () => c);
    expect(draws[0]).toEqual([j.frame, 0, 0, 4, 2]);
    expect(calls).toEqual([{ type: "image/jpeg", quality: 0.7 }]);
    expect(r).toEqual({ type: "done", id: 7, jpeg: btoa("A".repeat(30)), gray: null });
    expect(j.frame.close).toHaveBeenCalled();
  });

  it("returns the luma when asked (quality check / scene gate)", async () => {
    const { c } = jobCanvas([30]);
    const r = await runEncodeJob(job({ gray: true }), () => c);
    expect(r.type === "done" && r.gray?.length).toBe(8);
  });

  it("retries once at the lighter quality, then gives up as too_large", async () => {
    const big = jobCanvas([300, 30]);
    expect(await runEncodeJob(job(), () => big.c)).toMatchObject({ type: "done" });
    expect(big.calls.map((x) => x.quality)).toEqual([0.7, 0.6]);
    const huge = jobCanvas([300, 300]);
    expect(await runEncodeJob(job(), () => huge.c)).toEqual({ type: "fail", id: 7, code: "too_large" });
  });

  it("bytesToBase64 handles photos bigger than one chunk", () => {
    const bytes = new Uint8Array(100_000).map((_, i) => i % 256);
    let s = "";
    for (const b of bytes) s += String.fromCharCode(b);
    expect(bytesToBase64(bytes)).toBe(btoa(s));
  });
});

describe("captureFrame through the encode worker", () => {
  let worker: Omit<WorkerEncoder, "dead"> & { dead: boolean; calls: Array<{ w: number; h: number; o: unknown }> };
  let reply: (o: { gray: boolean }) => Promise<{ jpeg: string; gray: Float32Array | null }>;
  const grab = vi.fn(async () => frame());
  const canvasEnc = vi.fn(async () => "CANVAS");

  beforeEach(() => {
    grab.mockClear();
    canvasEnc.mockClear();
    reply = async (o) => ({ jpeg: "WORKER", gray: o.gray ? new Float32Array(576 * 1024).fill(128) : null });
    const calls: Array<{ w: number; h: number; o: unknown }> = [];
    worker = {
      calls,
      dead: false,
      encode: vi.fn(async (_f, w, h, o) => { calls.push({ w, h, o }); return reply(o); }),
      close: vi.fn(),
    };
    __setCaptureDeps({
      source: { width: () => 1080, height: () => 1920, draw: () => {}, grab },
      encoder: canvasEnc,
      makeCanvas: fakeCanvas,
      worker: () => worker,
    });
  });
  afterEach(() => {
    __setCaptureDeps({ worker: () => null });
    setQualityListener(null);
  });

  it("a count frame is grabbed and encoded in the worker at 1024 px / 0.7; the canvas encoder never runs", async () => {
    const f = await captureFrame("count");
    expect(f).toMatchObject({ t: "frame", jpeg: "WORKER", w: 576, h: 1024, purpose: "count" });
    expect(grab).toHaveBeenCalledTimes(1);
    expect(worker.calls[0]).toMatchObject({ w: 576, h: 1024, o: { quality: COUNT_QUALITY, maxB64: MAX_B64 } });
    expect(COUNT_MAX_SIDE).toBe(1024);
    expect(canvasEnc).not.toHaveBeenCalled();
    expect(encodedInWorker(f)).toBe(true);
  });

  it("ask frames go through the worker too, at their own size (1280 px / 0.9)", async () => {
    const f = await captureFrame("ask");
    expect(f).toMatchObject({ w: 720, h: 1280, jpeg: "WORKER" });
    expect(worker.calls[0].o).toMatchObject({ quality: 0.9 });
  });

  it("falls back to the canvas path when the worker fails (and marks the frame worker: false)", async () => {
    reply = async () => { throw new EncodeFailure("worker"); };
    const f = await captureFrame("count");
    expect(f.jpeg).toBe("CANVAS");
    expect(canvasEnc).toHaveBeenCalledWith(expect.anything(), 0.7);
    expect(encodedInWorker(f)).toBe(false);
  });

  it("falls back to the canvas path when the browser has no worker encoder (or it died)", async () => {
    __setCaptureDeps({ worker: () => null });
    expect((await captureFrame("count")).jpeg).toBe("CANVAS");
    worker.dead = true;
    __setCaptureDeps({ worker: () => worker });
    expect((await captureFrame("count")).jpeg).toBe("CANVAS");
    expect(grab).not.toHaveBeenCalled();
  });

  it("a frame too big at both qualities in the worker is the usual too_large camera error", async () => {
    reply = async () => { throw new EncodeFailure("too_large"); };
    await expect(captureFrame("count")).rejects.toBeInstanceOf(CameraError);
    await expect(captureFrame("count")).rejects.toMatchObject({ code: "too_large" });
    expect(canvasEnc).not.toHaveBeenCalled();
  });

  it("the keep gate reads the worker's luma: a refused frame is not returned", async () => {
    const keep = vi.fn(() => false);
    expect(await captureFrameIf("describe", keep)).toBeNull();
    expect(worker.calls[0].o).toMatchObject({ gray: true });
    expect(keep).toHaveBeenCalledWith(expect.any(Float32Array), 360, 640);
  });

  it("MediaPipe and background frames pause while a tool photo is captured, not for guide frames", async () => {
    let seen: boolean[] = [];
    reply = async () => { seen.push(detectionPaused()); return { jpeg: "W", gray: null }; };
    expect(detectionPaused()).toBe(false);
    await captureFrame("count");
    await captureFrame("cash");
    await captureFrame("guide");
    expect(seen).toEqual([true, true, false]);
    expect(detectionPaused()).toBe(false);
    seen = [];
  });
});

describe("grabVideoFrame", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("prefers a VideoFrame of the <video> (no copy on the main thread)", async () => {
    const VF = vi.fn(function (this: { src: unknown }, src: unknown) { this.src = src; });
    vi.stubGlobal("VideoFrame", VF);
    const v = document.createElement("video");
    const f = await grabVideoFrame(v);
    expect(VF).toHaveBeenCalledWith(v);
    expect((f as unknown as { src: unknown }).src).toBe(v);
  });
  it("uses createImageBitmap without VideoFrame", async () => {
    const bmp = { close: () => {} };
    vi.stubGlobal("VideoFrame", undefined);
    vi.stubGlobal("createImageBitmap", vi.fn(async () => bmp));
    expect(await grabVideoFrame(document.createElement("video"))).toBe(bmp);
  });
});
