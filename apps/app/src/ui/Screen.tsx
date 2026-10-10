import { GestureSurface } from "./GestureSurface";
import { VoiceOrb } from "./VoiceOrb";
import type { ScreenState } from "./screenState";

export const SURFACE_LABEL = "AIRA. Double-tap to talk. Long-press to stop.";

type Props = {
  state: ScreenState;
  /** Mic or speaker loudness, 0..1. */
  level?: number;
  shopName?: string;
  onTalk(): void;
  onStop(): void;
};

/**
 * The phone screen: top bar (logo + status chip), voice orb, live captions and the current task.
 * Everything sits inside ONE full-screen gesture surface; nothing inside is separately tappable.
 * Only phrasing elements (span, img, svg) are used so the markup stays valid inside a <button>.
 */
export function Screen({ state, level = 0, shopName, onTalk, onStop }: Props) {
  const { status, userText, airaText, task } = state;
  const idle = !userText && !airaText;
  return (
    <GestureSurface label={SURFACE_LABEL} onTalk={onTalk} onStop={onStop}>
      <span class="screen" data-status={status.kind}>
        <span class="topbar">
          <span class="brand">
            <img class="brand__logo" src="/icons/aira-logo-192.png" alt="" width={48} height={48} />
            {shopName ? <span class="brand__shop">{shopName}</span> : null}
          </span>
          <span class="status-chip">
            <span class="status-chip__dot" />
            {status.label}
          </span>
        </span>

        <span class="stage">
          <VoiceOrb level={level} />
        </span>

        <span class="captions">
          {idle ? (
            <span class="caption caption--hint">Double-tap anywhere to talk. Long-press to stop.</span>
          ) : null}
          {userText ? (
            <span class="caption caption--user">
              <span class="caption__text">{userText}</span>
            </span>
          ) : null}
          {airaText ? (
            <span class="caption caption--aira">
              <span class="caption__text">{airaText}</span>
            </span>
          ) : null}
        </span>

        {task ? (
          <span class="task-card" data-tone={task.tone} key={task.title}>
            <span class="task-card__mark" />
            <span class="task-card__body">
              <span class="task-card__title">{task.title}</span>
              <span class="task-card__detail">{task.detail}</span>
            </span>
          </span>
        ) : null}
      </span>
    </GestureSurface>
  );
}
