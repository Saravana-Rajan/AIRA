/**
 * Frame-source override for demo mode (/try).
 *
 * When set, captureFrame() draws from this demo <video>/<img>/<canvas> instead of the
 * live camera. The server pipeline is unchanged; only the pixels come from somewhere else.
 * Leaving /try MUST call setFrameSource(null) so the real camera is used again.
 */
export type FrameSourceEl = HTMLVideoElement | HTMLImageElement | HTMLCanvasElement;

let override: FrameSourceEl | null = null;

export function setFrameSource(el: FrameSourceEl | null): void {
  override = el;
}

export function getFrameSource(): FrameSourceEl | null {
  return override;
}

/** Read at capture time: a demo <video> only knows its size once its metadata has loaded. */
function sizeOf(el: FrameSourceEl): { w: number; h: number } {
  if (el instanceof HTMLVideoElement) return { w: el.videoWidth, h: el.videoHeight };
  if (el instanceof HTMLImageElement) return { w: el.naturalWidth, h: el.naturalHeight };
  return { w: el.width, h: el.height };
}

/** The element captureFrame() must draw from: the demo override if set, otherwise the live camera. */
export function pickSource(camera: HTMLVideoElement): { el: CanvasImageSource; w: number; h: number } {
  const el = override ?? camera;
  return { el, ...sizeOf(el) };
}

/**
 * The active override in the `{ width, height, draw }` shape captureFrame() uses for its
 * camera source, or null when no demo media is active. Lets captureFrame() skip
 * getUserMedia entirely in demo mode, so judges' laptops are never asked for the camera.
 */
export function overrideSource(): {
  width(): number;
  height(): number;
  draw(ctx: CanvasRenderingContext2D, w: number, h: number): void;
} | null {
  const el = override;
  if (!el) return null;
  return {
    width: () => sizeOf(el).w,
    height: () => sizeOf(el).h,
    draw: (ctx, w, h) => ctx.drawImage(el, 0, 0, w, h),
  };
}
