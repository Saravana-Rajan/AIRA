import { describe, it, expect, vi } from "vitest";
import { shouldClientSpeak, textFor } from "../src/core/announce";
import { ConfirmState } from "../src/core/confirm";
import { createDispatcher } from "../src/core/dispatch";
import { STRINGS, LABELS } from "../src/i18n/strings";

const live = { mock: false, msSinceLiveAudio: 200, screenReaderMode: false };

describe("announcer", () => {
  it("does not speak confirm/error when Live audio just played (no double speech)", () => {
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "order", prompt: "Order 30 L?" }, live)).toBe(false);
    expect(shouldClientSpeak({ t: "error", code: "network", say: "Network lost" }, live)).toBe(false);
  });
  it("speaks an error when no Live audio for >1 s, never a confirm (Live asks it), and everything in mock sessions", () => {
    const quiet = { ...live, msSinceLiveAudio: 1500 };
    expect(shouldClientSpeak({ t: "error", code: "network", say: "Network lost" }, quiet)).toBe(true);
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "order", prompt: "Order?" }, quiet)).toBe(false);
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "order", prompt: "Order?" }, { ...quiet, mock: true })).toBe(true);
    expect(shouldClientSpeak({ t: "state", entity: "sale", id: "s1", state: "SALE_COMPLETED", summary: "Sale done" }, { ...live, mock: true })).toBe(true);
  });
  it("never speaks raw audio/transcript messages", () => {
    expect(shouldClientSpeak({ t: "transcript", role: "aira", text: "hi", final: true }, { ...live, mock: true })).toBe(false);
  });
  it("textFor picks prompt/say/summary and trims long summaries to 120 chars", () => {
    expect(textFor({ t: "confirm", id: "c", kind: "payment", prompt: "Pay 1000?" })).toBe("Pay 1000?");
    expect(textFor({ t: "error", code: "camera", say: "Camera blocked" })).toBe("Camera blocked");
    const long = "x".repeat(300);
    expect(textFor({ t: "state", entity: "order", id: "o", state: "ORDER_PLACED", summary: long })!.length).toBe(120);
  });
  it("has every local status string in ta, hi and en", () => {
    for (const v of Object.values(STRINGS)) expect(Object.keys(v).sort()).toEqual(["en", "hi", "ta"]);
  });

  // Beyond the plan.
  it("never reads an OPEN_URL hand-off aloud (the link opener speaks for it)", () => {
    const m = { t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "OPEN_URL https://wa.me/919800000000?text=hi" } as const;
    expect(textFor(m)).toBeNull();
    expect(shouldClientSpeak(m, { ...live, mock: true })).toBe(false);
  });
  it("is silent at exactly 1000 ms since Live audio and speaks after it", () => {
    const m = { t: "error", code: "internal", say: "Sorry" } as const;
    expect(shouldClientSpeak(m, { ...live, msSinceLiveAudio: 1000 })).toBe(false);
    expect(shouldClientSpeak(m, { ...live, msSinceLiveAudio: 1001 })).toBe(true);
  });
  it("leaves state narration to Live in real sessions", () => {
    const m = { t: "state", entity: "sale", id: "s1", state: "SALE_COMPLETED", summary: "Sale done" } as const;
    expect(shouldClientSpeak(m, { ...live, msSinceLiveAudio: 9999 })).toBe(false);
  });
  it("has every screen label in ta, hi and en with no empty strings", () => {
    for (const v of [...Object.values(STRINGS), ...Object.values(LABELS)]) {
      expect(Object.keys(v).sort()).toEqual(["en", "hi", "ta"]);
      for (const s of Object.values(v)) expect(s.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("ConfirmState gestures", () => {
  it("double-tap answers yes exactly once, long-press answers no", () => {
    const c = new ConfirmState();
    c.set("c9", "payment");
    expect(c.onGesture("double")).toEqual({ t: "confirm", id: "c9", answer: "yes" });
    expect(c.onGesture("double")).toBeNull(); // cleared — must not send twice
    c.set("c10", "order");
    expect(c.onGesture("long")).toEqual({ t: "confirm", id: "c10", answer: "no" });
  });
  it("returns null when nothing is pending (caller treats it as talk/stop)", () => {
    expect(new ConfirmState().onGesture("double")).toBeNull();
  });

  // Beyond the plan.
  it("a newer confirm replaces the older one; only the newest id is answered", () => {
    const c = new ConfirmState();
    c.set("old", "order");
    c.set("new", "sale");
    expect(c.onGesture("double")).toEqual({ t: "confirm", id: "new", answer: "yes" });
    expect(c.onGesture("double")).toBeNull();
  });
  it("expires with the server's 120 s proposal TTL so a later double-tap talks again", () => {
    let now = 1_000;
    const c = new ConfirmState(() => now);
    c.set("c1", "payment");
    now += 120_000;
    expect(c.pending).toEqual({ id: "c1", kind: "payment" });
    now += 1;
    expect(c.pending).toBeNull();
    expect(c.onGesture("double")).toBeNull();
  });
  it("clear() drops the pending confirm", () => {
    const c = new ConfirmState();
    c.set("c1", "order");
    c.clear();
    expect(c.onGesture("long")).toBeNull();
  });
});

describe("dispatcher", () => {
  function deps() {
    return {
      playback: { enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), lastAudioAt: 0 },
      thinking: { disarm: vi.fn() },
      earcon: vi.fn(), haptic: vi.fn(), speak: vi.fn(),
      confirm: new ConfirmState(),
      ctx: () => ({ mock: false, msSinceLiveAudio: 5000, screenReaderMode: false }),
    };
  }
  it("interrupted flushes playback with hold, and the next user transcript resumes", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "cue", cue: "interrupted" });
    expect(d.playback.flush).toHaveBeenCalledWith({ holdUntilResume: true });
    dispatch({ t: "transcript", role: "user", text: "wait", final: false });
    expect(d.playback.resume).toHaveBeenCalled();
  });
  it("every server reply disarms the thinking timer", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "audio", pcm24: "AAAA" });
    dispatch({ t: "transcript", role: "aira", text: "ok", final: true });
    dispatch({ t: "cue", cue: "done" });
    dispatch({ t: "error", code: "internal", say: "Sorry" });
    expect(d.thinking.disarm).toHaveBeenCalledTimes(4);
  });
  it("confirm sets pending state and plays the warn earcon; the prompt is spoken only in a mock session", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "confirm", id: "c1", kind: "order", prompt: "Order 30 litres Sakthi curd?" });
    expect(d.confirm.pending).toEqual({ id: "c1", kind: "order" });
    expect(d.earcon).toHaveBeenCalledWith("warn");
    expect(d.speak).not.toHaveBeenCalled(); // Live asks the question itself (prompt rule 1)
    const m = { ...deps(), ctx: () => ({ mock: true, msSinceLiveAudio: Infinity, screenReaderMode: false }) };
    createDispatcher(m as any)({ t: "confirm", id: "c2", kind: "order", prompt: "Order 30 litres Sakthi curd?" });
    expect(m.speak).toHaveBeenCalledWith("Order 30 litres Sakthi curd?");
  });
  it("match/mismatch cues also fire matching haptics", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "cue", cue: "mismatch" });
    expect(d.earcon).toHaveBeenCalledWith("mismatch");
    expect(d.haptic).toHaveBeenCalledWith("mismatch");
  });

  // Beyond the plan.
  it("barge-in: audio arriving in the same tick after 'interrupted' never reaches the speaker", () => {
    const played: string[] = [];
    let held = false;
    const playback = {
      enqueue: (b64: string) => { if (!held) played.push(b64); },
      flush: (o?: { holdUntilResume?: boolean }) => { held = !!o?.holdUntilResume; },
      resume: () => { held = false; },
    };
    const dispatch = createDispatcher({ ...deps(), playback } as any);
    dispatch({ t: "audio", pcm24: "old1" });
    dispatch({ t: "cue", cue: "interrupted" });
    dispatch({ t: "audio", pcm24: "old2" }); // late chunk of the interrupted turn
    expect(played).toEqual(["old1"]);
    dispatch({ t: "transcript", role: "user", text: "stop", final: true });
    dispatch({ t: "audio", pcm24: "new1" });
    expect(played).toEqual(["old1", "new1"]);
  });
  it("interrupted also silences any client speech", () => {
    const d = { ...deps(), stopSpeech: vi.fn() };
    createDispatcher(d as any)({ t: "cue", cue: "interrupted" });
    expect(d.stopSpeech).toHaveBeenCalledOnce();
  });
  it("confirm, state, count and target replies also stop the thinking sound; user transcripts and frame requests do not", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "transcript", role: "user", text: "order curd", final: true });
    dispatch({ t: "request_frame", purpose: "count" });
    dispatch({ t: "ready", sessionId: "s", resume: "r", mock: false });
    expect(d.thinking.disarm).not.toHaveBeenCalled();
    dispatch({ t: "confirm", id: "c1", kind: "order", prompt: "Order?" });
    dispatch({ t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "Order sent" });
    dispatch({ t: "count", sku: "sakthi_curd_1l", counted: 3, expected: 30, final: false, confidence: 0.9 });
    dispatch({ t: "target", frameId: "f1", label: "curd", point: [500, 500], confidence: 0.8 });
    expect(d.thinking.disarm).toHaveBeenCalledTimes(4);
  });
  it("does not speak a confirm prompt Live just spoke, but still arms the gesture", () => {
    const d = { ...deps(), ctx: () => ({ mock: false, msSinceLiveAudio: 100, screenReaderMode: false }) };
    createDispatcher(d as any)({ t: "confirm", id: "c1", kind: "sale", prompt: "Sale 120 rupees?" });
    expect(d.speak).not.toHaveBeenCalled();
    expect(d.confirm.pending).toEqual({ id: "c1", kind: "sale" });
  });
  it("confirm and error also buzz a warn haptic", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "confirm", id: "c1", kind: "payment", prompt: "Pay?" });
    dispatch({ t: "error", code: "camera", say: "Camera blocked" });
    expect(d.haptic).toHaveBeenCalledTimes(2);
    expect(d.haptic).toHaveBeenNthCalledWith(1, "warn");
  });
  it("forwards every server message to the optional ui sink", () => {
    const seen: string[] = [];
    const dispatch = createDispatcher({ ...deps(), ui: (m: { t: string }) => seen.push(m.t) } as any);
    dispatch({ t: "ready", sessionId: "s", resume: "r", mock: false });
    dispatch({ t: "cue", cue: "ack" });
    dispatch({ t: "audio", pcm24: "AAAA" });
    expect(seen).toEqual(["ready", "cue", "audio"]);
  });
});
