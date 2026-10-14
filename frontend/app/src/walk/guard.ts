// frontend/app/src/walk/guard.ts
// Bump-guard detector loop (on the phone, no network). It warns only while the accelerometer says the owner
// is walking (walk/motion.ts); with no sensor only approaching objects count, and walking mode can switch it off.
// grab frame → detect (worker) → track → timeToContact → observe (2 frames) → shouldWarn → vibrate, earcon, phrase.
// Every side effect is injected, so the loop and the decisions are testable with fakes.
import type { Lang, ServerMsg } from "@aira/contracts";
import {
  initialBumpState, observe, raiseUrgency as raise, recordWarn, retainStreaks, shouldWarn, timeToContact, trigger,
  WARN_VIBRATE, type BumpDetection, type BumpState, type BumpWarning,
} from "./bump";
import type { BoxReport } from "./boxes";
import type { DetectFrame, DetectResult } from "./detector";
import { createFloorCheck, FLOOR_BOX, type FloorCheckOptions } from "./floor";
import { bumpText } from "./strings";
import { createTracker, type TrackerOptions } from "./tracker";

/** ~8 Hz (the addendum asks for 5–10 Hz). */
export const BUMP_INTERVAL_MS = 125;
/** Wait after a detector error before trying again. */
export const ERROR_BACKOFF_MS = 500;
/** A detection slower than this is treated as an error, so a hung worker cannot stop the guard. */
export const DETECT_TIMEOUT_MS = 1000;
/** How long a server `warn` cue keeps the guard in its more sensitive mode. */
export const SERVER_URGENCY_MS = 3000;
/** Latency samples kept for the benchmark slide. */
const LATENCY_KEEP = 500;

/** What the guard needs from the accelerometer (walk/motion.ts MotionSensor satisfies it). */
export interface MotionSource {
  available: boolean;
  isWalking(at: number): boolean;
}

export interface BumpGuardDeps {
  /** Latest camera frame and its timestamp (same clock as `now`), or null when the camera is not ready. */
  grab: () => Promise<{ frame: DetectFrame; ts: number } | null>;
  detector: { detect(frame: DetectFrame, ts: number): Promise<DetectResult> };
  vibrate: (pattern: number[]) => void;
  /** Sharp warn earcon. */
  earcon: () => void;
  speak: (text: string) => void;
  /**
   * The urgent speech path (core/wire speakUrgent): said at once over Live audio. When given, warnings use it
   * instead of `speak`, with the phrase key and the frame → alert age for the guard_alert diagnostic.
   */
  speakUrgent?: (text: string, info: { kind: string; ageMs: number }) => void;
  lang: () => Lang;
  /** Monotonic ms clock, e.g. performance.now(). */
  now: () => number;
  intervalMs?: number;
  /** Frame timestamp → vibrate call, for the bench log. */
  onLatency?: (ms: number, w: BumpWarning) => void;
  tracker?: TrackerOptions;
  floor?: FloorCheckOptions | false;
  /** Accelerometer. Absent or `available: false` ⇒ approach-only warnings (size warnings stay off). */
  motion?: MotionSource;
  /** Initial walking-mode switch (default on). Later changes come through setWalkingMode(). */
  walkingMode?: boolean;
  /**
   * Every frame's detections (label, box 0-1000, score) with `danger` set when the guard flags the object in
   * the walking path (before cooldowns): the monitor's boxes and the "Show boxes" overlay. Errors are ignored.
   */
  onDetections?: (reports: BoxReport[], ts: number) => void;
  /**
   * Per-frame detector diagnostic: objects the model returned before the MIN_SCORE filter (`raw`), the top score
   * seen (`top`), how many survived the filter (`n`), and a detect error (`err`). The integrator throttles and
   * logs it so an empty overlay has a visible cause (model error vs nothing-in-view vs scores-too-low). Errors
   * here are ignored.
   */
  onDiag?: (d: { raw: number; top: number; n: number; err?: string }) => void;
}

export interface BumpGuard {
  start(): void;
  stop(): void;
  isRunning(): boolean;
  /** Hand guidance has the hand near its target ⇒ mute (set by the guidance session's onNear). */
  setHandNearTarget(near: boolean): void;
  /** Walking mode off ⇒ no warnings at all (the owner stands at the counter all day). */
  setWalkingMode(on: boolean): void;
  isWalkingMode(): boolean;
  /** More sensitive for `forMs`. Can only extend, never shorten or silence. */
  raiseUrgency(forMs?: number): void;
  /** Server messages may only RAISE urgency (`cue: "warn"`); nothing from the server mutes the guard. */
  onServerMsg(m: ServerMsg): void;
  /**
   * A server walk ("walk with me", request_frame purpose "walk" continuous) is running: the guard stays in its
   * urgent sensitivity for the whole walk and counts the owner as walking even when the step counter (or a
   * missing sensor) says otherwise, and even if walking mode was switched off. Off ⇒ back to normal.
   */
  setServerWalk(on: boolean): void;
  isServerWalk(): boolean;
  /** One detector result → at most one alert. Exposed for tests and the bench. */
  process(r: DetectResult): BumpWarning | null;
  /** Recent frame → vibrate latencies in ms (bench). */
  latencies(): number[];
}

type Candidate = { det: BumpDetection; warn: BumpWarning };

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let id: ReturnType<typeof setTimeout> | undefined;
  const t = new Promise<never>((_, reject) => { id = setTimeout(() => reject(new Error("bump detector: timeout")), ms); });
  return Promise.race([p, t]).finally(() => clearTimeout(id));
}

