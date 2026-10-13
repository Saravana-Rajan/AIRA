import { describe, it, expect, vi, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { wireApp, type WireEnv, type WiredApp, MIC_IDLE_MS, MIC_WATCHDOG_MS, THINKING_LOOP_MS, MIC_MAX_BUFFERED_BYTES } from "../src/core/wire";
import type { PlaybackOpts } from "../src/voice/playback";
import { app } from "../src/core/app";
import { DEFAULT_SETTINGS, SETTINGS_KEY } from "../src/core/settings";
import type { Settings } from "../src/core/types";
import { memStore } from "../src/net/offline-queue";
import { STRINGS } from "../src/i18n/strings";
import {
  BACKGROUND_MAX_BUFFERED, LIVE_MAX_BUFFERED, TOOL_SEND_BUFFERED, __resetSendPriority, micBackedUp, micDropCount,
} from "../src/net/sendPriority";

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
  open = true;
  closed = false;
  hello: Extract<ClientMsg, { t: "hello" }> | null = null;
  sent: ClientMsg[] = [];
  buffered = 0;
  private hs = new Set<(m: ServerMsg) => void>();
  constructor(public url: string) {}
  bufferedAmount() { return this.buffered; }
  connect(h: Extract<ClientMsg, { t: "hello" }>) { this.hello = h; }
  send(m: ClientMsg) { if (!this.open) return false; this.sent.push(m); return true; }
  on(h: (m: ServerMsg) => void) { this.hs.add(h); return () => { this.hs.delete(h); }; }
  close() { this.closed = true; this.open = false; }
  emit(m: ServerMsg) { this.hs.forEach((h) => h(m)); }
  get handlers() { return this.hs.size; }
}

function setup(over: { settings?: Partial<Settings>; env?: Partial<WireEnv>; connect?: boolean; storage?: Storage } = {}) {
  const storage = over.storage ?? new MemStorage();
  let socket!: FakeSocket;
  const mic = { stop: vi.fn() };
  let onChunk: ((b64: string) => void) | null = null;
  let onLevel: ((l: number) => void) | null = null;
  const playback = { enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), msSinceAudio: vi.fn(() => Infinity) };
  let playbackOpts: PlaybackOpts = {};
  const win = new EventTarget();
  const doc = Object.assign(new EventTarget(), { hidden: false });
  const ctx = { resume: vi.fn(() => Promise.resolve()), close: vi.fn(() => Promise.resolve()) } as unknown as AudioContext;
  const env: WireEnv = {
    createSocket: (url) => (socket = new FakeSocket(url)) as never,
    createAudioContext: () => ctx,
    createPlayback: (_ctx, opts) => { playbackOpts = opts ?? {}; return playback; },
    playEarcon: vi.fn(),
    vibrate: vi.fn(),
    speech: { speak: vi.fn(), cancel: vi.fn() },
    announce: vi.fn(),
    startMic: vi.fn(async (o) => { onChunk = o.onChunk; onLevel = o.onLevel ?? null; return mic; }),
    queueStore: () => memStore(),
    storage,
    win,
    doc,
    requestWakeLock: vi.fn(),
    requestPermissions: vi.fn(),
    languages: ["en-IN"],
    ...over.env,
  };
  const w = wireApp({
    settings: { ...DEFAULT_SETTINGS, onboarded: true, ...over.settings },
    wsUrl: "ws://test/ws",
    connect: over.connect,
    env,
  });
  wired.push(w);
  return {
    w, env, storage, playback, mic, win, doc, ctx,
    get socket() { return socket; },
    get playbackOpts() { return playbackOpts; },
    chunk: (b64: string) => onChunk?.(b64),
    level: (l: number) => onLevel?.(l),
    earcons: () => (env.playEarcon as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[1]),
    haptics: () => (env.vibrate as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]),
    spoken: () => (env.speech!.speak as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]),
  };
}

