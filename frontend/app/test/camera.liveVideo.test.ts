// frontend/app/test/camera.liveVideo.test.ts — the 1 fps Gemini Live video sender and the scene-change detector.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import { CHANGE_THRESHOLD, LIVE_MAX_BUFFERED, createChangeDetector, startLiveVideo, type LiveVideoDeps } from "../src/camera/liveVideo";
import { GRID_H, GRID_W } from "../src/camera/sceneGate";
import { frameSpec } from "../src/camera/capture";
import type { FrameMsg } from "../src/camera/capture";

const W = 64;
const H = 48;
/** A synthetic gray frame: flat `base`, optionally with a bright block (an object held up) on the left half. */
function gray(base: number, block = 0): Float32Array {
  const g = new Float32Array(W * H).fill(base);
  if (block) for (let y = 8; y < 40; y++) for (let x = 4; x < 32; x++) g[y * W + x] = block;
  return g;
}
const grid = (n: number) => new Float32Array(GRID_W * GRID_H).fill(n);

function harness(over: Partial<LiveVideoDeps> = {}) {
  const sent: ClientMsg[] = [];
  const st = { active: true, camera: true, hidden: false, buffered: 0, toolBusy: false, toolCount: 0,
    labels: [] as string[], frame: gray(100), captures: 0 };
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
    labels: () => st.labels,
    ...over,
  };
  return { sent, st, deps };
}

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("live frame spec", () => {
  it("is 768 px at quality 0.7; count frames are 1024 px", () => {
    expect(frameSpec("live")).toEqual({ maxSide: 768, quality: 0.7 });
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

  it("never lets more than about one live frame wait on the socket (an owner frame would queue behind them)", () => {
    expect(LIVE_MAX_BUFFERED).toBeLessThanOrEqual(32_000);
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

  it("marks a frame changed, with the detector labels, once the new view is steady", async () => {
    const { sent, st, deps } = harness();
    const stop = startLiveVideo(deps);
    await flush();                                   // reference view
    st.frame = gray(100, 250); st.labels = ["bottle"];
    await vi.advanceTimersByTimeAsync(1000);         // moved, but not steady yet
    await vi.advanceTimersByTimeAsync(1000);         // same view again: steady -> changed
    await vi.advanceTimersByTimeAsync(1000);         // nothing new
    const flags = sent.map((m) => (m as FrameMsg & { changed?: boolean }).changed ?? false);
    expect(flags).toEqual([false, false, true, false]);
    expect((sent[2] as { labels?: string[] }).labels).toEqual(["bottle"]);
    stop();
  });
});

describe("createChangeDetector", () => {
  it("the first frame is only the reference", () => {
    const d = createChangeDetector();
    expect(d.feed(grid(100), [])).toBe(false);
  });
  it("a small drift is not a change; a big one is, once steady", () => {
    const d = createChangeDetector();
    d.feed(grid(100), []);
    expect(d.feed(grid(105), [])).toBe(false);
    expect(d.feed(grid(100 + CHANGE_THRESHOLD + 30), [])).toBe(false); // mid-motion: differs from last frame
    expect(d.feed(grid(100 + CHANGE_THRESHOLD + 31), [])).toBe(true);  // steady now
    expect(d.feed(grid(100 + CHANGE_THRESHOLD + 31), [])).toBe(false); // already settled
  });
  it("a new detector label on a steady view is a change; the same label again is not", () => {
    const d = createChangeDetector();
    d.feed(grid(100), ["cup"]);
    expect(d.feed(grid(100), ["Cup"])).toBe(false);
    expect(d.feed(grid(101), ["cup", "bottle"])).toBe(true);
    expect(d.feed(grid(101), ["bottle"])).toBe(false);
  });
  it("reset makes the next frame a reference again", () => {
    const d = createChangeDetector();
    d.feed(grid(0), []);
    d.reset();
    expect(d.feed(grid(200), [])).toBe(false);
  });
});
