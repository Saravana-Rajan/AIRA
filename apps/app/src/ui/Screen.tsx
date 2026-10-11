import type { Lang } from "@aira/contracts";
import { GestureSurface } from "./GestureSurface";
import { VoiceOrb } from "./VoiceOrb";
import { StatusChip } from "./StatusChip";
import { CaptionArea } from "./CaptionArea";
import { TaskCard } from "./TaskCard";
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
  onTalk(): void;
  onStop(): void;
};

/**
 * The phone screen: top bar (logo + status chip), voice orb, live captions and the current task.
 * Everything sits inside ONE full-screen gesture surface; nothing inside is separately tappable.
 * Only phrasing elements (span, img, svg) are used so the markup stays valid inside a <button>.
 */
export function Screen({ state, level = 0, shopName, lang = "en", progress = null, confirmPending = false, onTalk, onStop }: Props) {
  const { status, userText, airaText, task } = state;
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

        <span class="stage">
          <VoiceOrb level={level} />
        </span>

        <CaptionArea userText={userText} airaText={airaText} lang={lang} />

        <TaskCard key={task?.title} task={task} lang={lang} progress={progress} confirmPending={confirmPending} />
      </span>
    </GestureSurface>
  );
}
