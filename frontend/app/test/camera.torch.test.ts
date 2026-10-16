// frontend/app/test/camera.torch.test.ts — the flashlight obeys only a server command, only with the capability,
// reports its real state, and switches itself off after the auto-off timeout.
import { describe, it, expect, vi } from "vitest";
import { createTorchController, TORCH_AUTO_OFF_MS } from "../src/camera/torch";
import type { TorchState } from "@aira/contracts";

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

function setup(opts: { torch?: boolean; track?: boolean; applyFails?: boolean } = {}) {
  const apply = vi.fn(async (_c: MediaTrackConstraints) => { if (opts.applyFails) throw new Error("no"); });
  const track = opts.track === false ? null : {
    getCapabilities: () => ({ torch: opts.torch ?? true }),
    applyConstraints: apply,
  };
  const states: Array<[TorchState, string]> = [];
  const timers: Array<{ fn: () => void; ms: number }> = [];
  const cleared: unknown[] = [];
  const t = createTorchController({
    getTrack: () => track,
    onState: (s, reason) => states.push([s, reason]),
    setTimer: (fn, ms) => { const h = { fn, ms }; timers.push(h); return h; },
    clearTimer: (h) => cleared.push(h),
  });
  return { t, apply, states, timers, cleared };
}

describe("torch controller", () => {
  it("nothing turns the torch on but a server torch command", async () => {
    const s = setup();
    s.t.onServerMsg({ t: "cue", cue: "warn" });
    s.t.onServerMsg({ t: "confirm", id: "c1", kind: "generic", prompt: "It's too dark. Shall I turn on the flashlight?" });
    s.t.onServerMsg({ t: "transcript", role: "aira", text: "yes", final: true });
    await flush();
    expect(s.apply).not.toHaveBeenCalled();
    expect(s.t.state()).toBe("off");
    expect(s.states).toEqual([]);
  });

  it("a command with the capability applies torch:true, reports on, and arms the auto-off", async () => {
    const s = setup();
    s.t.onServerMsg({ t: "torch", on: true });
    await flush();
    expect(s.apply).toHaveBeenCalledWith({ advanced: [{ torch: true }] });
    expect(s.t.state()).toBe("on");
    expect(s.states).toEqual([["on", "command"]]);
    expect(s.timers).toHaveLength(1);
    expect(s.timers[0].ms).toBe(TORCH_AUTO_OFF_MS);
    expect(TORCH_AUTO_OFF_MS).toBe(5 * 60_000);
    // auto-off: torch:false, reported with its reason
    s.timers[0].fn();
    await flush();
    expect(s.apply).toHaveBeenLastCalledWith({ advanced: [{ torch: false }] });
    expect(s.t.state()).toBe("off");
    expect(s.states.at(-1)).toEqual(["off", "auto_off"]);
  });

  it("no capability (iPhone Safari): reports unsupported and never touches the track", async () => {
    const s = setup({ torch: false });
    s.t.onServerMsg({ t: "torch", on: true });
    await flush();
    expect(s.apply).not.toHaveBeenCalled();
    expect(s.t.state()).toBe("unsupported");
    expect(s.states).toEqual([["unsupported", "command"]]);
    // no camera open at all: the same honest answer
    const n = setup({ track: false });
    n.t.onServerMsg({ t: "torch", on: true });
    await flush();
    expect(n.t.state()).toBe("unsupported");
  });

  it("a claimed capability whose switch fails is unsupported too: never pretend it is on", async () => {
    const s = setup({ applyFails: true });
    s.t.onServerMsg({ t: "torch", on: true });
    await flush();
    expect(s.t.state()).toBe("unsupported");
    expect(s.timers).toHaveLength(0);
  });

  it("an off command switches off and clears the timer; off while already off just answers the real state", async () => {
    const s = setup();
    s.t.onServerMsg({ t: "torch", on: false });
    await flush();
    expect(s.states).toEqual([["off", "command"]]);
    expect(s.apply).not.toHaveBeenCalled();
    s.t.onServerMsg({ t: "torch", on: true });
    s.t.onServerMsg({ t: "torch", on: false });
    await flush();
    expect(s.t.state()).toBe("off");
    expect(s.cleared).toContain(s.timers[0]);
    expect(s.apply).toHaveBeenLastCalledWith({ advanced: [{ torch: false }] });
  });

  it("dispose switches an on torch off", async () => {
    const s = setup();
    s.t.onServerMsg({ t: "torch", on: true });
    await flush();
    s.t.dispose();
    await flush();
    expect(s.t.state()).toBe("off");
    expect(s.states.at(-1)).toEqual(["off", "dispose"]);
  });
});
