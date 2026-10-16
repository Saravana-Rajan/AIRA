import { describe, it, expect, vi } from "vitest";
import {
  attachMediaButton, audioKeepAlive, keepAliveWav, KEEPALIVE_PEAK, KEEPALIVE_RATE, KEEPALIVE_SECONDS, type MediaSessionLike,
} from "../src/handsfree/mediaButton";

class FakeMediaSession implements MediaSessionLike {
  handlers = new Map<string, (() => void) | null>();
  playbackState?: string = "none";
  metadata: unknown = null;
  unknownActions = new Set<string>();
  setActionHandler(action: string, handler: (() => void) | null) {
    if (this.unknownActions.has(action)) throw new TypeError(`unknown action ${action}`);
    this.handlers.set(action, handler);
  }
  press(action: string) { this.handlers.get(action)?.(); }
}

function setup(over: { enabled?: boolean; ms?: MediaSessionLike | null } = {}) {
  const ms = over.ms === undefined ? new FakeMediaSession() : over.ms;
  const onTalk = vi.fn();
  const onStop = vi.fn();
  const keepAlive = { start: vi.fn(), stop: vi.fn() };
  const mb = attachMediaButton({ enabled: over.enabled ?? true, mediaSession: ms, onTalk, onStop, keepAlive, metadata: { title: "AIRA" } });
  return { ms: ms as FakeMediaSession, onTalk, onStop, keepAlive, mb };
}

describe("attachMediaButton", () => {
  it("play starts listening (onTalk); pause and stop stop it (onStop)", () => {
    const a = setup();
    expect(a.mb.attached).toBe(true);
    a.ms.press("play");
    expect(a.onTalk).toHaveBeenCalledTimes(1);
    expect(a.onStop).not.toHaveBeenCalled();
    a.ms.press("pause");
    expect(a.onStop).toHaveBeenCalledTimes(1);
    a.ms.press("stop");
    expect(a.onStop).toHaveBeenCalledTimes(2);
    expect(a.onTalk).toHaveBeenCalledTimes(1);
  });

  it("leaves nexttrack / previoustrack to the music app (not registered)", () => {
    const a = setup();
    expect(a.ms.handlers.has("nexttrack")).toBe(false);
    expect(a.ms.handlers.has("previoustrack")).toBe(false);
  });

  it("flag off: no handlers, no keep-alive, nothing touched", () => {
    const a = setup({ enabled: false });
    expect(a.mb.attached).toBe(false);
    expect(a.ms.handlers.size).toBe(0);
    expect(a.ms.playbackState).toBe("none");
    expect(a.keepAlive.start).not.toHaveBeenCalled();
    a.mb.setListening(true);
    a.mb.resume();
    a.mb.dispose(); // all no-ops, none throw
  });

  it("missing API: no throw, not attached", () => {
    expect(() => setup({ ms: null })).not.toThrow();
    expect(setup({ ms: null }).mb.attached).toBe(false);
    expect(attachMediaButton({ enabled: true, mediaSession: undefined, onTalk() {}, onStop() {} }).attached).toBe(false);
    const broken = { setActionHandler: "nope" } as unknown as MediaSessionLike;
    expect(setup({ ms: broken }).mb.attached).toBe(false);
  });

  it("an action the browser does not know is skipped; the known ones still work", () => {
    const ms = new FakeMediaSession();
    ms.unknownActions.add("stop");
    const a = setup({ ms });
    expect(a.mb.attached).toBe(true);
    a.ms.press("play");
    expect(a.onTalk).toHaveBeenCalledTimes(1);
    expect(a.ms.handlers.has("stop")).toBe(false);
  });

  it("starts the keep-alive so the page holds the active media session, and stops it on dispose", () => {
    const a = setup();
    expect(a.keepAlive.start).toHaveBeenCalledTimes(1);
    a.mb.resume();
    expect(a.keepAlive.start).toHaveBeenCalledTimes(2);
    a.mb.dispose();
    expect(a.keepAlive.stop).toHaveBeenCalledTimes(1);
    expect(a.ms.handlers.get("play")).toBeNull();
    expect(a.ms.handlers.get("pause")).toBeNull();
    a.mb.resume(); // after dispose: nothing
    expect(a.keepAlive.start).toHaveBeenCalledTimes(2);
  });

  it("mirrors the listening state so one toggle button alternates talk / stop", () => {
    const a = setup();
    expect(a.ms.playbackState).toBe("paused"); // not listening: the next press is 'play' → talk
    a.mb.setListening(true);
    expect(a.ms.playbackState).toBe("playing"); // listening: the next press is 'pause' → stop
    a.mb.setListening(false);
    expect(a.ms.playbackState).toBe("paused");
  });

  it("sets metadata when MediaMetadata exists, and never throws when it does not", () => {
    class MM { constructor(public init: { title: string }) {} }
    const ms = new FakeMediaSession();
    const mb = attachMediaButton({ enabled: true, mediaSession: ms, onTalk() {}, onStop() {}, metadata: { title: "AIRA" }, MediaMetadata: MM as never });
    expect((ms.metadata as MM).init.title).toBe("AIRA");
    mb.dispose();
    expect(ms.metadata).toBeNull();
    const ms2 = new FakeMediaSession();
    expect(() => attachMediaButton({ enabled: true, mediaSession: ms2, onTalk() {}, onStop() {}, metadata: { title: "AIRA" }, MediaMetadata: undefined })).not.toThrow();
  });

  it("a keep-alive that throws does not stop the handlers", () => {
    const ms = new FakeMediaSession();
    const onTalk = vi.fn();
    const mb = attachMediaButton({ enabled: true, mediaSession: ms, onTalk, onStop() {}, keepAlive: { start() { throw new Error("no audio"); }, stop() { throw new Error("x"); } } });
    expect(mb.attached).toBe(true);
    ms.press("play");
    expect(onTalk).toHaveBeenCalled();
    expect(() => mb.dispose()).not.toThrow();
  });
});

