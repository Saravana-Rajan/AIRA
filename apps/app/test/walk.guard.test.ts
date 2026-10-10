// apps/app/test/walk.guard.test.ts — bump-guard detector loop (fakes for camera, detector, audio)
import { describe, it, expect, vi, afterEach } from "vitest";
import { createBumpGuard, BUMP_INTERVAL_MS, DETECT_TIMEOUT_MS } from "../src/walk/guard";

type B = [number, number, number, number];
const sq = (s: number, cx = 500, cy = 500): B => [cy - s / 2, cx - s / 2, cy + s / 2, cx + s / 2];
const res = (ts: number, boxes: B[], floor: number | null = null) =>
  ({ ts, floor, dets: boxes.map((box) => ({ label: "chair", score: 0.8, box })) });

function setup(over: Record<string, unknown> = {}) {
  let t = 0;
  const calls: string[] = [];
  const said: string[] = [];
  const vibrations: number[][] = [];
  const latencies: number[] = [];
  const deps = {
    grab: vi.fn(async () => ({ frame: {}, ts: t })),
    detector: { detect: vi.fn(async (_f: unknown, ts: number) => res(ts, [])) },
    vibrate: (p: number[]) => { calls.push("vibrate"); vibrations.push(p); },
    earcon: () => { calls.push("earcon"); },
    speak: (s: string) => { calls.push("speak"); said.push(s); },
    lang: () => "en" as const,
    now: () => t,
    onLatency: (ms: number) => { latencies.push(ms); },
    ...over,
  };
  const g = createBumpGuard(deps as any);
  return { g, deps, calls, said, vibrations, latencies, at: (ms: number) => { t = ms; } };
}

afterEach(() => { vi.useRealTimers(); });

