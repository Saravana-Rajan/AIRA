// frontend/app/src/camera/lightWatch.ts
// Samples the camera's raw mean luma a few times a second (the same cheap 32x24 probe the live video uses),
// runs the sustained-dark detector and sends `light` messages to the server: on every level change, on every
// torch state change, on a clear luma change (rate-limited, so the server can compare brightness before and
// after a light switch is pressed) and as a slow heartbeat while the torch is on. Pure: every browser touch
// comes through deps. The luma here is RAW: it is read before any low-light boost.
import type { ClientMsg, LightLevel, TorchState } from "@aira/contracts";
import { createDarkDetector, type DarkDetector } from "./lowlight";
import { meanLuma } from "./quality";

/** How often the probe is sampled. */
export const LIGHT_PROBE_MS = 500;
/** A luma change of at least this much (0-255) is reported even without a level change ... */
export const LUMA_REPORT_DELTA = 20;
/** ... at most once per this many ms. */
export const LUMA_REPORT_GAP_MS = 1000;
/** While the torch is on, a report goes at least this often (the server's turn-off check needs fresh readings). */
export const TORCH_HEARTBEAT_MS = 10_000;

export interface LightWatchDeps {
  /** The camera's gray grid right now, or null when there is no picture (camera off, a tool photo busy). */
  probe: () => Float32Array | null;
  send: (m: ClientMsg) => boolean;
  torch: () => TorchState;
  now: () => number;
  /** Every sample: the raw luma and the level in force (the wiring sets the upload boost from it). */
  onSample?: (luma: number, level: LightLevel) => void;
  detector?: DarkDetector;
  setInterval?: (fn: () => void, ms: number) => unknown;
  clearInterval?: (handle: unknown) => void;
  probeMs?: number;
}

export interface LightWatch {
  /** One sample (the timer calls it; tests call it by hand). */
  tick(): void;
  /** The torch state changed: report at once with the current level and luma. */
  noteTorch(state: TorchState): void;
  lastLuma(): number | null;
  level(): LightLevel;
  stop(): void;
}

const round1 = (v: number) => Math.round(v * 10) / 10;

export function startLightWatch(deps: LightWatchDeps): LightWatch {
  const detector = deps.detector ?? createDarkDetector();
  const setI = deps.setInterval ?? ((fn, ms) => setInterval(fn, ms));
  const clearI = deps.clearInterval ?? ((h) => clearInterval(h as ReturnType<typeof setInterval>));
  let luma: number | null = null;
  let sentLuma: number | null = null;
  let sentAt = Number.NEGATIVE_INFINITY;
  let stopped = false;

  const send = (now: number) => {
    if (luma === null) return;
    const value = Math.min(255, Math.max(0, round1(luma)));
    if (deps.send({ t: "light", level: detector.level(), luma: value, torch: deps.torch() })) {
      sentLuma = luma;
      sentAt = now;
    }
  };

  const tick = () => {
    if (stopped) return;
    const grid = deps.probe();
    if (!grid || grid.length === 0) return;
    const now = deps.now();
    luma = meanLuma(grid);
    const changed = detector.feed(luma, now);
    try { deps.onSample?.(luma, detector.level()); } catch { /* the boost is best effort */ }
    const jump = sentLuma === null || Math.abs(luma - sentLuma) >= LUMA_REPORT_DELTA;
    const gapOk = now - sentAt >= LUMA_REPORT_GAP_MS;
    const heartbeat = deps.torch() === "on" && now - sentAt >= TORCH_HEARTBEAT_MS;
    if (changed !== null || (jump && gapOk) || heartbeat) send(now);
  };

  const handle = setI(tick, deps.probeMs ?? LIGHT_PROBE_MS);
  return {
    tick,
    noteTorch() { if (!stopped) send(deps.now()); },
    lastLuma: () => luma,
    level: () => detector.level(),
    stop() {
      if (stopped) return;
      stopped = true;
      clearI(handle);
    },
  };
}
