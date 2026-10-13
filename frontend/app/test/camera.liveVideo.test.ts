// frontend/app/test/camera.liveVideo.test.ts — the 1 fps Gemini Live video sender, the "show" detector and its
// fast path, and the per-minute live_stats line.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import {
  CHANGE_THRESHOLD, FAST_QUIET_MS, LIVE_MAX_BUFFERED, PROBE_MS, SETTLE_MS, STATS_EVERY_MS, createChangeDetector, readScene,
  startLiveVideo, type LiveVideoDeps, type SeenObject,
} from "../src/camera/liveVideo";
import { GRID_H, GRID_W } from "../src/camera/sceneGate";
import { PREVIEW_MAX_BUFFERED, __setCaptureDeps, frameSpec, holdBackground, probeGrid } from "../src/camera/capture";
import type { FrameMsg } from "../src/camera/capture";
import type { Box } from "../src/guidance/geometry";

const W = 64;
const H = 48;
/** A synthetic gray frame: flat `base`, optionally with a bright block (an object held up) on the left half. */
function gray(base: number, block = 0): Float32Array {
  const g = new Float32Array(W * H).fill(base);
  if (block) for (let y = 8; y < 40; y++) for (let x = 4; x < 32; x++) g[y * W + x] = block;
  return g;
}
const grid = (n: number) => new Float32Array(GRID_W * GRID_H).fill(n);
const CENTRE: Box = [400, 400, 600, 600];
const CORNER: Box = [0, 0, 120, 120];
const sample = (g: Float32Array | null, ts: number, labels: string[] = [], held = false) => ({ grid: g, labels, held, ts });

function harness(over: Partial<LiveVideoDeps> = {}) {
  const sent: ClientMsg[] = [];
  const logs: { tag: string; data: Record<string, unknown> }[] = [];
  const st = { active: true, camera: true, hidden: false, buffered: 0, toolBusy: false, toolCount: 0,
    objects: [] as SeenObject[], hand: null as Box | null, frame: gray(100), probe: grid(100) as Float32Array | null, captures: 0 };
  let ts = 0;
  const deps: LiveVideoDeps = {
    capture: vi.fn(async (keep) => {
      st.captures++;
      keep?.(st.frame, W, H);
      return { t: "frame", id: `l${++ts}`, jpeg: "AAAA", w: 768, h: 432, ts, purpose: "live" } as FrameMsg;
    }),
    send: (m) => { sent.push(m); return true; },
    active: () => st.active,
    cameraOn: () => st.camera,
    hidden: () => st.hidden,
    buffered: () => st.buffered,
    toolBusy: () => st.toolBusy,
    toolCount: () => st.toolCount,
    objects: () => st.objects,
    hand: () => st.hand,
    log: (tag, data) => { logs.push({ tag, data }); },
    ...over,
  };
  return { sent, st, deps, logs };
}
const withProbe = (h: ReturnType<typeof harness>) => { h.deps.probe = () => h.st.probe; return h; };
const changedFlags = (sent: ClientMsg[]) => sent.map((m) => (m as FrameMsg & { changed?: boolean }).changed ?? false);

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("live frame spec", () => {
  it("is 640 px at quality 0.5 (iPhone frames at 768/0.7 were ~93 KB and held back speech); count frames are 1024 px", () => {
    expect(frameSpec("live")).toEqual({ maxSide: 640, quality: 0.5 });
    expect(frameSpec("count").maxSide).toBe(1024);
  });
});

