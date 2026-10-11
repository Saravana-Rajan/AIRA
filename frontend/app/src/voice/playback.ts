// AIRA's voice: plays the 24 kHz mono PCM16 chunks that Gemini Live streams through
// aira-live. Chunks are scheduled back to back on the AudioContext clock, so speech has
// no gaps. On barge-in (cue "interrupted") flush() stops everything at once.
import { base64ToInt16, int16ToFloat32 } from "./pcm";

/** Server → playback sample rate (Gemini Live output). */
export const PLAYBACK_RATE = 24000;

/** Default upper bound on a barge-in hold. Late chunks of the old turn are in flight for
 *  well under a second; a new answer needs the user to finish speaking first. */
export const DEFAULT_MAX_HOLD_MS = 1500;

export interface PlaybackOpts {
  /** A hold set by flush({holdUntilResume:true}) ends on its own after this long. */
  maxHoldMs?: number;
  /** Wall clock in ms. Not the audio clock, which stops while the context is suspended. */
  now?: () => number;
}

const wallClock = (): number =>
  typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();

export class PlaybackQueue {
  private nextAt = 0;
  private live = new Set<AudioBufferSourceNode>();
  private holdUntil: number | null = null;
  private readonly maxHoldMs: number;
  private readonly now: () => number;

  /**
   * Audio-clock time (ctx.currentTime, seconds) at which AIRA's scheduled speech ends:
   * the last moment Live audio is (or was) audible. -Infinity before any audio.
   * `(ctx.currentTime - lastAudioAt) * 1000` is the ms since AIRA last spoke, and is
   * negative while speech is still playing. The announcer uses it to avoid double speech.
   */
  lastAudioAt = -Infinity;

  constructor(
    private readonly ctx: AudioContext,
    private readonly out: AudioNode = ctx.destination,
    opts: PlaybackOpts = {},
  ) {
    this.maxHoldMs = opts.maxHoldMs ?? DEFAULT_MAX_HOLD_MS;
    this.now = opts.now ?? wallClock;
  }

  /** Queue one base64 PCM16 chunk. Dropped while a barge-in hold is active. */
  enqueue(pcm24b64: string): void {
    if (this.held) return;
    let f: Float32Array;
    try {
      f = int16ToFloat32(base64ToInt16(pcm24b64));
    } catch {
      return; // malformed base64: drop the chunk, never break the message loop
    }
    if (!f.length) return;
    const buf = this.ctx.createBuffer(1, f.length, PLAYBACK_RATE);
    buf.getChannelData(0).set(f);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.out);
    const startAt = Math.max(this.ctx.currentTime, this.nextAt);
    src.start(startAt);
    this.nextAt = startAt + buf.duration;
    this.lastAudioAt = this.nextAt;
    this.live.add(src);
    src.onended = () => {
      this.live.delete(src);
    };
  }

  /**
   * Barge-in: stop and drop every queued and playing chunk now. With holdUntilResume,
   * chunks of the interrupted turn that arrive afterwards (even in the same tick) are
   * dropped until resume(), or until maxHoldMs passes so AIRA is never stuck silent.
   */
  flush(opts: { holdUntilResume?: boolean } = {}): void {
    for (const s of this.live) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
      try {
        s.disconnect();
      } catch {
        /* already disconnected */
      }
    }
    this.live.clear();
    this.nextAt = 0;
    this.lastAudioAt = Math.min(this.lastAudioAt, this.ctx.currentTime);
    this.holdUntil = opts.holdUntilResume ? this.now() + this.maxHoldMs : null;
  }

  /** End a barge-in hold: the next turn's audio plays. */
  resume(): void {
    this.holdUntil = null;
  }

  /** True while a barge-in hold is dropping incoming chunks. */
  get held(): boolean {
    if (this.holdUntil === null) return false;
    if (this.now() >= this.holdUntil) this.holdUntil = null;
    return this.holdUntil !== null;
  }

  get playing(): boolean {
    return this.live.size > 0;
  }

  /** Ms since AIRA's audio stopped: 0 while it is playing, Infinity before any audio. */
  msSinceAudio(): number {
    return Math.max(0, (this.ctx.currentTime - this.lastAudioAt) * 1000);
  }
}
