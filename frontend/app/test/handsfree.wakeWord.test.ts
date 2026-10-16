import { describe, it, expect, vi } from "vitest";
import { WakeWordController, type WakeEngine, type WakeWordState } from "../src/handsfree/wakeWord";

/** A fake engine: records starts/stops, lets the test fire detections and errors. Sync by default. */
class FakeEngine implements WakeEngine {
  starts = 0;
  stops = 0;
  cb: { onDetect(): void; onError(e: unknown): void } | null = null;
  startError: unknown = null;
  async = false;
  start(cb: { onDetect(): void; onError(e: unknown): void }) {
    this.starts++;
    if (this.startError) throw this.startError;
    this.cb = cb;
    if (this.async) return Promise.resolve();
  }
  stop() { this.stops++; this.cb = null; }
  detect() { this.cb?.onDetect(); }
  die(e: unknown = new Error("mic lost")) { this.cb?.onError(e); }
}

/** Injected clock + timers so nothing waits for real time. */
function clock() {
  let now = 0;
  const timers: Array<{ at: number; fn: () => void; id: number }> = [];
  let seq = 0;
  return {
    now: () => now,
    setTimeout: (fn: () => void, ms: number) => { const id = ++seq; timers.push({ at: now + ms, fn, id }); return id; },
    clearTimeout: (id: unknown) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
    advance(ms: number) {
      now += ms;
      for (const t of [...timers].sort((a, b) => a.at - b.at)) {
        if (t.at <= now) { timers.splice(timers.indexOf(t), 1); t.fn(); }
      }
    },
  };
}

function setup(over: Partial<ConstructorParameters<typeof WakeWordController>[2]> = {}) {
  const engine = new FakeEngine();
  const cb = { bringToFront: vi.fn(), startVoiceSession: vi.fn(), ack: vi.fn(), onFailed: vi.fn(), onState: vi.fn() };
  const c = clock();
  const ctl = new WakeWordController(engine, cb, { now: c.now, setTimeout: c.setTimeout, clearTimeout: c.clearTimeout, ...over });
  const states = () => cb.onState.mock.calls.map((x) => x[0] as WakeWordState);
  return { engine, cb, c, ctl, states };
}

