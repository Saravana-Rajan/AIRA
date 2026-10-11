// Wires the voice app together after the onboarding Start tap: socket, audio, gestures,
// confirmations, offline queue and the screen's view state. Browser APIs come in through
// `WireEnv`, so every rule here is tested with fakes.
import type { ClientMsg, CueName, Lang, ServerMsg } from "@aira/contracts";
import { AiraSocket } from "../net/socket";
import { OfflineQueue, defaultStore, type QueueStore } from "../net/offline-queue";
import { initApp } from "./app";
import { saveSettings } from "./settings";
import { vibrate } from "./haptics";
import { ThinkingTimer } from "./thinking";
import { ConfirmState } from "./confirm";
import { createDispatcher } from "./dispatch";
import type { AppApi, HapticName, Settings } from "./types";
import { STRINGS } from "../i18n/strings";
import { PlaybackQueue } from "../voice/playback";
import { playEarcon } from "../voice/earcons";
import { startMic, type StartMicOpts } from "../voice/mic";
import { announceToRegion } from "../ui/ScreenReaderRegion";
import { initialScreen, reduceScreen, type LocalEvent, type ScreenState } from "../ui/screenState";
import type { CountProgress } from "../ui/view";

export const APP_VERSION = "0.1.0";
/** The mic closes after this long with no talk gesture and no user speech. */
export const MIC_IDLE_MS = 30_000;
/** The soft thinking sound repeats at this interval until AIRA answers. */
export const THINKING_LOOP_MS = 1200;
/** The same sentence handed to speak() twice within this window is announced or spoken once. */
export const SPEAK_DEDUPE_MS = 3000;
export const UID_KEY = "aira.uid";
const DEFAULT_WS = "ws://localhost:8080/ws";

/** What the dispatcher and the gestures need from the playback queue. */
export interface PlaybackLike {
  enqueue(b64: string): void;
  flush(o?: { holdUntilResume?: boolean }): void;
  resume(): void;
  /** Ms since AIRA's Live audio last played (Infinity before any). */
  msSinceAudio(): number;
}

type SocketLike = Pick<AiraSocket, "connect" | "send" | "on" | "close">;
type Listenable = Pick<EventTarget, "addEventListener" | "removeEventListener">;

/** Browser adapters. `browserEnv()` gives the real ones; tests pass fakes. */
export interface WireEnv {
  createSocket(url: string): SocketLike;
  createAudioContext(): AudioContext | null;
  createPlayback(ctx: AudioContext): PlaybackLike;
  playEarcon(ctx: AudioContext, n: CueName): void;
  vibrate(n: HapticName): void;
  /** Client TTS for local status and fallbacks. Null where speechSynthesis is missing. */
  speech: { speak(text: string, lang: Lang, rate: number): void; cancel(): void } | null;
  /** Writes to the single aria-live region (screen-reader mode). */
  announce(text: string): void;
  startMic(opts: StartMicOpts): Promise<{ stop(): void }>;
  queueStore(): QueueStore;
  storage: Storage | null;
  win: Listenable;
  doc: Listenable & { hidden: boolean };
  requestWakeLock(): void;
  /** Asks for mic and camera once, inside the Start tap, so later turns never prompt. */
  requestPermissions(): void;
}

/** Everything the screen draws. */
export interface AppView {
  screen: ScreenState;
  /** Mic loudness 0..1 for the voice orb. */
  level: number;
  /** Live delivery count for the task card bar. */
  progress: CountProgress | null;
  /** A confirm waits for double-tap (yes) or long-press (no). */
  confirmPending: boolean;
  online: boolean;
  lang: Lang;
}

export interface ViewStore {
  get(): AppView;
  subscribe(fn: (v: AppView) => void): () => void;
}

export interface WireOpts {
  settings: Settings;
  /** aira-live WebSocket URL. Defaults to VITE_AIRA_LIVE_WS, then ws://localhost:8080/ws. */
  wsUrl?: string;
  /** false: wire everything but send no hello (the demo page connects the socket itself). */
  connect?: boolean;
  env?: Partial<WireEnv>;
}

