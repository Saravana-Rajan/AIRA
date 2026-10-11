// frontend/app/test/walk.detector.test.ts — worker protocol of the bump-guard object detector
import { describe, it, expect, vi } from "vitest";
import { createWorkerDetector } from "../src/walk/detector";

function fakeWorker() {
  const posted: { msg: any; transfer?: unknown[] }[] = [];
  const w = {
    onmessage: null as ((e: { data: any }) => void) | null,
    onerror: null as ((e: unknown) => void) | null,
    postMessage: vi.fn((msg: any, transfer?: unknown[]) => { posted.push({ msg, transfer }); }),
    terminate: vi.fn(),
    reply: (data: any) => w.onmessage?.({ data }),
  };
  return { w, posted };
}

describe("worker detector", () => {
  it("initialises the worker, then sends frames and resolves results by sequence number", async () => {
    const { w, posted } = fakeWorker();
    const pending = createWorkerDetector(w as any, 1000);
    expect(posted[0].msg).toEqual({ type: "init" });
    w.reply({ type: "ready", delegate: "GPU" });
    const d = await pending;
    expect(d.kind).toBe("worker");

    const frame = { close: vi.fn() };
    const p = d.detect(frame as any, 1234);
    const sent = posted[1].msg;
    expect(sent).toMatchObject({ type: "frame", ts: 1234, frame });
    w.reply({ type: "result", seq: sent.seq + 99, ts: 1, dets: [], floor: null });   // unknown seq ignored
    w.reply({ type: "result", seq: sent.seq, ts: 1234, dets: [{ label: "chair", score: 0.9, box: [1, 2, 3, 4] }], floor: 0.1 });
    await expect(p).resolves.toEqual({ ts: 1234, dets: [{ label: "chair", score: 0.9, box: [1, 2, 3, 4] }], floor: 0.1 });
  });

  it("rejects when the worker reports an init error, and terminates it", async () => {
    const { w } = fakeWorker();
    const pending = createWorkerDetector(w as any, 1000);
    w.reply({ type: "error", message: "no webgl" });
    await expect(pending).rejects.toThrow(/no webgl/);
    expect(w.terminate).toHaveBeenCalled();
  });

  it("rejects when the worker never becomes ready", async () => {
    vi.useFakeTimers();
    const { w } = fakeWorker();
    const pending = createWorkerDetector(w as any, 500);
    const check = expect(pending).rejects.toThrow(/timeout/);
    await vi.advanceTimersByTimeAsync(600);
    await check;
    expect(w.terminate).toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("a crash after start rejects the pending frame and every later one", async () => {
    const { w } = fakeWorker();
    const pending = createWorkerDetector(w as any, 1000);
    w.reply({ type: "ready", delegate: "CPU" });
    const d = await pending;
    const p = d.detect({} as any, 1);
    w.onerror?.(new Error("boom"));
    await expect(p).rejects.toThrow();
    await expect(d.detect({} as any, 2)).rejects.toThrow();
  });

  it("close() terminates the worker and rejects pending frames", async () => {
    const { w } = fakeWorker();
    const pending = createWorkerDetector(w as any, 1000);
    w.reply({ type: "ready", delegate: "GPU" });
    const d = await pending;
    const p = d.detect({} as any, 1);
    d.close();
    await expect(p).rejects.toThrow();
    expect(w.terminate).toHaveBeenCalled();
  });
});
