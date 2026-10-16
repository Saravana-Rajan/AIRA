// Hear it on the laptop: plays the followed session's audio as the /monitor feed relays it (kind "audio": the
// phone's microphone at 16 kHz, AIRA's voice at 24 kHz, PCM16 base64) once the viewer pressed Listen and the
// server said it relays audio (the feed's "ready" line, AIRA_MONITOR_AUDIO=1).
//
// Browsers only start sound after a user gesture, so `start` (the button) creates or resumes the AudioContext
// and only then sends {t:"listen", session} on the socket. Each voice has its own lane: a gain node and a
// clock position (`nextAt`) on the context's timeline. Chunks are scheduled back to back as AudioBufferSource
// nodes at the stream's own sample rate (the context resamples; nothing is converted here). The first chunk
// after silence or an underrun starts JITTER_S ahead, a small buffer that absorbs network jitter; a chunk that
// would start more than MAX_LEAD_S ahead is late (the socket stalled and then burst) and is dropped, so the
// delay never grows. `attach`/`detach` follow the socket like the WebRTC viewer: a reconnect re-sends the listen
// request while Listen is on; `want` follows the person the page follows and re-targets the request.
// No DOM here: the page renders the button and the meters from `state()` and `subscribeLevels`.
import type { MonitorListenMsg, MonitorMsg, MonitorVoice } from "@aira/contracts";

export type AudioMsg = Extract<MonitorMsg, { kind: "audio" }>;

/** Lead given to the first chunk after silence or an underrun (the jitter buffer), seconds. */
export const JITTER_S = 0.2;
/** A chunk that would start further ahead than this is late and dropped instead of adding delay, seconds. */
export const MAX_LEAD_S = 0.6;

// The slice of the Web Audio API this uses; a real AudioContext satisfies it, tests pass a fake.
export interface AudioBufferLike { duration: number; getChannelData(channel: number): Float32Array }
export interface AudioNodeLike { connect(destination: unknown): unknown }
export interface SourceLike extends AudioNodeLike { buffer: AudioBufferLike | null; start(when?: number): void; onended: (() => void) | null }
export interface GainLike extends AudioNodeLike { gain: { value: number } }
export interface AudioCtxLike {
  currentTime: number;
  state: string;
  destination: unknown;
  resume(): Promise<void>;
  suspend(): Promise<void>;
  close(): Promise<void>;
  createBuffer(channels: number, length: number, sampleRate: number): AudioBufferLike;
  createBufferSource(): SourceLike;
  createGain(): GainLike;
}

export interface ListenDeps {
  /** Makes the AudioContext on the first start (tests pass a fake); the browser's by default. */
  audioContext?: () => AudioCtxLike;
}

export interface ListenState {
  /** Listen is on: the viewer pressed the button and did not stop. */
  on: boolean;
  /** The session asked for (the followed one); null when none is followed. */
  session: string | null;
  /** Chunks dropped as late since the last start. */
  dropped: number;
}

/** What the socket client calls (lib/monitor openMonitor): the send function while a socket is live, and every audio line. */
export interface ListenPort {
  attach(send: (m: MonitorListenMsg) => boolean): void;
  detach(): void;
  receive(m: AudioMsg): void;
}

export interface Listener extends ListenPort {
  state(): ListenState;
  subscribe(cb: (s: ListenState) => void): () => void;
  /** The level (0-1, RMS) of each chunk as it is scheduled, per voice; for a meter, outside the render loop. */
  subscribeLevels(cb: (who: MonitorVoice, level: number) => void): () => void;
  /** The session to hear (the followed one; null once it ends). While on, the request follows it. */
  want(session: string | null): void;
  /** The button, on: a user gesture. Creates or resumes the AudioContext, then asks for the wanted session. */
  start(): void;
  /** The button, off: asks the server to stop and suspends playback. */
  stop(): void;
  toggle(): void;
  /** The page is gone: stop and close the AudioContext. */
  dispose(): void;
  /** Per-voice volume, 0-1 (the gain node; 1 by default). */
  setGain(who: MonitorVoice, value: number): void;
}

/** base64 PCM16 little-endian -> Float32 samples in -1..1. Throws on malformed base64. */
export function decodePcm16(b64: string): Float32Array {
  const bin = atob(b64);
  const n = Math.floor(bin.length / 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
    if (v >= 0x8000) v -= 0x10000;
    out[i] = v / 32768;
  }
  return out;
}

