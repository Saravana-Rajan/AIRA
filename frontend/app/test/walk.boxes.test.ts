// frontend/app/test/walk.boxes.test.ts — MediaPipe boxes to the monitor: the BoundingBox conversion (spec §5a),
// the additive `boxes` client message, its 5/s throttle, the guard's danger flag and the overlay store.
import { describe, it, expect, vi } from "vitest";
import { isClientMsg } from "@aira/contracts";
import { fromMediaPipe, toBox1000 } from "../src/walk/detections";
import { BOXES_MAX_PER_S, createBoxSender, createBoxStore, toBoxesMsg } from "../src/walk/boxes";
import { createBumpGuard } from "../src/walk/guard";

describe("MediaPipe BoundingBox → AIRA [ymin, xmin, ymax, xmax] 0-1000", () => {
  it("converts pixel origin/size of the detector input into y-first, 0-1000 integers", () => {
    // 320×240 input: a box at x 32..160, y 24..120
    expect(toBox1000({ originX: 32, originY: 24, width: 128, height: 96 }, 320, 240)).toEqual([100, 100, 500, 500]);
    // rounding: 1/3 of 300 px → 333, not 333.33
    expect(toBox1000({ originX: 100, originY: 0, width: 100, height: 300 }, 300, 300)).toEqual([0, 333, 1000, 667]);
  });

  it("clamps boxes that spill past the frame to 0..1000 and never swaps x and y", () => {
    expect(toBox1000({ originX: -20, originY: -10, width: 400, height: 300 }, 320, 240)).toEqual([0, 0, 1000, 1000]);
    expect(toBox1000({ originX: 300, originY: 230, width: 100, height: 100 }, 320, 240)).toEqual([958, 938, 1000, 1000]);
    const b = toBox1000({ originX: 0, originY: 120, width: 320, height: 60 }, 320, 240);
    expect(b).toEqual([500, 0, 750, 1000]);           // y first: a wide band across the lower half
    expect(b.every((v) => Number.isInteger(v) && v >= 0 && v <= 1000)).toBe(true);
  });

  it("uses the detector input size (the video's intrinsic size), ignores angle and keeps the top score", () => {
    const res = {
      detections: [
        { boundingBox: { originX: 640, originY: 360, width: 640, height: 360, angle: 0 },
          categories: [{ categoryName: "chair", score: 0.5 }, { categoryName: "couch", score: 0.9 }] },
        { boundingBox: { originX: 0, originY: 0, width: 10, height: 10 }, categories: [{ categoryName: "cup", score: 0.1 }] },
        { categories: [{ categoryName: "ghost", score: 0.99 }] },
      ],
    };
    expect(fromMediaPipe(res, 1280, 720)).toEqual([{ label: "couch", score: 0.9, box: [500, 500, 1000, 1000] }]);
    expect(fromMediaPipe(res, 0, 720)).toEqual([]);   // no size yet: nothing to normalise against
  });
});

describe("the boxes client message", () => {
  it("builds a valid additive {t:'boxes', source:'mediapipe'} message with severity only for danger", () => {
    const m = toBoxesMsg([
      { label: " chair ", box: [420, 310, 900, 690], score: 0.8234, danger: true },
      { label: "bottle", box: [100, 50, 300, 120], score: 0.61 },
      { label: "", box: [0, 0, 10, 10] },
    ]);
    expect(m).toEqual({ t: "boxes", source: "mediapipe", objects: [
      { label: "chair", box: [420, 310, 900, 690], score: 0.82, severity: "danger" },
      { label: "bottle", box: [100, 50, 300, 120], score: 0.61 },
      { label: "object", box: [0, 0, 10, 10] },
    ] });
    expect(isClientMsg(m)).toBe(true);
    expect(isClientMsg(toBoxesMsg([], "bump"))).toBe(true);
  });

  it("sends at most 5 a second (every other 8 Hz frame), and never drops a danger frame", () => {
    let t = 0;
    const sent: unknown[] = [];
    const s = createBoxSender({ send: (m) => { sent.push(m); return true; }, now: () => t });
    expect(BOXES_MAX_PER_S).toBe(5);
    const r = [{ label: "chair", box: [1, 2, 3, 4] as [number, number, number, number], score: 0.5 }];
    const results: boolean[] = [];
    for (let i = 0; i < 16; i++) { t = i * 125; results.push(s.push(r)); }   // 8 Hz for 2 s
    expect(sent.length).toBe(8);                      // one every 250 ms: 4/s, under the 5/s cap
    expect(results.slice(0, 4)).toEqual([true, false, true, false]);
    expect(s.stats()).toEqual({ sent: 8, dropped: 8 });
    for (let i = 0; i < 10; i++) { t = 10_000 + i * 100; s.push(r); }       // 10 Hz: 5 of 10 go out
    expect(s.stats().sent).toBe(13);
    t = 2000 + 10;                                    // right after a sent frame: would be dropped...
    expect(s.push([{ ...r[0], danger: true }])).toBe(true);   // ...but danger goes out at once
    expect((sent[sent.length - 1] as { objects: { severity?: string }[] }).objects[0].severity).toBe("danger");
  });

  it("reports whether the socket took it", () => {
    const s = createBoxSender({ send: () => false, now: () => 0 });
    expect(s.push([])).toBe(false);
  });
});

