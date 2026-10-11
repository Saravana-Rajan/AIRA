// frontend/app/test/guidance.tones.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
vi.mock("../src/core/app", () => ({ app: { speak: vi.fn(), settings: { lang: "auto" }, lang: () => "en" } }));
import { app } from "../src/core/app";
import { guideTo, stopTone, __setAudioContext } from "../src/guidance/tones";

function param() {
  return { value: 0, setTargetAtTime: vi.fn(), setValueAtTime: vi.fn(), cancelScheduledValues: vi.fn() };
}
function fakeCtx() {
  const node = () => ({ connect: vi.fn((n: any) => n) });
  const osc = { ...node(), type: "", frequency: param(), start: vi.fn() };
  const pan = { ...node(), pan: param() };
  const gain = { ...node(), gain: param() };
  const ctx = {
    currentTime: 1,
    state: "running",
    destination: {},
    resume: vi.fn(async () => {}),
    createOscillator: vi.fn(() => osc),
    createStereoPanner: vi.fn(() => pan),
    createGain: vi.fn(() => gain),
  };
  return { ctx, osc, pan, gain };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
  (app.speak as any).mockClear();
});
afterEach(() => {
  stopTone();
  __setAudioContext(null);
  vi.useRealTimers();
});

describe("guideTo (Web Audio)", () => {
  it("target right of the tip pans right; target above raises the pitch; one oscillator reused", () => {
    const f = fakeCtx();
    __setAudioContext(f.ctx as any);
    guideTo([200, 800], { x1000: 200, y1000: 200 });     // target [y, x]: to the right
    expect(f.pan.pan.setTargetAtTime.mock.calls.at(-1)![0]).toBeGreaterThan(0);
    guideTo([100, 500], { x1000: 500, y1000: 500 });     // target above
    const upHz = f.osc.frequency.setTargetAtTime.mock.calls.at(-1)![0];
    guideTo([700, 500], { x1000: 500, y1000: 500 });     // target below
    const downHz = f.osc.frequency.setTargetAtTime.mock.calls.at(-1)![0];
    expect(upHz).toBeGreaterThan(downHz);
    expect(f.ctx.createOscillator).toHaveBeenCalledTimes(1);
    expect(f.osc.start).toHaveBeenCalledTimes(1);
  });

  it("beeps on a timer while guiding and stopTone silences it", () => {
    const f = fakeCtx();
    __setAudioContext(f.ctx as any);
    guideTo([500, 500], { x1000: 520, y1000: 500 });     // close => fast beeps
    vi.advanceTimersByTime(1000);
    const beeps = f.gain.gain.setValueAtTime.mock.calls.filter((c: any[]) => c[0] > 0).length;
    expect(beeps).toBeGreaterThanOrEqual(5);
    stopTone();
    f.gain.gain.setValueAtTime.mockClear();
    vi.advanceTimersByTime(1000);
    expect(f.gain.gain.setValueAtTime).not.toHaveBeenCalled();
  });

  it("no fingertip => silence and 'show your hand' at most once per 8 s", () => {
    const f = fakeCtx();
    __setAudioContext(f.ctx as any);
    guideTo([500, 500], null);
    guideTo([500, 500], null);
    expect(app.speak).toHaveBeenCalledTimes(1);
    expect(app.speak).toHaveBeenCalledWith("Show your hand to the camera.");
    vi.setSystemTime(107_999);
    guideTo([500, 500], null);
    expect(app.speak).toHaveBeenCalledTimes(1);
    vi.setSystemTime(108_001);
    guideTo([500, 500], null);
    expect(app.speak).toHaveBeenCalledTimes(2);
    expect(f.ctx.createOscillator).not.toHaveBeenCalled();
  });

  it("resumes a suspended audio context", () => {
    const f = fakeCtx();
    f.ctx.state = "suspended";
    __setAudioContext(f.ctx as any);
    guideTo([500, 800], { x1000: 500, y1000: 500 });
    expect(f.ctx.resume).toHaveBeenCalled();
  });
});
