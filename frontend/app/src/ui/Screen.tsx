import type { ComponentChildren } from "preact";
import type { Lang } from "@aira/contracts";
import { GestureSurface } from "./GestureSurface";
import { VoiceOrb } from "./VoiceOrb";
import { StatusChip } from "./StatusChip";
import { CaptionArea } from "./CaptionArea";
import { TaskCard } from "./TaskCard";
import { LABELS } from "../i18n/strings";
import type { CountProgress } from "./view";
import type { ScreenState } from "./screenState";

export const SURFACE_LABEL = "AIRA. Double-tap to talk. Long-press to stop.";

type Props = {
  state: ScreenState;
  /** Mic or speaker loudness, 0..1. */
  level?: number;
  shopName?: string;
  /** Language of the on-screen hints and speaker names: the one the speaker last used (English labels when not given). */
  lang?: Lang;
  /** A live delivery count, drawn as a bar on the task card. */
  progress?: CountProgress | null;
  /** True while a confirm waits for double-tap (yes) or long-press (no). */
  confirmPending?: boolean;
  /** The live camera card (ui/CameraPreview), shown under the top bar; visual only (aria-hidden). */
  preview?: ComponentChildren;
  onTalk(): void;
  onStop(): void;
};

/**
 * The phone screen, top to bottom: top bar (logo, shop name, status chip), voice orb, the caption
 * card, the current task card and the gesture hint. It is laid out as one phone-width column
 * (`.screen`, 480 px max, centred), so on a laptop it still reads as a phone app.
 * Everything sits inside ONE full-screen gesture surface; nothing inside is separately tappable.
 * Only phrasing elements (span, img, svg) are used so the markup stays valid inside a <button>.
 */
export function Screen({ state, level = 0, shopName, lang = "en", progress = null, confirmPending = false, preview, onTalk, onStop }: Props) {
  const { status, userText, airaText, task } = state;
  const started = Boolean(userText || airaText);
  return (
    <GestureSurface label={SURFACE_LABEL} onTalk={onTalk} onStop={onStop}>
      <span class="screen" data-status={status.kind}>
        <span class="topbar">
          <span class="brand">
            <img class="brand__logo" src="/icons/aira-logo-192.png" alt="" width={48} height={48} />
            {shopName ? <span class="brand__shop">{shopName}</span> : null}
          </span>
          <StatusChip kind={status.kind} label={status.label} />
        </span>

        {preview ?? null}

        <span class="stage">
          <VoiceOrb level={level} />
        </span>

        <CaptionArea userText={userText} airaText={airaText} lang={lang} />

        <TaskCard key={task?.title} task={task} lang={lang} progress={progress} confirmPending={confirmPending} />

        {/* The gesture reminder shows exactly once: in the empty caption card before the first word, down here after. */}
        {started ? <span class="hint">{LABELS.hintTalk[lang]}</span> : null}
      </span>
    </GestureSurface>
  );
}
