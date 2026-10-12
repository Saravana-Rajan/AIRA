// Wires the voice app together after the onboarding Start tap: socket, audio, gestures,
// confirmations, offline queue and the screen's view state. Browser APIs come in through
// `WireEnv`, so every rule here is tested with fakes.
import type { ClientMsg, CueName, Lang, ServerMsg } from "@aira/contracts";
import { AiraSocket } from "../net/socket";
import { OfflineQueue, defaultStore, type QueueStore } from "../net/offline-queue";
import { initApp } from "./app";
import { clientLog, loadBuildInfo, shortText, type BuildInfo } from "./clientLog";
import { langFromPhone, phoneLanguages, uiLangFor } from "./lang";
import { saveSettings } from "./settings";
import { vibrate } from "./haptics";
import { ThinkingTimer } from "./thinking";
import { ConfirmState } from "./confirm";
import { createDispatcher } from "./dispatch";
import type { AppApi, HapticName, Settings } from "./types";
import { STRINGS } from "../i18n/strings";
import { PlaybackQueue, type PlaybackOpts } from "../voice/playback";
import { playEarcon, type EarconName } from "../voice/earcons";
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
export const SPEAK_DEDUPE_MS = 10_000;
/** A local line waits while AIRA's Live audio plays or is queued; one that waited this long is dropped. */
export const SPEAK_STALE_MS = 8_000;
/** Live audio that ended less than this long ago still counts as busy: the next chunk is usually in flight. */
export const SPEAK_WAIT_GAP_MS = 400;
const SPEAK_POLL_MS = 250;
/**
 * Mic chunks are dropped while this many bytes sit unsent on the socket (about half a second of 32 ms
 * chunks as JSON). Old audio must never queue up behind a stalled link: AIRA would hear it seconds late
 * and answer late. Dropping a little speech during a stall is the lesser harm.
 */
export const MIC_MAX_BUFFERED_BYTES = 24_000;

/** The identity of a spoken line: trimmed, lowercased, punctuation and symbols dropped, spaces collapsed. */
export function normaliseSpeech(text: string): string {
  return text.toLowerCase().replace(/[\p{P}\p{S}]+/gu, " ").replace(/\s+/g, " ").trim();
}
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

type SocketLike = Pick<AiraSocket, "connect" | "send" | "on" | "close"> & Partial<Pick<AiraSocket, "bufferedAmount">>;
type Listenable = Pick<EventTarget, "addEventListener" | "removeEventListener">;

