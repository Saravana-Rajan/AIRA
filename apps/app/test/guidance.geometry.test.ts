// apps/app/test/guidance.geometry.test.ts
import { describe, it, expect } from "vitest";
import { vectorTo, toneParams, microCue, insideBox, DirectionHold, DwellTimer } from "../src/guidance/geometry";

describe("vector + tones (target is [y, x])", () => {
  it("target to the right of the tip ⇒ cue right, positive pan", () => {
    const v = vectorTo([200, 800], { x1000: 200, y1000: 200 });
    expect(v.dx).toBe(600); expect(v.dy).toBe(0);
    expect(microCue(v, false)).toBe("right");
    expect(toneParams(v).pan).toBeGreaterThan(0);
  });
  it("target above the tip ⇒ cue up, higher pitch than level", () => {
    const up = toneParams(vectorTo([100, 500], { x1000: 500, y1000: 500 }));
    const level = toneParams(vectorTo([500, 700], { x1000: 500, y1000: 500 }));
    expect(microCue(vectorTo([100, 500], { x1000: 500, y1000: 500 }), false)).toBe("up");
    expect(up.freqHz).toBeGreaterThan(level.freqHz);
  });
  it("beeps faster when closer, clamped 1..8 Hz", () => {
    const far = toneParams({ dx: 0, dy: 900, dist: 900 });
    const near = toneParams({ dx: 0, dy: 10, dist: 10 });
    expect(far.beepHz).toBe(1);
    expect(near.beepHz).toBeGreaterThan(7);
  });
  it("in box ⇒ stop", () => {
    expect(microCue({ dx: 300, dy: 0, dist: 300 }, true)).toBe("stop");
  });
});

describe("insideBox", () => {
  it("uses [ymin, xmin, ymax, xmax]", () => {
    expect(insideBox({ x1000: 520, y1000: 410 }, [390, 505, 435, 555], [412, 530])).toBe(true);
    expect(insideBox({ x1000: 410, y1000: 520 }, [390, 505, 435, 555], [412, 530])).toBe(false);
  });
  it("falls back to a radius around the point when no box", () => {
    expect(insideBox({ x1000: 530, y1000: 420 }, undefined, [412, 530])).toBe(true);
  });
});

describe("DirectionHold", () => {
  it("speaks the first cue immediately, then needs 400 ms before switching", () => {
    const h = new DirectionHold();
    expect(h.update("left", 0)).toBe("left");
    expect(h.update("down", 100)).toBeNull();
    expect(h.update("down", 450)).toBeNull();   // held only 350 ms
    expect(h.update("down", 501)).toBe("down");
    expect(h.update("down", 900)).toBeNull();   // unchanged ⇒ silent
  });
  it("stop is immediate", () => {
    const h = new DirectionHold();
    h.update("left", 0);
    expect(h.update("stop", 50)).toBe("stop");
  });
});

describe("DwellTimer", () => {
  it("fires once after 500 ms inside, tolerating ≤150 ms flicker", () => {
    const d = new DwellTimer();
    expect(d.update(true, 0)).toBe(false);
    expect(d.update(false, 100)).toBe(false);   // 100 ms flicker (≤150 ms grace)
    expect(d.update(true, 150)).toBe(false);
    expect(d.update(true, 520)).toBe(true);
    expect(d.update(true, 900)).toBe(false);    // only once
  });
  it("resets after leaving for more than 150 ms", () => {
    const d = new DwellTimer();
    d.update(true, 0);
    d.update(false, 100);
    d.update(false, 400);                        // gone 300 ms
    expect(d.update(true, 450)).toBe(false);
    expect(d.update(true, 900)).toBe(false);    // only 450 ms since re-entry
    expect(d.update(true, 951)).toBe(true);
  });
});
