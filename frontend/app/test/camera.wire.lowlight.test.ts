// frontend/app/test/camera.wire.lowlight.test.ts — the low-light wiring over the real app singleton: with the flags
// off nothing changes (no light report, no torch, no boost); with them on the phone reports its level, obeys the
// server's torch command only, reports the real torch state and shows it on the chip.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";

const h = vi.hoisted(() => ({
  grid: null as Float32Array | null,
  track: null as null | { getCapabilities: () => { torch?: boolean }; applyConstraints: ReturnType<typeof vi.fn> },
}));

vi.mock("../src/camera/capture", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/camera/capture")>()),
  captureToolFrame: vi.fn(async (purpose: string) => ({ t: "frame", id: "fr1", jpeg: "AAAA", w: 640, h: 480, ts: 1, purpose })),
  startContinuous: vi.fn(() => () => {}),
  setQualityListener: vi.fn(),
  stopCamera: vi.fn(),
  probeGrid: vi.fn(() => h.grid),
  getVideo: vi.fn(() => (h.track ? { srcObject: { getVideoTracks: () => [h.track] } } : null)),
}));
vi.mock("../src/camera/tilt", () => ({ startTiltHints: vi.fn(() => vi.fn()) }));
vi.mock("../src/guidance/hands", () => ({ freshHand: vi.fn(() => null), trackFingertip: vi.fn(async () => vi.fn()) }));
vi.mock("../src/guidance/tones", () => ({ guideTo: vi.fn(), stopTone: vi.fn() }));
vi.mock("../src/walk/start", () => ({ startBumpGuard: vi.fn(() => new Promise(() => {})) }));
vi.mock("../src/ambient/watch", () => ({ startHoldUpWatch: vi.fn(() => vi.fn()), noteBumpWarning: vi.fn(), freshObjects: vi.fn(() => []) }));

import { initApp } from "../src/core/app";
import type { Settings } from "../src/core/types";
import { frameBoost } from "../src/camera/capture";
import { wireCameraAndGuidance, wireCameraIntoApp, lowLightFlags, type CameraWiring } from "../src/camera/wire";
import { LIGHT_PROBE_MS } from "../src/camera/lightWatch";
import { MIN_MS } from "../src/camera/lowlight";

type Handler = (m: ServerMsg) => void;
function fakeSocket() {
  const hs = new Set<Handler>();
  const sent: ClientMsg[] = [];
  return {
    hs, sent,
    on: (fn: Handler) => { hs.add(fn); return () => { hs.delete(fn); }; },
    send: (m: ClientMsg) => { sent.push(m); return true; },
    emit: (m: ServerMsg) => [...hs].forEach((fn) => fn(m)),
    isOpen: () => true,
    bufferedAmount: () => 0,
  };
}
const SETTINGS: Settings = { lang: "en", verbosity: "short", screenReaderMode: false, speechRate: 1, onboarded: true, showBoxes: false };
const grid = (luma: number) => new Float32Array(32 * 24).fill(luma);
const lights = (sent: ClientMsg[]) => sent.filter((m) => m.t === "light");
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

let socket: ReturnType<typeof fakeSocket>;
const live: CameraWiring[] = [];

beforeEach(() => {
  // the light watch reads performance.now(): fake it with the timers, or MIN_MS never passes
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] });
  socket = fakeSocket();
  h.grid = grid(150);
  h.track = { getCapabilities: () => ({ torch: true }), applyConstraints: vi.fn(async () => {}) };
  initApp({ socket: socket as never, settings: { ...SETTINGS }, speakImpl: vi.fn(), earconImpl: vi.fn(), hapticImpl: vi.fn() });
});
afterEach(() => {
  for (const c of live.splice(0)) c.unsubscribe();
  vi.useRealTimers();
});

/** Darkness long enough for the detector: a probe every LIGHT_PROBE_MS for MIN_MS plus one. */
async function goDark() {
  h.grid = grid(5);
  for (let i = 0; i <= MIN_MS / LIGHT_PROBE_MS + 1; i++) { vi.advanceTimersByTime(LIGHT_PROBE_MS); await flush(); }
}

