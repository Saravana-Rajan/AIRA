// One sentence, once. Gemini Live is AIRA's voice: the phone's own speech (speechSynthesis / aria-live) only
// fills gaps, never talks over Live audio and never repeats a line within SPEAK_DEDUPE_MS.
import { describe, it, expect, vi, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import {
  wireApp, normaliseSpeech, type WireEnv, type WiredApp, SPEAK_DEDUPE_MS, SPEAK_STALE_MS, SPEAK_WAIT_GAP_MS,
} from "../src/core/wire";
import { app } from "../src/core/app";
import { shouldClientSpeak } from "../src/core/announce";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import type { Settings } from "../src/core/types";
import { memStore } from "../src/net/offline-queue";
import { createHintSpeaker, HINT_GAP_MS } from "../src/camera/hints";
import { TILT_HINT_GAP_MS } from "../src/camera/tilt";
import { SHOW_HAND_GAP_MS } from "../src/guidance/tones";
import { STRINGS } from "../src/i18n/strings";

class MemStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  key(i: number) { return [...this.m.keys()][i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}

class FakeSocket {
  private hs = new Set<(m: ServerMsg) => void>();
  constructor(public url: string) {}
  connect() {}
  send(_m: ClientMsg) { return true; }
  on(h: (m: ServerMsg) => void) { this.hs.add(h); return () => { this.hs.delete(h); }; }
  close() {}
  emit(m: ServerMsg) { this.hs.forEach((h) => h(m)); }
}

const wired: WiredApp[] = [];
afterEach(() => {
  while (wired.length) wired.pop()!.dispose();
  vi.useRealTimers();
});

function setup(over: { settings?: Partial<Settings>; msSinceAudio?: () => number } = {}) {
  let socket!: FakeSocket;
  const playback = { enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), msSinceAudio: over.msSinceAudio ?? (() => Infinity) };
  const env: WireEnv = {
    createSocket: (url) => (socket = new FakeSocket(url)) as never,
    createAudioContext: () => ({ resume: () => Promise.resolve(), close: () => Promise.resolve() } as unknown as AudioContext),
    createPlayback: () => playback,
    playEarcon: vi.fn(),
    vibrate: vi.fn(),
    speech: { speak: vi.fn(), cancel: vi.fn() },
    announce: vi.fn(),
    startMic: vi.fn(async () => ({ stop() {} })),
    queueStore: () => memStore(),
    storage: new MemStorage(),
    win: new EventTarget(),
    doc: Object.assign(new EventTarget(), { hidden: false }),
    requestWakeLock: vi.fn(),
    requestPermissions: vi.fn(),
    languages: ["en-IN"],
  };
  const w = wireApp({ settings: { ...DEFAULT_SETTINGS, onboarded: true, ...over.settings }, wsUrl: "ws://t/ws", env });
  wired.push(w);
  return {
    w, env, playback,
    get socket() { return socket; },
    spoken: () => (env.speech!.speak as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string),
    announced: () => (env.announce as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string),
  };
}

describe("server error lines: deferred behind Live audio, never dropped for it", () => {
  const ERR: ServerMsg = { t: "error", code: "internal", say: "Sorry, the sale did not save." };
  const real = (a: ReturnType<typeof setup>) => a.socket.emit({ t: "ready", sessionId: "s", resume: "s", mock: false });

  it("an error arriving while Live audio plays waits, never overlaps it, and is spoken once when playback is idle", () => {
    vi.useFakeTimers();
    let since = 0; // Live audio playing
    const a = setup({ msSinceAudio: () => since });
    real(a);
    a.socket.emit(ERR);
    expect(a.spoken()).toEqual([]);
    vi.advanceTimersByTime(2000);
    expect(a.spoken()).toEqual([]); // still playing: nothing over Live audio
    since = SPEAK_WAIT_GAP_MS + 1;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["Sorry, the sale did not save."]);
    vi.advanceTimersByTime(5000);
    expect(a.spoken()).toHaveLength(1);
  });

  it("an error that arrived just after Live audio ended (the old 1 s grace) is spoken", () => {
    vi.useFakeTimers();
    let since = 200;
    const a = setup({ msSinceAudio: () => since });
    real(a);
    a.socket.emit(ERR);
    since = 900;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["Sorry, the sale did not save."]);
  });

  it("the same error repeated within 10 s is spoken once (no loops)", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since });
    real(a);
    a.socket.emit(ERR);
    a.socket.emit(ERR);
    since = Infinity;
    vi.advanceTimersByTime(300);
    a.socket.emit(ERR);
    vi.advanceTimersByTime(SPEAK_DEDUPE_MS - 1000);
    a.socket.emit(ERR);
    expect(a.spoken()).toEqual(["Sorry, the sale did not save."]);
  });

  it("a waiting error is not displaced by a lesser local line", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since });
    real(a);
    a.socket.emit(ERR);
    app.speak("Hold the phone steady");
    since = Infinity;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["Sorry, the sale did not save."]);
  });

  it("the wait is bounded: an error still blocked by Live audio after SPEAK_STALE_MS is dropped, never spoken over it", () => {
    vi.useFakeTimers();
    const a = setup({ msSinceAudio: () => 0 });
    real(a);
    a.socket.emit(ERR);
    vi.advanceTimersByTime(SPEAK_STALE_MS + 1000);
    expect(a.spoken()).toEqual([]);
  });

  it("screen-reader mode: the deferred error goes to the aria-live region, never to speechSynthesis", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since, settings: { screenReaderMode: true } });
    real(a);
    a.socket.emit(ERR);
    expect(a.announced()).toEqual([]);
    since = Infinity;
    vi.advanceTimersByTime(300);
    expect(a.announced()).toEqual(["Sorry, the sale did not save."]);
    expect(a.spoken()).toEqual([]);
  });
});

