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
import { coverFit, drawBoxes, type Ctx2D } from "./boxDraw";

export const PREVIEW_KEY = "aira.cameraPreview";
/** The helper's full-screen choice (ui/LiveScreen): "full" (default while the camera runs) or "card". */
export const FULL_KEY = "aira.cameraFull";
/** The badge stays this long after a tool capture ends, so a 100 ms capture is still seen. */
export const LOOKING_LINGER_MS = 1200;
/** Canvas drawing width; the height follows the stream's aspect. */
export const OVERLAY_W = 360;

export const PREVIEW_TEXT = {
  looking: "AIRA is looking",
  shared: "Camera on · shared with AIRA",
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

/** Saved full-screen choice: full screen unless the helper left it. Storage may be missing or throw. */
export function loadFullScreen(storage: () => Storage | null = () => globalThis.localStorage ?? null): boolean {
  try {
    return storage()?.getItem(FULL_KEY) !== "card";
  } catch {
    return true;
  }
}

export function saveFullScreen(full: boolean, storage: () => Storage | null = () => globalThis.localStorage ?? null): void {
  try {
    storage()?.setItem(FULL_KEY, full ? "full" : "card");
  } catch {
    /* private window or blocked storage: the choice lasts this page only */
  }
}

/** The full-screen camera choice, persisted. */
export function useFullScreen(): [boolean, () => void] {
  const [full, setFull] = useState(loadFullScreen);
  const toggle = () => setFull((f) => { saveFullScreen(!f); return !f; });
  return [full, toggle];
}

/**
 * True while the shared camera <video> carries a stream (the camera opens after Start, from the camera wiring;
 * this only watches). Polled, like the preview itself: nothing fires an event when capture.ts opens the camera.
 */
export function useCameraOn(video: () => HTMLVideoElement | null = getVideo, pollMs = 1000): boolean {
  const [on, setOn] = useState(() => Boolean(video()?.srcObject));
  useEffect(() => {
    const look = () => setOn(Boolean(video()?.srcObject));
    look();
    const id = setInterval(look, pollMs);
    return () => clearInterval(id);
  }, [video, pollMs]);
  return on;
}

type ToggleProps = {
  shown: boolean;
  onToggle(): void;
  /** A round icon button for the full-screen control row (the text becomes the aria-label). */
  round?: boolean;
};

/** A crossed-out camera (hide) or a camera (show), 24 x 24 strokes. */
const CAMERA_ICON = {
  hide: "M3 3l18 18M9 6h6l2 3h3a1 1 0 0 1 1 1v8M4.5 9H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h12.5M12 11a3 3 0 0 1 3 3",
  show: "M9 6h6l2 3h3a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1h3l2-3zM12 17a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
} as const;

/** "Hide camera" / "Show camera". A real button outside the gesture surface (no nesting, no focus trap). */
export function CameraToggle({ shown, onToggle, round = false }: ToggleProps) {
  const label = shown ? PREVIEW_TEXT.hide : PREVIEW_TEXT.show;
  if (round) {
    return (
      <button type="button" class="cam-toggle cam-toggle--round" aria-pressed={shown} aria-label={label} onClick={onToggle} data-testid="camera-toggle">
        <svg class="cam-toggle__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d={shown ? CAMERA_ICON.hide : CAMERA_ICON.show} />
        </svg>
      </button>
    );
  }
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
  /**
   * Full screen (ui/LiveScreen): the card fills its parent edge to edge with `object-fit: cover`, and the box
   * canvas takes the parent's size, so boxes are mapped through the cover crop (boxDraw coverFit).
   */
  fill?: boolean;
  /** The size of the full-screen box (tests inject; default: the card's own size, else the window's). */
  sizeOf?: (card: HTMLElement) => { w: number; h: number };
};

function cardSize(card: HTMLElement): { w: number; h: number } {
  const w = card.clientWidth || window.innerWidth;
  const h = card.clientHeight || window.innerHeight;
  return { w: Math.max(1, Math.round(w)), h: Math.max(1, Math.round(h)) };
}

/** The big preview card. Rendered inside the gesture surface, so only phrasing elements are used. */
export function CameraPreview({
  store = defaultStore, video = getVideo, denied = cameraDenied, looking = currentLooking,
  subscribe = subscribeLooking, ctxOf, now, pollMs = 1000, fill = false, sizeOf = cardSize,
}: Props) {
  const el = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const card = useRef<HTMLElement>(null);
  const [stream, setStream] = useState<MediaProvider | null>(null);
  const [isDenied, setDenied] = useState(false);
  const [aspect, setAspect] = useState<{ w: number; h: number }>({ w: 3, h: 4 });
  const [size, setSize] = useState<{ w: number; h: number }>({ w: OVERLAY_W, h: OVERLAY_W });
  const [badge, setBadge] = useState<FramePurpose | null>(() => looking());
  const clock = now ?? (() => performance.now());

  // Full screen: the canvas follows the card's size (a rotation changes it), so boxes stay in place.
  useEffect(() => {
    if (!fill) return;
    const measure = () => {
      const c = card.current;
      if (!c) return;
      const s = sizeOf(c);
      setSize((cur) => (cur.w === s.w && cur.h === s.h ? cur : s));
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    const id = setInterval(measure, pollMs);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
      clearInterval(id);
    };
  }, [fill, sizeOf, pollMs]);

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
  // In the card the canvas has the stream's aspect (boxes map 1:1); full screen it has the screen's, and the
  // boxes are drawn inside the cover crop of the video.
  const overlayW = fill ? size.w : OVERLAY_W;
  const overlayH = fill ? size.h : Math.round((OVERLAY_W * aspect.h) / aspect.w);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !stream) return;   // nothing to draw over until the camera is showing
    let ctx: Ctx2D | null = null;
    try {
      ctx = ctxOf ? ctxOf(c) : (c.getContext("2d") as Ctx2D | null);
    } catch {
      ctx = null;
    }
    const fit = fill ? coverFit(aspect, { w: overlayW, h: overlayH }) : undefined;
    const redraw = (s: BoxSnapshot) => { if (ctx) drawBoxes(ctx, c.width, c.height, boxesToDraw(s, clock()), { tagAll: true, fit }); };
    redraw(store.get());
    const off = store.subscribe(redraw);
    const sweep = setInterval(() => redraw(store.get()), 500);
    return () => { off(); clearInterval(sweep); };
  }, [store, ctxOf, overlayW, overlayH, stream, fill, aspect]);

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
      ref={card}
      class={fill ? "cam-card cam-card--fill" : "cam-card"}
      aria-hidden="true"
      data-testid="camera-preview"
      data-fill={fill ? "true" : undefined}
      style={fill ? undefined : { aspectRatio: `${aspect.w} / ${aspect.h}`, "--cam-ar": String(aspect.w / aspect.h) }}
    >
      <video ref={el} class="cam-card__video" muted playsInline autoPlay />
      <canvas ref={canvas} class="cam-card__boxes" width={overlayW} height={overlayH} data-testid="camera-boxes" />
      {!stream ? (
        <span class="cam-card__placeholder" data-testid="camera-placeholder">
          {isDenied ? PREVIEW_TEXT.denied : PREVIEW_TEXT.waiting}
        </span>
      ) : null}
      {stream ? (
        // Always on with the camera: live video and background frames go to AIRA continuously, not only
        // while the "looking" badge below is lit for a question's photo.
        <span class="cam-card__shared" data-testid="camera-shared">{PREVIEW_TEXT.shared}</span>
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
