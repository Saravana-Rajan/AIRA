// frontend/app/test/walk.steps.test.ts — step reporter over a fake motion sensor and fake timers
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import { createStepReporter, STEP_REPORT_MS } from "../src/walk/steps";
import type { MotionSensor } from "../src/walk/motion";

function fakeMotion(available = true): MotionSensor & { steps: number } {
  const m = {
    steps: 0,
    available,
    isWalking: () => false,
    totalSteps(): number { return m.steps; },
    stop() {},
  };
  return m;
}

const stepsEvent = (total: number, available: boolean): ClientMsg =>
  ({ t: "event", name: "steps", data: { total, available } });

let sent: ClientMsg[];
let send: (m: ClientMsg) => void;

beforeEach(() => {
  vi.useFakeTimers();
  sent = [];
  send = (m) => { sent.push(m); };
});
afterEach(() => { vi.useRealTimers(); });

describe("step reporter", () => {
  it("reports every 500 ms", () => {
    expect(STEP_REPORT_MS).toBe(500);
  });

  it("no sensor, an unavailable one, or one without totalSteps: one available:false event and nothing after", () => {
    for (const motion of [null, undefined, fakeMotion(false), { available: true, isWalking: () => false, stop() {} }]) {
      sent = [];
      const r = createStepReporter({ send });
      r.start(motion);
      expect(sent).toEqual([stepsEvent(0, false)]);
      expect(r.active()).toBe(false);
      vi.advanceTimersByTime(STEP_REPORT_MS * 4);
      expect(sent).toHaveLength(1);
      r.stop();
    }
  });

  it("a live sensor: a baseline at once (even 0), then only when the total changed, once per interval", () => {
    const m = fakeMotion();
    const r = createStepReporter({ send });
    r.start(m);
    expect(sent).toEqual([stepsEvent(0, true)]);
    expect(r.active()).toBe(true);
    vi.advanceTimersByTime(STEP_REPORT_MS * 3);
    expect(sent).toHaveLength(1);                     // standing still: silence
    m.steps = 3;
    m.steps = 4;                                      // two steps inside one interval: one report
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent).toEqual([stepsEvent(0, true), stepsEvent(4, true)]);
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent).toHaveLength(2);
    m.steps = 5;
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent.at(-1)).toEqual(stepsEvent(5, true));
    r.stop();
  });

  it("a custom interval throttles the reports", () => {
    const m = fakeMotion();
    const r = createStepReporter({ send, intervalMs: 2000 });
    r.start(m);
    m.steps = 1;
    vi.advanceTimersByTime(1999);
    expect(sent).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sent).toHaveLength(2);
    r.stop();
  });

  it("stop ends the reports; start again restarts with the new sensor and a fresh baseline", () => {
    const a = fakeMotion();
    const r = createStepReporter({ send });
    r.start(a);
    r.stop();
    expect(r.active()).toBe(false);
    a.steps = 7;
    vi.advanceTimersByTime(STEP_REPORT_MS * 2);
    expect(sent).toEqual([stepsEvent(0, true)]);
    const b = fakeMotion();
    b.steps = 2;
    r.start(b);
    expect(sent.at(-1)).toEqual(stepsEvent(2, true));
    a.steps = 20;                                     // the old sensor no longer counts
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent).toHaveLength(2);
    r.stop();
  });

  it("start twice is idempotent: one timer, no duplicate reports", () => {
    const m = fakeMotion();
    const r = createStepReporter({ send });
    r.start(m);
    r.start(m);
    expect(sent).toEqual([stepsEvent(0, true), stepsEvent(0, true)]);
    m.steps = 1;
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent).toHaveLength(3);                     // not 4: the first timer was cleared
    r.stop();
  });

  it("a send that throws is swallowed and reporting goes on", () => {
    const m = fakeMotion();
    let fail = true;
    const r = createStepReporter({ send: (msg) => { if (fail) throw new Error("closed"); sent.push(msg); } });
    expect(() => r.start(m)).not.toThrow();
    fail = false;
    m.steps = 2;
    vi.advanceTimersByTime(STEP_REPORT_MS);
    expect(sent).toEqual([stepsEvent(2, true)]);
    r.stop();
  });
});
