// Safety lines (the bump guard's "Stop" / "Obstacle left/right") take the urgent speech path: said at once even
// over AIRA's Live audio (Live is cut, then plays on), deduped only within URGENT_DEDUPE_MS, and logged as
// guard_alert. Every other local line keeps the normal path: it waits behind Live audio and dedupes for 10 s.
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import {
  wireApp, type WireEnv, type WiredApp, SPEAK_DEDUPE_MS, URGENT_DEDUPE_MS, URGENT_QUIET_MS,
} from "../src/core/wire";
import { app } from "../src/core/app";
import { __resetClientLog } from "../src/core/clientLog";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { memStore } from "../src/net/offline-queue";

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
  sent: ClientMsg[] = [];
  constructor(public url: string) {}
  connect() {}
  send(m: ClientMsg) { this.sent.push(m); return true; }
  on(h: (m: ServerMsg) => void) { this.hs.add(h); return () => { this.hs.delete(h); }; }
  close() {}
  emit(m: ServerMsg) { this.hs.forEach((h) => h(m)); }
}

const wired: WiredApp[] = [];
beforeEach(() => { vi.useFakeTimers(); __resetClientLog(); });
afterEach(() => {
  while (wired.length) wired.pop()!.dispose();
  vi.useRealTimers();
});

function setup(screenReaderMode = false) {
  let socket!: FakeSocket;
  let since = Infinity; // ms since Live audio last played: 0 = playing now
  const playback = { enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), msSinceAudio: () => since };
  const ctx = { state: "running", resume: () => Promise.resolve(), close: () => Promise.resolve() } as unknown as AudioContext;
  const env: WireEnv = {
    createSocket: (url) => (socket = new FakeSocket(url)) as never,
    createAudioContext: () => ctx,
    createPlayback: () => playback,
    playEarcon: vi.fn(),
    vibrate: vi.fn(), // a no-op: iPhone Safari has no navigator.vibrate
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
    buildInfo: async () => null,
  };
  const w = wireApp({
    settings: { ...DEFAULT_SETTINGS, onboarded: true, lang: "en", screenReaderMode }, wsUrl: "ws://t/ws", env,
  });
  wired.push(w);
  return {
    w, env, playback, ctx,
    live: (playing: boolean) => { since = playing ? 0 : Infinity; },
    get socket() { return socket; },
    spoken: () => (env.speech!.speak as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string),
    alerts: () => socket.sent.flatMap((m) => (m.t === "log" && m.tag === "guard_alert" ? [m.data] : [])),
  };
}

describe("urgent speech path (bump guard)", () => {
  it("a repeated 'Stop' 3 s later is spoken, not silenced by the 10 s dedupe", () => {
    const a = setup();
    app.speakUrgent("Stop", { kind: "bump.stop" });
    vi.advanceTimersByTime(3000);
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.spoken()).toEqual(["Stop", "Stop"]);
    expect(3000).toBeLessThan(SPEAK_DEDUPE_MS); // the normal path would have dropped it
  });

  it("an urgent line interrupts Live audio immediately: Live is cut (not held) and the line is said at once", () => {
    const a = setup();
    a.live(true);
    app.speakUrgent("Stop", { kind: "bump.stop", ageMs: 40 });
    expect(a.playback.flush).toHaveBeenCalledWith({ holdUntilResume: false });
    expect(a.spoken()).toEqual(["Stop"]);
    expect(a.alerts()).toEqual([{ kind: "bump.stop", deduped: false, waitedMs: 40, overLive: true }]);
  });

  it("an identical urgent line within 2 s is said once (logged as deduped); after 2 s it speaks again", () => {
    const a = setup();
    app.speakUrgent("Stop", { kind: "bump.stop" });
    vi.advanceTimersByTime(URGENT_DEDUPE_MS - 500);
    app.speakUrgent("stop!", { kind: "bump.stop" });
    expect(a.spoken()).toEqual(["Stop"]);
    expect(a.alerts().map((d) => d.deduped)).toEqual([false, true]);
    vi.advanceTimersByTime(600);
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.spoken()).toEqual(["Stop", "Stop"]);
  });

  it("a different urgent line is never deduped: the newest wins (speech is cancelled and replaced)", () => {
    const a = setup();
    app.speakUrgent("Obstacle left", { kind: "bump.left_right" });
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.spoken()).toEqual(["Obstacle left", "Stop"]);
    expect(a.env.speech!.cancel).toHaveBeenCalledTimes(2);
  });

  it("normal lines still wait behind Live audio, unchanged", () => {
    const a = setup();
    a.live(true);
    app.speak("Hold the phone steady");
    vi.advanceTimersByTime(2000);
    expect(a.spoken()).toEqual([]);
    a.live(false);
    vi.advanceTimersByTime(300);
    expect(a.spoken()).toEqual(["Hold the phone steady"]);
    expect(a.playback.flush).not.toHaveBeenCalled();
  });

  it("an urgent line cancels a waiting non-urgent line", () => {
    const a = setup();
    a.live(true);
    app.speak("Hold the phone steady");
    app.speakUrgent("Stop", { kind: "bump.stop" });
    a.live(false);
    vi.advanceTimersByTime(3000);
    expect(a.spoken()).toEqual(["Stop"]);
  });

  it("Live audio never overlaps the warning, then resumes normally: its next chunks play after the quiet window", () => {
    const a = setup();
    a.socket.emit({ t: "ready", sessionId: "s", resume: "s", mock: false });
    a.live(true);
    app.speakUrgent("Stop", { kind: "bump.stop" });
    a.live(false);
    a.socket.emit({ t: "audio", pcm24: "AAA=" });
    a.socket.emit({ t: "audio", pcm24: "BBB=" });
    expect(a.playback.enqueue).not.toHaveBeenCalled();
    app.speak("Hold the phone steady");                  // a normal line also waits out the warning
    expect(a.spoken()).toEqual(["Stop"]);
    vi.advanceTimersByTime(URGENT_QUIET_MS);
    expect(a.playback.enqueue.mock.calls.map((c) => c[0])).toEqual(["AAA=", "BBB="]);
    a.socket.emit({ t: "audio", pcm24: "CCC=" });
    expect(a.playback.enqueue).toHaveBeenLastCalledWith("CCC=");
  });

  it("a barge-in hold (long-press stop) stays a hold: the cut answer does not come back", () => {
    const a = setup();
    a.live(true);
    a.w.onStop();
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.playback.flush).toHaveBeenLastCalledWith({ holdUntilResume: true });
  });

  it("no vibration (iPhone): the warn earcon plays on the tap-unlocked AudioContext with the urgent line", () => {
    const a = setup();
    app.haptic("warn");          // a no-op on this platform
    app.earcon("warn");
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.env.playEarcon).toHaveBeenCalledWith(a.ctx, "warn");
    expect(a.spoken()).toEqual(["Stop"]);
  });

  it("guard_alert carries no content, and overLive is false when Live is quiet", () => {
    const a = setup();
    app.speakUrgent("Stop", { kind: "bump.stop", ageMs: 12.4 });
    expect(a.alerts()).toEqual([{ kind: "bump.stop", deduped: false, waitedMs: 12, overLive: false }]);
    expect(Object.keys(a.alerts()[0]).sort()).toEqual(["deduped", "kind", "overLive", "waitedMs"]);
  });

  it("screen-reader mode: the urgent line goes to the live region at once, even over Live audio", () => {
    const a = setup(true);
    a.live(true);
    app.speakUrgent("Stop", { kind: "bump.stop" });
    expect(a.env.announce).toHaveBeenCalledWith("Stop");
    expect(a.spoken()).toEqual([]);
  });
});
