// Volume-button trigger: a QUICK double press of volume up + volume down together starts AIRA.
//
// Pure logic, no browser or Android API. A web page never sees hardware volume keys, so this detector is written
// for a native shell to feed (shared/docs/proposals/native-android-shell.md: Activity.dispatchKeyEvent in the
// foreground; an AccessibilityService in the background, owner approval needed) or to mirror in Kotlin. Flag:
// VITE_AIRA_VOLUME_TRIGGER (handsfree/flags.ts), off by default.
//
// Why it is this strict: holding volume up + down together for about three seconds is Android's accessibility
// shortcut (TalkBack on/off). A blind owner relies on it. So:
//   - a chord that is HELD (either key still down after `maxHoldMs`) never fires and is never consumed;
//   - a single volume press (one key, any length) never fires and is never consumed: volume keeps working;
//   - only two quick chords in a row fire, and only the keys of those chords are reported as consumed.
//
// Timings are configurable and are design values, not measured ones: tune them on a phone (none tested yet).

export type VolumeKey = "up" | "down";
export type KeyPhase = "down" | "up";

export interface VolumeKeyEvent {
  key: VolumeKey;
  type: KeyPhase;
  /** Monotonic time in ms (any origin). */
  t: number;
}

export interface VolumeChordConfig {
  /** Both keys must go down within this window to count as one chord press. */
  chordWindowMs: number;
  /** Both keys must be released within this long of the chord forming, else it is a hold (TalkBack's gesture). */
  maxHoldMs: number;
  /** The second chord must be complete within this long after the first one completed. */
  doublePressWindowMs: number;
}

export const DEFAULT_VOLUME_CHORD: Readonly<VolumeChordConfig> = {
  chordWindowMs: 150,
  maxHoldMs: 400,
  doublePressWindowMs: 800,
};

export interface VolumeChordResult {
  /** True exactly once, on the key release that completes the second quick chord. */
  fire: boolean;
  /**
   * True when this event belongs to a quick chord and the shell may swallow it (return true from dispatchKeyEvent)
   * so the volume does not step for the chord's second key. A lone press, or any event of a held chord, is never
   * consumed. The FIRST key's down of a chord is never consumed either: at that moment it could still be a normal
   * press, so the volume steps once. The trade-off is documented in shared/docs/ops/handsfree.md.
   */
  consume: boolean;
}

const NONE: VolumeChordResult = { fire: false, consume: false };

/**
 * Feed every volume key event in order (`feed`). State machine:
 *   idle --(one key down)--> armed --(other key down within chordWindowMs)--> chord
 *   chord --(both released within maxHoldMs)--> chordDone(1) or fire if a chord completed < doublePressWindowMs ago
 *   chord --(a key still down past maxHoldMs, seen on the next event)--> hold: ignored, reset on full release
 * `reset()` clears everything (e.g. when the app loses focus).
 */
export class VolumeChordDetector {
  readonly cfg: VolumeChordConfig;
  private down = new Map<VolumeKey, number>(); // keys currently down and when they went down
  private chordAt: number | null = null; // when both keys were down together (chord formed)
  private held = false; // this chord turned into a hold: ignore it until fully released
  private lastChordDoneAt: number | null = null; // when the previous quick chord was fully released
  private chordKeys = new Set<VolumeKey>(); // keys that still owe a release for the current quick chord

  constructor(cfg: Partial<VolumeChordConfig> = {}) {
    this.cfg = { ...DEFAULT_VOLUME_CHORD, ...cfg };
  }

  reset(): void {
    this.down.clear();
    this.chordAt = null;
    this.held = false;
    this.lastChordDoneAt = null;
    this.chordKeys.clear();
  }

  /** True while a quick chord is in progress (both keys down, not yet a hold). Diagnostics only. */
  get inChord(): boolean {
    return this.chordAt !== null && !this.held;
  }

  feed(ev: VolumeKeyEvent): VolumeChordResult {
    if (ev.type === "down") return this.onDown(ev);
    return this.onUp(ev);
  }

  private onDown(ev: VolumeKeyEvent): VolumeChordResult {
    if (this.down.has(ev.key)) return NONE; // key repeat while held: a hold, never ours
    this.down.set(ev.key, ev.t);
    if (this.chordAt !== null) {
      // a key re-pressed while the other side of a chord is still down: treat the whole thing as a hold
      this.held = true;
      return NONE;
    }
    const other: VolumeKey = ev.key === "up" ? "down" : "up";
    const otherAt = this.down.get(other);
    if (otherAt === undefined) return NONE; // first key of a maybe-chord: the volume steps, nothing consumed
    if (ev.t - otherAt > this.cfg.chordWindowMs) {
      // the first key has been down too long for a quick chord: this is a hold in the making (TalkBack) or two
      // unrelated presses; never ours
      this.held = true;
      this.chordAt = ev.t;
      return NONE;
    }
    this.chordAt = ev.t;
    this.held = false;
    this.chordKeys = new Set<VolumeKey>(["up", "down"]);
    return { fire: false, consume: true }; // the chord's second key: swallow it so the volume does not step again
  }

  private onUp(ev: VolumeKeyEvent): VolumeChordResult {
    const wasDown = this.down.has(ev.key);
    this.down.delete(ev.key);
    if (!wasDown) return NONE;
    if (this.chordAt === null) {
      // a lone press released: normal volume use. It also breaks any pending double press.
      this.lastChordDoneAt = null;
      return NONE;
    }
    if (!this.held && ev.t - this.chordAt > this.cfg.maxHoldMs) this.held = true; // held past the limit
    if (this.held) {
      if (this.down.size === 0) {
        this.chordAt = null;
        this.held = false;
        this.chordKeys.clear();
        this.lastChordDoneAt = null; // a hold is never half of a double press
      }
      return NONE;
    }
    // a quick chord key released
    this.chordKeys.delete(ev.key);
    if (this.down.size > 0) return { fire: false, consume: true }; // the other key is still to come up
    // chord complete
    const doneAt = ev.t;
    this.chordAt = null;
    this.chordKeys.clear();
    const prev = this.lastChordDoneAt;
    if (prev !== null && doneAt - prev <= this.cfg.doublePressWindowMs) {
      this.lastChordDoneAt = null;
      return { fire: true, consume: true };
    }
    this.lastChordDoneAt = doneAt;
    return { fire: false, consume: true };
  }
}