describe("the bump guard reports every frame's boxes with its danger flag", () => {
  const sq = (s: number, cx = 500, cy = 500): [number, number, number, number] => [cy - s / 2, cx - s / 2, cy + s / 2, cx + s / 2];
  const res = (ts: number, boxes: [number, number, number, number][]) =>
    ({ ts, floor: null, dets: boxes.map((box, i) => ({ label: i === 0 ? "crate" : "cup", score: [0.7, 0.8][i], box })) });

  it("flags an approaching object in the path as danger before any warning is spoken", () => {
    const reports: { reports: unknown[]; ts: number }[] = [];
    const g = createBumpGuard({
      grab: async () => null, detector: { detect: async () => res(0, []) },
      vibrate: () => {}, earcon: () => {}, speak: () => {}, lang: () => "en", now: () => 0,
      motion: { available: true, isWalking: () => true },
      onDetections: (r, ts) => reports.push({ reports: r, ts }),
    });
    g.process(res(0, [sq(100), [100, 50, 200, 120]]));   // a crate ahead and a cup far left (off the path)
    expect(reports[0].ts).toBe(0);
    expect(reports[0].reports).toEqual([
      { label: "crate", box: sq(100), score: 0.7, danger: false },
      { label: "cup", box: [100, 50, 200, 120], score: 0.8, danger: false },
    ]);
    g.process(res(250, [sq(150)]));                    // doubling in 0.5 s: TTC under the limit → danger
    expect(reports[1].reports).toEqual([{ label: "crate", box: sq(150), score: 0.7, danger: true }]);
    expect(reports[1].reports.length).toBe(1);        // the cup left the frame
    g.process(res(500, []));
    expect(reports[2].reports).toEqual([]);
  });

  it("an onDetections that throws never stops the guard", () => {
    const g = createBumpGuard({
      grab: async () => null, detector: { detect: async () => res(0, []) },
      vibrate: () => {}, earcon: () => {}, speak: () => {}, lang: () => "en", now: () => 0,
      onDetections: () => { throw new Error("boom"); },
    });
    expect(() => g.process(res(0, [sq(100)]))).not.toThrow();
  });
});

describe("the box store (overlay feed)", () => {
  it("keeps the latest MediaPipe reports and ER 2 server boxes, and notifies subscribers", () => {
    let t = 100;
    const store = createBoxStore(() => t);
    const seen: unknown[] = [];
    const off = store.subscribe((s) => seen.push(s));
    store.setMediapipe([{ label: "chair", box: [1, 2, 3, 4], danger: true }]);
    expect(store.get().mediapipe).toEqual({ reports: [{ label: "chair", box: [1, 2, 3, 4], danger: true }], at: 100 });
    t = 200;
    store.onServerMsg({ t: "cue", cue: "ack" });                                  // ignored
    store.onServerMsg({ t: "boxes", source: "er2", objects: [{ label: "milk", box: [1, 1, 5, 5] }], target: "milk", found: true });
    expect(store.get().er2).toEqual({ objects: [{ label: "milk", box: [1, 1, 5, 5] }], target: "milk", found: true, at: 200 });
    expect(seen.length).toBe(2);
    off();
    store.clear();
    expect(seen.length).toBe(2);
    expect(store.get().er2).toBeNull();
  });
});
