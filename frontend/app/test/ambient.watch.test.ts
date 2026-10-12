// frontend/app/test/ambient.watch.test.ts — the hold-up watcher: hand stream + object boxes → one identify_held frame
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import { __resetBumpMute, bumpMuted, BUMP_MUTE_MS, freshObjects, noteBumpWarning, startHoldUpWatcher } from "../src/ambient/watch";
import type { Hand } from "../src/guidance/hands";
import { boxStore } from "../src/walk/boxes";

const HAND: Hand = { tip: { x1000: 500, y1000: 500 }, box: [300, 350, 700, 650] };
const BOTTLE = { label: "bottle", box: [250, 400, 650, 560] as [number, number, number, number] };

function setup(opts: { suppressed?: () => boolean } = {}) {
  let onHand: ((h: Hand | null) => void) | null = null;
  const stopTrack = vi.fn();
  const sent: ClientMsg[] = [];
  let now = 0;
  const capture = vi.fn(async (purpose: string) => ({ t: "frame" as const, id: "h1", jpeg: "AAAA", w: 640, h: 480, ts: now, purpose: purpose as "identify_held" }));
  const objects = { list: [BOTTLE] };
  const stop = startHoldUpWatcher({
    trackHand: async (cb) => { onHand = cb; return stopTrack; },
    objects: () => objects.list,
    capture,
    send: (m) => { sent.push(m); return true; },
    now: () => now,
    suppressed: opts.suppressed,
  });
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
  /** The hand is seen every 100 ms from t0 to t1. */
  const show = async (hand: Hand | null, t0: number, t1: number) => {
    for (now = t0; now <= t1; now += 100) { onHand!(hand); await flush(); }
  };
  return { show, flush, sent, capture, stopTrack, stop, objects, tick: () => { now += 100; }, getOnHand: () => onHand };
}

beforeEach(() => {
  __resetBumpMute();
  boxStore.clear();
});

describe("startHoldUpWatcher", () => {
  it("sends one identify_held frame once an object is held steadily, and not again for the same object", async () => {
    const w = setup();
    await w.flush();
    await w.show(HAND, 0, 5000);
    expect(w.capture).toHaveBeenCalledTimes(1);
    expect(w.capture).toHaveBeenCalledWith("identify_held");
    expect(w.sent).toEqual([expect.objectContaining({ t: "frame", purpose: "identify_held" })]);
  });

  it("sends nothing without a hand, and nothing after stop()", async () => {
    const w = setup();
    await w.flush();
    await w.show(null, 0, 2000);
    expect(w.sent).toEqual([]);
    w.stop();
    expect(w.stopTrack).toHaveBeenCalled();
    await w.show(HAND, 2100, 4000);
    expect(w.sent).toEqual([]);
  });

  it("the bump guard beats everything: nothing goes while a warning is fresh, and the hold starts over", async () => {
    const w = setup({ suppressed: () => bumpMuted(0) });
    await w.flush();
    noteBumpWarning(0);                       // "Stop!" just now
    await w.show(HAND, 0, 2000);
    expect(w.sent).toEqual([]);
    expect(BUMP_MUTE_MS).toBe(2500);
  });

  it("a camera failure is swallowed and the next hold-up tries again", async () => {
    const w = setup();
    await w.flush();
    w.capture.mockRejectedValueOnce(new Error("no camera"));
    await w.show(HAND, 0, 1000);
    expect(w.sent).toEqual([]);
    // the object leaves and comes back: a new hold-up
    await w.show(null, 1100, 3000);
    await w.show(HAND, 3100, 4500);
    expect(w.sent).toHaveLength(1);
  });

  it("a stop before MediaPipe is ready stops the tracker as soon as it resolves", async () => {
    let resolveTrack: ((stop: () => void) => void) | null = null;
    const stopTrack = vi.fn();
    const stop = startHoldUpWatcher({
      trackHand: () => new Promise((r) => { resolveTrack = r; }),
      objects: () => [],
      capture: vi.fn(),
      send: () => true,
      now: () => 0,
    });
    stop();
    resolveTrack!(stopTrack);
    await Promise.resolve();
    expect(stopTrack).toHaveBeenCalled();
  });
});

describe("freshObjects", () => {
  it("reads the bump guard's MediaPipe boxes while they are fresh", () => {
    expect(freshObjects(1000)).toEqual([]);
    boxStore.setMediapipe([{ label: "bottle", box: [1, 2, 3, 4], score: 0.9 }]);
    const at = boxStore.get().mediapipe.at;
    expect(freshObjects(at + 10)).toEqual([{ label: "bottle", box: [1, 2, 3, 4] }]);
    expect(freshObjects(at + 5000)).toEqual([]);
  });
});