describe("bump guard decisions", () => {
  it("a crate approaching fast in the path: vibrate first, then earcon, then 'Stop' — within 150 ms", () => {
    const s = setup();
    s.at(5);  expect(s.g.process(res(0, [sq(100)]))).toBeNull();
    s.at(270); const w = s.g.process(res(250, [sq(150)]));       // doubles in 0.5 s => TTC 0.75 s
    expect(w).not.toBeNull();
    expect(s.calls).toEqual(["vibrate", "earcon", "speak"]);
    expect(s.vibrations).toEqual([[80, 40, 80]]);
    expect(s.said).toEqual(["Stop"]);
    expect(s.latencies).toEqual([20]);
    expect(s.g.latencies()).toEqual([20]);
    expect(s.latencies[0]).toBeLessThanOrEqual(150);
  });

  it("a big box (open fridge door) to the left says 'Obstacle left' at once", () => {
    const s = setup();
    s.at(10);
    s.g.process(res(0, [[300, 200, 800, 420]]));
    expect(s.said).toEqual(["Obstacle left"]);
  });

  it("things outside the walking path or far away stay silent", () => {
    const s = setup();
    s.g.process(res(0, [[300, 0, 800, 250], sq(80)]));
    s.g.process(res(250, [[300, 0, 800, 260], sq(82)]));
    expect(s.calls).toEqual([]);
  });

  it("cooldown 1.5 s per object", () => {
    const s = setup();
    const big: B = [300, 450, 800, 550];
    s.g.process(res(0, [big]));
    s.g.process(res(125, [big]));
    s.g.process(res(1400, [big]));
    expect(s.said).toEqual(["Stop"]);
    s.g.process(res(1500, [big]));
    expect(s.said).toEqual(["Stop", "Stop"]);
  });

  it("one alert per frame even with several obstacles; 'Stop' wins over a side obstacle", () => {
    const s = setup();
    s.g.process(res(0, [[300, 150, 800, 400], [300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);
    expect(s.vibrations).toHaveLength(1);
  });

  it("muted while hand guidance has the hand near the target", () => {
    const s = setup();
    s.g.setHandNearTarget(true);
    s.g.process(res(0, [[300, 450, 800, 550]]));
    expect(s.calls).toEqual([]);
    s.g.setHandNearTarget(false);
    s.g.process(res(125, [[300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);
  });

  it("the server can raise urgency but never silence the guard", () => {
    const s = setup();
    for (const m of [{ t: "cue", cue: "done" }, { t: "cue", cue: "interrupted" }, { t: "request_frame", purpose: "guide", stop: true }] as any[]) {
      s.g.onServerMsg(m);
    }
    s.g.process(res(0, [[300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);

    const u = setup();
    u.at(1000);
    u.g.onServerMsg({ t: "cue", cue: "warn" } as any);          // urgent for the next few seconds
    // TTC ~1.5 s: silent normally, a warning while urgent
    u.g.process(res(1000, [sq(100)]));
    u.g.process(res(1250, [sq(120)]));
    expect(u.said).toEqual(["Stop"]);
  });

  it("near-floor check: a sustained edge jump in the lower third says 'Stop'", () => {
    const s = setup();
    for (let i = 0; i < 5; i++) s.g.process(res(i * 125, [], 0.02));
    expect(s.calls).toEqual([]);
    s.g.process(res(625, [], 0.5));
    s.g.process(res(750, [], 0.5));
    expect(s.said).toEqual(["Stop"]);
  });

  it("speaks in the owner's language", () => {
    const s = setup({ lang: () => "ta" });
    s.g.process(res(0, [[300, 450, 800, 550]]));
    expect(s.said[0]).not.toBe("Stop");
    expect(s.said[0].length).toBeGreaterThan(0);
  });
});

describe("bump guard loop", () => {
  it(`grabs and detects about every ${BUMP_INTERVAL_MS} ms until stopped`, async () => {
    vi.useFakeTimers();
    const s = setup();
    s.g.start();
    expect(s.g.isRunning()).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    const n = s.deps.detector.detect.mock.calls.length;
    expect(n).toBeGreaterThanOrEqual(6);
    expect(n).toBeLessThanOrEqual(10);
    s.g.stop();
    await vi.advanceTimersByTimeAsync(1000);
    expect(s.deps.detector.detect.mock.calls.length).toBe(n);
    expect(s.g.isRunning()).toBe(false);
  });

  it("never overlaps: a slow detection holds the next frame; a hung one times out and the loop goes on", async () => {
    vi.useFakeTimers();
    const s = setup({ detector: { detect: vi.fn(() => new Promise(() => {})) } });
    s.g.start();
    s.g.start();                                              // idempotent
    await vi.advanceTimersByTimeAsync(900);
    expect((s.deps.detector.detect as any).mock.calls.length).toBe(1);
    await vi.advanceTimersByTimeAsync(DETECT_TIMEOUT_MS + 1000);
    expect((s.deps.detector.detect as any).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(s.g.isRunning()).toBe(true);
    s.g.stop();
  });

  it("detector errors and missing frames do not stop the loop", async () => {
    vi.useFakeTimers();
    let n = 0;
    const s = setup({
      grab: vi.fn(async () => (++n % 2 === 0 ? null : { frame: {}, ts: 0 })),
      detector: { detect: vi.fn(async () => { throw new Error("gpu lost"); }) },
    });
    s.g.start();
    await vi.advanceTimersByTimeAsync(3000);
    expect((s.deps.detector.detect as any).mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(s.g.isRunning()).toBe(true);
    s.g.stop();
  });

  it("feeds detections from the loop into the decision (end to end)", async () => {
    vi.useFakeTimers();
    const sizes = [100, 150, 200];
    let i = 0;
    const s = setup({
      now: () => Date.now(),
      grab: vi.fn(async () => ({ frame: {}, ts: Date.now() })),
      detector: { detect: vi.fn(async (_f: unknown, ts: number) => res(ts, [sq(sizes[Math.min(i++, 2)])])) },
    });
    s.g.start();
    await vi.advanceTimersByTimeAsync(400);
    s.g.stop();
    expect(s.said).toEqual(["Stop"]);
    expect(s.latencies[0]).toBeLessThanOrEqual(150);
  });
});
