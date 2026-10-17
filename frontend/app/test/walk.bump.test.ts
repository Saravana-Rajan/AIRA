// frontend/app/test/walk.bump.test.ts
import { describe, it, expect } from "vitest";
import type { Box } from "../src/guidance/geometry";
import {
  timeToContact, inWalkingPath, obstacleSide, trigger, observe, shouldWarn, recordWarn, raiseUrgency, initialBumpState,
  TTC_WARN_S, COOLDOWN_MS, GLOBAL_COOLDOWN_MS, CONFIRM_FRAMES, STREAK_GAP_MS, WARN_VIBRATE, ALERT_BUDGET_MS,
  type BumpDetection, type BumpState,
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

/** Owner walking with the motion sensor available. */
const walking = (over: Partial<BumpState> = {}): BumpState => ({ ...initialBumpState(), hasSensor: true, walking: true, ...over });
/** Owner standing still (sensor says so). */
const standing = (): BumpState => ({ ...initialBumpState(), hasSensor: true, walking: false });
/** Observe `det` for `frames` consecutive frames (125 ms apart) and return the state and last detection. */
function seen(s: BumpState, mk: (ts: number) => BumpDetection, frames = CONFIRM_FRAMES, t0 = 1000) {
  let d = mk(t0);
  for (let i = 0; i < frames; i++) { d = mk(t0 + i * 125); s = observe(s, d); }
  return { s, d };
}

describe("trigger (what could warn this frame)", () => {
  it("fast approach in the path while walking; size only while walking", () => {
    expect(trigger(walking(), det(sq(100), 0.8))).toBe("approach");
    expect(trigger(walking(), det([400, 450, 760, 550], Infinity))).toBe("size");
    expect(trigger(walking(), det([400, 450, 740, 550], Infinity))).toBeNull();
  });
  it("standing still at the counter: a big close object never triggers, nor does an approach", () => {
    expect(trigger(standing(), det([100, 300, 900, 700], Infinity))).toBeNull();
    expect(trigger(standing(), det(sq(100), 0.5))).toBeNull();
  });
  it("no motion sensor: approach-only (size warnings stay off)", () => {
    const s = initialBumpState();
    expect(s.hasSensor).toBe(false);
    expect(trigger(s, det(sq(100), 0.5))).toBe("approach");
    expect(trigger(s, det([100, 300, 900, 700], Infinity))).toBeNull();
  });
  it("walking mode off: nothing triggers, sensor or not", () => {
    expect(trigger(walking({ walkingMode: false }), det(sq(100), 0.5))).toBeNull();
    expect(trigger({ ...initialBumpState(), walkingMode: false }, det(sq(100), 0.5))).toBeNull();
  });
  it("slow approach or outside the path never triggers", () => {
    expect(trigger(walking(), det(sq(100), TTC_WARN_S + 0.1))).toBeNull();
    expect(trigger(walking(), det(sq(100, 100), 0.5))).toBeNull();
  });
});

describe("shouldWarn", () => {
  it("needs the trigger in 2 consecutive frames: a single-frame spike gives no warning", () => {
    expect(CONFIRM_FRAMES).toBe(2);
    const one = seen(walking(), (ts) => det(sq(100), 0.8, ts), 1);
    expect(shouldWarn(one.s, one.d)).toBeNull();
    const two = seen(walking(), (ts) => det(sq(100), 0.8, ts), 2);
    const w = shouldWarn(two.s, two.d);
    expect(w).not.toBeNull();
    expect(w!.phrase).toBe("bump.stop");
    expect(w!.side).toBe("centre");
    expect(w!.vibrate).toEqual([80, 40, 80]);
    expect(WARN_VIBRATE).toEqual([80, 40, 80]);
    expect(ALERT_BUDGET_MS).toBe(150);
  });
  it("a quiet frame in between resets the streak; so does a long gap", () => {
    let s = observe(walking(), det(sq(100), 0.8, 1000));
    s = observe(s, det(sq(100), Infinity, 1125));
    const d = det(sq(100), 0.8, 1250);
    s = observe(s, d);
    expect(shouldWarn(s, d)).toBeNull();
    let g = observe(walking(), det(sq(100), 0.8, 1000));
    const late = det(sq(100), 0.8, 1000 + STREAK_GAP_MS + 1);
    g = observe(g, late);
    expect(shouldWarn(g, late)).toBeNull();
  });
  it("size trigger (big box, not growing) warns only while walking and after 2 frames", () => {
    const big: Box = [400, 450, 760, 550];
    const w = seen(walking(), (ts) => det(big, Infinity, ts));
    expect(shouldWarn(w.s, w.d)).not.toBeNull();
    const st = seen(standing(), (ts) => det(big, Infinity, ts));
    expect(shouldWarn(st.s, st.d)).toBeNull();
    const ns = seen(initialBumpState(), (ts) => det(big, Infinity, ts));
    expect(shouldWarn(ns.s, ns.d)).toBeNull();
  });
  it("standing still with a big object right in front: never a warning, however many frames", () => {
    const r = seen(standing(), (ts) => det([100, 300, 900, 700], Infinity, ts), 20);
    expect(shouldWarn(r.s, r.d)).toBeNull();
  });
  it("walking mode off: no warnings even for a confirmed approach", () => {
    const r = seen(walking({ walkingMode: false }), (ts) => det(sq(100), 0.5, ts), 4);
    expect(shouldWarn(r.s, r.d)).toBeNull();
  });
  it("side obstacle says left / right", () => {
    const r = seen(walking(), (ts) => det([500, 250, 900, 380], 0.5, ts));
    const w = shouldWarn(r.s, r.d);
    expect(w!.phrase).toBe("bump.left_right");
    expect(w!.side).toBe("left");
  });
  it("global cooldown: after one warning nothing warns for 5 s, whatever the object", () => {
    expect(GLOBAL_COOLDOWN_MS).toBe(5000);
    const first = seen(walking(), (ts) => det(sq(100), 0.5, ts, "crate"));
    let s = recordWarn(first.s, first.d);
    const t = first.d.ts;
    const other = seen(s, (ts) => det(sq(100), 0.5, ts, "fridge_door"), 2, t + 200);
    expect(shouldWarn(other.s, other.d)).toBeNull();
    const late = seen(s, (ts) => det(sq(100), 0.5, ts, "fridge_door"), 2, t + GLOBAL_COOLDOWN_MS - 125 - 1);
    expect(shouldWarn(late.s, late.d)).toBeNull();
    const ok = seen(s, (ts) => det(sq(100), 0.5, ts, "fridge_door"), 2, t + GLOBAL_COOLDOWN_MS - 125);
    expect(shouldWarn(ok.s, ok.d)).not.toBeNull();
    s = recordWarn(ok.s, ok.d);
    expect(s.lastAnyWarnAt).toBe(ok.d.ts);
    expect(s.lastWarnAt.fridge_door).toBe(ok.d.ts);
  });
  it("per-object cooldown 1.5 s still applies on top", () => {
    expect(COOLDOWN_MS).toBe(1500);
    const first = seen(walking(), (ts) => det(sq(100), 0.5, ts, "crate"));
    const s = recordWarn(first.s, first.d);
    // pretend the global cooldown is over but the per-object one is not
    const again = seen({ ...s, lastAnyWarnAt: null }, (ts) => det(sq(100), 0.5, ts, "crate"), 2, first.d.ts + 125);
    expect(shouldWarn(again.s, again.d)).toBeNull();
    const later = seen({ ...s, lastAnyWarnAt: null }, (ts) => det(sq(100), 0.5, ts, "crate"), 2, first.d.ts + COOLDOWN_MS - 125);
    expect(shouldWarn(later.s, later.d)).not.toBeNull();
  });
  it("muted while hand guidance is active and the hand is near the target", () => {
    const r = seen(walking({ handNearTarget: true }), (ts) => det(sq(100), 0.5, ts));
    expect(shouldWarn(r.s, r.d)).toBeNull();
  });
  it("server can only raise urgency (wider thresholds), never lower it", () => {
    let s = raiseUrgency(walking(), 5000);
    const urgent = seen(s, (ts) => det(sq(100), TTC_WARN_S + 0.3, ts), 2, 4000 - 125);
    expect(shouldWarn(urgent.s, urgent.d)).not.toBeNull();   // urgent ⇒ more sensitive
    const expired = seen(s, (ts) => det(sq(100), TTC_WARN_S + 0.3, ts), 2, 6000);
    expect(shouldWarn(expired.s, expired.d)).toBeNull();      // urgency expired
    s = raiseUrgency(s, 3000);                                // earlier deadline is ignored
    expect(s.urgentUntil).toBe(5000);
    const normal = seen(s, (ts) => det(sq(100), 0.5, ts), 2, 6000);
    expect(shouldWarn(normal.s, normal.d)).not.toBeNull();    // normal warnings stay on
  });
  it("does not mutate the state", () => {
    const s = walking();
    const copy = { ...s, streak: { ...s.streak }, lastWarnAt: { ...s.lastWarnAt } };
    observe(s, det(sq(100), 0.5));
    recordWarn(s, det(sq(100), 0.5));
    raiseUrgency(s, 9999);
    shouldWarn(s, det(sq(100), 0.5));
    expect(s).toEqual(copy);
  });
});
