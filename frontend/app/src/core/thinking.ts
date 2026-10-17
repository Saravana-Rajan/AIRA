import type { ServerMsg } from "@aira/contracts";

/**
 * Plays a soft "thinking" sound only when the server has been silent for
 * `delayMs` (1.5 s) after a trigger. `arm()` on each trigger; `disarm()` as
 * soon as anything audible comes back. `onStop` fires only if `onStart` did.
 */
export class ThinkingTimer {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private on = false;

  constructor(
    private onStart: () => void,
    private onStop: () => void,
    private delayMs = 1500,
  ) {}

  arm(): void {
    this.disarm();
    this.timer = setTimeout(() => {
      this.timer = null;
      this.on = true;
      this.onStart();
    }, this.delayMs);
  }

  disarm(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.on) {
      this.on = false;
      this.onStop();
    }
  }

  get active(): boolean {
    return this.on;
  }
}

/**
 * True for server messages that mean AIRA is answering, so the thinking sound
 * must never play over them: `audio`, an AIRA `transcript`, any `cue`, any `error`.
 */
export function disarmsThinking(msg: ServerMsg): boolean {
  switch (msg.t) {
    case "audio":
    case "cue":
    case "error":
      return true;
    case "transcript":
      return msg.role === "aira";
    default:
      return false;
  }
}
