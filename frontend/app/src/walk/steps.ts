// frontend/app/src/walk/steps.ts
// Reports the phone's cumulative step count to the server during a walk ("walk with me") as
// `{ t: "event", name: "steps", data: { total, available } }`. One baseline event at start (even at 0 steps),
// then one more every STEP_REPORT_MS only when the total moved; a phone without a usable accelerometer sends a
// single `available: false` and nothing else. Pure: the sensor and `send` are injected; the interval timer is
// the only clock, so a changed total goes at most once per interval.

import type { ClientMsg } from "@aira/contracts";
import type { MotionSensor } from "./motion";

/** How often a changed step total goes to the server. */
export const STEP_REPORT_MS = 500;

export interface StepReporterOptions {
  send: (m: ClientMsg) => void;
  intervalMs?: number;
}

export interface StepReporter {
  /** Starts (or restarts, with the new sensor) reporting. Safe to call twice. */
  start(motion: MotionSensor | null | undefined): void;
  stop(): void;
  /** Reporting a live sensor right now (a timer is running). */
  active(): boolean;
}

export function createStepReporter({ send, intervalMs = STEP_REPORT_MS }: StepReporterOptions): StepReporter {
  let timer: ReturnType<typeof setInterval> | null = null;
  let lastSent = 0;

  const emit = (total: number, available: boolean) => {
    lastSent = total;
    try { send({ t: "event", name: "steps", data: { total, available } }); } catch { /* a closed socket: nothing to do */ }
  };

  const stop = () => {
    if (timer) clearInterval(timer);
    timer = null;
  };

  return {
    start(motion) {
      stop();
      const read = motion?.totalSteps;
      if (!motion || motion.available === false || typeof read !== "function") {
        emit(0, false);
        return;
      }
      const total = () => { try { return read.call(motion); } catch { return lastSent; } };
      emit(total(), true);
      timer = setInterval(() => {
        const t = total();
        if (t !== lastSent) emit(t, true);
      }, intervalMs);
    },
    stop,
    active: () => timer !== null,
  };
}