export interface WiredApp {
  app: AppApi;
  socket: AiraSocket;
  ctx: AudioContext | null;
  playback: PlaybackLike;
  confirm: ConfirmState;
  queue: OfflineQueue;
  view: ViewStore;
  /** Double-tap: "yes" to a pending confirm, otherwise talk. */
  onTalk(): void;
  /** Long-press: "no" to a pending confirm, otherwise a hard stop. */
  onStop(): void;
  /**
   * For ops that must survive a dropped connection: queued under `idemKey` (never twice) and
   * sent in order now or on the next reconnect. True when it went out now.
   */
  sendOrQueue(m: ClientMsg, idemKey: string): Promise<boolean>;
  /** Sends whatever is queued; returns how many went out. */
  replay(): Promise<number>;
  /** Raise a local screen event, e.g. the bump guard's "bump" / "bump_clear". */
  local(event: LocalEvent["event"]): void;
  dispose(): void;
}

const VOICE: Record<Lang, string> = { ta: "ta-IN", hi: "hi-IN", en: "en-IN" };

const NULL_PLAYBACK: PlaybackLike = { enqueue() {}, flush() {}, resume() {}, msSinceAudio: () => Infinity };

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** The real browser adapters. Nothing here runs until wireApp calls it. */
export function browserEnv(): WireEnv {
  const synth = typeof speechSynthesis === "undefined" ? null : speechSynthesis;
  return {
    createSocket: (url) => new AiraSocket(url),
    createAudioContext: () => {
      const AC = globalThis.AudioContext ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      return AC ? new AC() : null;
    },
    createPlayback: (ctx) => new PlaybackQueue(ctx),
    playEarcon,
    vibrate: (n) => { vibrate(n); },
    speech: synth
      ? {
          speak(text, lang, rate) {
            const u = new SpeechSynthesisUtterance(text);
            u.lang = VOICE[lang];
            u.rate = rate;
            synth.cancel();
            synth.speak(u);
          },
          cancel() { synth.cancel(); },
        }
      : null,
    announce: announceToRegion,
    startMic,
    queueStore: () => defaultStore(),
    storage: safeStorage(),
    win: window,
    doc: document,
    requestWakeLock: () => {
      const wl = (navigator as Navigator & { wakeLock?: { request(t: "screen"): Promise<unknown> } }).wakeLock;
      wl?.request("screen").catch(() => { /* best effort: battery saver or not visible */ });
    },
    requestPermissions: () => {
      const md = navigator.mediaDevices;
      if (!md?.getUserMedia) return;
      const release = (s: MediaStream) => s.getTracks().forEach((t) => t.stop());
      md.getUserMedia({ audio: true, video: { facingMode: "environment" } })
        .then(release)
        .catch(() => md.getUserMedia({ audio: true }).then(release))
        .catch(() => { /* the mic start reports a block when the owner talks */ });
    },
  };
}

/** A per-phone id, kept across reloads. There is no sign-in in this build. */
export function deviceUid(storage: Storage | null): string {
  try {
    const have = storage?.getItem(UID_KEY);
    if (have) return have;
  } catch { /* storage blocked */ }
  const rand =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(36).slice(2, 14);
  const uid = `owner-${rand}`;
  try { storage?.setItem(UID_KEY, uid); } catch { /* keep it for this page only */ }
  return uid;
}