const wired: WiredApp[] = [];
afterEach(() => {
  while (wired.length) wired.pop()!.dispose();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  __resetSendPriority();
});
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const CONFIRM: ServerMsg = { t: "confirm", id: "c1", kind: "sale", prompt: "Sell 2 Sakthi curd for 120 rupees?" };

describe("wireApp: connection", () => {
  it("says hello for the demo shop with a stable device uid, and no idToken when no app key is built in", () => {
    vi.stubEnv("VITE_AIRA_APP_KEY", "");
    const storage = new MemStorage();
    const a = setup({ storage, settings: { lang: "ta" } });
    const h = a.socket.hello!;
    expect(h).toMatchObject({ t: "hello", shopId: "sunrise_dairy", lang: "ta", verbosity: "short" });
    expect("idToken" in h).toBe(false);
    expect(h.uid).toMatch(/^owner-/);
    expect(a.socket.url).toBe("ws://test/ws");
    const b = setup({ storage });
    expect(b.socket.hello!.uid).toBe(h.uid);
  });

  it("sends the build-time app key VITE_AIRA_APP_KEY as hello.idToken (the shared demo key, not user auth)", () => {
    vi.stubEnv("VITE_AIRA_APP_KEY", "k-demo-key");
    const a = setup({ storage: new MemStorage() });
    expect(a.socket.hello!.idToken).toBe("k-demo-key");
  });

  it("saves the settings chosen at Start", () => {
    const a = setup({ settings: { lang: "hi", screenReaderMode: true } });
    expect(JSON.parse(a.storage.getItem(SETTINGS_KEY)!)).toMatchObject({ lang: "hi", screenReaderMode: true, onboarded: true });
  });

  it("does all unlock work inside the Start tap: audio, wake lock and permissions", () => {
    const a = setup();
    expect(a.ctx.resume).toHaveBeenCalled();
    expect(a.env.requestWakeLock).toHaveBeenCalledTimes(1);
    expect(a.env.requestPermissions).toHaveBeenCalledTimes(1);
  });

  it("connect:false wires everything but sends no hello (the demo page connects itself)", () => {
    const a = setup({ connect: false });
    expect(a.socket.hello).toBeNull();
    expect(app.socket).toBe(a.w.socket);
  });
});

describe("wireApp: mic stays available through a hands-free conversation", () => {
  it("keeps the mic open while the owner is speaking, even with no server transcript (no idle self-lock)", async () => {
    vi.useFakeTimers();
    const a = setup();
    await a.w.startListening();
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    // The owner keeps talking past the 30 s idle window; Live sends NO user transcript (the bug's trigger).
    for (let t = 0; t < MIC_IDLE_MS + 10_000; t += 1_000) {
      a.level(0.2); // onLevel: audible speech energy
      vi.advanceTimersByTime(1_000);
      await flush();
    }
    expect(a.mic.stop).not.toHaveBeenCalled(); // current code FAILS here: mic idle-closes 30 s after the last transcript
  });

  it("reopens the mic if it ever closes while a listening session is live (watchdog)", async () => {
    vi.useFakeTimers();
    const a = setup();
    await a.w.startListening();
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    // Total silence (no speech energy to re-arm): the idle timer closes the mic...
    vi.advanceTimersByTime(MIC_IDLE_MS + 100);
    await flush();
    expect(a.mic.stop).toHaveBeenCalledTimes(1);
    // ...and the watchdog reopens it, so the owner can still be heard without tapping.
    vi.advanceTimersByTime(MIC_WATCHDOG_MS + 100);
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(2);
  });

  it("after an explicit stop the watchdog does not reopen the mic", async () => {
    vi.useFakeTimers();
    const a = setup();
    await a.w.startListening();
    await flush();
    a.w.onStop();
    await flush();
    const opens = (a.env.startMic as ReturnType<typeof vi.fn>).mock.calls.length;
    vi.advanceTimersByTime((MIC_WATCHDOG_MS + MIC_IDLE_MS) * 2);
    await flush();
    expect((a.env.startMic as ReturnType<typeof vi.fn>).mock.calls.length).toBe(opens);
  });
});