/** Browser adapters. `browserEnv()` gives the real ones; tests pass fakes. */
export interface WireEnv {
  createSocket(url: string): SocketLike;
  createAudioContext(): AudioContext | null;
  createPlayback(ctx: AudioContext, opts?: PlaybackOpts): PlaybackLike;
  playEarcon(ctx: AudioContext, n: EarconName): void;
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
  /** The phone's locales: only the hint for AIRA's first line (`hello.lang`) and the labels before anyone speaks. */
  languages: readonly string[];
  /** The deployed build (/version.json) for the "build" diagnostic line. Default: `loadBuildInfo`. */
  buildInfo?: () => Promise<BuildInfo | null>;
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
   * Hands-free start (core/autostart.ts, ui/AppShell): opens the mic with no tap. Resolves true once the mic
   * streams, after the local "listening" earcon and a short greeting; false when the browser refused (no user
   * gesture on this origin yet, or the permission was not persisted). Nothing is spoken on failure: the
   * caller shows the Start screen and says "Tap anywhere to start".
   */
  startListening(): Promise<boolean>;
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
    createPlayback: (ctx, opts) => new PlaybackQueue(ctx, undefined, opts),
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
    languages: phoneLanguages(),
    buildInfo: () => loadBuildInfo(),
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

/**
 * The shared demo key built in as VITE_AIRA_APP_KEY (shared/docs/ops/security.md); undefined when not set.
 * In the browser only import.meta.env exists; the process.env fallback is the test seam (vi.stubEnv).
 */
export function appKey(): string | undefined {
  try {
    const meta = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_AIRA_APP_KEY;
    const proc = typeof process !== "undefined" ? process.env?.VITE_AIRA_APP_KEY : undefined;
    const key = meta || proc;
    return key ? key : undefined;
  } catch {
    return undefined;
  }
}

/** VITE_AIRA_LIVE_WS, else ws://localhost:8080/ws. The start-up device check derives its HTTP address from it. */
export function wsDefault(): string {
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
  // Latency diagnostics: one "latency" line per AIRA answer, when its first chunk starts playing.
  let lastMicChunkAt = 0; // wall clock of the last mic chunk sent
  let lastUserFinalAt = 0; // wall clock of the last final user transcript
  const playback = ctx
    ? env.createPlayback(ctx, {
        onPlayStart: (i) => {
          const now = Date.now();
          clientLog("latency", {
            stage: "play_start",
            ms: Math.round(i.msSinceFirstChunk),
            ...(lastUserFinalAt ? { sinceUserFinalMs: now - lastUserFinalAt } : {}),
            ...(lastMicChunkAt ? { sinceMicMs: now - lastMicChunkAt } : {}),
          });
        },
      })
    : NULL_PLAYBACK;
  const confirm = new ConfirmState();
  const queue = new OfflineQueue(env.queueStore());
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const cleanups: Array<() => void> = [];
  let disposed = false;
  let mock = false;

  // ---- the app singleton ----
  // No double speech. Gemini Live is AIRA's voice; the phone's own speech only fills gaps:
  // - a local line never talks over Live audio: while audio plays or is queued it waits, and only the newest
  //   waiting line is spoken once the audio is idle (a line that waited SPEAK_STALE_MS is dropped);
  // - the same sentence (ignoring case, punctuation and spacing) within SPEAK_DEDUPE_MS is spoken once;
  // - screen-reader mode writes to the aria-live region only, never to speechSynthesis.
  let lastSpoken: { key: string; at: number } | null = null;
  let waiting: { text: string; at: number } | null = null;
  let waitTimer: ReturnType<typeof setTimeout> | null = null;
  const liveBusy = () => playback.msSinceAudio() < SPEAK_WAIT_GAP_MS;
  const sayNow = (text: string) => {
    const now = Date.now();
    const key = normaliseSpeech(text);
    if (!key) return;
    if (lastSpoken && lastSpoken.key === key && now - lastSpoken.at < SPEAK_DEDUPE_MS) {
      clientLog("speak", { text: shortText(text), dropped: "dedupe" });
      return;
    }
    lastSpoken = { key, at: now };
    const sr = api.settings.screenReaderMode;
    if (sr) env.announce(text);
    else env.speech?.speak(text, uiLang, api.settings.speechRate);
    clientLog("speak", { text: shortText(text), mode: sr ? "sr" : "tts", lang: uiLang });
  };
  const dropWaiting = () => {
    waiting = null;
    if (waitTimer) { clearTimeout(waitTimer); timers.delete(waitTimer); }
    waitTimer = null;
  };
  const drainWaiting = () => {
    waitTimer = null;
    if (disposed || !waiting) return;
    if (Date.now() - waiting.at > SPEAK_STALE_MS) {
      clientLog("speak", { text: shortText(waiting.text), dropped: "stale" });
      waiting = null;
      return;
    }
    if (liveBusy()) {
      const id = setTimeout(() => { timers.delete(id); drainWaiting(); }, SPEAK_POLL_MS);
      timers.add(id);
      waitTimer = id;
      return;
    }
    const { text } = waiting;
    waiting = null;
    sayNow(text);
  };
  const api = initApp({
    socket,
    settings,
    speakImpl: (text) => {
      if (!liveBusy()) { sayNow(text); return; }
      waiting = { text, at: Date.now() }; // the newest line replaces an older one: TTS would cancel it anyway
      if (!waitTimer) drainWaiting();
    },
    earconImpl: (n) => { if (ctx) tryRun(() => env.playEarcon(ctx!, n)); },
    hapticImpl: (n) => tryRun(() => env.vibrate(n)),
    langImpl: () => uiLang,
  });
  // No fixed language. The labels and local TTS start from the phone's locale (the hint sent as hello.lang)
  // and then follow whatever language the server last heard the speaker use (transcript.lang).
  let uiLang: Lang = settings.lang === "auto" ? langFromPhone(env.languages) : settings.lang;
  const lang = (): Lang => uiLang;

  // ---- view state ----
  let view: AppView = {
    screen: initialScreen,
    level: 0,
    progress: null,
    confirmPending: false,
    online: true,
    lang: uiLang,
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
    if (m.t === "transcript" && m.lang) uiLang = uiLangFor(m.lang, uiLang); // follow the speaker's last turn
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
    stopSpeech: () => { dropWaiting(); env.speech?.cancel(); },
    ctx: () => ({ mock, msSinceLiveAudio: playback.msSinceAudio(), screenReaderMode: api.settings.screenReaderMode }),
    ui: (m) => setView(viewFor(m)),
  });

  const replay = () => queue.replay((m) => api.send(m));

