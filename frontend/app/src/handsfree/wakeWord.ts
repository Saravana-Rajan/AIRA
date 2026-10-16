// "Hello AIRA" wake-word controller: a state machine around an injectable engine.
//
// Pure logic. The engine (openWakeWord, Porcupine, Vosk, or a fake in tests) does the listening; this controller
// decides WHEN it may run and WHAT happens on a detection. A web page cannot listen while closed, so the real
// engine lives in a native shell (shared/docs/proposals/native-android-shell.md) that calls this controller or
// mirrors it in Kotlin. Flag: VITE_AIRA_WAKEWORD (handsfree/flags.ts), off by default.
//
// Rules:
//   - off by default; `enable()` needs `ownerConfirmed: true` (a spoken or tapped yes), otherwise it stays off;
//   - on detect: bring the app to the front, start the voice session, play the ack earcon (three callbacks), then
//     a cooldown so one "Hello AIRA" never starts two sessions;
//   - `disable()` / `dispose()` stop the engine at once and are always safe to call;
//   - engine errors and kills restart the engine after `restartDelayMs`, at most `maxRestarts` times within
//     `restartWindowMs`; past that the controller gives up ("failed") and tells the owner through `onFailed`.
// Audio never leaves the device until the wake word fires: the controller has no network callback at all.

export interface WakeEngine {
  /** Starts listening. May be async. Call `onDetect` on the wake word, `onError` when the engine dies. */
  start(cb: { onDetect(): void; onError(err: unknown): void }): void | Promise<void>;
  stop(): void | Promise<void>;
}

export interface WakeWordCallbacks {
  bringToFront(): void;
  startVoiceSession(): void;
  /** The ack earcon (and haptic): "I heard you". */
  ack(): void;
  /** The engine failed too often; the owner should be told (the native shell speaks WAKEWORD_STRINGS.failed). */
  onFailed?(reason: string): void;
  /** State changes, for logs and the UI. */
  onState?(state: WakeWordState, prev: WakeWordState): void;
}

export interface WakeWordOptions {
  /** Detections closer together than this are ignored. */
  cooldownMs: number;
  restartDelayMs: number;
  maxRestarts: number;
  restartWindowMs: number;
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

export const DEFAULT_WAKE_WORD: Readonly<Omit<WakeWordOptions, "now" | "setTimeout" | "clearTimeout">> = {
  cooldownMs: 3_000,
  restartDelayMs: 2_000,
  maxRestarts: 3,
  restartWindowMs: 60_000,
};

export type WakeWordState =
  | "off" // never enabled, or disabled by the owner
  | "starting" // engine start in flight
  | "listening" // engine running, waiting for the wake word
  | "restarting" // engine died; a restart is scheduled
  | "failed"; // too many restarts; off until the owner enables it again

export class WakeWordController {
  private _state: WakeWordState = "off";
  private readonly opts: WakeWordOptions;
  private gen = 0; // bumps on every stop, so a late start/detect from an old engine run is ignored
  private restartTimer: unknown = null;
  private restarts: number[] = []; // wall clocks of recent restarts
  private lastDetectAt = -Infinity;

  constructor(
    private readonly engine: WakeEngine,
    private readonly cb: WakeWordCallbacks,
    opts: Partial<WakeWordOptions> = {},
  ) {
    this.opts = {
      ...DEFAULT_WAKE_WORD,
      now: () => Date.now(),
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
      ...opts,
    };
  }

  get state(): WakeWordState {
    return this._state;
  }

  /** Listening or about to be: the engine holds the microphone. */
  get active(): boolean {
    return this._state === "starting" || this._state === "listening" || this._state === "restarting";
  }

  /**
   * Turns the wake word on. Refused (returns false, state stays "off") unless the owner confirmed. A second call
   * while active is a no-op (true). After "failed" the owner's confirmation starts a fresh attempt.
   */
  enable(o: { ownerConfirmed: boolean }): boolean {
    if (!o.ownerConfirmed) return false;
    if (this.active) return true;
    this.restarts = [];
    this.startEngine();
    return true;
  }

  /** Stops the engine and any pending restart. Safe at any time. */
  disable(): void {
    this.stopEngine();
    this.setState("off");
  }

  dispose(): void {
    this.disable();
  }

  private setState(s: WakeWordState): void {
    const prev = this._state;
    if (prev === s) return;
    this._state = s;
    try { this.cb.onState?.(s, prev); } catch { /* a listener must never break the controller */ }
  }

  private stopEngine(): void {
    this.gen++;
    if (this.restartTimer !== null) {
      this.opts.clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    try {
      const r = this.engine.stop();
      if (r && typeof (r as Promise<void>).catch === "function") (r as Promise<void>).catch(() => {});
    } catch { /* already stopped or dead */ }
  }

  private startEngine(): void {
    const gen = ++this.gen;
    this.setState("starting");
    const onError = (err: unknown) => { if (gen === this.gen) this.onEngineDown(err); };
    try {
      const r = this.engine.start({
        onDetect: () => { if (gen === this.gen) this.onDetect(); },
        onError,
      });
      if (r && typeof (r as Promise<void>).then === "function") {
        (r as Promise<void>).then(
          () => { if (gen === this.gen) this.setState("listening"); },
          (e) => onError(e),
        );
      } else {
        this.setState("listening");
      }
    } catch (e) {
      onError(e);
    }
  }

  private onDetect(): void {
    if (this._state !== "listening") return;
    const now = this.opts.now();
    if (now - this.lastDetectAt < this.opts.cooldownMs) return;
    this.lastDetectAt = now;
    // Order matters for a blind owner: the ack is heard first (fast), then the app comes up and listens.
    try { this.cb.ack(); } catch { /* best effort */ }
    try { this.cb.bringToFront(); } catch { /* best effort */ }
    try { this.cb.startVoiceSession(); } catch { /* best effort */ }
  }

  /** The engine died (error, OS kill reported by the shell, mic lost): restart within the budget, else fail. */
  private onEngineDown(err: unknown): void {
    if (!this.active) return;
    const now = this.opts.now();
    this.restarts = this.restarts.filter((t) => now - t <= this.opts.restartWindowMs);
    if (this.restarts.length >= this.opts.maxRestarts) {
      this.stopEngine();
      this.setState("failed");
      const reason = err instanceof Error ? err.message : String(err ?? "engine stopped");
      try { this.cb.onFailed?.(reason); } catch { /* best effort */ }
      return;
    }
    this.restarts.push(now);
    this.gen++; // whatever the dead engine still reports is ignored
    this.setState("restarting");
    this.restartTimer = this.opts.setTimeout(() => {
      this.restartTimer = null;
      if (this._state === "restarting") this.startEngine();
    }, this.opts.restartDelayMs);
  }

  /** The native shell tells us the OS killed the listening service (same restart rules as an engine error). */
  onKilled(reason = "killed"): void {
    this.onEngineDown(new Error(reason));
  }
}
