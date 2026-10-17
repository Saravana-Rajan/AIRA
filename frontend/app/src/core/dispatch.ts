import type { CueName, ServerMsg } from "@aira/contracts";
import type { HapticName } from "./types";
import type { ConfirmState } from "./confirm";
import { disarmsThinking } from "./thinking";
import { startsWorking } from "../camera/working";
import { shouldClientSpeak, textFor, type SpeakCtx } from "./announce";

export interface DispatchDeps {
  playback: { enqueue(b64: string): void; flush(o?: { holdUntilResume?: boolean }): void; resume(): void };
  thinking: { disarm(): void };
  earcon(n: CueName): void;
  haptic(n: HapticName): void;
  /** `kind` is the server message type the line came from: an "error" line must not be lost behind Live audio. */
  speak(text: string, kind?: "confirm" | "error" | "state"): void;
  /** Cancels client speech (speechSynthesis) on barge-in. Optional. */
  stopSpeech?(): void;
  confirm: ConfirmState;
  ctx(): SpeakCtx;
  /** Receives every server message after it is handled, e.g. the screen reducer. Optional. */
  ui?(m: ServerMsg): void;
}

const CUE_HAPTIC: Partial<Record<CueName, HapticName>> = {
  ack: "ack", done: "done", warn: "warn", match: "match", mismatch: "mismatch", unsure: "warn",
};

/**
 * A server answer has started, so the 1.5 s thinking sound must stop (it never plays over AIRA).
 * Task 4's rule (audio, AIRA transcript, cue, error) plus the visible answers: a confirm prompt,
 * a state change, a live count and a pointing target. `ready` means the server is still working, and a
 * user transcript is the user still talking. A tool photo request (count, ask, ...) also ends it: the camera's
 * own "working" earcon (camera/working.ts) takes over, so the two sounds never play together.
 */
function isReply(m: ServerMsg): boolean {
  return disarmsThinking(m) || m.t === "confirm" || m.t === "state" || m.t === "count" || m.t === "target"
    || startsWorking(m);
}

/** Routes each server message to playback, earcons, haptics, the confirm gesture state and the announcer. */
export function createDispatcher(d: DispatchDeps): (m: ServerMsg) => void {
  return (m) => {
    if (isReply(m)) d.thinking.disarm();
    switch (m.t) {
      case "audio":
        d.playback.enqueue(m.pcm24); // dropped by the queue while held after an interruption
        break;
      case "transcript":
        if (m.role === "user") d.playback.resume(); // a new user turn: AIRA may speak again
        break;
      case "cue": {
        if (m.cue === "interrupted") {
          d.playback.flush({ holdUntilResume: true });
          d.stopSpeech?.();
        }
        d.earcon(m.cue);
        const h = CUE_HAPTIC[m.cue];
        if (h) d.haptic(h);
        break;
      }
      case "confirm":
        d.confirm.set(m.id, m.kind);
        d.earcon("warn");
        d.haptic("warn");
        break;
      case "error":
        d.earcon("warn");
        d.haptic("warn");
        break;
      default:
        break;
    }
    if (shouldClientSpeak(m, d.ctx())) {
      const t = textFor(m);
      if (t) d.speak(t, m.t as "confirm" | "error" | "state");
    }
    d.ui?.(m);
  };
}
