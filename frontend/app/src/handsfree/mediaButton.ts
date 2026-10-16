// Earphone button: the play/pause button on wired or Bluetooth earphones talks / stops, exactly like the
// screen's double-tap / long-press (core/wire onTalk / onStop, confirm answers included). Flag:
// VITE_AIRA_MEDIA_BUTTON (handsfree/flags.ts), off by default. Docs: shared/docs/ops/handsfree.md.
//
// How: the Media Session API. navigator.mediaSession.setActionHandler("play" | "pause" | "stop") makes the browser
// hand the headset's media keys to the page instead of acting on them itself. Which of "play" or "pause" a single
// toggle button produces follows `mediaSession.playbackState`, so the page sets "playing" while AIRA listens and
// "paused" when it does not: one button, press = talk, press again = stop. "nexttrack" / "previoustrack" (a double
// or triple press on many headsets) are left unregistered on purpose: the owner's music app keeps them.
//
// The catch (known as of mid-2026; verify on a phone, none tested yet): a browser routes media keys only to a page
// with an ACTIVE media session, which in Chrome means an HTMLMediaElement that is playing, not muted, and (Chrome's
// media-notification rule) longer than about 5 s. AIRA's own voice is Web Audio (voice/playback.ts), which does not
// count. So, while the flag is on, the page keeps a looping <audio> "keep-alive" playing: a 6 s, 8 kHz, 16-bit WAV
// built in code (no asset file), a 50 Hz tone at about -60 dBFS, which is below hearing on any earphone but is not
// digital silence. Whether Chrome needs non-silent samples (and not only an unmuted element) is UNCERTAIN; the
// level is one constant below. Side effects to expect and verify on device: the OS shows a media notification for
// AIRA ("AIRA, Listening") and other apps' music pauses when AIRA starts (audio focus). Battery impact: not measured.
//
// Everything is feature-detected; without the API, with the flag off, or when anything throws, nothing is attached
// and the gestures keep working as before (silent fallback).

/** The subset of navigator.mediaSession this module uses; tests pass a fake. */
export interface MediaSessionLike {
  setActionHandler(action: string, handler: (() => void) | null): void;
  playbackState?: string;
  metadata?: unknown;
}

/** A looping, near-silent media element that makes this page the active media session. */
export interface KeepAlive {
  /** Starts (or restarts after an interruption). Call inside a user gesture the first time. Never throws. */
  start(): void;
  stop(): void;
}

export interface MediaButtonOptions {
  /** The build flag (handsfree/flags.ts mediaButton). false: nothing is attached. */
  enabled: boolean;
  /** navigator.mediaSession, or null / undefined where missing. */
  mediaSession: MediaSessionLike | null | undefined;
  onTalk(): void;
  onStop(): void;
  /** Default: none. core/wire passes `audioKeepAlive(document)` so Chrome routes the keys here. */
  keepAlive?: KeepAlive | null;
  /** Shown in the OS media notification. */
  metadata?: { title: string; artist?: string };
  /** The MediaMetadata constructor (default: the global one, if any). */
  MediaMetadata?: new (init: { title: string; artist?: string }) => unknown;
}

export interface MediaButton {
  /** True when handlers were installed (flag on, API present, no throw). */
  readonly attached: boolean;
  /** Mirrors AIRA's listening state into playbackState, so the next button press means the opposite. */
  setListening(listening: boolean): void;
  /** Restart the keep-alive after an interruption (a call, Siri, the page hidden). */
  resume(): void;
  dispose(): void;
}

const NOOP: MediaButton = { attached: false, setListening() {}, resume() {}, dispose() {} };

export const MEDIA_ACTIONS = ["play", "pause", "stop"] as const;