/** Root mean square of the samples, 0..1. */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length));
}

const browserContext = (): AudioCtxLike => new AudioContext() as unknown as AudioCtxLike;

interface Lane { gain: GainLike; nextAt: number }

export function createListener(deps: ListenDeps = {}): Listener {
  const makeContext = deps.audioContext ?? browserContext;
  let ctx: AudioCtxLike | null = null;
  let lanes: Record<MonitorVoice, Lane> | null = null;
  const gains: Record<MonitorVoice, number> = { owner: 1, aira: 1 };
  let on = false;
  let session: string | null = null;
  /** The session the server was last asked for on the live socket (null: none, or no socket). */
  let asked: string | null = null;
  let dropped = 0;
  let send: ((m: MonitorListenMsg) => boolean) | null = null;
  const listeners = new Set<(s: ListenState) => void>();
  const levelListeners = new Set<(who: MonitorVoice, level: number) => void>();

  const state = (): ListenState => ({ on, session, dropped });
  const notify = () => {
    for (const fn of listeners) {
      try { fn(state()); } catch { /* a listener must not break playback */ }
    }
  };

  /** Tells the server what to relay: the wanted session while on, else nothing. Sends only on a change. */
  const sync = () => {
    const want = on ? session : null;
    if (!send || want === asked) return;
    try {
      if (send({ t: "listen", session: want })) asked = want;
    } catch {
      /* the socket is going away: the next attach asks again */
    }
  };

  const ensureContext = (): AudioCtxLike => {
    if (!ctx) {
      ctx = makeContext();
      const lane = (): Lane => {
        const gain = ctx!.createGain();
        gain.connect(ctx!.destination);
        return { gain, nextAt: 0 };
      };
      lanes = { owner: lane(), aira: lane() };
      lanes.owner.gain.gain.value = gains.owner;
      lanes.aira.gain.gain.value = gains.aira;
    }
    return ctx;
  };

  const start = () => {
    const c = ensureContext();
    if (c.state !== "running") c.resume().catch(() => undefined);
    if (lanes) lanes.owner.nextAt = lanes.aira.nextAt = 0; // the first chunk gets a fresh jitter buffer
    dropped = 0;
    on = true;
    sync();
    notify();
  };

  const stop = () => {
    if (!on) return;
    on = false;
    sync();
    if (ctx && ctx.state === "running") ctx.suspend().catch(() => undefined);
    notify();
  };

  return {
    state,
    subscribe(cb) {
      listeners.add(cb);
      return () => { listeners.delete(cb); };
    },
    subscribeLevels(cb) {
      levelListeners.add(cb);
      return () => { levelListeners.delete(cb); };
    },
    attach(fn) {
      send = fn;
      asked = null; // a new socket knows nothing: ask again if listening
      sync();
    },
    detach() {
      send = null;
      asked = null;
    },
    want(id) {
      if (id === session) return;
      session = id;
      if (lanes) lanes.owner.nextAt = lanes.aira.nextAt = 0; // another person: start their sound afresh
      sync();
      notify();
    },
    start,
    stop,
    toggle() {
      if (on) stop();
      else start();
    },
    dispose() {
      stop();
      const c = ctx;
      ctx = null;
      lanes = null;
      c?.close().catch(() => undefined);
    },
    setGain(who, value) {
      gains[who] = Math.max(0, Math.min(1, value));
      if (lanes) lanes[who].gain.gain.value = gains[who];
    },
    receive(m) {
      if (!on || !ctx || !lanes || m.session !== session) return;
      let samples: Float32Array;
      try {
        samples = decodePcm16(m.pcm);
      } catch {
        return; // malformed base64: drop the chunk, never break the message loop
      }
      if (samples.length === 0) return;
      const lane = lanes[m.who];
      const now = ctx.currentTime;
      let at = lane.nextAt;
      if (at < now) at = now + JITTER_S; // silence or an underrun: buffer a little before the next sound
      else if (at - now - MAX_LEAD_S > 1e-6) {
        dropped += 1; // the socket stalled and burst: this chunk would only add delay
        notify();
        return;
      }
      const buf = ctx.createBuffer(1, samples.length, m.rate);
      buf.getChannelData(0).set(samples);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(lane.gain);
      src.start(at);
      lane.nextAt = at + buf.duration;
      const level = rms(samples);
      for (const fn of levelListeners) {
        try { fn(m.who, level); } catch { /* a meter must not break playback */ }
      }
    },
  };
}