describe("wireApp: language follows the speaker", () => {
  it("with no fixed language, the hello carries the phone's locale as a hint and the labels start there", () => {
    const a = setup({ settings: { lang: "auto" }, env: { languages: ["ta-IN", "en-US"] } });
    expect(a.socket.hello!.lang).toBe("ta");
    expect(a.w.view.get().lang).toBe("ta");
    expect(app.lang()).toBe("ta");
    const b = setup({ settings: { lang: "auto" }, env: { languages: ["ja-JP"] } });
    expect(b.socket.hello!.lang).toBe("en");
  });

  it("switches the labels and the local TTS to whatever language the server last heard", () => {
    const a = setup({ settings: { lang: "auto" }, env: { languages: ["en-IN"] } });
    const seen: string[] = [];
    a.w.view.subscribe((v) => { seen.push(v.lang); });
    a.socket.emit({ t: "transcript", role: "user", text: "दो दही", final: true, lang: "hi" });
    expect(a.w.view.get().lang).toBe("hi");
    expect(app.lang()).toBe("hi");
    a.win.dispatchEvent(new Event("offline"));
    expect(a.spoken()).toContain(STRINGS.offline.hi);
    expect((a.env.speech!.speak as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1]).toBe("hi");
    a.socket.emit({ t: "transcript", role: "user", text: "ரெண்டு", final: true, lang: "ta" });
    expect(a.w.view.get().lang).toBe("ta");
    a.socket.emit({ t: "transcript", role: "user", text: "two", final: false }); // a partial carries no lang
    expect(a.w.view.get().lang).toBe("ta");
    a.socket.emit({ t: "transcript", role: "user", text: "ヨーグルト", final: true, lang: "ja" });
    expect(a.w.view.get().lang).toBe("en"); // no Japanese labels: English ones, never blank
    expect(seen).toEqual(expect.arrayContaining(["hi", "ta", "en"]));
  });

  it("the language is never saved as a choice", () => {
    const a = setup({ settings: { lang: "auto" }, env: { languages: ["hi-IN"] } });
    a.socket.emit({ t: "transcript", role: "user", text: "ரெண்டு", final: true, lang: "ta" });
    expect(JSON.parse(a.storage.getItem(SETTINGS_KEY)!).lang).toBe("auto");
  });
});

describe("wireApp: the app singleton", () => {
  it("fills app: send goes to the socket, speak uses TTS in the chosen language", () => {
    const a = setup({ settings: { lang: "hi" } });
    expect(app.send({ t: "stop" })).toBe(true);
    expect(a.socket.sent).toEqual([{ t: "stop" }]);
    app.speak("namaste");
    expect(a.env.speech!.speak).toHaveBeenCalledWith("namaste", "hi", 1);
    expect(a.env.announce).not.toHaveBeenCalled();
  });

  it("in screen-reader mode speak writes to the aria-live region and never uses TTS", () => {
    const a = setup({ settings: { screenReaderMode: true } });
    app.speak("Offline");
    expect(a.env.announce).toHaveBeenCalledWith("Offline");
    expect(a.env.speech!.speak).not.toHaveBeenCalled();
  });
});

