import "./screen-parts.css";
import type { Lang } from "@aira/contracts";
import { LABELS } from "../i18n/strings";
import { captionTail } from "./view";

type Props = {
  /** Last thing the shopkeeper said. */
  userText: string;
  /** Last thing AIRA said. */
  airaText: string;
  lang: Lang;
  /** Longest caption kept, in characters; older words are cut from the front. */
  max?: number;
};

/**
 * The last exchange as one card: a small "You" label over the shopkeeper's words (muted), then an
 * "AIRA" label in brand blue over AIRA's words (navy, a step larger). Both lines share one clamp()
 * type scale, wrap freely at line-height 1.5 (Tamil needs the room) and are capped at three lines;
 * a longer line loses its OLDEST words and stays anchored to the bottom, so the newest words
 * always show. The card doubles as captions in the demo video. With nothing said yet, it carries
 * the gesture hint instead. Phrasing content only: it sits inside the GestureSurface button.
 */
export function CaptionArea({ userText, airaText, lang, max = 140 }: Props) {
  if (!userText && !airaText) {
    return (
      <span class="captions captions--empty">
        <span class="caption caption--hint">{LABELS.hintTalk[lang]}</span>
      </span>
    );
  }
  return (
    <span class="captions">
      {userText ? (
        <span class="caption caption--user">
          <span class="caption__who">{LABELS.you[lang]}</span>
          <span class="caption__text">{captionTail(userText, max)}</span>
        </span>
      ) : null}
      {airaText ? (
        <span class="caption caption--aira">
          <span class="caption__who">{LABELS.aira[lang]}</span>
          <span class="caption__text">{captionTail(airaText, max)}</span>
        </span>
      ) : null}
    </span>
  );
}
