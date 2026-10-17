// frontend/app/test/ambient.holdUp.test.ts — hold-up detection (pure): stable 600 ms, central and close,
// one frame per 3 s, never the same object twice, reset when it leaves and comes back or a new object appears.
import { describe, it, expect } from "vitest";
import {
  candidateOf, createHoldUpDetector, HAND_LABEL, intersection, isCentral, MIN_GAP_MS, MIN_HAND_AREA, STABLE_MS,
} from "../src/ambient/holdUp";
import type { Box } from "../src/guidance/geometry";

const HAND: Box = [300, 350, 700, 650];          // central, 400×300 = 120 000 ⇒ close
const SMALL_HAND: Box = [450, 480, 550, 520];    // central but tiny (far away)
const EDGE_HAND: Box = [50, 20, 450, 320];       // top-left corner, large
const BOTTLE: Box = [250, 400, 650, 560];        // overlaps HAND
const BAG: Box = [100, 50, 300, 200];            // nowhere near the hand

const objects = (...boxes: Array<[string, Box]>) => boxes.map(([label, box]) => ({ label, box }));
const o = { centre: 0.5, minHandArea: MIN_HAND_AREA };

/** Drive the detector with the same scene every `stepMs` from t0 to t1; returns every hold-up it produced. */
function hold(det: ReturnType<typeof createHoldUpDetector>, scene: { hand: Box | null; objects: ReturnType<typeof objects> },
              t0: number, t1: number, stepMs = 100) {
  const out: Array<{ label: string; ts: number }> = [];
  for (let ts = t0; ts <= t1; ts += stepMs) {
    const h = det.update({ ...scene, ts });
    if (h) out.push({ label: h.label, ts: h.ts });
  }
  return out;
}

describe("geometry", () => {
  it("intersection and the central region", () => {
    expect(intersection(HAND, BOTTLE)).toBe((650 - 300) * (560 - 400));
    expect(intersection(HAND, BAG)).toBe(0);
    expect(isCentral(HAND)).toBe(true);
    expect(isCentral(EDGE_HAND)).toBe(false);
    expect(isCentral([0, 0, 1000, 1000])).toBe(true);     // centre of the frame
  });
});

describe("candidateOf", () => {
  it("the object the hand overlaps wins, by overlap", () => {
    expect(candidateOf({ hand: HAND, objects: objects(["bag", BAG], ["bottle", BOTTLE]) }, o)).toEqual({ label: "bottle", box: BOTTLE });
  });
  it("a hand alone counts only when central and close", () => {
    expect(candidateOf({ hand: HAND, objects: [] }, o)).toEqual({ label: HAND_LABEL, box: HAND });
    expect(candidateOf({ hand: SMALL_HAND, objects: [] }, o)).toBeNull();
    expect(candidateOf({ hand: EDGE_HAND, objects: [] }, o)).toBeNull();
    expect(candidateOf({ hand: null, objects: objects(["bottle", BOTTLE]) }, o)).toBeNull();   // no hand, no hold-up
  });
  it("an object the hand overlaps counts even away from the centre", () => {
    const corner: Box = [0, 0, 300, 300];
    expect(candidateOf({ hand: EDGE_HAND, objects: objects(["bag", corner]) }, o)).toEqual({ label: "bag", box: corner });
  });
});

describe("createHoldUpDetector", () => {
  it("fires once the same object has been held for 600 ms, not before", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 500)).toEqual([]);
    expect(hold(det, scene, 600, 600)).toEqual([{ label: "bottle", ts: 600 }]);
    expect(STABLE_MS).toBe(600);
  });

  it("a hand that is central and close fires on its own", () => {
    const det = createHoldUpDetector();
    expect(hold(det, { hand: HAND, objects: [] }, 0, 700)).toEqual([{ label: HAND_LABEL, ts: 600 }]);
  });

  it("a gap in the hold restarts the 600 ms", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 400)).toEqual([]);
    expect(det.update({ hand: null, objects: [], ts: 500 })).toBeNull();
    expect(hold(det, scene, 600, 1100)).toEqual([]);        // only 500 ms since it came back
    expect(hold(det, scene, 1200, 1200)).toEqual([{ label: "bottle", ts: 1200 }]);
  });

  it("never fires twice for the same object while it stays in the hand", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 20_000)).toEqual([{ label: "bottle", ts: 600 }]);
  });

  it("a different object in the hand fires again, but not within 3 s of the last frame", () => {
    const det = createHoldUpDetector();
    expect(hold(det, { hand: HAND, objects: objects(["bottle", BOTTLE]) }, 0, 1000)).toEqual([{ label: "bottle", ts: 600 }]);
    const cup: Box = [280, 380, 680, 600];
    // the cup is stable from 1100; 3 s after the bottle's frame (3600) is the first allowed moment
    expect(hold(det, { hand: HAND, objects: objects(["cup", cup]) }, 1100, 4000)).toEqual([{ label: "cup", ts: 3600 }]);
    expect(MIN_GAP_MS).toBe(3000);
  });

  it("the same object, taken away and shown again, is new", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 1000)).toEqual([{ label: "bottle", ts: 600 }]);
    expect(hold(det, { hand: null, objects: [] }, 1100, 3000)).toEqual([]);          // gone for ~2 s
    expect(hold(det, scene, 3100, 4000)).toEqual([{ label: "bottle", ts: 3700 }]);
  });

  it("a brief flicker of the hand (under the leave time) does not make the object new", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 1000)).toEqual([{ label: "bottle", ts: 600 }]);
    expect(det.update({ hand: null, objects: [], ts: 1100 })).toBeNull();
    expect(hold(det, scene, 1200, 6000)).toEqual([]);
  });

  it("the same label in a very different place is a new object", () => {
    const det = createHoldUpDetector();
    expect(hold(det, { hand: HAND, objects: objects(["bottle", BOTTLE]) }, 0, 1000)).toEqual([{ label: "bottle", ts: 600 }]);
    const other: Box = [50, 50, 250, 150];
    const hand2: Box = [0, 0, 300, 300];
    expect(hold(det, { hand: hand2, objects: objects(["bottle", other]) }, 1100, 4000)).toEqual([{ label: "bottle", ts: 3600 }]);
  });

  it("reset forgets everything", () => {
    const det = createHoldUpDetector();
    const scene = { hand: HAND, objects: objects(["bottle", BOTTLE]) };
    expect(hold(det, scene, 0, 1000)).toEqual([{ label: "bottle", ts: 600 }]);
    det.reset();
    expect(hold(det, scene, 10_000, 11_000)).toEqual([{ label: "bottle", ts: 10_600 }]);
  });
});
