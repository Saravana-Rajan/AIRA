// frontend/app/src/ui/LiveScreen.tsx
// The full-screen camera layout (like a live video call): while the camera runs, the live picture fills the
// whole phone edge to edge, and everything else floats over it. Top: the status chip (left) and "Exit full
// screen" (right). Bottom, over a dark fade so the words stay readable: the last exchange as two caption bubbles
// (the owner's words right-aligned, AIRA's reply left with a small "AIRA" label), the task card, and a row of
// large round buttons: Talk, Hide camera, Stop (red). The camera and the captions sit INSIDE the one gesture
// surface, so a double-tap / long-press anywhere on the picture still talks / stops exactly as before; the
// buttons are real buttons outside it (never nested), each a 56 px target with an aria-label.
// For a sighted helper or the demo video: the owner is blind and never needs it, so the picture and captions
// are aria-hidden (AIRA's words are the Live audio he hears; the "You" line is what he just said).
import "./live.css";
import { useEffect } from "preact/hooks";
import type { ComponentChildren } from "preact";
import type { Lang } from "@aira/contracts";
import { GestureSurface } from "./GestureSurface";
import { StatusChip } from "./StatusChip";
import { TaskCard } from "./TaskCard";
import { CameraToggle } from "./CameraPreview";
import { SURFACE_LABEL } from "./Screen";
import { LABELS } from "../i18n/strings";
import { captionTail, type CountProgress } from "./view";
import type { ScreenState } from "./screenState";

/** Helper-facing control names (English, like the camera card's own text). */
export const LIVE_TEXT = {
  talk: "Talk to AIRA",
  stop: "Stop AIRA",
  exit: "Exit full screen",
  enter: "Full screen camera",
} as const;

/** The class on <html> while the live layout is up: no page scroll behind the fixed screen. */
export const LIVE_HTML_CLASS = "live-full";

// 24 x 24 stroke icons: a microphone, a square (stop), and "shrink" arrows (leave full screen).
const ICON = {
  mic: "M12 3a3 3 0 0 1 3 3v6a3 3 0 1 1-6 0V6a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v4M9 21h6",
  stop: "M7 7h10v10H7z",
  exit: "M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5",
} as const;

type Props = {
  state: ScreenState;
  /** Mic or speaker loudness, 0..1 (swells the Talk button). */
  level?: number;
  lang?: Lang;
  progress?: CountProgress | null;
  confirmPending?: boolean;
  /** The full-screen camera (ui/CameraPreview with `fill`); visual only (aria-hidden). */
  preview: ComponentChildren;
  /** Longest caption kept, in characters. */
  max?: number;
  previewShown: boolean;
  onTogglePreview(): void;
  onTalk(): void;
  onStop(): void;
  /** Back to the card layout (the choice is remembered by the caller). */
  onExit(): void;
};

export function LiveScreen({
  state, level = 0, lang = "en", progress = null, confirmPending = false, preview, max = 140,
  previewShown, onTogglePreview, onTalk, onStop, onExit,
}: Props) {
  const { status, userText, airaText, task } = state;
  const lv = Math.min(1, Math.max(0, Number.isFinite(level) ? level : 0));

  // The screen is fixed over the page: stop the page itself from scrolling or bouncing behind it.
  useEffect(() => {
    const html = document.documentElement;
    html.classList.add(LIVE_HTML_CLASS);
    return () => html.classList.remove(LIVE_HTML_CLASS);
  }, []);

  return (
    <div class="live" data-status={status.kind} data-testid="live-screen" style={{ "--level": String(lv) }}>
      <GestureSurface label={SURFACE_LABEL} onTalk={onTalk} onStop={onStop}>
        <span class="live__camera">{preview}</span>
        <span class="live__shade live__shade--top" aria-hidden="true" />
        <span class="live__shade live__shade--bottom" aria-hidden="true" />
        <span class="live__captions" aria-hidden="true" data-testid="live-captions">
          {!userText && !airaText ? (
            <span class="live__bubble live__bubble--hint">{LABELS.hintTalk[lang]}</span>
          ) : null}
          {userText ? (
            <span class="live__bubble live__bubble--user" data-testid="live-user">
              <span class="live__text">{captionTail(userText, max)}</span>
            </span>
          ) : null}
          {airaText ? (
            <span class="live__bubble live__bubble--aira" data-testid="live-aira">
              <span class="live__who">{LABELS.aira[lang]}</span>
              <span class="live__text">{captionTail(airaText, max)}</span>
            </span>
          ) : null}
          <TaskCard key={task?.title} task={task} lang={lang} progress={progress} confirmPending={confirmPending} />
        </span>
      </GestureSurface>

      {/* Real buttons: siblings of the surface, floating over it. */}
      <div class="live__top">
        <StatusChip kind={status.kind} label={status.label} />
        <button type="button" class="live__btn live__btn--exit" aria-label={LIVE_TEXT.exit} onClick={onExit} data-testid="live-exit">
          <svg class="live__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={ICON.exit} /></svg>
        </button>
      </div>
      <div class="live__controls">
        <button type="button" class="live__btn live__btn--talk" aria-label={LIVE_TEXT.talk} onClick={onTalk} data-testid="live-talk">
          <svg class="live__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={ICON.mic} /></svg>
        </button>
        <CameraToggle shown={previewShown} onToggle={onTogglePreview} round />
        <button type="button" class="live__btn live__btn--stop" aria-label={LIVE_TEXT.stop} onClick={onStop} data-testid="live-stop">
          <svg class="live__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={ICON.stop} /></svg>
        </button>
      </div>
    </div>
  );
}

/** "Full screen camera": the way back into the live layout from the card layout (bottom-right, by the toggle). */
export function FullScreenButton({ onClick }: { onClick(): void }) {
  return (
    <button type="button" class="cam-toggle cam-toggle--round" aria-label={LIVE_TEXT.enter} onClick={onClick} data-testid="live-enter">
      <svg class="cam-toggle__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
      </svg>
    </button>
  );
}
