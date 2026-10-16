// frontend/app/src/camera/torch.ts
// The rear-camera flashlight (AIRA_TORCH). Pure apart from the injected track: nothing here ever turns the
// torch on by itself. Only a server `torch` command does, and the server sends one only after the owner's
// spoken "yes" (backend/aira_live/tools/light.py). The torch goes on only when the camera track reports the
// capability (`getCapabilities().torch === true`) and `applyConstraints({advanced: [{torch: true}]})` succeeds;
// otherwise the phone reports "unsupported" and never pretends. iPhone Safari (WebKit) does not expose the torch
// capability, so there the report is always "unsupported". The torch switches itself off after TORCH_AUTO_OFF_MS
// (phone-local, so it never stays on because the server went away) and whenever the camera wiring is disposed.
import type { ServerMsg, TorchState } from "@aira/contracts";

/** The torch turns itself off this long after it went on (the server may ask earlier). */
export const TORCH_AUTO_OFF_MS = 5 * 60_000;

/** The parts of a MediaStreamTrack the torch uses (a fake in tests). */
export interface TorchTrack {
  /** `MediaTrackCapabilities` (lib.dom has no `torch` on it; the controller reads it by name). */
  getCapabilities?(): object;
  applyConstraints(constraints: MediaTrackConstraints): Promise<void>;
}

/** True only when the track's capabilities say `torch: true` (any other shape is "no torch"). */
export function torchSupported(track: TorchTrack | null): boolean {
  try {
    const caps = track?.getCapabilities?.() as { torch?: unknown } | undefined;
    return caps?.torch === true;
  } catch {
    return false;
  }
}

export interface TorchDeps {
  /** The open rear camera's video track, or null while the camera is off. */
  getTrack: () => TorchTrack | null;
  /** The real state after every change or refusal: the wiring reports it to the server and the status chip. */
  onState: (state: TorchState, reason: "command" | "auto_off" | "dispose") => void;
  autoOffMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  log?: (tag: string, data: Record<string, unknown>) => void;
}

export interface TorchController {
  /** Handles the server's `torch` command; every other message is ignored. */
  onServerMsg(m: ServerMsg): void;
  state(): TorchState;
  /** Switches off (no-op when not on). */
  off(reason?: "command" | "auto_off" | "dispose"): Promise<void>;
  dispose(): void;
}

export function createTorchController(deps: TorchDeps): TorchController {
  const autoOffMs = deps.autoOffMs ?? TORCH_AUTO_OFF_MS;
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let state: TorchState = "off";
  let timer: unknown = null;
  let busy: Promise<void> = Promise.resolve();

  const report = (next: TorchState, reason: "command" | "auto_off" | "dispose") => {
    state = next;
    deps.log?.("torch", { state: next, reason });
    deps.onState(next, reason);
  };

  const clearAutoOff = () => {
    if (timer !== null) { clearTimer(timer); timer = null; }
  };

  async function turnOn(): Promise<void> {
    if (state === "on") { report("on", "command"); return; } // the server asked again: say so, keep the timer
    const track = deps.getTrack();
    if (!track || !torchSupported(track)) { report("unsupported", "command"); return; }
    try {
      await track.applyConstraints({ advanced: [{ torch: true } as MediaTrackConstraintSet] });
    } catch {
      report("unsupported", "command"); // the capability was claimed but the switch failed: never pretend
      return;
    }
    clearAutoOff();
    timer = setTimer(() => { timer = null; void off("auto_off"); }, autoOffMs);
    report("on", "command");
  }

  async function off(reason: "command" | "auto_off" | "dispose" = "command"): Promise<void> {
    clearAutoOff();
    if (state !== "on") {
      if (reason === "command") report(state, reason); // the server asked for off: answer with the real state
      return;
    }
    try {
      await deps.getTrack()?.applyConstraints({ advanced: [{ torch: false } as MediaTrackConstraintSet] });
    } catch { /* the track is gone or refused: it is off either way */ }
    report("off", reason);
  }

  return {
    onServerMsg(m) {
      if (m.t !== "torch") return;
      busy = busy.then(() => (m.on ? turnOn() : off("command"))).catch(() => {});
    },
    state: () => state,
    off,
    dispose() {
      clearAutoOff();
      if (state === "on") void off("dispose");
    },
  };
}
