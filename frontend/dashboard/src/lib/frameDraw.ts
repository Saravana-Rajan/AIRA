// Paints the newest camera frame onto a canvas, and only the newest: frames that arrive while one is being
// decoded or waiting for the next animation frame replace it instead of queueing behind it, so the picture on
// the dashboard is never older than one decode. Decoding uses createImageBitmap (off the main thread, no
// layout) when the browser has it, else an Image. The DOM pieces (requestAnimationFrame, the decoder) are
// injected so tests drive them.
import type { Frame } from "./monitor";

/** Anything drawImage accepts; a bitmap is closed once drawn. */
export type Drawable = (ImageBitmap | HTMLImageElement) & { close?: () => void };

export interface DrawDeps {
  raf: (cb: () => void) => void;
  decode: (frame: Frame) => Promise<Drawable>;
}

export interface FrameDrawer {
  /** The newest frame to show; an older one still waiting is forgotten. */
  push(frame: Frame | null): void;
  /** Timestamp (frame.ts) of the frame on the canvas, or null before the first draw. */
  drawnTs(): number | null;
  stop(): void;
}

const base64Of = (src: string) => src.slice(src.indexOf(",") + 1);

/** The JPEG bytes of a frame's data URL as a Blob (base64 -> bytes, no fetch). */
export function frameBlob(frame: Frame): Blob {
  const bin = atob(base64Of(frame.src));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/jpeg" });
}

/** createImageBitmap when the browser has it (decodes off the main thread), else an Image that loads the data URL. */
export function browserDecode(frame: Frame): Promise<Drawable> {
  if (typeof createImageBitmap === "function") {
    return createImageBitmap(frameBlob(frame));
  }
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("frame decode failed"));
    img.src = frame.src;
  });
}

export const browserDeps: DrawDeps = {
  raf: (cb) => requestAnimationFrame(cb),
  decode: browserDecode,
};

/**
 * One drawer per canvas. `push` keeps the latest frame and schedules one animation frame; when it fires, the
 * latest frame is decoded and drawn, and if a newer one arrived meanwhile the cycle runs once more for it.
 * There is never more than one decode in flight and never a backlog.
 */
export function createFrameDrawer(getCanvas: () => HTMLCanvasElement | null, deps: DrawDeps = browserDeps): FrameDrawer {
  let latest: Frame | null = null;
  let scheduled = false;
  let decoding = false;
  let drawn: number | null = null;
  let stopped = false;

  const schedule = () => {
    if (scheduled || decoding || stopped) return;
    scheduled = true;
    deps.raf(run);
  };

  const run = () => {
    scheduled = false;
    const frame = latest;
    if (stopped || !frame || frame.ts === drawn) return;
    decoding = true;
    deps.decode(frame).then(
      (img) => {
        decoding = false;
        try {
          if (!stopped) paint(frame, img);
        } finally {
          img.close?.();
        }
        if (latest !== frame) schedule(); // a newer frame came while decoding: show it next
      },
      () => {
        decoding = false;
        if (latest !== frame) schedule(); // a broken frame is skipped; the next one is drawn
      },
    );
  };

  const paint = (frame: Frame, img: Drawable) => {
    const canvas = getCanvas();
    if (!canvas) return;
    if (canvas.width !== frame.w) canvas.width = frame.w;
    if (canvas.height !== frame.h) canvas.height = frame.h;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      /* no 2D canvas here (jsdom): nothing to paint on */
    }
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, frame.w, frame.h);
    drawn = frame.ts;
  };

  return {
    push(frame) {
      latest = frame;
      if (frame) schedule();
    },
    drawnTs: () => drawn,
    stop() {
      stopped = true;
      latest = null;
    },
  };
}