describe("flags off (the default)", () => {
  it("reads the build flags, both off unless set", () => {
    expect(lowLightFlags({ torch: false, boost: false })).toEqual({ torch: false, boost: false });
    expect(lowLightFlags({ torch: true })).toMatchObject({ torch: true });
  });

  it("no light report, no torch, no boost: behaviour unchanged", async () => {
    const cam = wireCameraAndGuidance({ devtools: false, lowLight: { torch: false, boost: false } });
    live.push(cam);
    await goDark();
    expect(lights(socket.sent)).toEqual([]);
    socket.emit({ t: "torch", on: true });
    await flush();
    expect(h.track!.applyConstraints).not.toHaveBeenCalled();
    expect(cam.torchState()).toBe("off");
    expect(frameBoost()).toBe(1);
  });
});

describe("flags on", () => {
  it("reports the sustained level (never one frame) and the raw luma", async () => {
    live.push(wireCameraAndGuidance({ devtools: false, lowLight: { torch: true, boost: false } }));
    vi.advanceTimersByTime(LIGHT_PROBE_MS);
    expect(lights(socket.sent)).toEqual([{ t: "light", level: "bright", luma: 150, torch: "off" }]);
    h.grid = grid(5);
    vi.advanceTimersByTime(LIGHT_PROBE_MS * 2); // past the 1 s report gap
    expect(lights(socket.sent).at(-1)).toMatchObject({ level: "bright", luma: 5 }); // the jump, but still bright
    await goDark();
    expect(lights(socket.sent).at(-1)).toMatchObject({ level: "dark", luma: 5, torch: "off" });
  });

  it("the torch turns on only on the server's command, and the phone reports the real state and the chip", async () => {
    const localEvents: string[] = [];
    const hooks = { onTalk: vi.fn(), onStop: vi.fn(), dispose: vi.fn(), local: (e: string) => localEvents.push(e), confirm: { pending: null } } as never;
    const pw = wireCameraIntoApp(hooks, { devtools: false, lowLight: { torch: true, boost: false } });
    live.push(pw.cam);
    await goDark();
    expect(h.track!.applyConstraints).not.toHaveBeenCalled(); // dark alone never switches it on
    socket.emit({ t: "torch", on: true });
    await flush();
    expect(h.track!.applyConstraints).toHaveBeenCalledWith({ advanced: [{ torch: true }] });
    expect(pw.cam.torchState()).toBe("on");
    expect(lights(socket.sent).at(-1)).toMatchObject({ torch: "on", level: "dark" });
    expect(localEvents).toContain("torch_on");
    socket.emit({ t: "torch", on: false });
    await flush();
    expect(pw.cam.torchState()).toBe("off");
    expect(lights(socket.sent).at(-1)).toMatchObject({ torch: "off" });
    expect(localEvents.at(-1)).toBe("torch_off");
    pw.dispose();
  });

  it("no torch capability (iPhone Safari): reports unsupported, never pretends", async () => {
    h.track = { getCapabilities: () => ({}), applyConstraints: vi.fn(async () => {}) };
    const cam = wireCameraAndGuidance({ devtools: false, lowLight: { torch: true, boost: false } });
    live.push(cam);
    vi.advanceTimersByTime(LIGHT_PROBE_MS);
    socket.emit({ t: "torch", on: true });
    await flush();
    expect(h.track.applyConstraints).not.toHaveBeenCalled();
    expect(cam.torchState()).toBe("unsupported");
    expect(lights(socket.sent).at(-1)).toMatchObject({ torch: "unsupported" });
  });

  it("the boost follows the raw luma: on in dim light, off when bright, cleared on unsubscribe", async () => {
    const cam = wireCameraAndGuidance({ devtools: false, lowLight: { torch: false, boost: true } });
    live.push(cam);
    vi.advanceTimersByTime(LIGHT_PROBE_MS);
    expect(frameBoost()).toBe(1);
    h.grid = grid(55);
    for (let i = 0; i <= MIN_MS / LIGHT_PROBE_MS + 1; i++) vi.advanceTimersByTime(LIGHT_PROBE_MS);
    expect(frameBoost()).toBe(2);
    expect(lights(socket.sent)).toEqual([]); // boost alone sends nothing to the server
    h.grid = grid(150);
    for (let i = 0; i <= MIN_MS / LIGHT_PROBE_MS + 1; i++) vi.advanceTimersByTime(LIGHT_PROBE_MS);
    expect(frameBoost()).toBe(1);
    h.grid = grid(55);
    for (let i = 0; i <= MIN_MS / LIGHT_PROBE_MS + 1; i++) vi.advanceTimersByTime(LIGHT_PROBE_MS);
    expect(frameBoost()).toBe(2);
    cam.unsubscribe();
    live.pop();
    expect(frameBoost()).toBe(1);
  });
});