describe("WakeWordController", () => {
  it("is off by default and the engine is not started", () => {
    const { ctl, engine } = setup();
    expect(ctl.state).toBe("off");
    expect(ctl.active).toBe(false);
    expect(engine.starts).toBe(0);
  });

  it("refuses to enable without the owner's confirmation", () => {
    const { ctl, engine } = setup();
    expect(ctl.enable({ ownerConfirmed: false })).toBe(false);
    expect(ctl.state).toBe("off");
    expect(engine.starts).toBe(0);
  });

  it("enables after a confirmation, listens, and a second enable is a no-op", () => {
    const { ctl, engine, states } = setup();
    expect(ctl.enable({ ownerConfirmed: true })).toBe(true);
    expect(ctl.state).toBe("listening");
    expect(engine.starts).toBe(1);
    expect(ctl.enable({ ownerConfirmed: true })).toBe(true);
    expect(engine.starts).toBe(1);
    expect(states()).toEqual(["starting", "listening"]);
  });

  it("an async engine is 'starting' until its start resolves", async () => {
    const { ctl, engine } = setup();
    engine.async = true;
    ctl.enable({ ownerConfirmed: true });
    expect(ctl.state).toBe("starting");
    await Promise.resolve();
    expect(ctl.state).toBe("listening");
  });

  it("on detect: ack first, then bring the app to the front and start the voice session", () => {
    const { ctl, engine, cb } = setup();
    ctl.enable({ ownerConfirmed: true });
    const order: string[] = [];
    cb.ack.mockImplementation(() => order.push("ack"));
    cb.bringToFront.mockImplementation(() => order.push("front"));
    cb.startVoiceSession.mockImplementation(() => order.push("voice"));
    engine.detect();
    expect(order).toEqual(["ack", "front", "voice"]);
  });

  it("a second detection inside the cooldown is ignored; after it, it counts", () => {
    const { ctl, engine, cb, c } = setup({ cooldownMs: 3_000 });
    ctl.enable({ ownerConfirmed: true });
    engine.detect();
    c.advance(1_000);
    engine.detect();
    expect(cb.startVoiceSession).toHaveBeenCalledTimes(1);
    c.advance(2_500);
    engine.detect();
    expect(cb.startVoiceSession).toHaveBeenCalledTimes(2);
  });

  it("a detection while off (engine still reporting after a stop) does nothing", () => {
    const { ctl, engine, cb } = setup();
    ctl.enable({ ownerConfirmed: true });
    const late = engine.cb!;
    ctl.disable();
    late.onDetect();
    expect(cb.startVoiceSession).not.toHaveBeenCalled();
  });

  it("disable stops the engine and is safe to repeat; dispose too", () => {
    const { ctl, engine } = setup();
    ctl.enable({ ownerConfirmed: true });
    ctl.disable();
    expect(ctl.state).toBe("off");
    expect(engine.stops).toBe(1);
    ctl.disable();
    ctl.dispose();
    expect(ctl.state).toBe("off");
  });

  it("an engine error restarts the engine after the delay", () => {
    const { ctl, engine, c, states } = setup({ restartDelayMs: 2_000 });
    ctl.enable({ ownerConfirmed: true });
    engine.die();
    expect(ctl.state).toBe("restarting");
    expect(engine.starts).toBe(1);
    c.advance(1_999);
    expect(engine.starts).toBe(1);
    c.advance(1);
    expect(engine.starts).toBe(2);
    expect(ctl.state).toBe("listening");
    expect(states()).toEqual(["starting", "listening", "restarting", "starting", "listening"]);
  });

  it("an OS kill reported by the shell follows the same restart rule", () => {
    const { ctl, engine, c } = setup({ restartDelayMs: 500 });
    ctl.enable({ ownerConfirmed: true });
    ctl.onKilled("foreground service stopped");
    expect(ctl.state).toBe("restarting");
    c.advance(500);
    expect(engine.starts).toBe(2);
  });

  it("gives up after maxRestarts within the window, tells the owner, and a fresh enable tries again", () => {
    const { ctl, engine, cb, c } = setup({ maxRestarts: 2, restartDelayMs: 100, restartWindowMs: 60_000 });
    ctl.enable({ ownerConfirmed: true });
    engine.die(); c.advance(100); // restart 1
    engine.die(); c.advance(100); // restart 2
    expect(engine.starts).toBe(3);
    engine.die(new Error("mic busy")); // over budget
    expect(ctl.state).toBe("failed");
    expect(ctl.active).toBe(false);
    expect(cb.onFailed).toHaveBeenCalledWith("mic busy");
    c.advance(10_000);
    expect(engine.starts).toBe(3); // no more restarts
    expect(ctl.enable({ ownerConfirmed: true })).toBe(true);
    expect(ctl.state).toBe("listening");
    expect(engine.starts).toBe(4);
  });

  it("restarts outside the window are forgotten (a long-running engine may die now and then)", () => {
    const { ctl, engine, c } = setup({ maxRestarts: 1, restartDelayMs: 100, restartWindowMs: 10_000 });
    ctl.enable({ ownerConfirmed: true });
    engine.die(); c.advance(100);
    c.advance(20_000);
    engine.die(); c.advance(100);
    expect(ctl.state).toBe("listening");
    expect(engine.starts).toBe(3);
  });

  it("a disable during the restart delay cancels the restart", () => {
    const { ctl, engine, c } = setup({ restartDelayMs: 1_000 });
    ctl.enable({ ownerConfirmed: true });
    engine.die();
    ctl.disable();
    c.advance(5_000);
    expect(engine.starts).toBe(1);
    expect(ctl.state).toBe("off");
  });

  it("a start that throws counts as an engine error", () => {
    const { ctl, engine, c } = setup({ restartDelayMs: 100 });
    engine.startError = new Error("no mic permission");
    ctl.enable({ ownerConfirmed: true });
    expect(ctl.state).toBe("restarting");
    engine.startError = null;
    c.advance(100);
    expect(ctl.state).toBe("listening");
  });

  it("an async start that rejects counts as an engine error", async () => {
    const { ctl, engine, c } = setup({ restartDelayMs: 100 });
    engine.start = () => { engine.starts++; return Promise.reject(new Error("boom")); };
    ctl.enable({ ownerConfirmed: true });
    await Promise.resolve(); await Promise.resolve();
    expect(ctl.state).toBe("restarting");
    c.advance(100);
    expect(engine.starts).toBe(2);
  });

  it("a callback that throws never breaks the controller", () => {
    const { ctl, engine, cb } = setup();
    cb.ack.mockImplementation(() => { throw new Error("no audio"); });
    cb.onState.mockImplementation(() => { throw new Error("ui gone"); });
    ctl.enable({ ownerConfirmed: true });
    engine.detect();
    expect(cb.startVoiceSession).toHaveBeenCalledTimes(1);
    expect(ctl.state).toBe("listening");
  });
});