/** "Stop" (straight ahead) beats a side warning; then the soonest contact. */
function pick(cands: Candidate[]): Candidate {
  return cands.reduce((best, c) => {
    const bs = best.warn.phrase === "bump.stop" ? 0 : 1;
    const cs = c.warn.phrase === "bump.stop" ? 0 : 1;
    if (cs !== bs) return cs < bs ? c : best;
    return c.det.ttcS < best.det.ttcS ? c : best;
  });
}

export function createBumpGuard(deps: BumpGuardDeps): BumpGuard {
  const interval = deps.intervalMs ?? BUMP_INTERVAL_MS;
  const tracker = createTracker(deps.tracker);
  const floor = deps.floor === false ? null : createFloorCheck(deps.floor);
  let state: BumpState = { ...initialBumpState(), walkingMode: deps.walkingMode ?? true };
  let ownerMode = deps.walkingMode ?? true;   // the owner's switch; a server walk overrides it while it lasts
  let serverWalk = false;
  let running = false;
  let busy = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const lat: number[] = [];

  function alert(c: Candidate) {
    // Order matters for the 150 ms budget: the vibration goes first, synchronously.
    deps.vibrate([...WARN_VIBRATE]);
    const ms = Math.max(0, deps.now() - c.det.ts);
    lat.push(ms);
    if (lat.length > LATENCY_KEEP) lat.shift();
    deps.onLatency?.(ms, c.warn);
    // The earcon is the instant cue where there is no vibration (iPhone Safari); it plays everywhere.
    try { deps.earcon(); } catch { /* audio problems must not block the phrase */ }
    const text = bumpText(c.warn.phrase, c.warn.side, deps.lang());
    if (deps.speakUrgent) deps.speakUrgent(text, { kind: c.warn.phrase, ageMs: ms });
    else deps.speak(text);
  }

  function process(r: DetectResult): BumpWarning | null {
    const motion = deps.motion;
    const hasSensor = motion?.available === true;
    state = serverWalk
      // A server walk: he is walking (perhaps shuffling slowly), whatever the step counter says.
      ? raise({ ...state, hasSensor: true, walking: true, walkingMode: true }, r.ts + SERVER_URGENCY_MS)
      : { ...state, hasSensor, walking: hasSensor ? motion!.isWalking(r.ts) : false, walkingMode: ownerMode };
    const cands: Candidate[] = [];
    const seen = new Set<string>();
    const reports: BoxReport[] = [];
    const scores = new Map<unknown, number>(r.dets.map((d) => [d.box, d.score]));
    const consider = (det: BumpDetection) => {
      seen.add(det.key);
      state = observe(state, det);
      const warn = shouldWarn(state, det);
      if (warn) cands.push({ det, warn });
    };
    for (const t of tracker.update(r.dets, r.ts)) {
      const det = { key: t.key, box: t.box, ttcS: timeToContact(t.boxes, t.ts), ts: r.ts };
      consider(det);
      reports.push({ label: t.label, box: t.box, score: scores.get(t.box), danger: trigger(state, det) !== null });
    }
    if (deps.onDetections) {
      try { deps.onDetections(reports, r.ts); } catch { /* the monitor never blocks the guard */ }
    }
    if (deps.onDiag) {
      try { deps.onDiag({ raw: r.raw ?? 0, top: r.top ?? 0, n: r.dets.length, err: r.err }); }
      catch { /* diagnostics never block the guard */ }
    }
    if (floor && r.floor !== null && floor.update(r.floor)) {
      consider({ key: "floor", box: FLOOR_BOX, ttcS: Infinity, ts: r.ts });
    }
    state = retainStreaks(state, seen);   // an object missing for a frame starts over
    if (!cands.length) return null;
    const chosen = pick(cands);
    alert(chosen);
    // One alert covers every obstacle that warned in this frame: start all their cooldowns.
    for (const c of cands) state = recordWarn(state, c.det);
    return chosen.warn;
  }

  function schedule(ms: number) {
    if (!running) return;
    timer = setTimeout(() => { timer = null; void tick(); }, Math.max(0, ms));
  }

  async function tick() {
    if (!running || busy) return;
    busy = true;
    const started = deps.now();
    let wait = interval;
    try {
      const g = await deps.grab();
      if (g && running) {
        const r = await withTimeout(deps.detector.detect(g.frame, g.ts), DETECT_TIMEOUT_MS);
        if (running) process(r);
      }
      wait = interval - (deps.now() - started);
    } catch {
      wait = ERROR_BACKOFF_MS;   // camera or detector hiccup: keep guarding, a bit slower
    } finally {
      busy = false;
    }
    schedule(wait);
  }

  return {
    start() {
      if (running) return;
      running = true;
      void tick();
    },
    stop() {
      running = false;
      if (timer) clearTimeout(timer);
      timer = null;
      tracker.reset();
    },
    isRunning: () => running,
    setHandNearTarget(near) { state = { ...state, handNearTarget: near }; },
    setWalkingMode(on) {
      if (ownerMode === on) return;
      ownerMode = on;
      if (!serverWalk) state = { ...state, walkingMode: on, streak: {} };
    },
    isWalkingMode: () => ownerMode,
    setServerWalk(on) {
      if (serverWalk === on) return;
      serverWalk = on;
      if (on) state = raise({ ...state, walkingMode: true }, deps.now() + SERVER_URGENCY_MS);
      else state = { ...state, walkingMode: ownerMode, ...(ownerMode ? {} : { streak: {} }) };
    },
    isServerWalk: () => serverWalk,
    raiseUrgency(forMs = SERVER_URGENCY_MS) { state = raise(state, deps.now() + forMs); },
    onServerMsg(m) {
      if (m.t === "cue" && m.cue === "warn") state = raise(state, deps.now() + SERVER_URGENCY_MS);
    },
    process,
    latencies: () => [...lat],
  };
}
