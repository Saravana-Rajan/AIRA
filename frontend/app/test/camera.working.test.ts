// frontend/app/test/camera.working.test.ts — the "working" earcon. Production 2026-10-13: Live asked the camera for
// a count without saying anything and the owner waited 12-23 s in silence.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ServerMsg } from "@aira/contracts";
import { createWorkingCue, startsWorking, stopsWorking, WORKING_MAX_MS, WORKING_PERIOD_MS } from "../src/camera/working";
import { LOCAL_EARCONS } from "../src/voice/earcons";

describe("working earcon", () => {
  let plays: number;
  let cue: ReturnType<typeof createWorkingCue>;
  beforeEach(() => {
    vi.useFakeTimers();
    plays = 0;
    cue = createWorkingCue({ play: () => { plays++; } });
  });
  afterEach(() => { cue.stop(); vi.useRealTimers(); });

  it("plays at once when a count / ask / identify / invoice / cash photo is requested, then every 2 s", () => {
    expect(WORKING_PERIOD_MS).toBe(2000);
    cue.onServerMsg({ t: "request_frame", purpose: "count" });
    expect(plays).toBe(1);
    vi.advanceTimersByTime(2000);
    expect(plays).toBe(2);
    vi.advanceTimersByTime(4000);
    expect(plays).toBe(4);
    for (const purpose of ["ask", "identify", "invoice", "cash"] as const) {
      expect(startsWorking({ t: "request_frame", purpose })).toBe(true);
    }
  });

  it("stops on the first AIRA audio", () => {
    cue.onServerMsg({ t: "request_frame", purpose: "count" });
    cue.onServerMsg({ t: "audio", pcm24: "AAAA" });
    expect(cue.active).toBe(false);
    vi.advanceTimersByTime(10_000);
    expect(plays).toBe(1);
  });

  it("stops on an AIRA transcript, an error or a confirm, but not on the owner's own words", () => {
    const stops: ServerMsg[] = [
      { t: "transcript", role: "aira", text: "Six", final: false },
      { t: "error", code: "camera", say: "No camera" },
      { t: "confirm", id: "c", kind: "generic", prompt: "OK?" },
    ];
    for (const m of stops) expect(stopsWorking(m)).toBe(true);
    expect(stopsWorking({ t: "transcript", role: "user", text: "how many", final: true })).toBe(false);
    cue.onServerMsg({ t: "request_frame", purpose: "ask" });
    cue.onServerMsg({ t: "transcript", role: "user", text: "how many", final: true });
    expect(cue.active).toBe(true);
    cue.onServerMsg(stops[0]);
    expect(cue.active).toBe(false);
  });

  it("gives up after 30 s of silence", () => {
    expect(WORKING_MAX_MS).toBe(30_000);
    cue.onServerMsg({ t: "request_frame", purpose: "count" });
    vi.advanceTimersByTime(60_000);
    expect(cue.active).toBe(false);
    expect(plays).toBeLessThanOrEqual(16);
    expect(plays).toBeGreaterThanOrEqual(14);
  });

  it("a second request while working does not double the sound", () => {
    cue.onServerMsg({ t: "request_frame", purpose: "count" });
    cue.onServerMsg({ t: "request_frame", purpose: "count" });
    expect(plays).toBe(1);
    vi.advanceTimersByTime(2000);
    expect(plays).toBe(2);
  });

  it("background and continuous requests never start it", () => {
    for (const m of [
      { t: "request_frame", purpose: "describe", continuous: true },
      { t: "request_frame", purpose: "preview", continuous: true },
      { t: "request_frame", purpose: "walk" },
      { t: "request_frame", purpose: "guide" },
      { t: "request_frame", purpose: "count", stop: true },
      { t: "request_frame", purpose: "identify", continuous: true },
    ] as ServerMsg[]) cue.onServerMsg(m);
    expect(plays).toBe(0);
  });

  it("is a soft, short, word-free sound", () => {
    const e = LOCAL_EARCONS.working;
    expect(e.gain).toBeLessThanOrEqual(0.1);
    expect(e.tones.reduce((s, t) => s + t.ms, 0)).toBeLessThanOrEqual(600);
  });
});
