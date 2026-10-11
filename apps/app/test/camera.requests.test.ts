// apps/app/test/camera.requests.test.ts
import { describe, it, expect, vi } from "vitest";
import { createFrameRequestHandler, CONTINUOUS_FPS, MAX_BURST, MAX_INTERVAL_MS, defaultSleep } from "../src/camera/requests";
import { CameraError } from "../src/camera/capture";

vi.mock("../src/core/app", () => ({ app: { send: vi.fn(), speak: vi.fn(), earcon: vi.fn(), settings: { lang: "en" } } }));

function setup(over: Partial<Parameters<typeof createFrameRequestHandler>[0]> = {}) {
  const sent: any[] = []; const hints: string[] = []; const stops: string[] = [];
  let n = 0;
  const deps = {
    capture: vi.fn(async (purpose: any) => ({ t: "frame", id: `f${++n}`, jpeg: "A", w: 1, h: 1, ts: n, purpose }) as any),
    startContinuous: vi.fn((purpose: any) => () => { stops.push(purpose); }),
    send: vi.fn((m: any) => { sent.push(m); return true; }),
    hint: vi.fn((k: any) => { hints.push(k); return true; }),
    sleep: vi.fn(async () => {}),
    ...over,
  };
  return { handle: createFrameRequestHandler(deps), deps, sent, hints, stops };
}

describe("request_frame handler", () => {
  it("single request sends one frame with the requested purpose", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "invoice" });
    expect(s.sent.map((f) => f.purpose)).toEqual(["invoice"]);
    expect(s.deps.sleep).not.toHaveBeenCalled();
  });

  it("burst sends exactly count frames, sleeping between them", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "count", burst: { count: 3, intervalMs: 400 } });
    expect(s.sent).toHaveLength(3);
    expect(s.sent.every((f) => f.purpose === "count")).toBe(true);
    expect(new Set(s.sent.map((f) => f.id)).size).toBe(3);
    expect(s.deps.sleep).toHaveBeenCalledTimes(2);
    expect(s.deps.sleep).toHaveBeenCalledWith(400);
  });

  it("continuous starts at CONTINUOUS_FPS; burst during continuous still works; stop stops continuous only", async () => {
    const s = setup();
    expect(CONTINUOUS_FPS).toBe(2);
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    expect(s.deps.startContinuous).toHaveBeenCalledWith("guide", CONTINUOUS_FPS);
    await s.handle({ t: "request_frame", purpose: "identify", burst: { count: 2, intervalMs: 100 } });
    expect(s.sent).toHaveLength(2);
    expect(s.sent.every((f) => f.purpose === "identify")).toBe(true);
    expect(s.stops).toEqual([]);
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });
    expect(s.stops).toEqual(["guide"]);
    expect(s.sent).toHaveLength(2);
  });

  it("a second continuous request of the same purpose replaces the first", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    expect(s.stops).toEqual(["guide"]);
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });
    expect(s.stops).toEqual(["guide", "guide"]);
  });

  it("continuous streams are tracked per purpose: guidance and a delivery count run side by side", async () => {
    // product_find guidance streams `guide`; delivery_count streams `identify`. Ending one must not kill the other.
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    await s.handle({ t: "request_frame", purpose: "identify", continuous: true });
    expect(s.stops).toEqual([]);
    expect(s.deps.startContinuous).toHaveBeenCalledTimes(2);
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });   // the guidance loop timed out
    expect(s.stops).toEqual(["guide"]);                                    // identify keeps running
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });   // a second stop does nothing
    await s.handle({ t: "request_frame", purpose: "identify", stop: true });
    expect(s.stops).toEqual(["guide", "identify"]);
  });

  it("stop with nothing running is a no-op, and a second stop does nothing", async () => {
    const s = setup();
    await expect(s.handle({ t: "request_frame", purpose: "guide", stop: true })).resolves.toBeUndefined();
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });
    expect(s.sent).toHaveLength(0);
    expect(s.stops).toEqual(["guide"]);
  });

  it("camera failure speaks no_camera once and sends nothing (no retry loop)", async () => {
    const s = setup({ capture: vi.fn(async () => { throw new CameraError("permission"); }) });
    await s.handle({ t: "request_frame", purpose: "cash", burst: { count: 3, intervalMs: 10 } });
    expect(s.sent).toHaveLength(0);
    expect(s.hints).toEqual(["no_camera"]);
    expect(s.deps.capture).toHaveBeenCalledTimes(1);
    expect(s.deps.sleep).not.toHaveBeenCalled();
  });

  it("a failure mid-burst keeps the frames already sent and stops there", async () => {
    let calls = 0;
    const s = setup({
      capture: vi.fn(async (purpose: any) => {
        if (++calls === 2) throw new CameraError("no_frame");
        return { t: "frame", id: `f${calls}`, jpeg: "A", w: 1, h: 1, ts: calls, purpose } as any;
      }),
    });
    await s.handle({ t: "request_frame", purpose: "count", burst: { count: 4, intervalMs: 50 } });
    expect(s.sent.map((f) => f.id)).toEqual(["f1"]);
    expect(s.hints).toEqual(["no_camera"]);
    expect(s.deps.capture).toHaveBeenCalledTimes(2);
  });

  it("a non-camera error also speaks no_camera and does not throw", async () => {
    const s = setup({ capture: vi.fn(async () => { throw new Error("boom"); }) });
    await expect(s.handle({ t: "request_frame", purpose: "ask" })).resolves.toBeUndefined();
    expect(s.hints).toEqual(["no_camera"]);
  });

  it("oversize frame speaks the blurry/hold-still hint", async () => {
    const s = setup({ capture: vi.fn(async () => { throw new CameraError("too_large"); }) });
    await s.handle({ t: "request_frame", purpose: "invoice" });
    expect(s.hints).toEqual(["blurry"]);
    expect(s.sent).toHaveLength(0);
  });

  it("clamps a malformed burst: count below 1 sends one frame, count above MAX_BURST is capped", async () => {
    const low = setup();
    await low.handle({ t: "request_frame", purpose: "count", burst: { count: 0, intervalMs: -5 } });
    expect(low.sent).toHaveLength(1);
    expect(low.deps.sleep).not.toHaveBeenCalled();

    const high = setup();
    await high.handle({ t: "request_frame", purpose: "count", burst: { count: 1000, intervalMs: 999_999 } });
    expect(high.sent).toHaveLength(MAX_BURST);
    expect(high.deps.sleep).toHaveBeenCalledWith(MAX_INTERVAL_MS);
  });

  it("a fractional or NaN count is treated safely", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "count", burst: { count: 2.7, intervalMs: Number.NaN } });
    expect(s.sent).toHaveLength(2);
    expect(s.deps.sleep).not.toHaveBeenCalled();
    const n = setup();
    await n.handle({ t: "request_frame", purpose: "count", burst: { count: Number.NaN, intervalMs: 10 } });
    expect(n.sent).toHaveLength(1);
  });

  it("defaultSleep resolves after the given delay", async () => {
    vi.useFakeTimers();
    try {
      let done = false;
      const p = defaultSleep(250).then(() => { done = true; });
      await vi.advanceTimersByTimeAsync(249);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await p;
      expect(done).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
