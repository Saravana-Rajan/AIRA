import { describe, it, expect } from "vitest";
import { DEFAULT_VOLUME_CHORD, VolumeChordDetector, type VolumeKeyEvent } from "../src/handsfree/volumeChord";

const ev = (key: "up" | "down", type: "down" | "up", t: number): VolumeKeyEvent => ({ key, type, t });

/** Feeds a list and returns the results. */
function run(d: VolumeChordDetector, events: VolumeKeyEvent[]) {
  return events.map((e) => d.feed(e));
}

/** One quick chord: both down within 20 ms of `t0`, both up `hold` ms later. */
const chord = (t0: number, hold = 120): VolumeKeyEvent[] => [
  ev("up", "down", t0), ev("down", "down", t0 + 20), ev("up", "up", t0 + hold), ev("down", "up", t0 + hold + 10),
];

describe("VolumeChordDetector", () => {
  it("fires exactly once on a quick double press of up + down together", () => {
    const d = new VolumeChordDetector();
    const r = run(d, [...chord(0), ...chord(400)]);
    expect(r.filter((x) => x.fire)).toHaveLength(1);
    expect(r[r.length - 1]).toEqual({ fire: true, consume: true }); // on the release that completes chord 2
  });

  it("a single volume press never fires and is never consumed, whatever its length", () => {
    const d = new VolumeChordDetector();
    for (const r of run(d, [ev("up", "down", 0), ev("up", "up", 50)])) expect(r).toEqual({ fire: false, consume: false });
    for (const r of run(d, [ev("down", "down", 100), ev("down", "up", 5_000)])) expect(r).toEqual({ fire: false, consume: false });
    // two single presses of different keys, one after the other, are not a chord
    for (const r of run(d, [ev("up", "down", 6_000), ev("up", "up", 6_050), ev("down", "down", 6_100), ev("down", "up", 6_150)])) {
      expect(r).toEqual({ fire: false, consume: false });
    }
  });

  it("the first key of a chord is never consumed (it could still be a normal press); the second is", () => {
    const d = new VolumeChordDetector();
    const r = run(d, chord(0));
    expect(r[0]).toEqual({ fire: false, consume: false });
    expect(r[1]).toEqual({ fire: false, consume: true });
    expect(r[2]).toEqual({ fire: false, consume: true });
    expect(r[3]).toEqual({ fire: false, consume: true });
  });

  it("a long hold of both keys (TalkBack's accessibility shortcut) never fires and nothing is consumed on release", () => {
    const d = new VolumeChordDetector();
    const hold = [ev("up", "down", 0), ev("down", "down", 30), ev("up", "up", 3_200), ev("down", "up", 3_210)];
    const r = run(d, hold);
    expect(r.some((x) => x.fire)).toBe(false);
    expect(r[2]).toEqual({ fire: false, consume: false });
    expect(r[3]).toEqual({ fire: false, consume: false });
    // and a quick chord right after a hold is only the FIRST half of a double press
    expect(run(d, chord(3_300)).some((x) => x.fire)).toBe(false);
  });

  it("a hold followed by a quick chord, or a chord followed by a hold, does not fire", () => {
    const d = new VolumeChordDetector();
    const r1 = run(d, [...chord(0), ev("up", "down", 300), ev("down", "down", 320), ev("up", "up", 2_000), ev("down", "up", 2_010)]);
    expect(r1.some((x) => x.fire)).toBe(false);
  });

  it("key repeats while held (Android sends repeated downs) do not make a chord", () => {
    const d = new VolumeChordDetector();
    const r = run(d, [ev("up", "down", 0), ev("up", "down", 500), ev("up", "down", 1_000), ev("down", "down", 1_010), ev("up", "up", 1_100), ev("down", "up", 1_110)]);
    expect(r.some((x) => x.fire)).toBe(false);
    expect(r.some((x) => x.consume)).toBe(false);
  });

  it("two chords too far apart do not fire; the second one starts a new double press", () => {
    const d = new VolumeChordDetector();
    const far = DEFAULT_VOLUME_CHORD.doublePressWindowMs + 500;
    expect(run(d, [...chord(0), ...chord(far)]).some((x) => x.fire)).toBe(false);
    expect(run(d, chord(far + 300)).some((x) => x.fire)).toBe(true); // chord 2 + chord 3 are close enough
  });

  it("keys pressed too far apart to be one chord are not a chord", () => {
    const d = new VolumeChordDetector();
    const r = run(d, [ev("up", "down", 0), ev("down", "down", DEFAULT_VOLUME_CHORD.chordWindowMs + 100), ev("up", "up", 300), ev("down", "up", 310), ...chord(500)]);
    expect(r.some((x) => x.fire)).toBe(false);
    expect(r.slice(0, 4).some((x) => x.consume)).toBe(false);
  });

  it("a lone press between two chords breaks the double press", () => {
    const d = new VolumeChordDetector();
    const r = run(d, [...chord(0), ev("up", "down", 200), ev("up", "up", 250), ...chord(400)]);
    expect(r.some((x) => x.fire)).toBe(false);
  });

  it("timings are configurable", () => {
    const d = new VolumeChordDetector({ maxHoldMs: 1_000, doublePressWindowMs: 3_000, chordWindowMs: 500 });
    const slow = (t0: number) => [ev("up", "down", t0), ev("down", "down", t0 + 400), ev("up", "up", t0 + 900), ev("down", "up", t0 + 950)];
    const r = run(d, [...slow(0), ...slow(2_500)]);
    expect(r[r.length - 1].fire).toBe(true);
    expect(d.cfg).toEqual({ maxHoldMs: 1_000, doublePressWindowMs: 3_000, chordWindowMs: 500 });
  });

  it("reset clears a half-done double press", () => {
    const d = new VolumeChordDetector();
    run(d, chord(0));
    d.reset();
    expect(run(d, chord(300)).some((x) => x.fire)).toBe(false);
  });

  it("an up for a key that was never down is ignored", () => {
    const d = new VolumeChordDetector();
    expect(d.feed(ev("up", "up", 0))).toEqual({ fire: false, consume: false });
    expect(run(d, [...chord(10), ...chord(400)]).some((x) => x.fire)).toBe(true);
  });
});
