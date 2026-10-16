import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  angleFromTilt,
  createOrientationJudge,
  startOrientation,
  ROLL_POS_ANGLE,
  ROLL_NEG_ANGLE,
  ORIENT_HOLD_MS,
} from "../src/camera/orientation";
import { __resetMotionAccess } from "../src/core/motionPermission";

/** A fake event target that records deviceorientation listeners and can fire (beta, gamma) at them. */
function fakeWin() {
  const listeners: Record<string, Array<(e: unknown) => void>> = {};
  return {
    win: {
      addEventListener: (t: string, fn: (e: unknown) => void) => { (listeners[t] ??= []).push(fn); },
      removeEventListener: (t: string, fn: (e: unknown) => void) => {
        listeners[t] = (listeners[t] ?? []).filter((f) => f !== fn);
      },
    },
    fire: (beta: number | null, gamma: number | null) =>
      (listeners.deviceorientation ?? []).slice().forEach((f) => f({ beta, gamma })),
    count: () => (listeners.deviceorientation ?? []).length,
  };
}

/** Flush a few microtask turns so chained permission promises settle. */
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

beforeEach(() => { __resetMotionAccess(); });

describe("camera/orientation angleFromTilt", () => {
  it("maps the physical tilt to a screen orientation", () => {
    expect(angleFromTilt(90, 0)).toBe(0); // upright portrait (also the chest-mount pose)
    expect(angleFromTilt(0, 85)).toBe(ROLL_POS_ANGLE); // rolled onto one side -> landscape
    expect(angleFromTilt(0, -85)).toBe(ROLL_NEG_ANGLE); // rolled onto the other side
    expect(angleFromTilt(-90, 0)).toBe(180); // held upside down
  });

  it("the axis tilted furthest decides, at the boundary too", () => {
    expect(angleFromTilt(40, 55)).toBe(ROLL_POS_ANGLE); // roll beats pitch -> landscape
    expect(angleFromTilt(55, 40)).toBe(0); // pitch beats roll -> portrait
  });

  it("keeps the last angle when the phone is lying flat (both axes level)", () => {
    expect(angleFromTilt(5, 5, 90)).toBe(90);
    expect(angleFromTilt(-8, 8, 270)).toBe(270);
    expect(angleFromTilt(5, 5)).toBe(0); // default last
  });

  it("keeps the last angle for a missing or non-finite reading", () => {
    expect(angleFromTilt(null, 0, 90)).toBe(90);
    expect(angleFromTilt(0, null, 270)).toBe(270);
    expect(angleFromTilt(Number.NaN, Number.NaN, 180)).toBe(180);
    expect(angleFromTilt(undefined, undefined)).toBe(0);
  });
});

describe("camera/orientation createOrientationJudge", () => {
  it("starts at 0 and never reports while the phone stays portrait", () => {
    let clock = 0;
    const j = createOrientationJudge({ now: () => clock, holdMs: ORIENT_HOLD_MS });
    expect(j.current).toBe(0);
    expect(j.sample(90, 0)).toBeNull();
    clock += 1000;
    expect(j.sample(88, 2)).toBeNull();
    expect(j.current).toBe(0);
  });

  it("reports a change only after it has held for holdMs", () => {
    let clock = 0;
    const j = createOrientationJudge({ now: () => clock, holdMs: 400 });
    expect(j.sample(0, 85)).toBeNull(); // landscape seen, timer starts
    clock += 399;
    expect(j.sample(0, 85)).toBeNull(); // not held long enough
    clock += 1;
    expect(j.sample(0, 85)).toBe(90); // held 400 ms: committed
    expect(j.current).toBe(90);
    expect(j.sample(0, 85)).toBeNull(); // same angle: nothing more
  });

  it("does not report a change that reverts before the hold elapses (jitter while turning)", () => {
    let clock = 0;
    const j = createOrientationJudge({ now: () => clock, holdMs: 400 });
    expect(j.sample(0, 85)).toBeNull(); // flicks toward landscape
    clock += 100;
    expect(j.sample(90, 0)).toBeNull(); // back to portrait before the hold
    clock += 100;
    expect(j.sample(90, 0)).toBeNull();
    clock += 1000;
    expect(j.sample(90, 0)).toBeNull(); // settled portrait: never reported a change
    expect(j.current).toBe(0);
  });

  it("restarts the hold timer when the candidate changes", () => {
    let clock = 0;
    const j = createOrientationJudge({ now: () => clock, holdMs: 400 });
    expect(j.sample(0, 85)).toBeNull(); // candidate 90 at t=0
    clock += 300;
    expect(j.sample(-90, 0)).toBeNull(); // candidate flips to 180 at t=300, timer restarts
    clock += 300; // 600 total, but only 300 on candidate 180
    expect(j.sample(-90, 0)).toBeNull();
    clock += 100; // 400 on candidate 180
    expect(j.sample(-90, 0)).toBe(180);
  });
});

describe("camera/orientation startOrientation", () => {
  it("sends the new angle after the hold, both ways, and stop() unbinds (no permission needed)", () => {
    let clock = 0;
    const f = fakeWin();
    const send = vi.fn();
    const judge = createOrientationJudge({ now: () => clock, holdMs: 400 });
    const stop = startOrientation({ send, win: f.win, g: {} as unknown as typeof globalThis, judge });
    expect(f.count()).toBe(1);

    f.fire(90, 0); // portrait: the default, nothing sent
    expect(send).toHaveBeenCalledTimes(0);

    f.fire(0, 85); // landscape begins, timer starts
    clock += 400;
    f.fire(0, 85); // held: one send
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenLastCalledWith({ t: "orientation", angle: 90 });

    f.fire(0, 85); // same: nothing more
    expect(send).toHaveBeenCalledTimes(1);

    f.fire(90, 0); // back toward portrait, timer restarts
    clock += 400;
    f.fire(90, 0); // held: portrait is a real change from landscape, sent
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith({ t: "orientation", angle: 0 });

    stop();
    expect(f.count()).toBe(0);
    f.fire(0, 85);
    clock += 1000;
    f.fire(0, 85);
    expect(send).toHaveBeenCalledTimes(2); // nothing after stop()
  });

  it("attaches only after the iOS motion permission is granted", async () => {
    const f = fakeWin();
    const send = vi.fn();
    const g = { DeviceOrientationEvent: { requestPermission: () => Promise.resolve("granted") } } as unknown as typeof globalThis;
    startOrientation({ send, win: f.win, g });
    expect(f.count()).toBe(0); // waits for the prompt
    await flush();
    expect(f.count()).toBe(1);
  });

  it("never attaches when the iOS motion permission is refused", async () => {
    const f = fakeWin();
    const send = vi.fn();
    const g = { DeviceOrientationEvent: { requestPermission: () => Promise.resolve("denied") } } as unknown as typeof globalThis;
    const stop = startOrientation({ send, win: f.win, g });
    await flush();
    expect(f.count()).toBe(0);
    stop(); // safe even though nothing attached
  });
});
