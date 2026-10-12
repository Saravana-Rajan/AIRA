// frontend/app/test/camera-mode.boxes.test.ts — the room camera (/camera) also sends MediaPipe boxes at a low rate.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { isClientMsg } from "@aira/contracts";
import { ROOM_BOXES_INTERVAL_MS, startRoomBoxes } from "../src/camera-mode/boxes";
import { startShopCamera, type CameraSocket } from "../src/camera-mode/stream";
import { DEFAULT_SETTINGS } from "../src/core/settings";

function fakeSocket() {
  const handlers = new Set<(m: ServerMsg) => void>();
  const sent: ClientMsg[] = [];
  const sock = {
    connect: vi.fn(),
    send: vi.fn((m: ClientMsg) => { sent.push(m); return true; }),
    on: vi.fn((h: (m: ServerMsg) => void) => { handlers.add(h); return () => { handlers.delete(h); }; }),
    close: vi.fn(),
  };
  return { sock: sock as unknown as CameraSocket, sent, emit: (m: ServerMsg) => handlers.forEach((h) => h(m)) };
}

const frame = (id: string): Extract<ClientMsg, { t: "frame" }> => ({ t: "frame", id, jpeg: "AAAA", w: 4, h: 4, ts: 1, purpose: "ask" });
const dets = [{ label: "chair", box: [420, 310, 900, 690] as [number, number, number, number], score: 0.82 }];

describe("startRoomBoxes", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("runs the detector once per interval and sends a valid boxes message; null results send nothing", async () => {
    const sent: ClientMsg[] = [];
    let n = 0;
    const detect = vi.fn(async () => (++n === 2 ? null : dets));
    const run = startRoomBoxes({ detect, send: (m) => { sent.push(m); return true; }, intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(0);
    expect(detect).toHaveBeenCalledTimes(1);
    expect(sent).toEqual([{ t: "boxes", source: "mediapipe", objects: [{ label: "chair", box: [420, 310, 900, 690], score: 0.82 }] }]);
    expect(isClientMsg(sent[0])).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    expect(detect).toHaveBeenCalledTimes(3);
    expect(sent.length).toBe(2);
    run.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(detect).toHaveBeenCalledTimes(3);
  });

  it("a slow or failing detector never overlaps or stops the loop", async () => {
    const sent: ClientMsg[] = [];
    let n = 0;
    const detect = vi.fn(async () => {
      if (++n === 1) throw new Error("model not ready");
      await new Promise((r) => setTimeout(r, 2500));
      return dets;
    });
    const run = startRoomBoxes({ detect, send: (m) => { sent.push(m); return true; }, intervalMs: 1000 });
    await vi.advanceTimersByTimeAsync(0);          // throws: skipped
    await vi.advanceTimersByTimeAsync(1000);       // starts a slow pass
    await vi.advanceTimersByTimeAsync(2000);       // ticks while busy are skipped
    expect(detect).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(600);
    expect(sent.length).toBe(1);
    run.stop();
  });

  it("defaults to a low rate", () => {
    expect(ROOM_BOXES_INTERVAL_MS).toBeGreaterThanOrEqual(500);
  });
});

describe("startShopCamera with boxes", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("sends boxes alongside frames once the server is ready, and stops both together", async () => {
    const { sock, sent, emit } = fakeSocket();
    let n = 0;
    const capture = vi.fn(async () => frame(`f${++n}`));
    const detect = vi.fn(async () => dets);
    const run = startShopCamera({ socket: sock, capture, name: "front", settings: DEFAULT_SETTINGS, intervalMs: 5000,
                                  boxes: { detect, intervalMs: 1000 } });
    expect(detect).not.toHaveBeenCalled();         // nothing before `ready`
    emit({ t: "ready", sessionId: "s1", resume: "s1", mock: true });
    await vi.advanceTimersByTimeAsync(0);
    expect(sent.map((m) => m.t)).toEqual(["frame", "boxes"]);
    await vi.advanceTimersByTimeAsync(2000);
    expect(sent.filter((m) => m.t === "boxes").length).toBe(3);
    expect(sent.filter((m) => m.t === "frame").length).toBe(1);
    run.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(sent.filter((m) => m.t === "boxes").length).toBe(3);
  });

  it("without a detector only frames are sent (the contracts stay as before)", async () => {
    const { sock, sent, emit } = fakeSocket();
    const run = startShopCamera({ socket: sock, capture: async () => frame("f1"), name: "front", settings: DEFAULT_SETTINGS, intervalMs: 1000 });
    emit({ t: "ready", sessionId: "s1", resume: "s1", mock: true });
    await vi.advanceTimersByTimeAsync(2500);
    expect(sent.every((m) => m.t === "frame")).toBe(true);
    run.stop();
  });
});