export function attachMediaButton(o: MediaButtonOptions): MediaButton {
  if (!o.enabled) return NOOP;
  const ms = o.mediaSession;
  if (!ms || typeof ms.setActionHandler !== "function") return NOOP;
  const handlers: Record<(typeof MEDIA_ACTIONS)[number], () => void> = {
    play: () => o.onTalk(),
    pause: () => o.onStop(),
    stop: () => o.onStop(),
  };
  const installed: string[] = [];
  try {
    for (const a of MEDIA_ACTIONS) {
      ms.setActionHandler(a, handlers[a]);
      installed.push(a);
    }
  } catch {
    // an action this browser does not know throws TypeError: keep the ones that went in, give up if none did
    if (installed.length === 0) return NOOP;
  }
  try {
    const MM = o.MediaMetadata ?? (globalThis as { MediaMetadata?: MediaButtonOptions["MediaMetadata"] }).MediaMetadata;
    if (MM && o.metadata) ms.metadata = new MM(o.metadata);
  } catch { /* metadata is cosmetic */ }
  const setState = (s: "playing" | "paused") => { try { ms.playbackState = s; } catch { /* read-only in a fake */ } };
  setState("paused");
  const ka = o.keepAlive ?? null;
  try { ka?.start(); } catch { /* no element: the keys may not arrive, the gestures still work */ }
  let disposed = false;
  return {
    attached: true,
    setListening: (l) => { if (!disposed) setState(l ? "playing" : "paused"); },
    resume: () => { if (!disposed) { try { ka?.start(); } catch { /* best effort */ } } },
    dispose: () => {
      if (disposed) return;
      disposed = true;
      for (const a of installed) { try { ms.setActionHandler(a, null); } catch { /* ignore */ } }
      setState("paused");
      try { ms.metadata = null; } catch { /* ignore */ }
      try { ka?.stop(); } catch { /* ignore */ }
    },
  };
}

/** Keep-alive clip: 6 s (over Chrome's ~5 s media-notification minimum), 8 kHz, 16-bit mono. */
export const KEEPALIVE_SECONDS = 6;
export const KEEPALIVE_RATE = 8000;
/** Peak sample value of the 50 Hz tone: 32 of 32767, about -60 dBFS. Not digital silence; inaudible on earphones. */
export const KEEPALIVE_PEAK = 32;
export const KEEPALIVE_HZ = 50;

/** The WAV bytes of the keep-alive clip, built in code so there is no asset to download. Pure. */
export function keepAliveWav(seconds = KEEPALIVE_SECONDS, rate = KEEPALIVE_RATE, peak = KEEPALIVE_PEAK): Uint8Array {
  const n = Math.round(seconds * rate);
  const dataBytes = n * 2;
  const buf = new ArrayBuffer(44 + dataBytes);
  const v = new DataView(buf);
  const str = (off: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + dataBytes, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, dataBytes, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.round(peak * Math.sin((2 * Math.PI * KEEPALIVE_HZ * i) / rate)), true);
  return new Uint8Array(buf);
}

/** What `audioKeepAlive` needs from the document; tests pass a fake. */
export interface KeepAliveDoc {
  createElement(tag: "audio"): HTMLAudioElement;
}

/**
 * The real keep-alive: a looping, unmuted <audio> at full element volume (the samples are the quiet part) that is
 * never added to the page. `start()` is best effort: play() may be refused before a user gesture, and then the
 * next `start()` (core/wire calls `resume()` on each touch / return to the page) tries again.
 */
export function audioKeepAlive(doc: KeepAliveDoc, wav: Uint8Array = keepAliveWav()): KeepAlive | null {
  let el: HTMLAudioElement;
  let url: string | null = null;
  try {
    el = doc.createElement("audio");
    const blob = new Blob([wav.buffer as ArrayBuffer], { type: "audio/wav" });
    if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
      url = URL.createObjectURL(blob);
      el.src = url;
    } else {
      let s = "";
      for (let i = 0; i < wav.length; i++) s += String.fromCharCode(wav[i]);
      el.src = "data:audio/wav;base64," + btoa(s);
    }
    el.loop = true;
    el.preload = "auto";
    el.setAttribute("playsinline", "");
    el.volume = 1;
    el.muted = false;
  } catch {
    return null;
  }
  return {
    start() {
      try {
        if (!el.paused) return;
        const p = el.play();
        if (p && typeof p.catch === "function") p.catch(() => { /* refused: retried on the next gesture */ });
      } catch { /* best effort */ }
    },
    stop() {
      try { el.pause(); } catch { /* ignore */ }
      try { el.removeAttribute("src"); el.load(); } catch { /* ignore */ }
      if (url) { try { URL.revokeObjectURL(url); } catch { /* ignore */ } url = null; }
    },
  };
}
