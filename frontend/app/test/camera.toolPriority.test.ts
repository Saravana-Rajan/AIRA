// frontend/app/test/camera.toolPriority.test.ts — production bug 2026-10-13: a count request reached the server
// ~15 s late on a congested uplink. A tool frame request must capture at once (live encode in flight, socket
// backed up) and nothing non-essential may be queued ahead of it while it is pending.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";

const { app } = vi.hoisted(() => ({
  app: { send: vi.fn((_m: unknown) => true), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" }, socket: { bufferedAmount: vi.fn(() => 0) } },
}));
vi.mock("../src/core/app", () => ({ app }));
import {
  __setCaptureDeps, backgroundHeld, captureFrame, captureFrameIf, holdBackground, liveCaptureBusy, LIVE_QUALITY, startContinuous, toolCaptureBusy, toolCaptureCount,
} from "../src/camera/capture";
import { startLiveVideo } from "../src/camera/liveVideo";
import { createFrameRequestHandler, defaultSleep } from "../src/camera/requests";

const fakeCanvas = () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D });

interface Enc { q: number; side: number; at: number; resolve: (s: string) => void }
/** The count frame's long side (a 720x1280 camera scaled to 1024). Live and describe frames are both 640 px, so
 *  the live frame is told apart by its JPEG quality (LIVE_QUALITY). */
const COUNT_SIDE = 1024;
type Frame = Extract<ClientMsg, { t: "frame" }>;
const isFrame = (m: ClientMsg): m is Frame => m.t === "frame";

