// frontend/app/test/walk.bump.test.ts
import { describe, it, expect } from "vitest";
import type { Box } from "../src/guidance/geometry";
import {
  timeToContact, inWalkingPath, obstacleSide, shouldWarn, recordWarn, raiseUrgency, initialBumpState,
  TTC_WARN_S, COOLDOWN_MS, WARN_VIBRATE, ALERT_BUDGET_MS, type BumpDetection,
} from "../src/walk/bump";

/** A box of side `s` (0–1000) centred at (cx, cy). Box is [ymin, xmin, ymax, xmax]. */
const sq = (s: number, cx = 500, cy = 500): Box => [cy - s / 2, cx - s / 2, cy + s / 2, cx + s / 2];
const det = (box: Box, ttcS: number, ts = 1000, key = "crate"): BumpDetection => ({ key, box, ttcS, ts });

describe("timeToContact", () => {
  it("box doubling in 0.5 s at constant speed ⇒ about 1 s to contact", () => {
    expect(timeToContact([sq(100), sq(150), sq(200)], [0, 250, 500])).toBeCloseTo(1.0, 2);
  });
  it("uses a least-squares slope so one noisy box does not dominate", () => {
    const ttc = timeToContact([sq(100), sq(160), sq(150), sq(200)], [0, 166, 333, 500]);
    expect(ttc).toBeGreaterThan(0.8);
    expect(ttc).toBeLessThan(1.3);
  });
  it("shrinking or steady boxes never collide", () => {
    expect(timeToContact([sq(200), sq(150), sq(100)], [0, 250, 500])).toBe(Infinity);
    expect(timeToContact([sq(200), sq(200)], [0, 250])).toBe(Infinity);
  });
  it("needs at least two samples spread in time", () => {
    expect(timeToContact([sq(200)], [0])).toBe(Infinity);
    expect(timeToContact([], [])).toBe(Infinity);
    expect(timeToContact([sq(100), sq(200)], [100, 100])).toBe(Infinity);
  });
});

describe("inWalkingPath (centre 40% = x 300..700)", () => {
  it("a box overlapping the centre band is in the path", () => {
    expect(inWalkingPath([600, 450, 900, 550])).toBe(true);
    expect(inWalkingPath([600, 100, 900, 320])).toBe(true);   // edge pokes into the band
  });
  it("a box fully at the side is not", () => {
    expect(inWalkingPath([600, 0, 900, 290])).toBe(false);
    expect(inWalkingPath([600, 720, 900, 1000])).toBe(false);
  });
  it("degenerate boxes are ignored", () => {
    expect(inWalkingPath([600, 500, 600, 500])).toBe(false);
  });
  it("reports the side for the spoken phrase", () => {
    expect(obstacleSide([600, 280, 900, 400])).toBe("left");
    expect(obstacleSide([600, 600, 900, 720])).toBe("right");
    expect(obstacleSide([600, 450, 900, 550])).toBe("centre");
  });
});

describe("shouldWarn", () => {
  it("warns when approaching fast in the path, with the warn vibration", () => {
    const w = shouldWarn(initialBumpState(), det(sq(100), 0.8));
    expect(w).not.toBeNull();
    expect(w!.phrase).toBe("bump.stop");
    expect(w!.side).toBe("centre");
    expect(w!.vibrate).toEqual([80, 40, 80]);
    expect(WARN_VIBRATE).toEqual([80, 40, 80]);
    expect(ALERT_BUDGET_MS).toBe(150);
  });
  it("warns on a big box (> 35% of frame height) even if it is not growing", () => {
    expect(shouldWarn(initialBumpState(), det([400, 450, 760, 550], Infinity))).not.toBeNull();
    expect(shouldWarn(initialBumpState(), det([400, 450, 740, 550], Infinity))).toBeNull();
  });
  it("does not warn for slow approach or things outside the path", () => {
    expect(shouldWarn(initialBumpState(), det(sq(100), TTC_WARN_S + 0.1))).toBeNull();
    expect(shouldWarn(initialBumpState(), det(sq(100, 100), 0.5))).toBeNull();
  });
  it("side obstacle says left / right", () => {
    const w = shouldWarn(initialBumpState(), det([500, 250, 900, 380], 0.5));
    expect(w!.phrase).toBe("bump.left_right");
    expect(w!.side).toBe("left");
  });
  it("cooldown 1.5 s per object", () => {
    const d0 = det(sq(100), 0.5, 1000, "crate");
    let s = recordWarn(initialBumpState(), d0);
    expect(shouldWarn(s, det(sq(100), 0.5, 1000 + COOLDOWN_MS - 1, "crate"))).toBeNull();
    expect(shouldWarn(s, det(sq(100), 0.5, 1200, "fridge_door"))).not.toBeNull();   // other object
    expect(shouldWarn(s, det(sq(100), 0.5, 1000 + COOLDOWN_MS, "crate"))).not.toBeNull();
    s = recordWarn(s, det(sq(100), 0.5, 2600, "crate"));
    expect(s.lastWarnAt.crate).toBe(2600);
  });
  it("muted while hand guidance is active and the hand is near the target", () => {
    const s = { ...initialBumpState(), handNearTarget: true };
    expect(shouldWarn(s, det(sq(100), 0.5))).toBeNull();
  });
  it("server can only raise urgency (wider thresholds), never lower it", () => {
    let s = raiseUrgency(initialBumpState(), 5000);
    expect(shouldWarn(s, det(sq(100), TTC_WARN_S + 0.3, 4000))).not.toBeNull();   // urgent ⇒ more sensitive
    expect(shouldWarn(s, det(sq(100), TTC_WARN_S + 0.3, 6000))).toBeNull();      // urgency expired
    s = raiseUrgency(s, 3000);                                                    // earlier deadline is ignored
    expect(s.urgentUntil).toBe(5000);
    expect(shouldWarn(s, det(sq(100), 0.5, 6000))).not.toBeNull();               // normal warnings stay on
  });
  it("does not mutate the state", () => {
    const s = initialBumpState();
    recordWarn(s, det(sq(100), 0.5));
    raiseUrgency(s, 9999);
    expect(s).toEqual(initialBumpState());
  });
});
