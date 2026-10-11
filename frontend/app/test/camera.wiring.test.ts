// frontend/app/test/camera.wiring.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import type { ServerMsg } from "@aira/contracts";
import { createCameraWiring } from "../src/camera/wiring";
import { installFake } from "../src/camera/devDispatch";

function fakeSocket() {
  const hs = new Set<(m: ServerMsg) => void>();
  return {
    on: vi.fn((h: (m: ServerMsg) => void) => { hs.add(h); return () => { hs.delete(h); }; }),
    emit: (m: ServerMsg) => hs.forEach((h) => h(m)),
    size: () => hs.size,
  };
}
const rf = { t: "request_frame" as const, purpose: "count" as const, burst: { count: 2, intervalMs: 100 } };

describe("createCameraWiring", () => {
  it("routes request_frame to the frame handler and every message to each handler", () => {
    const socket = fakeSocket(); const handleFrame = vi.fn(); const a = vi.fn(); const b = vi.fn();
    createCameraWiring({ socket, handleFrame, handlers: [a, b] });
    socket.emit(rf);
    socket.emit({ t: "cue", cue: "ack" });
    expect(handleFrame).toHaveBeenCalledTimes(1);
    expect(handleFrame).toHaveBeenCalledWith(rf);
    expect(a.mock.calls.map((c) => c[0].t)).toEqual(["request_frame", "cue"]);
    expect(b).toHaveBeenCalledTimes(2);
  });

  it("one failing handler does not stop the others (sync throw or rejected frame request)", async () => {
    const socket = fakeSocket(); const after = vi.fn();
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    createCameraWiring({
      socket,
      handleFrame: () => Promise.reject(new Error("camera gone")),
      handlers: [() => { throw new Error("boom"); }, after],
    });
    socket.emit(rf);
    await Promise.resolve(); await Promise.resolve();
    expect(after).toHaveBeenCalledTimes(1);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("unsubscribe detaches from the socket and runs cleanups once", () => {
    const socket = fakeSocket(); const c1 = vi.fn(); const c2 = vi.fn(); const h = vi.fn();
    const w = createCameraWiring({ socket, handleFrame: vi.fn(), handlers: [h], cleanup: [c1, c2] });
    expect(socket.size()).toBe(1);
    w.unsubscribe(); w.unsubscribe();
    expect(socket.size()).toBe(0);
    expect(c1).toHaveBeenCalledTimes(1);
    expect(c2).toHaveBeenCalledTimes(1);
    socket.emit({ t: "cue", cue: "ack" });
    expect(h).not.toHaveBeenCalled();
  });

  it("hands the same dispatch to the dev hook", () => {
    const socket = fakeSocket(); const h = vi.fn(); let dev: ((m: ServerMsg) => void) | null = null;
    const w = createCameraWiring({ socket, handleFrame: vi.fn(), handlers: [h], installDev: (d) => { dev = d; } });
    expect(dev).toBe(w.dispatch);
    dev!({ t: "count", sku: "s", counted: 1, expected: 3, final: false, confidence: 0.9 });
    expect(h).toHaveBeenCalledTimes(1);
  });
});

describe("dev dispatch", () => {
  afterEach(() => { delete (globalThis as { __aira?: unknown }).__aira; });
  it("exposes window.__aira.dispatch and removes it on uninstall", () => {
    const d = vi.fn();
    const off = installFake(d);
    const g = globalThis as unknown as { __aira?: { dispatch: (m: ServerMsg) => void } };
    g.__aira!.dispatch({ t: "cue", cue: "ack" });
    expect(d).toHaveBeenCalledWith({ t: "cue", cue: "ack" });
    off();
    expect(g.__aira).toBeUndefined();
  });
});