describe("no double speech: Live audio is the voice", () => {
  it("while AIRA's audio plays or is queued, a local line waits and is spoken once the audio is idle", () => {
    vi.useFakeTimers();
    let since = 0; // 0 = playing / queued
    const a = setup({ msSinceAudio: () => since });
    app.speak("Offline");
    expect(a.spoken()).toEqual([]);
    vi.advanceTimersByTime(1000);
    expect(a.spoken()).toEqual([]);
    since = SPEAK_WAIT_GAP_MS + 1;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["Offline"]);
  });

  it("a line that waited longer than SPEAK_STALE_MS is dropped, never spoken late", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since });
    app.speak("Paused");
    vi.advanceTimersByTime(SPEAK_STALE_MS + 500);
    since = Infinity;
    vi.advanceTimersByTime(500);
    expect(a.spoken()).toEqual([]);
  });

  it("only the newest waiting line is spoken (speechSynthesis would cancel the older one anyway)", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since });
    app.speak("one");
    app.speak("two");
    since = Infinity;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["two"]);
  });

  it("the same sentence twice within 10 s is spoken once; punctuation, case and spaces do not make it new", () => {
    vi.useFakeTimers();
    const a = setup();
    app.speak("Network is back.");
    app.speak("network is back");
    app.speak("  Network  is back!  ");
    expect(a.spoken()).toEqual(["Network is back."]);
    vi.advanceTimersByTime(SPEAK_DEDUPE_MS - 1);
    app.speak("Network is back.");
    expect(a.spoken()).toHaveLength(1);
    vi.advanceTimersByTime(2);
    app.speak("Network is back.");
    expect(a.spoken()).toHaveLength(2);
    expect(SPEAK_DEDUPE_MS).toBe(10_000);
  });

  it("normaliseSpeech trims, lowercases and strips punctuation", () => {
    expect(normaliseSpeech("  Hello, World!  ")).toBe("hello world");
    expect(normaliseSpeech("ரெண்டு தயிர்.")).toBe("ரெண்டு தயிர்");
  });

  it("TalkBack mode: text goes to the aria-live region only, never to speechSynthesis", () => {
    const a = setup({ settings: { screenReaderMode: true } });
    app.speak("Offline");
    a.socket.emit({ t: "error", code: "network", say: "Network lost" });
    expect(a.announced()).toEqual(["Offline", "Network lost"]);
    expect(a.spoken()).toEqual([]);
  });

  it("in a real session the client never speaks a confirm prompt: Live asks it", () => {
    const a = setup();
    a.socket.emit({ t: "ready", sessionId: "s", resume: "s", mock: false });
    a.socket.emit({ t: "confirm", id: "c1", kind: "sale", prompt: "Sell 2 curd for 120 rupees?" });
    expect(a.spoken()).toEqual([]);
    expect(a.w.confirm.pending).toEqual({ id: "c1", kind: "sale" });
    const real = { mock: false, msSinceLiveAudio: 99_999, screenReaderMode: false };
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "sale", prompt: "x" }, real)).toBe(false);
    expect(shouldClientSpeak({ t: "error", code: "network", say: "x" }, real)).toBe(true);
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "sale", prompt: "x" }, { ...real, mock: true })).toBe(true);
  });

  it("barge-in (interrupted) and a talk gesture drop a waiting local line", () => {
    vi.useFakeTimers();
    let since = 0;
    const a = setup({ msSinceAudio: () => since });
    app.speak(STRINGS.offline.en);
    a.socket.emit({ t: "cue", cue: "interrupted" });
    since = Infinity;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual([]);
    since = 0;
    app.speak("later");
    a.w.onTalk();
    since = Infinity;
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual([]);
  });
});

describe("hint loops speak a hint at most once per 8 s", () => {
  it("camera, tilt and show-hand hints all use an 8 s gap", () => {
    expect(HINT_GAP_MS).toBe(8000);
    expect(TILT_HINT_GAP_MS).toBe(8000);
    expect(SHOW_HAND_GAP_MS).toBe(8000);
  });

  it("the throttled hint speaker repeats a hint only after the gap, and a different hint is new", () => {
    let t = 0;
    const said: string[] = [];
    const hint = createHintSpeaker((s) => said.push(s), () => "en", () => t);
    expect(hint("dark")).toBe(true);
    t += 7999;
    expect(hint("dark")).toBe(false);
    expect(hint("blurry")).toBe(true);
    t += 1;
    expect(hint("dark")).toBe(true);
    expect(said).toHaveLength(3);
  });
});