describe("wireApp: gestures", () => {
  it("double-tap acknowledges in the same tick and opens the mic only once", async () => {
    const a = setup();
    a.w.onTalk();
    expect(a.earcons()).toContain("ack");
    expect(a.haptics()).toContain("ack");
    a.w.onTalk(); // second double-tap while the mic is still opening
    await flush();
    a.w.onTalk();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    a.chunk("AAAA");
    expect(a.socket.sent).toContainEqual({ t: "audio", pcm16: "AAAA" });
  });

  it("double-tap while AIRA speaks cuts her off so the owner can talk", () => {
    const a = setup();
    a.w.onTalk();
    expect(a.playback.flush).toHaveBeenCalledWith({ holdUntilResume: true });
    expect(a.env.speech!.cancel).toHaveBeenCalled();
  });

  it("double-tap with a pending confirm sends yes exactly once and does not start a talk turn", async () => {
    const a = setup();
    a.socket.emit(CONFIRM);
    a.w.onTalk();
    expect(a.socket.sent.filter((m) => m.t === "confirm")).toEqual([{ t: "confirm", id: "c1", answer: "yes" }]);
    expect(a.env.startMic).not.toHaveBeenCalled();
    a.w.onTalk(); // answered already: this one is a normal talk
    await flush();
    expect(a.socket.sent.filter((m) => m.t === "confirm")).toHaveLength(1);
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
  });

  it("long-press with a pending confirm sends no and is not a hard stop", () => {
    const a = setup();
    a.socket.emit(CONFIRM);
    a.w.onStop();
    expect(a.socket.sent).toEqual([{ t: "confirm", id: "c1", answer: "no" }]);
  });

  it("a confirm answer that cannot be sent stays pending, is never queued, and says so", async () => {
    const a = setup();
    a.socket.emit(CONFIRM);
    a.socket.open = false;
    a.w.onTalk();
    expect(a.socket.sent).toEqual([]);
    expect(await a.w.queue.pending()).toBe(0);
    expect(a.spoken()).toContain(STRINGS.notSent.en);
    expect(a.w.confirm.pending).toEqual({ id: "c1", kind: "sale" });
    expect(a.env.startMic).not.toHaveBeenCalled();
    a.socket.open = true;
    a.w.onTalk();
    expect(a.socket.sent).toEqual([{ t: "confirm", id: "c1", answer: "yes" }]);
  });

  it("long-press is a hard stop: flush audio, cancel speech, close the mic, tell the server", async () => {
    const a = setup();
    a.w.onTalk();
    await flush();
    a.playback.flush.mockClear();
    a.w.onStop();
    expect(a.playback.flush).toHaveBeenCalledWith({ holdUntilResume: true });
    expect(a.env.speech!.cancel).toHaveBeenCalled();
    expect(a.mic.stop).toHaveBeenCalled();
    expect(a.socket.sent).toContainEqual({ t: "stop" });
    expect(a.haptics()).toContain("done");
  });

  it("a stop while the mic is still opening closes it as soon as it opens", async () => {
    const a = setup();
    a.w.onTalk();
    a.w.onStop();
    await flush();
    expect(a.mic.stop).toHaveBeenCalled();
  });

  it("a blocked mic warns and says how to fix it", async () => {
    const err = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    const a = setup({ env: { startMic: vi.fn(async () => { throw err; }) } });
    a.w.onTalk();
    await flush();
    expect(a.spoken()).toContain(STRINGS.micBlocked.en);
    expect(a.earcons()).toContain("warn");
  });

  it("closes the mic after the idle window, and a new talk reopens it", async () => {
    vi.useFakeTimers();
    const a = setup();
    a.w.onTalk();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(MIC_IDLE_MS - 10);
    a.socket.emit({ t: "transcript", role: "user", text: "two curd", final: false }); // still talking
    await vi.advanceTimersByTimeAsync(MIC_IDLE_MS - 10);
    expect(a.mic.stop).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(20);
    expect(a.mic.stop).toHaveBeenCalledTimes(1);
    a.w.onTalk();
    await vi.advanceTimersByTimeAsync(0);
    expect(a.env.startMic).toHaveBeenCalledTimes(2);
  });
});

