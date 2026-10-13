// frontend/app/src/ui/CameraPreview.tsx
// The live camera on the phone screen, for a sighted tester or helper: a large card in the upper part of the
// main screen showing the SAME stream the frames come from (camera/capture getVideo(); never a second
// getUserMedia), the on-device MediaPipe boxes drawn over it, and an "AIRA is looking" badge while a tool frame
// (ask, count, read, walk...) is captured. Display only: it never starts, stops or encodes anything.
// The card is aria-hidden (TalkBack users are unaffected); "Hide camera" / "Show camera" is a real button
// that sits outside the one gesture surface, and the choice is kept in localStorage.
import { useEffect, useRef, useState } from "preact/hooks";
import type { FramePurpose } from "@aira/contracts";
import { cameraDenied, getVideo } from "../camera/capture";
import { currentLooking, lookingKind, subscribeLooking } from "../camera/looking";
import { boxStore as defaultStore, type BoxSnapshot, type BoxStore } from "../walk/boxes";
import { boxesToDraw } from "./BoxOverlay";
import { drawBoxes, type Ctx2D } from "./boxDraw";

export const PREVIEW_KEY = "aira.cameraPreview";
/** The badge stays this long after a tool capture ends, so a 100 ms capture is still seen. */
export const LOOKING_LINGER_MS = 1200;
/** Canvas drawing width; the height follows the stream's aspect. */
export const OVERLAY_W = 360;

export const PREVIEW_TEXT = {
  looking: "AIRA is looking",
  counting: "Counting…",
  reading: "Reading…",
  waiting: "Camera starting…",
  denied: "Camera is off (no permission)",
  hide: "Hide camera",
  show: "Show camera",
} as const;

/** Saved preview choice: shown unless the helper hid it. Storage may be missing or throw. */
export function loadPreviewShown(storage: () => Storage | null = () => globalThis.localStorage ?? null): boolean {
  try {
    return storage()?.getItem(PREVIEW_KEY) !== "hidden";
  } catch {
    return true;
  }
}

export function savePreviewShown(shown: boolean, storage: () => Storage | null = () => globalThis.localStorage ?? null): void {
  try {
    storage()?.setItem(PREVIEW_KEY, shown ? "shown" : "hidden");
  } catch {
    /* private window or blocked storage: the choice lasts this page only */
  }
}

/** The preview's shown/hidden state, persisted. */
export function usePreviewShown(): [boolean, () => void] {
  const [shown, setShown] = useState(loadPreviewShown);
  const toggle = () => setShown((s) => { savePreviewShown(!s); return !s; });
  return [shown, toggle];
}

type ToggleProps = { shown: boolean; onToggle(): void };

/** "Hide camera" / "Show camera". A real button outside the gesture surface (no nesting, no focus trap). */
export function CameraToggle({ shown, onToggle }: ToggleProps) {
  const label = shown ? PREVIEW_TEXT.hide : PREVIEW_TEXT.show;
  return (
    <button type="button" class="cam-toggle" aria-pressed={shown} onClick={onToggle} data-testid="camera-toggle">
      {label}
    </button>
  );
}

type Props = {
  store?: BoxStore;
  /** The shared camera <video> (default: camera/capture getVideo()). */
  video?: () => HTMLVideoElement | null;
  /** True when the camera could not be opened (default: camera/capture cameraDenied()). */
  denied?: () => boolean;
  /** The tool capture in progress and its subscription (default: camera/looking). */
  looking?: () => FramePurpose | null;
  subscribe?: (fn: (p: FramePurpose | null) => void) => () => void;
  /** The drawing context (tests pass a recorder; jsdom has no canvas). */
  ctxOf?: (c: HTMLCanvasElement) => Ctx2D | null;
  now?: () => number;
  /** How often the stream is looked for while the camera is not open yet (ms). */
  pollMs?: number;
};