  // Diagnostics: the deployed build (/version.json) is fetched once and reported after every `ready`, so a
  // Cloud Logging session always starts with which build the phone runs. Never blocks anything.
  const buildLoad: Promise<BuildInfo | null> = (async () => {
    try { return await (env.buildInfo ?? loadBuildInfo)(); } catch { return null; }
  })();
  const ua = typeof navigator === "undefined" ? "" : String(navigator.userAgent ?? "");

  cleanups.push(
    socket.on((m) => {
      if (m.t === "ready") {
        mock = m.mock;
        void replay().catch(() => {});
        const isMock = m.mock;
        void buildLoad.then((b) => {
          if (disposed) return;
          clientLog("build", {
            build: b?.build ?? "unknown", ...(b?.updated ? { updated: b.updated } : {}), appVersion: APP_VERSION,
            mock: isMock, lang: uiLang, screenReader: api.settings.screenReaderMode, ua: shortText(ua, 120),
          });
        });
      }
      if (m.t === "transcript" && m.role === "user") {
        // The owner is still speaking: no thinking sound yet. When he finishes, start the 1.5 s wait.
        if (m.final) { thinking.arm(); lastUserFinalAt = Date.now(); }
        else thinking.disarm();
        if (mic || micOpen) armMicIdle();
      }
      dispatch(m);
    }),
  );

  // ---- mic ----
  let mic: { stop(): void } | null = null;
  let micOpen: Promise<boolean> | null = null; // an open in flight
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
    micOpen = null;
    if (mic) tryRun(() => mic!.stop());
    mic = null;
    if (view.level !== 0) setView({ level: 0 });
  };
  /**
   * Resolves true once the mic streams (or already does), false when it could not open. `quiet` skips the
   * warning, the "Microphone is blocked" line and the screen reset: the hands-free start handles that itself.
   */
  const openMic = (quiet = false): Promise<boolean> => {
    armMicIdle();
    if (mic) return Promise.resolve(true);
    if (micOpen) return micOpen;
    const gen = micGen;
    let lastLevel = 0;
    micOpen = env
      .startMic({
        onChunk: (b64) => {
          // Sent at once, one 32 ms chunk per call. Never queued behind a stalled socket: see MIC_MAX_BUFFERED_BYTES.
          const buffered = socket.bufferedAmount?.() ?? 0;
          if (buffered > MIC_MAX_BUFFERED_BYTES) {
            clientLog("mic_drop", { buffered }, { minGapMs: 2000 });
            return;
          }
          if (api.send({ t: "audio", pcm16: b64 })) lastMicChunkAt = Date.now();
        },
        onLevel: (lv) => {
          if (Math.abs(lv - lastLevel) < 0.03 && lv !== 0) return; // ~30 chunks/s: redraw only on change
          lastLevel = lv;
          setView({ level: lv });
        },
      })
      .then((h) => {
        if (disposed || gen !== micGen) { tryRun(() => h.stop()); return false; }
        micOpen = null;
        mic = h;
        return true;
      })
      .catch((e: unknown) => {
        if (gen !== micGen) return false;
        micOpen = null;
        clearMicIdle();
        thinking.disarm();
        if (quiet) return false;
        api.earcon("warn");
        api.haptic("warn");
        if ((e as { name?: string })?.name === "NotAllowedError") api.speak(STRINGS.micBlocked[lang()]);
        local("stop");
        return false;
      });
    return micOpen;
  };

  // ---- hands-free start ----
  // No ack earcon and no thinking timer: nothing was asked yet. Audio output may still be locked (the
  // AudioContext was created without a gesture), but Chrome lets a page with a live getUserMedia stream start
  // Web Audio, so resume once more after the mic opens, then play the listening earcon and greet.
  const startListening = async (): Promise<boolean> => {
    local("talk");
    const ok = await openMic(true);
    if (!ok) {
      local("stop");
      return false;
    }
    if (ctx) {
      void ctx.resume().catch(() => {});
      tryRun(() => env.playEarcon(ctx!, "listening"));
    }
    api.speak(STRINGS.greeting[lang()]);
    return true;
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
    dropWaiting();
    env.speech?.cancel();
    local("talk");
    thinking.arm();
    void openMic();
  };

  const stop = () => {
    playback.flush({ holdUntilResume: true });
    thinking.disarm();
    dropWaiting();
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
    // idToken is the shared demo key VITE_AIRA_APP_KEY, not a user token: there is no Firebase Auth in this
    // build (shared/docs/ops/security.md). The server also checks the page's Origin and caps sessions per address.
    const key = appKey();
    socket.connect({
      t: "hello",
      uid: deviceUid(env.storage),
      ...(key ? { idToken: key } : {}),
      shopId: settings.shopId,
      lang: uiLang, // only a hint for the first line; AIRA then follows the speaker
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
    startListening,
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