describe("a tool frame request beats live / describe work and socket backpressure", () => {
  let encodes: Enc[];
  let buffered: number;
  const wire: ClientMsg[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    encodes = [];
    buffered = 0;
    wire.length = 0;
    (app.send as any).mockReset().mockImplementation((m: ClientMsg) => { wire.push(m); return true; });
    (app.socket.bufferedAmount as any).mockImplementation(() => buffered);
    // Live / describe encodes hang (a slow, busy phone); count encodes take 80 ms.
    __setCaptureDeps({
      source: { width: () => 720, height: () => 1280, draw: () => {} },
      makeCanvas: (w, h) => ({ ...fakeCanvas(), canvas: { width: w, height: h } as HTMLCanvasElement }),
      worker: () => null,
      encoder: (c, q) => new Promise<string>((resolve) => {
        const side = Math.max(c.width, c.height);
        encodes.push({ q, side, at: Date.now(), resolve });
        if (side === COUNT_SIDE) setTimeout(() => resolve("C".repeat(1000)), 80);
      }),
    });
  });
  afterEach(() => { vi.useRealTimers(); });

  function rig(over: Partial<Parameters<typeof createFrameRequestHandler>[0]> = {}) {
    const logs: any[] = [];
    const stopLive = startLiveVideo({
      capture: (keep) => captureFrameIf("live", keep),
      send: (m) => app.send(m),
      active: () => true,
      cameraOn: () => true,
      hidden: () => false,
      buffered: () => buffered,
      toolBusy: () => toolCaptureBusy() || backgroundHeld(),
      toolCount: toolCaptureCount,
      objects: () => [],
    });
    const stopDescribe = startContinuous("describe", 1);
    const handle = createFrameRequestHandler({
      capture: captureFrame,
      startContinuous: () => () => {},
      send: (m) => app.send(m),
      hint: () => true,
      sleep: defaultSleep,
      hold: holdBackground,
      now: () => Date.now(),
      log: (tag, data) => { logs.push({ tag, ...data }); },
      buffered: () => buffered,
      ...over,
    });
    return { handle, logs, stop: () => { stopLive(); stopDescribe(); } };
  }

  it("captures the count frame within 300 ms with a live encode in flight and 170 KB queued", async () => {
    const r = rig();
    await vi.advanceTimersByTimeAsync(10);
    const liveEnc = encodes.find((e) => e.q === LIVE_QUALITY);
    expect(liveEnc).toBeDefined();               // a live frame is mid-encode and does not finish on its own
    buffered = 170_000;                          // the uplink is congested (mic_drop said 168-173 KB)

    const t0 = Date.now();
    const done = r.handle({ t: "request_frame", purpose: "count", burst: { count: 2, intervalMs: 400 } });
    await vi.advanceTimersByTimeAsync(300);
    const countEnc = encodes.filter((e) => e.side === COUNT_SIDE);
    expect(countEnc.length).toBeGreaterThanOrEqual(1);
    expect(countEnc[0].at - t0).toBeLessThan(300);
    expect(wire.filter((m) => isFrame(m) && m.purpose === "count")).toHaveLength(1);

    liveEnc!.resolve("L".repeat(66_000));        // the stale live frame finishes now: it is dropped, never sent
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    expect(wire.filter(isFrame).map((f) => f.purpose)).toEqual(["count", "count"]);
    const req = r.logs.filter((l) => l.tag === "frame_req");
    expect(req).toHaveLength(2);
    expect(req[0]).toMatchObject({ purpose: "count", bytes: 1000, buffered: 170_000 });
    expect(req[0].waitedMs).toBeLessThan(300);
    expect(req[0].encodeMs).toBeGreaterThanOrEqual(80);
    r.stop();
  });

  it("pauses live and describe for the whole request, and while the socket is still congested after it", async () => {
    __setCaptureDeps({ encoder: async () => "A".repeat(100) }); // encodes finish at once: anything allowed is sent
    let release!: () => void;
    const gate = new Promise<void>((res) => { release = res; });
    const r = rig({ sleep: () => gate });       // the request stays pending between its two frames
    await vi.advanceTimersByTimeAsync(10);
    wire.length = 0;
    const done = r.handle({ t: "request_frame", purpose: "count", burst: { count: 2, intervalMs: 400 } });
    await vi.advanceTimersByTimeAsync(5000);
    expect(wire.filter(isFrame).map((f) => f.purpose)).toEqual(["count"]);
    expect(backgroundHeld()).toBe(true);
    release();
    await done;
    expect(backgroundHeld()).toBe(false);
    buffered = 120_000;                          // the count frames are still leaving the phone
    wire.length = 0;
    await vi.advanceTimersByTimeAsync(3000);
    expect(wire.filter(isFrame)).toHaveLength(0);
    buffered = 0;
    await vi.advanceTimersByTimeAsync(1100);
    expect(wire.some((m) => isFrame(m) && (m.purpose === "live" || m.purpose === "describe"))).toBe(true);
    r.stop();
  });

  it("preview and describe yield to a live frame being captured: the live frame goes first", async () => {
    let releaseLive: ((s: string) => void) | null = null;
    __setCaptureDeps({
      encoder: (_c, q) => new Promise<string>((resolve) => {
        if (q === LIVE_QUALITY) releaseLive = resolve;                   // the live frame: slow encode
        else resolve("P".repeat(500));                                  // preview / describe: instant
      }),
    });
    const stopLive = startLiveVideo({
      capture: (keep) => captureFrameIf("live", keep),
      send: (m) => app.send(m),
      active: () => true, cameraOn: () => true, hidden: () => false, buffered: () => buffered,
      toolBusy: () => toolCaptureBusy() || backgroundHeld(), toolCount: toolCaptureCount, objects: () => [],
    });
    const stopPreview = startContinuous("preview", 8);
    await vi.advanceTimersByTimeAsync(10);
    expect(liveCaptureBusy()).toBe(true);
    await vi.advanceTimersByTimeAsync(500);                  // 4 preview ticks while the live frame encodes
    expect(wire.filter((m) => isFrame(m) && m.purpose === "preview")).toHaveLength(0);
    releaseLive!("L".repeat(60_000));
    await vi.advanceTimersByTimeAsync(300);
    const order = wire.filter(isFrame).map((f) => f.purpose);
    expect(order[0]).toBe("live");
    expect(order.filter((p) => p === "preview").length).toBeGreaterThan(0);
    stopLive();
    stopPreview();
  });
});
