// frontend/app/test/net.sendPriority.test.ts — one WebSocket, many streams: under congestion the owner's mic audio
// goes first, then tool frames the server asked for, then the live video, then preview / describe.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" }, socket: { bufferedAmount: vi.fn(() => 0) } },
}));
vi.mock("../src/core/app", () => ({ app }));

import {
  BACKGROUND_MAX_BUFFERED, LIVE_MAX_BUFFERED, MIC_BACKLOG_MS, MIC_MAX_BUFFERED_BYTES, MIC_PRESSURE_BYTES, TOOL_DRAIN_MAX_MS,
  TOOL_SEND_BUFFERED, __resetSendPriority, micBackedUp, micDropCount, micMaySend, waitForDrain,
} from "../src/net/sendPriority";
import { __setCaptureDeps, captureToolFrame, socketBusy, startContinuous, PREVIEW_MAX_BUFFERED } from "../src/camera/capture";

const fakeCanvas = () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D });
const buffered = app.socket.bufferedAmount as ReturnType<typeof vi.fn>;
const frames = (purpose: string) => (app.send as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as { t: string; purpose?: string })
  .filter((m) => m.t === "frame" && m.purpose === purpose);

beforeEach(() => {
  vi.useFakeTimers();
  __resetSendPriority();
  (app.send as ReturnType<typeof vi.fn>).mockClear();
  buffered.mockReturnValue(0);
  __setCaptureDeps({
    source: { width: () => 1280, height: () => 720, draw: () => {} },
    encoder: async () => "AAAA",
    makeCanvas: fakeCanvas,
    worker: () => null,
  });
});
afterEach(() => {
  vi.useRealTimers();
  buffered.mockReturnValue(0);
});

describe("thresholds", () => {
  it("each lower-priority stream stops well before the one above it", () => {
    expect(BACKGROUND_MAX_BUFFERED).toBeLessThan(LIVE_MAX_BUFFERED);
    expect(PREVIEW_MAX_BUFFERED).toBe(BACKGROUND_MAX_BUFFERED);
    expect(TOOL_SEND_BUFFERED).toBeLessThan(MIC_MAX_BUFFERED_BYTES);
    // a live frame (~60 KB) put on the socket right at the live cap still leaves room under the mic's cap
    expect(LIVE_MAX_BUFFERED + 60_000).toBeLessThan(MIC_MAX_BUFFERED_BYTES);
    // a count photo (~110 KB) put on an empty-enough socket never makes the mic drop
    expect(TOOL_SEND_BUFFERED + 90_000).toBeLessThan(MIC_MAX_BUFFERED_BYTES);
    expect(TOOL_DRAIN_MAX_MS).toBeLessThanOrEqual(2000);
  });
});

describe("mic", () => {
  it("sends up to its cap, counts drops, and marks itself backed up so video yields for a while", () => {
    expect(micMaySend(0, 1000)).toBe(true);
    expect(micBackedUp(1000)).toBe(false);
    expect(micMaySend(MIC_PRESSURE_BYTES + 1, 1000)).toBe(true);   // still sent...
    expect(micBackedUp(1000)).toBe(true);                          // ...but video now yields
    expect(micBackedUp(1000 + MIC_BACKLOG_MS - 1)).toBe(true);
    expect(micBackedUp(1000 + MIC_BACKLOG_MS)).toBe(false);
    expect(micMaySend(MIC_MAX_BUFFERED_BYTES + 1, 5000)).toBe(false);
    expect(micDropCount()).toBe(1);
  });

  it("preview / describe frames are skipped while the mic is backed up, even on an empty socket", async () => {
    const stop = startContinuous("preview", 8);
    await vi.advanceTimersByTimeAsync(10);
    expect(frames("preview")).toHaveLength(1);
    micMaySend(MIC_PRESSURE_BYTES + 1); // the mic saw a backlog just now
    buffered.mockReturnValue(0);
    expect(socketBusy()).toBe(true);
    await vi.advanceTimersByTimeAsync(500);
    expect(frames("preview")).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(MIC_BACKLOG_MS);
    expect(frames("preview").length).toBeGreaterThan(1);
    stop();
  });

  it("mic keeps sending while live / preview frames are skipped under congestion", () => {
    const level = LIVE_MAX_BUFFERED + 10_000; // live and preview are both over their caps here
    buffered.mockReturnValue(level);
    expect(socketBusy()).toBe(true);
    let sent = 0;
    for (let i = 0; i < 30; i++) if (micMaySend(level)) sent++;
    expect(sent).toBe(30);
    expect(micDropCount()).toBe(0);
  });
});

