// The camera drawer shows the newest frame only: frames arriving while one decodes, or before the next
// animation frame, replace the waiting one instead of queueing behind it.
import { describe, expect, it, vi } from "vitest";
import { createFrameDrawer, frameBlob, type DrawDeps, type Drawable } from "../src/lib/frameDraw";
import type { Frame } from "../src/lib/monitor";

const frame = (ts: number, w = 360, h = 640): Frame => ({ src: `data:image/jpeg;base64,QUFB${ts}`, w, h, purpose: "preview", ts });

/** A Blob's bytes; jsdom's Blob has no arrayBuffer(), so fall back to FileReader. */
function bytesOf(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === "function") return blob.arrayBuffer();
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as ArrayBuffer);
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(blob);
  });
}

function harness() {
  const rafs: Array<() => void> = [];
  const pending: Array<{ frame: Frame; resolve: (d: Drawable) => void; reject: (e: unknown) => void }> = [];
  const drawn: Array<[number, number, number]> = [];
  const closed: number[] = [];
  const canvas = {
    width: 0, height: 0,
    getContext: () => ({ drawImage: (img: any, _x: number, _y: number, w: number, h: number) => { drawn.push([img.ts, w, h]); } }),
  } as unknown as HTMLCanvasElement;
  const deps: DrawDeps = {
    raf: (cb) => { rafs.push(cb); },
    decode: (f) => new Promise((resolve, reject) => { pending.push({ frame: f, resolve, reject }); }),
  };
  const drawer = createFrameDrawer(() => canvas, deps);
  const tick = () => { const cbs = rafs.splice(0); cbs.forEach((cb) => cb()); return cbs.length; };
  const resolveAll = async () => {
    for (const p of pending.splice(0)) p.resolve({ ts: p.frame.ts, close: () => closed.push(p.frame.ts) } as unknown as Drawable);
    await Promise.resolve(); await Promise.resolve();
  };
  return { drawer, rafs, pending, drawn, closed, canvas, tick, resolveAll };
}

describe("createFrameDrawer", () => {
  it("decodes and draws the newest frame once per animation frame, sizing the canvas to the frame", async () => {
    const h = harness();
    h.drawer.push(frame(1));
    h.drawer.push(frame(2));
    h.drawer.push(frame(3));
    expect(h.rafs).toHaveLength(1);                // one animation frame scheduled, not three
    expect(h.tick()).toBe(1);
    expect(h.pending.map((p) => p.frame.ts)).toEqual([3]); // only the newest is decoded
    await h.resolveAll();
    expect(h.drawn).toEqual([[3, 360, 640]]);
    expect([h.canvas.width, h.canvas.height]).toEqual([360, 640]);
    expect(h.closed).toEqual([3]);                 // the bitmap is released after the draw
    expect(h.drawer.drawnTs()).toBe(3);
  });

  it("a frame that arrives during a decode is drawn next; older ones in between are dropped", async () => {
    const h = harness();
    h.drawer.push(frame(1));
    h.tick();
    expect(h.pending).toHaveLength(1);
    h.drawer.push(frame(2));
    h.drawer.push(frame(3));
    expect(h.rafs).toHaveLength(0);                // nothing scheduled while a decode is in flight
    await h.resolveAll();                           // frame 1 drawn; frame 3 is now scheduled
    expect(h.drawn.map((d) => d[0])).toEqual([1]);
    expect(h.tick()).toBe(1);
    expect(h.pending.map((p) => p.frame.ts)).toEqual([3]);
    await h.resolveAll();
    expect(h.drawn.map((d) => d[0])).toEqual([1, 3]);
    expect(h.rafs).toHaveLength(0);                // settled: nothing more to draw
  });

  it("skips a frame that fails to decode and goes on with the next; the same frame is not drawn twice", async () => {
    const h = harness();
    h.drawer.push(frame(1));
    h.tick();
    h.drawer.push(frame(2));
    h.pending.splice(0).forEach((p) => p.reject(new Error("bad jpeg")));
    await Promise.resolve(); await Promise.resolve();
    expect(h.tick()).toBe(1);
    await h.resolveAll();
    expect(h.drawn.map((d) => d[0])).toEqual([2]);
    h.drawer.push(frame(2));                        // the frame already on the canvas
    h.tick();
    expect(h.pending).toHaveLength(0);
  });

  it("stop() ends drawing; a null push clears the pending frame", () => {
    const h = harness();
    h.drawer.push(null);
    expect(h.rafs).toHaveLength(0);
    h.drawer.push(frame(1));
    h.drawer.stop();
    h.tick();
    expect(h.pending).toHaveLength(0);
  });

  it("frameBlob turns the data URL back into JPEG bytes", async () => {
    const blob = frameBlob({ src: "data:image/jpeg;base64,/9j/4AA=", w: 1, h: 1, purpose: "preview", ts: 1 });
    expect(blob.type).toBe("image/jpeg");
    expect(new Uint8Array(await bytesOf(blob))).toEqual(new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00]));
  });

  it("the drawer never calls raf more than once per cycle under a burst", () => {
    const raf = vi.fn();
    const drawer = createFrameDrawer(() => null, { raf, decode: () => new Promise(() => {}) });
    for (let i = 1; i <= 50; i++) drawer.push(frame(i));
    expect(raf).toHaveBeenCalledTimes(1);
  });
});