/** The big preview card. Rendered inside the gesture surface, so only phrasing elements are used. */
export function CameraPreview({
  store = defaultStore, video = getVideo, denied = cameraDenied, looking = currentLooking,
  subscribe = subscribeLooking, ctxOf, now, pollMs = 1000,
}: Props) {
  const el = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [stream, setStream] = useState<MediaProvider | null>(null);
  const [isDenied, setDenied] = useState(false);
  const [aspect, setAspect] = useState<{ w: number; h: number }>({ w: 3, h: 4 });
  const [badge, setBadge] = useState<FramePurpose | null>(() => looking());
  const clock = now ?? (() => performance.now());

  // Find the shared stream (the camera opens after Start) and attach it. Never opens or stops a camera.
  useEffect(() => {
    const find = () => {
      const src = video();
      const s = src?.srcObject ?? null;
      setStream((cur) => (cur === s ? cur : s));
      setDenied(!s && denied());
      if (src && src.videoWidth > 0 && src.videoHeight > 0) {
        const w = src.videoWidth;
        const h = src.videoHeight;
        setAspect((a) => (a.w === w && a.h === h ? a : { w, h }));
      }
    };
    find();
    const id = setInterval(find, pollMs);
    return () => clearInterval(id);
  }, [video, denied, pollMs]);

  useEffect(() => {
    const v = el.current;
    if (!v || !stream) return;
    if (v.srcObject !== stream) {
      v.srcObject = stream;
      try {
        v.play?.()?.catch?.(() => { /* best effort: the frames do not depend on it */ });
      } catch {
        /* jsdom / old browsers */
      }
    }
    return () => {
      // Detach only this element; the shared stream and its tracks keep running.
      try { v.srcObject = null; } catch { /* ignore */ }
    };
  }, [stream]);

  // Boxes: the latest on-device detections, redrawn on each update and swept so stale ones fade.
  const overlayH = Math.round((OVERLAY_W * aspect.h) / aspect.w);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !stream) return;   // nothing to draw over until the camera is showing
    let ctx: Ctx2D | null = null;
    try {
      ctx = ctxOf ? ctxOf(c) : (c.getContext("2d") as Ctx2D | null);
    } catch {
      ctx = null;
    }
    const redraw = (s: BoxSnapshot) => { if (ctx) drawBoxes(ctx, c.width, c.height, boxesToDraw(s, clock()), { tagAll: true }); };
    redraw(store.get());
    const off = store.subscribe(redraw);
    const sweep = setInterval(() => redraw(store.get()), 500);
    return () => { off(); clearInterval(sweep); };
  }, [store, ctxOf, overlayH, stream]);

  // "AIRA is looking": on while a tool frame is captured, then lingers a moment.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const off = subscribe((p) => {
      if (p) {
        if (timer) clearTimeout(timer);
        timer = null;
        setBadge(p);
      } else {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => { timer = null; setBadge(null); }, LOOKING_LINGER_MS);
      }
    });
    return () => { off(); if (timer) clearTimeout(timer); };
  }, [subscribe]);

  const kind = badge ? lookingKind(badge) : null;
  return (
    <span
      class="cam-card"
      aria-hidden="true"
      data-testid="camera-preview"
      style={{ aspectRatio: `${aspect.w} / ${aspect.h}`, "--cam-ar": String(aspect.w / aspect.h) }}
    >
      <video ref={el} class="cam-card__video" muted playsInline autoPlay />
      <canvas ref={canvas} class="cam-card__boxes" width={OVERLAY_W} height={overlayH} data-testid="camera-boxes" />
      {!stream ? (
        <span class="cam-card__placeholder" data-testid="camera-placeholder">
          {isDenied ? PREVIEW_TEXT.denied : PREVIEW_TEXT.waiting}
        </span>
      ) : null}
      {kind ? (
        <span class="cam-card__badge" data-testid="looking-badge" data-kind={kind}>
          <span class="cam-card__dot" />
          {PREVIEW_TEXT.looking}
          {kind !== "looking" ? <span class="cam-card__purpose">{PREVIEW_TEXT[kind]}</span> : null}
        </span>
      ) : null}
    </span>
  );
}
