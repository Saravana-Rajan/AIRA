// The earphone button inside the wired app: a headset play/pause key must do exactly what the screen gestures
// do (core/wire onTalk / onStop), only with VITE_AIRA_MEDIA_BUTTON on, and never break anything without it.
import { describe, it, expect, vi, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { wireApp, type WireEnv, type WiredApp } from "../src/core/wire";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { memStore } from "../src/net/offline-queue";
import { HANDSFREE_STRINGS } from "../src/handsfree/strings";
import { STRINGS } from "../src/i18n/strings";
import type { MediaSessionLike } from "../src/handsfree/mediaButton";

class FakeSocket {
  open = true;
  sent: ClientMsg[] = [];
  private hs = new Set<(m: ServerMsg) => void>();
  constructor(public url: string) {}
  connect() {}
  send(m: ClientMsg) { if (!this.open) return false; this.sent.push(m); return true; }
  on(h: (m: ServerMsg) => void) { this.hs.add(h); return () => { this.hs.delete(h); }; }
  close() { this.open = false; }
  emit(m: ServerMsg) { this.hs.forEach((h) => h(m)); }
}

class FakeMediaSession implements MediaSessionLike {
  handlers = new Map<string, (() => void) | null>();
  playbackState?: string = "none";
  metadata: unknown = null;
  setActionHandler(a: string, h: (() => void) | null) { this.handlers.set(a, h); }
  press(a: string) { this.handlers.get(a)?.(); }
}

const wired: WiredApp[] = [];
afterEach(() => { while (wired.length) wired.pop()!.dispose(); vi.unstubAllEnvs(); });
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function setup(o: { flag?: boolean; ms?: MediaSessionLike | null; keepAlive?: boolean } = {}) {
  let socket!: FakeSocket;
  const ms = o.ms === undefined ? new FakeMediaSession() : o.ms;
  const keepAlive = { start: vi.fn(), stop: vi.fn() };
  const env: Partial<WireEnv> = {
    createSocket: (url) => (socket = new FakeSocket(url)) as never,
    createAudioContext: () => ({ resume: () => Promise.resolve(), close: () => Promise.resolve(), state: "running" } as unknown as AudioContext),
    createPlayback: () => ({ enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), msSinceAudio: () => Infinity }),
    playEarcon: vi.fn(),
    vibrate: vi.fn(),
    speech: { speak: vi.fn(), cancel: vi.fn() },
    announce: vi.fn(),
    startMic: vi.fn(async () => ({ stop() {} })),
    queueStore: () => memStore(),
    storage: null,
    win: new EventTarget(),
    doc: Object.assign(new EventTarget(), { hidden: false }),
    requestWakeLock: vi.fn(),
    requestPermissions: vi.fn(),
    languages: ["en-IN"],
    mediaSession: ms,
    mediaKeepAlive: o.keepAlive === false ? undefined : () => keepAlive,
  };
  const w = wireApp({ settings: { ...DEFAULT_SETTINGS, onboarded: true }, wsUrl: "ws://test/ws", env, handsfree: o.flag === undefined ? undefined : { mediaButton: o.flag } });
  wired.push(w);
  return {
    w, env, keepAlive, ms: ms as FakeMediaSession,
    get socket() { return socket; },
    earcons: () => (env.playEarcon as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[1]),
    spoken: () => (env.speech!.speak as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]),
  };
}

describe("wireApp: earphone button (VITE_AIRA_MEDIA_BUTTON)", () => {
  it("off by default: no media-session handlers, no keep-alive, playbackState untouched", () => {
    const a = setup();
    expect(a.ms.handlers.size).toBe(0);
    expect(a.ms.playbackState).toBe("none");
    expect(a.keepAlive.start).not.toHaveBeenCalled();
  });

  it("the build env VITE_AIRA_MEDIA_BUTTON=1 turns it on", () => {
    vi.stubEnv("VITE_AIRA_MEDIA_BUTTON", "1");
    const a = setup();
    expect(a.ms.handlers.get("play")).toBeTypeOf("function");
    expect(a.keepAlive.start).toHaveBeenCalledTimes(1); // inside the Start tap
  });

  it("play = the double-tap: ack earcon, talk event, mic opens; pause = the long-press: stop", async () => {
    const a = setup({ flag: true });
    a.ms.press("play");
    expect(a.earcons()).toContain("ack");
    expect(a.socket.sent).toContainEqual({ t: "event", name: "talk", data: {} });
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    expect(a.ms.playbackState).toBe("playing"); // listening: the same button now means pause
    a.ms.press("pause");
    expect(a.socket.sent).toContainEqual({ t: "stop" });
    expect(a.ms.playbackState).toBe("paused");
  });

  it("with a confirm pending, play answers yes and pause answers no, like the gestures", () => {
    const a = setup({ flag: true });
    a.socket.emit({ t: "confirm", id: "c1", kind: "sale", prompt: "Sell 2 curd?" });
    a.ms.press("play");
    expect(a.socket.sent.filter((m) => m.t === "confirm")).toEqual([{ t: "confirm", id: "c1", answer: "yes" }]);
    expect(a.env.startMic).not.toHaveBeenCalled();
    a.socket.emit({ t: "confirm", id: "c2", kind: "sale", prompt: "Sell 3 curd?" });
    a.ms.press("pause");
    expect(a.socket.sent.filter((m) => m.t === "confirm")).toContainEqual({ t: "confirm", id: "c2", answer: "no" });
  });

  it("the hands-free start marks the session as listening and tells the owner about the button once", async () => {
    const a = setup({ flag: true });
    await a.w.startListening();
    await flush();
    expect(a.ms.playbackState).toBe("playing");
    expect(a.spoken()).toContain(STRINGS.greeting.en);
    expect(a.spoken().filter((s) => s === HANDSFREE_STRINGS.earphoneButton.en)).toHaveLength(1);
    a.w.onStop();
    expect(a.ms.playbackState).toBe("paused");
    await a.w.startListening();
    expect(a.spoken().filter((s) => s === HANDSFREE_STRINGS.earphoneButton.en)).toHaveLength(1);
  });

  it("flag on but no Media Session API: nothing attached, no throw, gestures still work", async () => {
    const a = setup({ flag: true, ms: null });
    a.w.onTalk();
    await flush();
    expect(a.env.startMic).toHaveBeenCalledTimes(1);
    expect(a.spoken()).not.toContain(HANDSFREE_STRINGS.earphoneButton.en);
  });

  it("flag on, API present, no keep-alive factory: handlers still attach", () => {
    const a = setup({ flag: true, keepAlive: false });
    expect(a.ms.handlers.get("play")).toBeTypeOf("function");
  });

  it("a return to the page restarts the keep-alive; dispose removes the handlers and stops it", () => {
    const a = setup({ flag: true });
    (a.env.win as EventTarget).dispatchEvent(new Event("pageshow"));
    expect(a.keepAlive.start).toHaveBeenCalledTimes(2);
    a.w.dispose();
    expect(a.keepAlive.stop).toHaveBeenCalledTimes(1);
    expect(a.ms.handlers.get("play")).toBeNull();
    expect(a.ms.playbackState).toBe("paused");
  });
});