describe("tool frames wait (bounded) for the socket to drain", () => {
  it("waitForDrain returns at once on a quiet socket and polls until the backlog falls", async () => {
    let b = 0;
    expect(await waitForDrain(() => b)).toEqual({ waitedMs: 0, drained: true });
    b = TOOL_SEND_BUFFERED + 1;
    const p = waitForDrain(() => b);
    await vi.advanceTimersByTimeAsync(300);
    b = TOOL_SEND_BUFFERED;
    await vi.advanceTimersByTimeAsync(60);
    const r = await p;
    expect(r.drained).toBe(true);
    expect(r.waitedMs).toBeGreaterThanOrEqual(300);
    expect(r.waitedMs).toBeLessThan(TOOL_DRAIN_MAX_MS);
  });

  it("gives up after TOOL_DRAIN_MAX_MS and lets the frame go anyway", async () => {
    const p = waitForDrain(() => 1_000_000);
    let done = false;
    void p.then(() => { done = true; });
    await vi.advanceTimersByTimeAsync(TOOL_DRAIN_MAX_MS - 10);
    expect(done).toBe(false);
    await vi.advanceTimersByTimeAsync(20);
    expect(await p).toMatchObject({ drained: false });
  });

  it("captureToolFrame captures at once, then holds the frame until the backlog drains", async () => {
    buffered.mockReturnValue(TOOL_SEND_BUFFERED + 50_000);
    let f: unknown = null;
    void captureToolFrame("count").then((x) => { f = x; });
    await vi.advanceTimersByTimeAsync(500);
    expect(f).toBeNull();
    buffered.mockReturnValue(1000);
    await vi.advanceTimersByTimeAsync(60);
    expect(f).toMatchObject({ t: "frame", purpose: "count" });
  });

  it("captureToolFrame never waits longer than TOOL_DRAIN_MAX_MS on a stalled socket", async () => {
    buffered.mockReturnValue(10_000_000);
    let f: unknown = null;
    void captureToolFrame("ask").then((x) => { f = x; });
    await vi.advanceTimersByTimeAsync(TOOL_DRAIN_MAX_MS + 60);
    expect(f).toMatchObject({ t: "frame", purpose: "ask" });
  });

  it("a continuous tool stream (guide) waits for the drain instead of piling frames on; one capture at a time", async () => {
    buffered.mockReturnValue(TOOL_SEND_BUFFERED + 1);
    const stop = startContinuous("guide", 4);
    await vi.advanceTimersByTimeAsync(600);
    expect(frames("guide")).toHaveLength(0);
    buffered.mockReturnValue(0);
    await vi.advanceTimersByTimeAsync(60);
    expect(frames("guide")).toHaveLength(1);
    stop();
  });
});

// Production (iPhone, 2026-10-13): ~93 KB live frames filled the socket and the owner's speech queued behind
// them (a mic chunk was dropped at 129 KB buffered). Speech now goes first, and video stops queuing early.
describe("the owner's speech goes before video", () => {
  it("video yields while the owner is speaking and for a short hold after", async () => {
    const sp = await import("../src/net/sendPriority");
    sp.__resetSendPriority();
    expect(sp.videoYields(10_000)).toBe(false);
    sp.noteSpeech(10_000);
    expect(sp.videoYields(10_000)).toBe(true);
    expect(sp.videoYields(10_000 + sp.SPEECH_HOLD_MS - 1)).toBe(true);
    expect(sp.videoYields(10_000 + sp.SPEECH_HOLD_MS + 1)).toBe(false);
  });

  it("a mic backlog still makes video yield", async () => {
    const sp = await import("../src/net/sendPriority");
    sp.__resetSendPriority();
    sp.micMaySend(sp.MIC_PRESSURE_BYTES + 1, 5_000);
    expect(sp.videoYields(5_000)).toBe(true);
  });

  it("video stops queuing well before the mic: one live frame can't bury the owner's speech", async () => {
    const sp = await import("../src/net/sendPriority");
    expect(sp.LIVE_MAX_BUFFERED).toBeLessThanOrEqual(24_000);
    expect(sp.LIVE_MAX_BUFFERED).toBeGreaterThanOrEqual(sp.BACKGROUND_MAX_BUFFERED * 2);
  });
});
