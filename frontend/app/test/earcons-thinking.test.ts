import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CUE_NAMES } from "@aira/contracts";
import { EARCONS, playEarcon } from "../src/voice/earcons";
import { ThinkingTimer, disarmsThinking } from "../src/core/thinking";

const ALL = ["ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline"];

describe("earcons", () => {
  it("covers exactly the frozen contract's CueName list", () => {
    expect(Object.keys(EARCONS).sort()).toEqual([...CUE_NAMES].sort());
    expect([...CUE_NAMES].sort()).toEqual([...ALL].sort());
  });

  it("playEarcon ignores an unknown cue name", () => {
    const ctx = { currentTime: 0, createOscillator: vi.fn(), createGain: vi.fn() } as unknown as AudioContext;
    playEarcon(ctx, "nope" as any);
    expect((ctx as any).createOscillator).not.toHaveBeenCalled();
  });

  it("defines a short earcon for every CueName", () => {
    expect(Object.keys(EARCONS).sort()).toEqual([...ALL].sort());
    for (const k of ALL) {
      const e = (EARCONS as any)[k];
      const total = e.tones.reduce((s: number, t: any) => s + t.ms, 0);
      expect(total).toBeLessThanOrEqual(600);
      expect(e.gain).toBeGreaterThan(0);
    }
  });

  it("match rises and mismatch falls (distinguishable without words)", () => {
    expect(EARCONS.match.tones.at(-1)!.hz).toBeGreaterThan(EARCONS.match.tones[0].hz);
    expect(EARCONS.mismatch.tones.at(-1)!.hz).toBeLessThan(EARCONS.mismatch.tones[0].hz);
  });

  it("playEarcon schedules one oscillator per tone, back to back", () => {
    const started: number[] = [];
    const freqs: number[] = [];
    const ctx = {
      currentTime: 10,
      destination: {},
      createOscillator: () => {
        const osc: any = {
          frequency: { value: 0 },
          connect: (n: unknown) => n,
          start: (t: number) => { started.push(t); freqs.push(osc.frequency.value); },
          stop: vi.fn(),
        };
        return osc;
      },
      createGain: () => ({
        gain: { setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn() },
        connect: (n: unknown) => n,
      }),
    } as unknown as AudioContext;

    playEarcon(ctx, "match");
    expect(freqs).toEqual(EARCONS.match.tones.map((t) => t.hz));
    expect(started[0]).toBeCloseTo(10);
    expect(started[1]).toBeCloseTo(10 + EARCONS.match.tones[0].ms / 1000);
    expect(started[2]).toBeCloseTo(10 + (EARCONS.match.tones[0].ms + EARCONS.match.tones[1].ms) / 1000);
  });
});

describe("ThinkingTimer", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts the thinking sound only after 1.5 s", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm();
    vi.advanceTimersByTime(1499);
    expect(start).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(start).toHaveBeenCalledOnce();
    expect(t.active).toBe(true);
  });

  it("disarm before 1.5 s means no sound at all", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm(); vi.advanceTimersByTime(800); t.disarm(); vi.advanceTimersByTime(5000);
    expect(start).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("disarm after start stops the sound exactly once", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm(); vi.advanceTimersByTime(2000); t.disarm(); t.disarm();
    expect(stop).toHaveBeenCalledOnce();
    expect(t.active).toBe(false);
  });

  it("re-arming restarts the 1.5 s window", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm(); vi.advanceTimersByTime(1000); t.arm(); vi.advanceTimersByTime(1000);
    expect(start).not.toHaveBeenCalled();
    vi.advanceTimersByTime(500);
    expect(start).toHaveBeenCalledOnce();
  });
});

describe("disarmsThinking (thinking sound never plays over AIRA's speech)", () => {
  it("audio, AIRA transcript, cue and error disarm the timer", () => {
    expect(disarmsThinking({ t: "audio", pcm24: "AAAA" })).toBe(true);
    expect(disarmsThinking({ t: "transcript", role: "aira", text: "Hi", final: false })).toBe(true);
    expect(disarmsThinking({ t: "cue", cue: "done" })).toBe(true);
    expect(disarmsThinking({ t: "error", code: "network", say: "Offline" })).toBe(true);
  });

  it("the user's own transcript and other messages do not", () => {
    expect(disarmsThinking({ t: "transcript", role: "user", text: "Hi", final: true })).toBe(false);
    expect(disarmsThinking({ t: "ready", sessionId: "s", resume: "r", mock: false })).toBe(false);
    expect(disarmsThinking({ t: "request_frame", purpose: "count" })).toBe(false);
  });
});