describe("startLiveVideo", () => {
  it("sends one live frame a second while the session is up", async () => {
    const { sent, deps } = harness();
    const stop = startLiveVideo(deps);
    await flush();
    await vi.advanceTimersByTimeAsync(3000);
    expect(sent.length).toBe(4); // t=0, 1, 2, 3 s
    expect(sent.every((m) => m.t === "frame" && m.purpose === "live")).toBe(true);
    stop();
    await vi.advanceTimersByTimeAsync(3000);
    expect(sent.length).toBe(4);
  });

  it("stops while the page is hidden, the camera is off or the session is down", async () => {
    const { sent, st, deps } = harness();
    st.hidden = true;
    const stop = startLiveVideo(deps);
    await vi.advanceTimersByTimeAsync(2000);
    st.hidden = false; st.camera = false;
    await vi.advanceTimersByTimeAsync(2000);
    st.camera = true; st.active = false;
    await vi.advanceTimersByTimeAsync(2000);
    expect(sent).toEqual([]);
    expect(st.captures).toBe(0);
    st.active = true;
    await vi.advanceTimersByTimeAsync(1000);
    expect(sent.length).toBe(1);
    stop();
  });

  it("is not starved by preview / describe: their backlog cap sits well below the live one", async () => {
    expect(LIVE_MAX_BUFFERED).toBeGreaterThanOrEqual(PREVIEW_MAX_BUFFERED * 2);
    expect(LIVE_MAX_BUFFERED).toBeLessThan(120_000); // a count photo still leaving the phone does hold live back
    const { sent, st, deps } = harness();
    st.buffered = PREVIEW_MAX_BUFFERED + 15_000;   // a preview backlog at its worst (cap + one preview frame)
    const stop = startLiveVideo(deps);
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    expect(sent.length).toBe(3);
    stop();
  });

  it("yields to the owner's mic: no live frame while the mic is backed up, and one overtaken by it is dropped", async () => {
    let mic = true;
    const { sent, st, deps, logs } = harness({ micBusy: () => mic });
    const stop = startLiveVideo(deps);
    await vi.advanceTimersByTimeAsync(2000);
    expect(sent).toEqual([]);
    expect(st.captures).toBe(0);                 // not even encoded: the CPU and the socket go to speech
    mic = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(sent.length).toBe(1);
    // the mic backs up while a live frame is being made: that frame is dropped ("late")
    (deps.capture as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      mic = true;
      return { t: "frame", id: "lx", jpeg: "AAAA", w: 768, h: 432, ts: 9, purpose: "live" } as FrameMsg;
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(sent.length).toBe(1);
    stop();
    const s = logs.find((l) => l.tag === "live_stats")!.data as { skipped: Record<string, number> };
    expect(s.skipped.mic).toBeGreaterThanOrEqual(2);
    expect(s.skipped.late).toBe(1);
  });

  it("skips a tick while the socket is backpressured", async () => {
    const { sent, st, deps } = harness();
    st.buffered = LIVE_MAX_BUFFERED + 1;
    const stop = startLiveVideo(deps);
    await vi.advanceTimersByTimeAsync(2000);
    expect(sent).toEqual([]);
    st.buffered = 0;
    await vi.advanceTimersByTimeAsync(1000);
    expect(sent.length).toBe(1);
    stop();
  });

  it("tool frames come first: no live capture during one, and a live frame overtaken by one is dropped", async () => {
    const { sent, st, deps } = harness();
    st.toolBusy = true;
    const stop = startLiveVideo(deps);
    await vi.advanceTimersByTimeAsync(2000);
    expect(st.captures).toBe(0);
    st.toolBusy = false;
    // An ask frame starts while the live frame is being encoded.
    deps.capture = vi.fn(async () => {
      st.toolCount++;
      return { t: "frame", id: "lx", jpeg: "AAAA", w: 768, h: 432, ts: 9, purpose: "live" } as FrameMsg;
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(sent).toEqual([]);
    stop();
  });

  it("without a probe, marks a live frame changed (with labels) once a big new view is steady", async () => {
    const h = harness({ detector: createChangeDetector({ settleMs: 0, gapMs: 0 }) });
    const stop = startLiveVideo(h.deps);
    await flush();                                     // reference view
    h.st.frame = gray(100, 250); h.st.objects = [{ label: "bottle", box: CENTRE }];
    await vi.advanceTimersByTimeAsync(1000);           // moved: changed (label + scene)
    await vi.advanceTimersByTimeAsync(1000);           // nothing new
    expect(changedFlags(h.sent)).toEqual([false, true, false]);
    expect((h.sent[1] as { labels?: string[] }).labels).toEqual(["bottle"]);
    stop();
  });

  it("fast path: a new object shown in the centre sends ONE changed frame within ~0.6 s, not at the next tick", async () => {
    const h = withProbe(harness());
    const stop = startLiveVideo(h.deps);
    await vi.advanceTimersByTimeAsync(PROBE_MS);       // regular frame at 0, first probe = reference
    expect(h.sent.length).toBe(1);
    h.st.objects = [{ label: "Bottle", box: CENTRE }, { label: "person", box: CENTRE }];
    await vi.advanceTimersByTimeAsync(SETTLE_MS + PROBE_MS); // t=800 < the 1 s tick
    expect(h.sent.length).toBe(2);
    const f = h.sent[1] as FrameMsg & { changed?: boolean; labels?: string[] };
    expect(f.changed).toBe(true);
    expect(f.labels?.[0]).toBe("bottle");
    await vi.advanceTimersByTimeAsync(3000);           // still shown: no more changed frames
    expect(changedFlags(h.sent).filter(Boolean)).toHaveLength(1);
    stop();
  });

  it("fast path: a hand holding an object near the centre fires; a hand-held item is labelled", async () => {
    const h = withProbe(harness());
    const stop = startLiveVideo(h.deps);
    await vi.advanceTimersByTimeAsync(PROBE_MS);
    h.st.hand = [350, 350, 650, 650];
    h.st.objects = [{ label: "cup", box: [450, 300, 700, 500] }];
    await vi.advanceTimersByTimeAsync(SETTLE_MS + 2 * PROBE_MS);
    const changed = h.sent.filter((m) => (m as { changed?: boolean }).changed) as (FrameMsg & { labels?: string[] })[];
    expect(changed).toHaveLength(1);
    expect(changed[0].labels).toEqual(expect.arrayContaining(["held item", "cup"]));
    stop();
  });

  it("a fast frame is queued behind a live capture in flight, and the next regular tick stays quiet", async () => {
    const h = withProbe(harness());
    let release!: () => void;
    const base = h.deps.capture;
    h.deps.capture = vi.fn(async (keep) => {
      if (h.st.captures === 0) await new Promise<void>((r) => { release = r; });
      return base(keep);
    });
    const stop = startLiveVideo(h.deps);
    await vi.advanceTimersByTimeAsync(PROBE_MS);
    h.st.objects = [{ label: "bottle", box: CENTRE }];
    await vi.advanceTimersByTimeAsync(SETTLE_MS + PROBE_MS);
    expect(h.sent).toHaveLength(0);
    release();
    await flush(); await flush();
    expect(changedFlags(h.sent)).toEqual([false, true]);
    await vi.advanceTimersByTimeAsync(1000 - (SETTLE_MS + 2 * PROBE_MS) + 1); // the 1 s tick, < FAST_QUIET_MS later
    expect(FAST_QUIET_MS).toBeGreaterThan(0);
    expect(h.sent).toHaveLength(2);
    stop();
  });

  it("logs live_stats once a minute: sent, changed, fast and skips by reason", async () => {
    const h = withProbe(harness());
    const stop = startLiveVideo(h.deps);
    await vi.advanceTimersByTimeAsync(PROBE_MS);
    h.st.objects = [{ label: "bottle", box: CENTRE }];
    await vi.advanceTimersByTimeAsync(2000);
    h.st.buffered = LIVE_MAX_BUFFERED + 1;
    await vi.advanceTimersByTimeAsync(3000);
    h.st.buffered = 0; h.st.toolBusy = true;
    await vi.advanceTimersByTimeAsync(2000);
    h.st.toolBusy = false;
    expect(h.logs).toEqual([]);
    await vi.advanceTimersByTimeAsync(STATS_EVERY_MS);
    const line = h.logs.find((l) => l.tag === "live_stats");
    expect(line).toBeDefined();
    const d = line!.data as { sent: number; changed: number; fast: number; skipped: Record<string, number>; fired: Record<string, number> };
    expect(d.changed).toBe(1);
    expect(d.fast).toBe(1);
    expect(d.fired.label).toBe(1);
    expect(d.skipped.buffered).toBe(3);
    expect(d.skipped.tool).toBe(2);
    expect(d.sent).toBeGreaterThan(50);
    stop();
  });
});

describe("createChangeDetector", () => {
  it("the first sample is only the reference", () => {
    const d = createChangeDetector();
    expect(d.feed(sample(grid(100), 0, ["cup"], true))).toBeNull();
  });

  it("a new centred label fires once it stayed ~400 ms; the same label again does not", () => {
    const d = createChangeDetector();
    d.feed(sample(grid(100), 0, ["cup"]));
    expect(d.feed(sample(grid(100), 200, ["Cup"]))).toBeNull();
    expect(d.feed(sample(grid(101), 400, ["cup", "bottle"]))).toBeNull();
    expect(d.feed(sample(grid(101), 600, ["cup", "bottle"]))).toBeNull();
    expect(d.feed(sample(grid(101), 800, ["cup", "bottle"]))).toBe("label");
    expect(d.feed(sample(grid(101), 3000, ["bottle"]))).toBeNull();
  });

  it("people never count as something shown", () => {
    const d = createChangeDetector();
    d.feed(sample(grid(100), 0));
    for (let t = 200; t <= 2000; t += 200) expect(d.feed(sample(grid(100), t, ["person"]))).toBeNull();
  });

  it("a hand holding something fires after settling, once per showing", () => {
    const d = createChangeDetector();
    d.feed(sample(grid(100), 0));
    expect(d.feed(sample(grid(100), 200, [], true))).toBeNull();
    expect(d.feed(sample(grid(100), 400, [], true))).toBeNull();
    expect(d.feed(sample(grid(100), 600, [], true))).toBe("held");
    expect(d.feed(sample(grid(100), 4000, [], true))).toBeNull();       // still held: said already
    expect(d.feed(sample(grid(100), 4200, [], false))).toBeNull();
    expect(d.feed(sample(grid(100), 6000, [], false))).toBeNull();      // put down long enough: re-armed
    expect(d.feed(sample(grid(100), 6200, [], true))).toBeNull();
    expect(d.feed(sample(grid(100), 6600, [], true))).toBe("held");
  });

  it("a big gray change fires once steady for ~400 ms, and tolerates chest-mount sway", () => {
    const d = createChangeDetector();
    d.feed(sample(grid(100), 0));
    // Sway: +-6 around the reference, sample to sample: never far enough to count.
    for (let t = 200, i = 0; t <= 3000; t += 200, i++) expect(d.feed(sample(grid(i % 2 ? 106 : 94), t))).toBeNull();
    const big = 100 + CHANGE_THRESHOLD + 30;
    expect(d.feed(sample(grid(big), 3200))).toBeNull();                  // just moved
    expect(d.feed(sample(grid(big + 4), 3400))).toBeNull();              // steady-ish (sway < 8)...
    expect(d.feed(sample(grid(big - 3), 3600))).toBe("scene");           // ...for 400 ms
    expect(d.feed(sample(grid(big), 3800))).toBeNull();                  // settled: nothing new
  });

  it("mid-motion never fires", () => {
    const d = createChangeDetector();
    d.feed(sample(grid(0), 0));
    for (let t = 200, v = 40; t <= 2000; t += 200, v += 20) expect(d.feed(sample(grid(v), t))).toBeNull();
  });

  it("works without a picture (labels / hand only) and reset makes the next sample a reference", () => {
    const d = createChangeDetector({ settleMs: 0 });
    d.feed(sample(null, 0));
    expect(d.feed(sample(null, 200, ["bottle"]))).toBe("label");
    d.reset();
    expect(d.feed(sample(grid(200), 5000, ["cup"]))).toBeNull();
  });
});

describe("readScene", () => {
  it("centred labels first, people and corners not 'shown', held items flagged", () => {
    const r = readScene([{ label: "Person", box: CENTRE }, { label: "chair", box: CORNER }, { label: "bottle", box: CENTRE }], null);
    expect(r.centred).toEqual(["bottle"]);
    expect(r.held).toBe(false);
    expect(r.labels).toEqual(["bottle", "person", "chair"]);
    const held = readScene([{ label: "cup", box: [450, 300, 700, 500] }], [350, 350, 650, 650]);
    expect(held.held).toBe(true);
    expect(held.labels.slice(0, 2)).toEqual(["held item", "cup"]);
    expect(readScene([], [0, 0, 100, 100]).held).toBe(false); // a small hand in the corner holds nothing
  });
});

describe("probeGrid", () => {
  it("draws the camera onto a 32x24 gray grid without encoding, and yields to an owner tool photo", () => {
    const draws: [number, number][] = [];
    __setCaptureDeps({
      source: { width: () => 1280, height: () => 720, draw: (_c, w, h) => { draws.push([w, h]); } },
      makeCanvas: (w, h) => ({ canvas: { width: w, height: h } as HTMLCanvasElement,
        ctx: { getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4).fill(120) }) } as unknown as CanvasRenderingContext2D }),
      worker: () => null,
    });
    const g = probeGrid();
    expect(g).not.toBeNull();
    expect(g!.length).toBe(GRID_W * GRID_H);
    expect(Math.round(g![0])).toBe(120);
    expect(draws).toEqual([[GRID_W, GRID_H]]);
    const release = holdBackground();
    expect(probeGrid()).toBeNull();
    release();
    __setCaptureDeps({ source: null });
    expect(probeGrid()).toBeNull();
  });
});
