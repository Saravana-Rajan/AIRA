// frontend/app/test/walk.guard.test.ts — bump-guard detector loop (fakes for camera, detector, audio)
import { describe, it, expect, vi, afterEach } from "vitest";
import { createBumpGuard, BUMP_INTERVAL_MS, DETECT_TIMEOUT_MS } from "../src/walk/guard";
import { GLOBAL_COOLDOWN_MS } from "../src/walk/bump";

type B = [number, number, number, number];
const sq = (s: number, cx = 500, cy = 500): B => [cy - s / 2, cx - s / 2, cy + s / 2, cx + s / 2];
const res = (ts: number, boxes: B[], floor: number | null = null) =>
  ({ ts, floor, dets: boxes.map((box) => ({ label: "chair", score: 0.8, box })) });

function setup(over: Record<string, unknown> = {}) {
  let t = 0;
  let walking = true;
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
    motion: { available: true, isWalking: () => walking },
    ...over,
  };
  const g = createBumpGuard(deps as any);
  return { g, deps, calls, said, vibrations, latencies, at: (ms: number) => { t = ms; }, setWalking: (w: boolean) => { walking = w; } };
}

afterEach(() => { vi.useRealTimers(); });

describe("bump guard decisions", () => {
  it("a crate approaching fast in the path, seen in 2 frames: vibrate first, then earcon, then 'Stop' — within 150 ms", () => {
    const s = setup();
    s.at(5);   expect(s.g.process(res(0, [sq(100)]))).toBeNull();
    s.at(260); expect(s.g.process(res(250, [sq(150)]))).toBeNull();   // doubles in 0.5 s => TTC 0.75 s, 1st frame
    s.at(395); const w = s.g.process(res(375, [sq(175)]));             // 2nd consecutive frame confirms
    expect(w).not.toBeNull();
    expect(s.calls).toEqual(["vibrate", "earcon", "speak"]);
    expect(s.vibrations).toEqual([[80, 40, 80]]);
    expect(s.said).toEqual(["Stop"]);
    expect(s.latencies).toEqual([20]);
    expect(s.g.latencies()).toEqual([20]);
    expect(s.latencies[0]).toBeLessThanOrEqual(150);
  });

  it("reports the detector diagnostic each frame: raw count, top score, surviving count, and any error", () => {
    const onDiag = vi.fn();
    const s = setup({ onDiag });
    s.g.process({ ts: 0, floor: null, dets: [{ label: "chair", score: 0.8, box: sq(100) }], raw: 3, top: 0.8 });
    expect(onDiag).toHaveBeenCalledWith({ raw: 3, top: 0.8, n: 1, err: undefined });
    s.g.process({ ts: 125, floor: null, dets: [], raw: 0, top: 0, err: "detectForVideo exploded" });
    expect(onDiag).toHaveBeenLastCalledWith({ raw: 0, top: 0, n: 0, err: "detectForVideo exploded" });
  });

  it("a single-frame spike (one big box, then gone) gives no warning", () => {
    const s = setup();
    s.g.process(res(0, [[300, 450, 800, 550]]));
    s.g.process(res(125, []));
    s.g.process(res(250, [[300, 450, 800, 550]]));
    s.g.process(res(375, []));
    expect(s.calls).toEqual([]);
  });

  it("a big box (open fridge door) to the left while walking says 'Obstacle left' on the 2nd frame", () => {
    const s = setup();
    s.at(10);
    s.g.process(res(0, [[300, 200, 800, 420]]));
    expect(s.said).toEqual([]);
    s.at(135);
    s.g.process(res(125, [[300, 200, 800, 420]]));
    expect(s.said).toEqual(["Obstacle left"]);
  });

  it("standing still at the counter with a big object in front: no warning, ever", () => {
    const s = setup();
    s.setWalking(false);
    const big: B = [100, 300, 900, 700];
    for (let i = 0; i < 40; i++) s.g.process(res(i * 125, [big]));
    expect(s.calls).toEqual([]);
    // and not even for a fast approach
    s.g.process(res(6000, [sq(100)]));
    s.g.process(res(6250, [sq(150)]));
    s.g.process(res(6375, [sq(175)]));
    expect(s.calls).toEqual([]);
  });

  it("no motion sensor: approach warnings still work, size-only warnings do not", () => {
    const s = setup({ motion: { available: false, isWalking: () => false } });
    const big: B = [300, 450, 800, 550];
    for (let i = 0; i < 10; i++) s.g.process(res(i * 125, [big]));
    expect(s.calls).toEqual([]);
    s.g.process(res(6000, [sq(100)]));
    s.g.process(res(6250, [sq(150)]));
    s.g.process(res(6375, [sq(175)]));
    expect(s.said).toEqual(["Stop"]);
  });

  it("no motion dep at all behaves like no sensor", () => {
    const s = setup({ motion: undefined });
    const big: B = [300, 450, 800, 550];
    for (let i = 0; i < 10; i++) s.g.process(res(i * 125, [big]));
    expect(s.calls).toEqual([]);
  });

  it("walking mode off: no warnings; on again: warnings return", () => {
    const s = setup();
    s.g.setWalkingMode(false);
    expect(s.g.isWalkingMode()).toBe(false);
    s.g.process(res(0, [sq(100)]));
    s.g.process(res(250, [sq(150)]));
    s.g.process(res(375, [sq(175)]));
    expect(s.calls).toEqual([]);
    s.g.setWalkingMode(true);
    s.g.process(res(500, [sq(200)]));
    s.g.process(res(625, [sq(225)]));
    expect(s.said).toEqual(["Stop"]);
  });

  it("walking with an approaching object warns once, then nothing within 5 s (global cooldown)", () => {
    const s = setup();
    const big: B = [300, 450, 800, 550];
    s.g.process(res(0, [big]));
    s.g.process(res(125, [big]));
    expect(s.said).toEqual(["Stop"]);
    const side: B = [300, 150, 800, 400];
    for (let t = 250; t < 125 + GLOBAL_COOLDOWN_MS; t += 125) {
      // a different object appearing inside the window is quiet too
      s.g.process(res(t, t >= 3000 && t <= 3250 ? [big, side] : [big]));
    }
    expect(s.said).toEqual(["Stop"]);
    s.g.process(res(125 + GLOBAL_COOLDOWN_MS, [big]));
    expect(s.said).toEqual(["Stop", "Stop"]);
  });

  it("one alert per frame even with several obstacles; 'Stop' wins over a side obstacle", () => {
    const s = setup();
    s.g.process(res(0, [[300, 150, 800, 400], [300, 450, 800, 550]]));
    s.g.process(res(125, [[300, 150, 800, 400], [300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);
    expect(s.vibrations).toHaveLength(1);
  });

  it("muted while hand guidance has the hand near the target", () => {
    const s = setup();
    s.g.setHandNearTarget(true);
    s.g.process(res(0, [[300, 450, 800, 550]]));
    s.g.process(res(125, [[300, 450, 800, 550]]));
    expect(s.calls).toEqual([]);
    s.g.setHandNearTarget(false);
    s.g.process(res(250, [[300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);
  });

  it("the server can raise urgency but never silence the guard", () => {
    const s = setup();
    for (const m of [{ t: "cue", cue: "done" }, { t: "cue", cue: "interrupted" }, { t: "request_frame", purpose: "guide", stop: true }] as any[]) {
      s.g.onServerMsg(m);
    }
    s.g.process(res(0, [[300, 450, 800, 550]]));
    s.g.process(res(125, [[300, 450, 800, 550]]));
    expect(s.said).toEqual(["Stop"]);

    const u = setup();
    u.at(1000);
    u.g.onServerMsg({ t: "cue", cue: "warn" } as any);          // urgent for the next few seconds
    // TTC ~1.5 s: silent normally, a warning while urgent
    u.g.process(res(1000, [sq(100)]));
    u.g.process(res(1250, [sq(120)]));
    u.g.process(res(1375, [sq(130)]));
    expect(u.said).toEqual(["Stop"]);
  });

  it("near-floor check: a sustained edge jump in the lower third says 'Stop' while walking", () => {
    const s = setup();
    for (let i = 0; i < 5; i++) s.g.process(res(i * 125, [], 0.02));
    expect(s.calls).toEqual([]);
    s.g.process(res(625, [], 0.5));
    s.g.process(res(750, [], 0.5));
    s.g.process(res(875, [], 0.5));
    expect(s.said).toEqual(["Stop"]);
  });

  it("speaks in the owner's language", () => {
    const s = setup({ lang: () => "ta" });
    s.g.process(res(0, [[300, 450, 800, 550]]));
    s.g.process(res(125, [[300, 450, 800, 550]]));
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
    const sizes = [100, 150, 200, 250];
    let i = 0;
    const s = setup({
      now: () => Date.now(),
      grab: vi.fn(async () => ({ frame: {}, ts: Date.now() })),
      detector: { detect: vi.fn(async (_f: unknown, ts: number) => res(ts, [sq(sizes[Math.min(i++, 3)])])) },
    });
    s.g.start();
    await vi.advanceTimersByTimeAsync(600);
    s.g.stop();
    expect(s.said).toEqual(["Stop"]);
    expect(s.latencies[0]).toBeLessThanOrEqual(150);
  });
});

describe("bump guard during a server walk, and the urgent speech path", () => {
  // TTC ~1.5 s: silent at normal sensitivity, a warning while urgent (see the server-urgency test above)
  const slowApproach = (s: ReturnType<typeof setup>, t0: number) => {
    s.at(t0 + 400);
    s.g.process(res(t0, [sq(100)]));
    s.g.process(res(t0 + 250, [sq(120)]));
    return s.g.process(res(t0 + 375, [sq(130)]));
  };

  it("walk start raises urgency for the whole walk and keeps the guard active with no steps counted", () => {
    const s = setup();
    s.setWalking(false);                       // the step counter says he is standing (shuffling slowly)
    s.at(0);
    s.g.setServerWalk(true);
    expect(s.g.isServerWalk()).toBe(true);
    expect(slowApproach(s, 20_000)).not.toBeNull();   // long after a 3 s cue-raised urgency would have ended
    expect(s.said).toEqual(["Stop"]);
    s.g.setServerWalk(false);
    expect(slowApproach(s, 60_000)).toBeNull();       // walk over, standing: silent again
  });

  it("without a server walk the same slow approach while standing is silent", () => {
    const s = setup();
    s.setWalking(false);
    expect(slowApproach(s, 20_000)).toBeNull();
  });

  it("a server walk also works with no motion sensor and with walking mode switched off", () => {
    const s = setup({ motion: { available: false, isWalking: () => false }, walkingMode: false });
    s.g.setServerWalk(true);
    expect(slowApproach(s, 10_000)).not.toBeNull();
    s.g.setServerWalk(false);
    expect(s.g.isWalkingMode()).toBe(false);
    expect(slowApproach(s, 40_000)).toBeNull();
  });

  it("warnings go to the urgent speech path with their kind and age", () => {
    const urgent: Array<[string, { kind: string; ageMs: number }]> = [];
    const s = setup({ speakUrgent: (t: string, i: { kind: string; ageMs: number }) => { urgent.push([t, i]); } });
    s.at(5);   s.g.process(res(0, [sq(100)]));
    s.at(260); s.g.process(res(250, [sq(150)]));
    s.at(395); s.g.process(res(375, [sq(175)]));
    expect(urgent).toEqual([["Stop", { kind: "bump.stop", ageMs: 20 }]]);
    expect(s.said).toEqual([]);                 // not the normal path
  });

  it("no vibration (iPhone Safari): the warn earcon still plays, before the phrase", () => {
    const s = setup({ vibrate: () => { /* navigator.vibrate missing */ } });
    s.g.process(res(0, [[300, 450, 800, 550]]));
    s.g.process(res(125, [[300, 450, 800, 550]]));
    expect(s.calls).toEqual(["earcon", "speak"]);
  });
});