describe("wireApp: hands-free start (startListening)", () => {
  it("opens the mic with no tap, then plays the listening earcon and greets", async () => {
    const a = setup({ settings: { lang: "ta" } });
    const ok = await a.w.startListening();
    expect(ok).toBe(true);
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    expect(a.earcons()).toContain("listening");
    expect(a.spoken()).toContain(STRINGS.greeting.ta);
    expect(a.w.view.get().screen.status.kind).toBe("listening");
    a.chunk("AAAA");
    expect(a.socket.sent).toContainEqual({ t: "audio", pcm16: "AAAA" });
    expect(a.ctx.resume).toHaveBeenCalledTimes(2); // once at wiring, again once the mic stream unlocked audio
  });

  it("resolves false, quietly, when getUserMedia rejects (no gesture yet): the caller shows Start", async () => {
    const err = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    const a = setup({ env: { startMic: vi.fn(async () => { throw err; }) } });
    expect(await a.w.startListening()).toBe(false);
    expect(a.spoken()).toEqual([]); // not "Microphone is blocked": the owner only has to tap
    expect(a.earcons()).not.toContain("warn");
    expect(a.earcons()).not.toContain("listening");
    expect(a.w.view.get().screen.status.kind).not.toBe("listening");
  });

  it("does not start the thinking sound: nobody has asked anything yet", async () => {
    vi.useFakeTimers();
    const a = setup();
    await a.w.startListening();
    await vi.advanceTimersByTimeAsync(3000);
    expect(a.earcons()).not.toContain("thinking");
  });

  it("a later double-tap reuses the open mic", async () => {
    const a = setup();
    await a.w.startListening();
    a.w.onTalk();
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
  });
});

describe("wireApp: audio latency", () => {
  it("drops a mic chunk instead of queuing it behind a stalled socket (old audio never piles up)", async () => {
    const a = setup();
    a.w.onTalk();
    await flush();
    a.chunk("AAAA");
    expect(a.socket.sent.filter((m) => m.t === "audio")).toHaveLength(1);
    a.socket.buffered = MIC_MAX_BUFFERED_BYTES + 1;
    a.chunk("BBBB");
    a.chunk("CCCC");
    expect(a.socket.sent.filter((m) => m.t === "audio")).toHaveLength(1);
    a.socket.buffered = 0;
    a.chunk("DDDD");
    expect(a.socket.sent.filter((m) => m.t === "audio").map((m) => (m as { pcm16: string }).pcm16)).toEqual(["AAAA", "DDDD"]);
    expect(MIC_MAX_BUFFERED_BYTES).toBeLessThanOrEqual(160_000); // a few seconds of speech at most on a dead link
  });

  it("keeps sending mic audio at backlogs where every camera stream already stops (mic goes first)", async () => {
    const a = setup();
    a.w.onTalk();
    await flush();
    // A live frame and a count photo still leaving the phone: video is skipped at these levels, speech is not.
    for (const b of [BACKGROUND_MAX_BUFFERED + 1, LIVE_MAX_BUFFERED + 1, TOOL_SEND_BUFFERED + 1, 110_000]) {
      a.socket.buffered = b;
      a.chunk(`A${b}`);
    }
    expect(a.socket.sent.filter((m) => m.t === "audio")).toHaveLength(4);
    // ...and the mic being backed up makes the video yield for a while.
    expect(micBackedUp()).toBe(true);
  });

  it("logs mic drops with a running count", async () => {
    const t0 = Date.now() + 600_000; // past any earlier test's rate-limited mic_drop line
    vi.useFakeTimers();
    vi.setSystemTime(t0);
    const a = setup();
    a.w.onTalk();
    await flush();
    a.socket.buffered = MIC_MAX_BUFFERED_BYTES + 1;
    a.chunk("X");
    a.chunk("Y");
    const logs = a.socket.sent.filter((m) => m.t === "log" && (m as { tag: string }).tag === "mic_drop") as unknown as Array<{ data: { drops: number } }>;
    expect(logs).toHaveLength(1); // rate-limited
    expect(logs[0].data.drops).toBeGreaterThanOrEqual(1);
    expect(micDropCount()).toBeGreaterThanOrEqual(2);
  });

  it("logs one latency line when AIRA's answer starts playing, with the ms since the user finished", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const a = setup();
    a.w.onTalk();
    await flush();
    a.chunk("AAAA");
    vi.setSystemTime(10_300);
    a.socket.emit({ t: "transcript", role: "user", text: "two curd", final: true });
    vi.setSystemTime(11_100);
    a.socket.emit({ t: "audio", pcm24: "AAAA" });
    a.playbackOpts.onPlayStart?.({ msSinceFirstChunk: 62, leadMs: 60 });
    const logs = a.socket.sent.filter((m) => m.t === "log" && m.tag === "latency");
    expect(logs).toHaveLength(1);
    expect(logs[0]).toMatchObject({ t: "log", tag: "latency", data: { stage: "play_start", ms: 62, sinceUserFinalMs: 800, sinceMicMs: 1100 } });
  });
});

