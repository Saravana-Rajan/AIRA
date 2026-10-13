// frontend/app/src/camera/working.ts
// The "working" earcon. Production 2026-10-13: when Live asked the camera for a count it said nothing first and the
// owner waited 12-23 s in silence. Now the moment the phone receives a tool frame request (count, ask, identify,
// invoice, cash) it plays a soft, word-free tick, and repeats it every 2 s until AIRA answers (audio, an AIRA
// transcript, a confirm or an error) or 30 s pass. Pure: the sound and the timers are injected.
import type { FramePurpose, ServerMsg } from "@aira/contracts";

/** Tool frame requests that start the working earcon. */
export const WORKING_PURPOSES: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["count", "ask", "identify", "invoice", "cash"]);
export const WORKING_PERIOD_MS = 2000;
export const WORKING_MAX_MS = 30_000;

/** True for a one-shot tool frame request that should start the working earcon. */
export function startsWorking(m: ServerMsg): boolean {
  return m.t === "request_frame" && !m.stop && !m.continuous && WORKING_PURPOSES.has(m.purpose);
}

/** True for a server message that means AIRA is answering: the working earcon stops at once. */
export function stopsWorking(m: ServerMsg): boolean {
  return m.t === "audio" || (m.t === "transcript" && m.role === "aira") || m.t === "error" || m.t === "confirm";
}

export interface WorkingCueDeps {
  play: () => void;
  periodMs?: number;
  maxMs?: number;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (id: unknown) => void;
  now?: () => number;
}

export interface WorkingCue {
  onServerMsg(m: ServerMsg): void;
  start(): void;
  stop(): void;
  readonly active: boolean;
}

export function createWorkingCue(deps: WorkingCueDeps): WorkingCue {
  const period = deps.periodMs ?? WORKING_PERIOD_MS;
  const maxMs = deps.maxMs ?? WORKING_MAX_MS;
  const setI = deps.setInterval ?? ((fn: () => void, ms: number) => setInterval(fn, ms));
  const clearI = deps.clearInterval ?? ((id: unknown) => clearInterval(id as ReturnType<typeof setInterval>));
  const now = deps.now ?? (() => Date.now());
  let timer: unknown = null;
  let until = 0;

  const play = () => { try { deps.play(); } catch { /* audio trouble never breaks the camera */ } };
  const stop = () => {
    if (timer !== null) clearI(timer);
    timer = null;
  };
  const start = () => {
    until = now() + maxMs; // a second request while working only extends the 30 s
    if (timer !== null) return;
    play();
    timer = setI(() => {
      if (now() >= until) { stop(); return; }
      play();
    }, period);
  };

  return {
    onServerMsg(m) {
      if (stopsWorking(m)) stop();
      else if (startsWorking(m)) start();
    },
    start,
    stop,
    get active() { return timer !== null; },
  };
}
