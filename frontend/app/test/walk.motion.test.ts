// frontend/app/test/walk.motion.test.ts — step counter over injected accelerometer samples (no DOM sensor)
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  createStepCounter, startMotionSensor, WALK_WINDOW_MS, WALK_MIN_STEPS, STEP_MIN_INTERVAL_MS,
} from "../src/walk/motion";

const G = 9.81;

/** Feed `n` strides of `periodMs` starting at t0: a 50 Hz sinusoid of ±amp around gravity. */
function stride(c: ReturnType<typeof createStepCounter>, t0: number, n: number, periodMs: number, amp = 3) {
  const dt = 20;
  for (let t = 0; t <= n * periodMs; t += dt) {
    const mag = G + amp * Math.sin((2 * Math.PI * t) / periodMs);
    c.push({ t: t0 + t, x: 0, y: mag, z: 0 });
  }
}

/** Standing still: gravity plus a little noise. */
function still(c: ReturnType<typeof createStepCounter>, t0: number, ms: number) {
  for (let t = 0; t <= ms; t += 20) c.push({ t: t0 + t, x: 0.1 * Math.sin(t), y: G + 0.2 * Math.cos(t / 3), z: 0 });
}

describe("step counter", () => {
  it("constants: 2 steps in 2.5 s, cadence capped at 2.5 Hz", () => {
    expect(WALK_WINDOW_MS).toBe(2500);
    expect(WALK_MIN_STEPS).toBe(2);
    expect(STEP_MIN_INTERVAL_MS).toBe(400);
  });

  it("standing still (gravity + noise) counts no steps and is not walking", () => {
    const c = createStepCounter();
    still(c, 0, 5000);
    expect(c.steps(5000)).toBe(0);
    expect(c.isWalking(5000)).toBe(false);
  });

  it("a 1.8 Hz stride counts about one step per period and is walking", () => {
    const c = createStepCounter();
    stride(c, 0, 6, 550);
    expect(c.steps(3300)).toBeGreaterThanOrEqual(4);   // only the last 2.5 s count
    expect(c.steps(3300)).toBeLessThanOrEqual(5);
    expect(c.isWalking(3300)).toBe(true);
  });

  it("a slow 1 Hz stride still walks; a single bump (one peak) does not", () => {
    const c = createStepCounter();
    stride(c, 0, 4, 1000);
    expect(c.isWalking(4000)).toBe(true);

    const d = createStepCounter();
    still(d, 0, 1000);
    d.push({ t: 1010, x: 0, y: G + 5, z: 0 });        // phone knocked once
    d.push({ t: 1030, x: 0, y: G, z: 0 });
    still(d, 1040, 1000);
    expect(d.steps(2040)).toBe(1);
    expect(d.isWalking(2040)).toBe(false);
  });

  it("stops being walking 2.5 s after the last step", () => {
    const c = createStepCounter();
    stride(c, 0, 4, 500);
    expect(c.isWalking(2000)).toBe(true);
    still(c, 2020, 3000);
    expect(c.isWalking(4600)).toBe(false);
  });

  it("peaks faster than 2.5 Hz (vibration, shaking) are not separate steps", () => {
    const c = createStepCounter();
    stride(c, 0, 20, 100);                              // 10 Hz buzz
    expect(c.steps(2000)).toBeLessThanOrEqual(5);        // at most one per 400 ms
  });

  it("total() counts every step across the window; reset zeroes it", () => {
    const c = createStepCounter();
    expect(c.total()).toBe(0);
    stride(c, 0, 10, 500);                              // 5 s of walking: the window holds only the last 2.5 s
    expect(c.steps(5000)).toBeLessThanOrEqual(6);
    expect(c.total()).toBeGreaterThanOrEqual(9);
    expect(c.total()).toBeLessThanOrEqual(11);
    const before = c.total();
    still(c, 5020, 3000);                               // standing still: the window empties, the total holds
    expect(c.steps(8020)).toBe(0);
    expect(c.total()).toBe(before);
    c.reset();
    expect(c.total()).toBe(0);
  });
});

describe("startMotionSensor", () => {
  afterEach(() => { vi.unstubAllGlobals(); });

  it("no DeviceMotionEvent ⇒ available=false and isWalking stays false", async () => {
    vi.stubGlobal("DeviceMotionEvent", undefined);
    const s = await startMotionSensor();
    expect(s.available).toBe(false);
    expect(s.isWalking(0)).toBe(false);
    expect(s.totalSteps?.()).toBe(0);
    s.stop();
  });

  it("iOS permission denied ⇒ available=false; granted ⇒ listens to devicemotion", async () => {
    const add = vi.fn();
    const remove = vi.fn();
    vi.stubGlobal("addEventListener", add);
    vi.stubGlobal("removeEventListener", remove);
    vi.stubGlobal("DeviceMotionEvent", { requestPermission: vi.fn(async () => "denied") });
    const denied = await startMotionSensor();
    expect(denied.available).toBe(false);
    expect(add).not.toHaveBeenCalled();

    vi.stubGlobal("DeviceMotionEvent", { requestPermission: vi.fn(async () => "granted") });
    const ok = await startMotionSensor();
    expect(ok.available).toBe(true);
    expect(add).toHaveBeenCalledWith("devicemotion", expect.any(Function), expect.anything());
    const handler = add.mock.calls[0][1] as (e: unknown) => void;
    let t = 0;
    for (let i = 0; i <= 4 * 500; i += 20) {
      t = i;
      handler({ timeStamp: t, accelerationIncludingGravity: { x: 0, y: G + 3 * Math.sin((2 * Math.PI * t) / 500), z: 0 } });
    }
    expect(ok.isWalking(t)).toBe(true);
    expect(ok.totalSteps?.()).toBeGreaterThanOrEqual(3);   // the cumulative count the walk reporter sends
    ok.stop();
    expect(remove).toHaveBeenCalledWith("devicemotion", handler, expect.anything());
  });

  it("a requestPermission that throws (not a user gesture) ⇒ available=false, no throw", async () => {
    vi.stubGlobal("DeviceMotionEvent", { requestPermission: vi.fn(async () => { throw new Error("gesture"); }) });
    const s = await startMotionSensor();
    expect(s.available).toBe(false);
    s.stop();
  });
});