describe("keep-alive clip", () => {
  it("is a 6 s, 8 kHz, 16-bit mono WAV with a quiet (not silent) tone", () => {
    const wav = keepAliveWav();
    const v = new DataView(wav.buffer);
    expect(String.fromCharCode(...wav.subarray(0, 4))).toBe("RIFF");
    expect(String.fromCharCode(...wav.subarray(8, 12))).toBe("WAVE");
    expect(v.getUint16(22, true)).toBe(1); // mono
    expect(v.getUint32(24, true)).toBe(KEEPALIVE_RATE);
    expect(v.getUint16(34, true)).toBe(16);
    const n = KEEPALIVE_SECONDS * KEEPALIVE_RATE;
    expect(v.getUint32(40, true)).toBe(n * 2);
    expect(wav.length).toBe(44 + n * 2);
    let peak = 0;
    for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(v.getInt16(44 + i * 2, true)));
    expect(peak).toBe(KEEPALIVE_PEAK);
    expect(peak).toBeGreaterThan(0);
    expect(KEEPALIVE_SECONDS).toBeGreaterThan(5); // Chrome's media-notification minimum
  });

  it("audioKeepAlive: a looping, unmuted element that is never added to the page; play refusals are swallowed", async () => {
    let el: HTMLAudioElement | null = null;
    const doc = {
      createElement: () => {
        el = document.createElement("audio");
        Object.defineProperty(el, "paused", { get: () => true, configurable: true });
        el.play = () => Promise.reject(new DOMException("NotAllowedError"));
        el.pause = () => {}; // jsdom has no media playback
        el.load = () => {};
        return el;
      },
    };
    const ka = audioKeepAlive(doc, keepAliveWav(1));
    expect(ka).not.toBeNull();
    expect(el!.loop).toBe(true);
    expect(el!.muted).toBe(false);
    expect(el!.src).toMatch(/^(blob:|data:audio\/wav)/);
    expect(el!.isConnected).toBe(false);
    expect(() => ka!.start()).not.toThrow();
    await Promise.resolve();
    expect(() => ka!.stop()).not.toThrow();
    expect(audioKeepAlive({ createElement: () => { throw new Error("no DOM"); } })).toBeNull();
  });
});