describe("wireApp: server messages", () => {
  it("cue interrupted flushes AIRA's audio and holds late chunks", () => {
    const a = setup();
    a.socket.emit({ t: "audio", pcm24: "AAAA" });
    expect(a.playback.enqueue).toHaveBeenCalledWith("AAAA");
    a.socket.emit({ t: "cue", cue: "interrupted" });
    expect(a.playback.flush).toHaveBeenCalledWith({ holdUntilResume: true });
  });

  it("thinking sound starts 1.5 s after a talk, repeats, and stops when AIRA answers", async () => {
    vi.useFakeTimers();
    const a = setup();
    a.w.onTalk();
    const thinking = () => a.earcons().filter((n) => n === "thinking").length;
    await vi.advanceTimersByTimeAsync(1499);
    expect(thinking()).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(thinking()).toBe(1);
    await vi.advanceTimersByTimeAsync(THINKING_LOOP_MS);
    expect(thinking()).toBe(2);
    a.socket.emit({ t: "audio", pcm24: "AAAA" });
    await vi.advanceTimersByTimeAsync(THINKING_LOOP_MS * 3);
    expect(thinking()).toBe(2);
  });

  it("the thinking timer waits while the owner is still speaking and re-arms when he finishes", async () => {
    vi.useFakeTimers();
    const a = setup();
    a.w.onTalk();
    const thinking = () => a.earcons().filter((n) => n === "thinking").length;
    await vi.advanceTimersByTimeAsync(1000);
    a.socket.emit({ t: "transcript", role: "user", text: "two Sakthi", final: false });
    await vi.advanceTimersByTimeAsync(2000);
    expect(thinking()).toBe(0);
    a.socket.emit({ t: "transcript", role: "user", text: "two Sakthi curd", final: true });
    await vi.advanceTimersByTimeAsync(1500);
    expect(thinking()).toBe(1);
  });

  it("speaks a confirm prompt itself only in a mock session (no Live audio)", () => {
    const a = setup();
    a.socket.emit({ t: "ready", sessionId: "s", resume: "r", mock: true });
    a.socket.emit(CONFIRM);
    expect(a.spoken()).toContain(CONFIRM.t === "confirm" ? CONFIRM.prompt : "");
  });
});

describe("wireApp: offline queue", () => {
  it("ops made offline are kept and replayed in order on ready, exactly once", async () => {
    const a = setup();
    a.socket.open = false;
    expect(await a.w.sendOrQueue({ t: "text", text: "sale 1" }, "k1")).toBe(false);
    expect(await a.w.sendOrQueue({ t: "text", text: "sale 2" }, "k2")).toBe(false);
    expect(await a.w.sendOrQueue({ t: "text", text: "sale 1" }, "k1")).toBe(false); // retry of the same op
    a.socket.open = true;
    a.socket.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    a.socket.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    await a.w.replay();
    expect(a.socket.sent.filter((m) => m.t === "text")).toEqual([
      { t: "text", text: "sale 1" },
      { t: "text", text: "sale 2" },
    ]);
    expect(await a.w.queue.pending()).toBe(0);
  });

  it("sendOrQueue sends at once when online", async () => {
    const a = setup();
    expect(await a.w.sendOrQueue({ t: "text", text: "hi" }, "k1")).toBe(true);
    expect(a.socket.sent).toEqual([{ t: "text", text: "hi" }]);
  });

  it("the browser's online event replays the queue too", async () => {
    const a = setup();
    a.socket.open = false;
    await a.w.sendOrQueue({ t: "text", text: "sale 1" }, "k1");
    a.socket.open = true;
    a.win.dispatchEvent(new Event("online"));
    await a.w.replay();
    // "Back online." is spoken locally, which also sends a "speak" diagnostic line; the queue itself is one text
    expect(a.socket.sent.filter((m) => m.t !== "log")).toEqual([{ t: "text", text: "sale 1" }]);
  });
});