function wsDefault(): string {
  try {
    return (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_AIRA_LIVE_WS ?? DEFAULT_WS;
  } catch {
    return DEFAULT_WS;
  }
}

/**
 * Call once, synchronously, inside the onboarding Start tap: that tap is the user gesture
 * that unlocks audio playback, the mic and camera prompts and the wake lock.
 * Fills the `app` singleton (core/app.ts) and returns the gesture handlers and view state.
 */
export function wireApp(opts: WireOpts): WiredApp {
  const env: WireEnv = { ...browserEnv(), ...opts.env };
  const settings: Settings = { ...opts.settings, onboarded: true };
  saveSettings(settings, env.storage);

  // ---- unlock everything inside the tap ----
  let ctx: AudioContext | null = null;
  try {
    ctx = env.createAudioContext();
    void ctx?.resume().catch(() => {});
  } catch {
    ctx = null; // no Web Audio: earcons and Live audio are silent, captions still work
  }
  const tryRun = (fn: () => void) => { try { fn(); } catch { /* best effort */ } };
  tryRun(() => env.requestWakeLock());
  tryRun(() => env.requestPermissions());

  const socket = env.createSocket(opts.wsUrl ?? wsDefault());
  const playback = ctx ? env.createPlayback(ctx) : NULL_PLAYBACK;
  const confirm = new ConfirmState();
  const queue = new OfflineQueue(env.queueStore());
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const cleanups: Array<() => void> = [];
  let disposed = false;
  let mock = false;

  // ---- the app singleton ----
  // No double speech: the confirm card, the AIRA caption and a notice can carry the same sentence within the
  // same moment. The same text within SPEAK_DEDUPE_MS is announced (aria-live) or spoken (TTS) once; both
  // stay visible on screen.
  let lastSpoken: { text: string; at: number } | null = null;
  const api = initApp({
    socket,
    settings,
    speakImpl: (text, s) => {
      const now = Date.now();
      if (lastSpoken && lastSpoken.text === text && now - lastSpoken.at < SPEAK_DEDUPE_MS) return;
      lastSpoken = { text, at: now };
      if (s.screenReaderMode) env.announce(text);
      else env.speech?.speak(text, s.lang, s.speechRate);
    },
    earconImpl: (n) => { if (ctx) tryRun(() => env.playEarcon(ctx!, n)); },
    hapticImpl: (n) => tryRun(() => env.vibrate(n)),
  });
  const lang = (): Lang => api.settings.lang;

  // ---- view state ----
  let view: AppView = {
    screen: initialScreen,
    level: 0,
    progress: null,
    confirmPending: false,
    online: true,
    lang: settings.lang,
  };
  const subs = new Set<(v: AppView) => void>();
  const setView = (patch: Partial<AppView>) => {
    view = { ...view, ...patch, confirmPending: confirm.pending !== null, lang: lang() };
    subs.forEach((fn) => fn(view));
  };
  const viewStore: ViewStore = {
    get: () => view,
    subscribe: (fn) => {
      subs.add(fn);
      return () => { subs.delete(fn); };
    },
  };
  const local = (event: LocalEvent["event"]) => setView({ screen: reduceScreen(view.screen, { t: "local", event }) });

  const viewFor = (m: ServerMsg): Partial<AppView> => {
    const patch: Partial<AppView> = { screen: reduceScreen(view.screen, m) };
    if (m.t === "count") patch.progress = { counted: m.counted, expected: m.expected };
    else if (m.t === "confirm" || (m.t === "state" && m.entity !== "delivery" && !m.summary.startsWith("OPEN_URL "))) {
      patch.progress = null;
    }
    return patch;
  };

  // ---- thinking sound: 1.5 s of silence after a trigger, repeating until AIRA answers ----
  let loop: ReturnType<typeof setInterval> | null = null;
  const thinking = new ThinkingTimer(
    () => {
      api.earcon("thinking");
      loop = setInterval(() => api.earcon("thinking"), THINKING_LOOP_MS);
    },
    () => {
      if (loop) clearInterval(loop);
      loop = null;
    },
  );

  // ---- server messages ----
  const dispatch = createDispatcher({
    playback,
    thinking,
    confirm,
    earcon: (n) => api.earcon(n),
    haptic: (n) => api.haptic(n),
    speak: (t) => api.speak(t),
    stopSpeech: () => env.speech?.cancel(),
    ctx: () => ({ mock, msSinceLiveAudio: playback.msSinceAudio(), screenReaderMode: api.settings.screenReaderMode }),
    ui: (m) => setView(viewFor(m)),
  });

  const replay = () => queue.replay((m) => api.send(m));

  cleanups.push(
    socket.on((m) => {
      if (m.t === "ready") {
        mock = m.mock;
        void replay().catch(() => {});
      }
      if (m.t === "transcript" && m.role === "user") {
        // The owner is still speaking: no thinking sound yet. When he finishes, start the 1.5 s wait.
        if (m.final) thinking.arm();
        else thinking.disarm();
        if (mic || micOpening) armMicIdle();
      }
      dispatch(m);
    }),
  );

  // ---- mic ----
  let mic: { stop(): void } | null = null;
  let micOpening = false;
  let micIdle: ReturnType<typeof setTimeout> | null = null;
  let micGen = 0; // bumps on every stop, so a mic that opens after a stop is closed at once

  const later = (fn: () => void, ms: number) => {
    const id = setTimeout(() => { timers.delete(id); fn(); }, ms);
    timers.add(id);
    return id;
  };
  const clearMicIdle = () => {
    if (micIdle) { clearTimeout(micIdle); timers.delete(micIdle); }
    micIdle = null;
  };
  const armMicIdle = () => {
    clearMicIdle();
    micIdle = later(() => { micIdle = null; stopMic(); }, MIC_IDLE_MS);
  };
  const stopMic = () => {
    micGen++;
    clearMicIdle();
    micOpening = false;
    if (mic) tryRun(() => mic!.stop());
    mic = null;
    if (view.level !== 0) setView({ level: 0 });
  };
  const openMic = () => {
    armMicIdle();
    if (mic || micOpening) return;
    micOpening = true;
    const gen = micGen;
    let lastLevel = 0;
    env
      .startMic({
        onChunk: (b64) => { api.send({ t: "audio", pcm16: b64 }); },
        onLevel: (lv) => {
          if (Math.abs(lv - lastLevel) < 0.03 && lv !== 0) return; // ~30 chunks/s: redraw only on change
          lastLevel = lv;
          setView({ level: lv });
        },
      })
      .then((h) => {
        if (disposed || gen !== micGen) { tryRun(() => h.stop()); return; }
        micOpening = false;
        mic = h;
      })
      .catch((e: unknown) => {
        if (gen !== micGen) return;
        micOpening = false;
        clearMicIdle();
        thinking.disarm();
        api.earcon("warn");
        api.haptic("warn");
        if ((e as { name?: string })?.name === "NotAllowedError") api.speak(STRINGS.micBlocked[lang()]);
        local("stop");
      });
  };

  // ---- gestures ----
  const answer = (g: "double" | "long"): boolean => {
    const p = confirm.pending;
    if (!p) return false;
    const reply = confirm.onGesture(g)!;
    if (api.send(reply)) {
      api.earcon("ack");
      api.haptic("ack");
    } else {
      // Never queued: a late "yes" to a sale or a payment is worse than asking again.
      confirm.set(p.id, p.kind);
      api.earcon("warn");
      api.haptic("warn");
      api.speak(STRINGS.notSent[lang()]);
    }
    setView({});
    return true;
  };

  const talk = () => {
    api.earcon("ack"); // < 150 ms acknowledgement, before anything async
    api.haptic("ack");
    playback.flush({ holdUntilResume: true }); // an explicit talk cuts AIRA off
    env.speech?.cancel();
    local("talk");
    thinking.arm();
    openMic();
  };

  const stop = () => {
    playback.flush({ holdUntilResume: true });
    thinking.disarm();
    env.speech?.cancel();
    stopMic();
    api.send({ t: "stop" });
    api.haptic("done");
    local("stop");
  };

  // ---- phone status ----
  const on = (target: Listenable, type: string, fn: () => void) => {
    target.addEventListener(type, fn);
    cleanups.push(() => target.removeEventListener(type, fn));
  };
  on(env.win, "offline", () => {
    api.earcon("offline");
    api.speak(STRINGS.offline[lang()]);
    setView({ online: false, screen: reduceScreen(view.screen, { t: "local", event: "offline" }) });
  });
  on(env.win, "online", () => {
    api.speak(STRINGS.online[lang()]);
    setView({ online: true });
    void replay().catch(() => {});
  });
  on(env.doc, "visibilitychange", () => {
    const hidden = env.doc.hidden;
    api.send({ t: "event", name: "visibility", data: { hidden } });
    if (hidden) api.speak(STRINGS.paused[lang()]);
    else tryRun(() => env.requestWakeLock()); // the lock is released whenever the page hides
  });

  if (opts.connect !== false) {
    // No idToken: there is no Firebase Auth in this build (docs/ops/security.md). The server checks the
    // page's Origin and caps sessions per address instead.
    socket.connect({
      t: "hello",
      uid: deviceUid(env.storage),
      shopId: settings.shopId,
      lang: settings.lang,
      verbosity: settings.verbosity,
      appVersion: APP_VERSION,
    });
  }

  return {
    app: api,
    socket: socket as AiraSocket,
    ctx,
    playback,
    confirm,
    queue,
    view: viewStore,
    onTalk: () => { if (!answer("double")) talk(); },
    onStop: () => { if (!answer("long")) stop(); },
    sendOrQueue: async (m, idemKey) => {
      await queue.add(m, idemKey);
      await replay();
      return !(await queue.has(idemKey));
    },
    replay,
    local,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      stopMic();
      thinking.disarm();
      timers.forEach((id) => clearTimeout(id));
      timers.clear();
      cleanups.splice(0).forEach((fn) => tryRun(fn));
      subs.clear();
      tryRun(() => socket.close());
      if (ctx) void ctx.close().catch(() => {});
    },
  };
}
