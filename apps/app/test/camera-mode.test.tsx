// Shop-camera mode (/camera): the phone on a stand streams frames at a low rate over the same socket with the
// camera role (uid "camera:<name>"); no mic; screen-reader friendly; light theme.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { routeFor } from "../src/demo/router";
import {
  CAMERA_UID_PREFIX, DEFAULT_INTERVAL_MS, cameraHello, normalizeCameraName, startShopCamera, type CameraSocket,
} from "../src/camera-mode/stream";
import { CameraPage, type StartArgs } from "../src/camera-mode/CameraPage";
import { DEFAULT_SETTINGS } from "../src/core/settings";

describe("route", () => {
  it("opens the shop-camera page at /camera", () => {
    expect(routeFor("/camera")).toBe("camera");
    expect(routeFor("/camera/")).toBe("camera");
    expect(routeFor("/cameras")).toBe("app");
    expect(routeFor("/")).toBe("app");
  });
});

describe("camera name and hello", () => {
  it("normalises the name to a safe, lower-case id", () => {
    expect(normalizeCameraName("Front Door")).toBe("front-door");
    expect(normalizeCameraName("  ")).toBe("front");
    expect(normalizeCameraName("../x")).toBe("x");
    expect(normalizeCameraName("a".repeat(50))).toHaveLength(32);
  });

  it("builds a hello with the camera role and no token, in the owner's language", () => {
    const h = cameraHello("front", { ...DEFAULT_SETTINGS, lang: "ta" });
    expect(h.t).toBe("hello");
    expect(h.uid).toBe(`${CAMERA_UID_PREFIX}front`);
    expect(h.lang).toBe("ta");
    expect(h.verbosity).toBe("short");
    expect("idToken" in h).toBe(false);
    expect(h.shopId).toBe(DEFAULT_SETTINGS.shopId);
  });
});

function fakeSocket() {
  const handlers = new Set<(m: ServerMsg) => void>();
  const sent: ClientMsg[] = [];
  const sock = {
    connect: vi.fn(),
    send: vi.fn((m: ClientMsg) => { sent.push(m); return true; }),
    on: vi.fn((h: (m: ServerMsg) => void) => { handlers.add(h); return () => { handlers.delete(h); }; }),
    close: vi.fn(),
  };
  const emit = (m: ServerMsg) => handlers.forEach((h) => h(m));
  return { sock: sock as unknown as CameraSocket, raw: sock, sent, emit };
}

const frame = (id: string): Extract<ClientMsg, { t: "frame" }> => ({ t: "frame", id, jpeg: "AAAA", w: 4, h: 4, ts: 1, purpose: "ask" });

describe("startShopCamera", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends hello, then one 'ask' frame per interval once the server is ready", async () => {
    const { sock, raw, sent, emit } = fakeSocket();
    let n = 0;
    const capture = vi.fn(async () => frame(`f${++n}`));
    const status: string[] = [];
    const run = startShopCamera({ socket: sock, capture, name: "front", settings: DEFAULT_SETTINGS, intervalMs: 1000, onStatus: (s) => status.push(s) });
    expect(raw.connect).toHaveBeenCalledTimes(1);
    expect((raw.connect.mock.calls[0][0] as { uid: string }).uid).toBe("camera:front");
    expect(capture).not.toHaveBeenCalled(); // nothing is sent before `ready`
    emit({ t: "ready", sessionId: "s1", resume: "s1", mock: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(capture).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(capture).toHaveBeenCalledTimes(2);
    expect(sent.filter((m) => m.t === "frame").map((m) => (m as { id: string }).id)).toEqual(["f1", "f2"]);
    expect(sent.every((m) => m.t !== "audio")).toBe(true);
    expect(status).toContain("streaming");
    run.stop();
    expect(raw.close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(capture).toHaveBeenCalledTimes(2);
    expect(status[status.length - 1]).toBe("stopped");
  });

  it("keeps going when one capture fails and reports a camera error", async () => {
    const { sock, emit } = fakeSocket();
    let n = 0;
    const capture = vi.fn(async () => { if (++n === 1) throw new Error("camera:permission"); return frame(`f${n}`); });
    const status: string[] = [];
    const run = startShopCamera({ socket: sock, capture, name: "front", settings: DEFAULT_SETTINGS, intervalMs: 1000, onStatus: (s) => status.push(s) });
    emit({ t: "ready", sessionId: "s1", resume: "s1", mock: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(status).toContain("camera_error");
    await vi.advanceTimersByTimeAsync(1000);
    expect(capture).toHaveBeenCalledTimes(2);
    run.stop();
  });

  it("defaults to a low frame rate", () => {
    expect(DEFAULT_INTERVAL_MS).toBeGreaterThanOrEqual(3000);
  });
});

describe("CameraPage", () => {
  let root: HTMLElement;
  beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
  afterEach(() => { act(() => render(null, root)); root.remove(); });

  it("renders a labelled name field, a start button and a polite live region; no mic, no audio", () => {
    act(() => render(<CameraPage start={() => ({ stop: () => {} })} />, root));
    expect(root.querySelector("h1")?.textContent).toMatch(/camera/i);
    const input = root.querySelector<HTMLInputElement>("input#camera-name")!;
    expect(input).not.toBeNull();
    expect(root.querySelector('label[for="camera-name"]')).not.toBeNull();
    const live = root.querySelector('[aria-live="polite"]')!;
    expect(live).not.toBeNull();
    expect(root.querySelector("button.camera__start")?.textContent).toMatch(/start/i);
    expect(root.querySelector("audio")).toBeNull();
    expect(root.textContent).toMatch(/no microphone/i);
  });

  it("starts streaming with the typed name and stops on the second tap", () => {
    const stop = vi.fn();
    const start = vi.fn((_a: StartArgs) => ({ stop }));
    act(() => render(<CameraPage start={start} />, root));
    const input = root.querySelector<HTMLInputElement>("input#camera-name")!;
    act(() => { input.value = "Shelf One"; input.dispatchEvent(new Event("input", { bubbles: true })); });
    act(() => root.querySelector<HTMLButtonElement>("button.camera__start")!.click());
    expect(start).toHaveBeenCalledTimes(1);
    expect(start.mock.calls[0][0].name).toBe("shelf-one");
    expect(root.querySelector("button.camera__start")?.textContent).toMatch(/stop/i);
    act(() => root.querySelector<HTMLButtonElement>("button.camera__start")!.click());
    expect(stop).toHaveBeenCalledTimes(1);
    expect(root.querySelector("button.camera__start")?.textContent).toMatch(/start/i);
  });
});