describe("wireApp: phone status", () => {
  it("going offline plays the offline earcon, says so in the owner's language, and shows Offline", () => {
    const a = setup({ settings: { lang: "ta" } });
    a.win.dispatchEvent(new Event("offline"));
    expect(a.earcons()).toContain("offline");
    expect(a.spoken()).toContain(STRINGS.offline.ta);
    expect(a.w.view.get().online).toBe(false);
    expect(a.w.view.get().screen.status.kind).toBe("warning");
    a.win.dispatchEvent(new Event("online"));
    expect(a.spoken()).toContain(STRINGS.online.ta);
    expect(a.w.view.get().online).toBe(true);
  });

  it("tells the server when the app is hidden and re-takes the wake lock when visible", () => {
    const a = setup();
    a.doc.hidden = true;
    a.doc.dispatchEvent(new Event("visibilitychange"));
    expect(a.socket.sent).toContainEqual({ t: "event", name: "visibility", data: { hidden: true } });
    expect(a.spoken()).toContain(STRINGS.paused.en);
    a.doc.hidden = false;
    a.doc.dispatchEvent(new Event("visibilitychange"));
    expect(a.socket.sent).toContainEqual({ t: "event", name: "visibility", data: { hidden: false } });
    expect(a.env.requestWakeLock).toHaveBeenCalledTimes(2);
  });
});

describe("wireApp: view", () => {
  it("tracks the screen, the live count, the pending confirm and the mic level", async () => {
    const a = setup();
    const seen: unknown[] = [];
    const off = a.w.view.subscribe((v) => seen.push(v));
    a.socket.emit({ t: "ready", sessionId: "s", resume: "r", mock: false });
    expect(a.w.view.get().screen.status.kind).toBe("ready");
    a.socket.emit({ t: "count", sku: "sakthi_curd_1l", counted: 12, expected: 30, final: false, confidence: 0.9 });
    expect(a.w.view.get().progress).toEqual({ counted: 12, expected: 30 });
    expect(a.w.view.get().screen.status.kind).toBe("counting");
    a.socket.emit(CONFIRM);
    expect(a.w.view.get().confirmPending).toBe(true);
    expect(a.w.view.get().progress).toBeNull();
    a.w.onTalk();
    expect(a.w.view.get().confirmPending).toBe(false);
    a.w.onTalk();
    await flush();
    a.level(0.5);
    expect(a.w.view.get().level).toBe(0.5);
    expect(seen.length).toBeGreaterThan(3);
    off();
  });

  it("local events from other modules (bump guard) reach the screen", () => {
    const a = setup();
    a.w.local("bump");
    expect(a.w.view.get().screen.status.kind).toBe("stop");
    a.w.local("bump_clear");
    expect(a.w.view.get().screen.status.kind).toBe("ready");
  });
});

describe("wireApp: dispose", () => {
  it("removes every listener, closes the socket and the mic", async () => {
    const a = setup();
    a.w.onTalk();
    await flush();
    a.w.dispose();
    expect(a.socket.closed).toBe(true);
    expect(a.socket.handlers).toBe(0);
    expect(a.mic.stop).toHaveBeenCalled();
    a.win.dispatchEvent(new Event("offline"));
    expect(a.spoken()).not.toContain(STRINGS.offline.en);
  });
});
