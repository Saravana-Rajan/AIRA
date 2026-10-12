// frontend/app/src/ui/BoxOverlay.tsx
// "Show boxes" (Settings, off by default): a small camera preview in a corner of the phone screen with the
// latest MediaPipe (thin blue, red when the bump guard flags them) and Gemini Robotics-ER 2 (thick, labelled,
// green when the target is found) boxes drawn over it. For sighted helpers and the demo video: a blind owner
// never needs it, so it is aria-hidden and costs nothing while off.
import { useEffect, useRef } from "preact/hooks";
import { getVideo } from "../camera/capture";
import { boxStore as defaultStore, ER2_TTL_MS, MEDIAPIPE_TTL_MS, type BoxSnapshot, type BoxStore } from "../walk/boxes";
import { drawBoxes, type Ctx2D, type DrawBox } from "./boxDraw";

export const PREVIEW_W = 160;
export const PREVIEW_H = 120;

/** What to draw right now: MediaPipe boxes younger than 1 s, ER 2 boxes younger than 6 s. */
export function boxesToDraw(s: BoxSnapshot, now: number): DrawBox[] {
  const out: DrawBox[] = [];
  if (now - s.mediapipe.at <= MEDIAPIPE_TTL_MS) {
    for (const r of s.mediapipe.reports) {
      out.push({ label: r.label, box: r.box, score: r.score, source: "mediapipe", kind: r.danger ? "danger" : "object" });
    }
  }
  if (s.er2 && now - s.er2.at <= ER2_TTL_MS) {
    const target = s.er2.target?.trim().toLowerCase();
    for (const o of s.er2.objects) {
      const isTarget = !!target && o.label.trim().toLowerCase() === target;
      out.push({
        label: o.label, box: o.box, score: o.score, source: "er2",
        kind: o.severity === "danger" ? "danger" : isTarget && s.er2.found ? "found" : "object",
      });
    }
  }
  return out;
}

type Props = {
  store?: BoxStore;
  /** The shared camera <video>'s stream for the preview (default: camera/capture getVideo()). */
  video?: () => HTMLVideoElement | null;
  /** The drawing context (tests pass a recorder; jsdom has no canvas). */
  ctxOf?: (c: HTMLCanvasElement) => Ctx2D | null;
  now?: () => number;
};

export function BoxOverlay({ store = defaultStore, video = getVideo, ctxOf, now }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const preview = useRef<HTMLVideoElement>(null);
  const clock = now ?? (() => performance.now());

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ctx = ctxOf ? ctxOf(c) : (c.getContext("2d") as Ctx2D | null);
    const redraw = (s: BoxSnapshot) => { if (ctx) drawBoxes(ctx, c.width, c.height, boxesToDraw(s, clock())); };
    redraw(store.get());
    const off = store.subscribe(redraw);
    const sweep = setInterval(() => redraw(store.get()), 500);   // stale boxes fade even with no new frame
    return () => { off(); clearInterval(sweep); };
  }, [store, ctxOf]);

  useEffect(() => {
    const v = preview.current;
    if (!v) return;
    const attach = () => {
      const src = video();
      const stream = src?.srcObject ?? null;
      if (stream && v.srcObject !== stream) {
        v.srcObject = stream;
        v.play?.()?.catch?.(() => { /* preview is best effort */ });
      }
    };
    attach();
    const id = setInterval(attach, 1000);   // the camera opens after the Start tap; keep trying quietly
    return () => clearInterval(id);
  }, [video]);

  return (
    <div class="boxes" aria-hidden="true" data-testid="box-overlay">
      <video ref={preview} class="boxes__video" width={PREVIEW_W} height={PREVIEW_H} muted playsInline autoPlay />
      <canvas ref={canvas} class="boxes__canvas" width={PREVIEW_W} height={PREVIEW_H} />
    </div>
  );
}
