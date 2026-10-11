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
 * Live captions in large high-contrast text (28 px user, 32 px AIRA); they double as captions in
 * the demo video. The speaker name ("You", "AIRA", localised) runs in before each line from
 * `data-who`, so the text node holds only the words. Long lines lose their OLDEST words and stay
 * anchored to the bottom, so the newest words always show. With nothing said yet, it shows how
 * to start. Phrasing content only.
 */
export function CaptionArea({ userText, airaText, lang, max = 140 }: Props) {
  if (!userText && !airaText) {
    return (
      <span class="captions">
        <span class="caption caption--hint">{LABELS.hintTalk[lang]}</span>
      </span>
    );
  }
  return (
    <span class="captions">
      {userText ? (
        <span class="caption caption--user">
          <span class="caption__text" data-who={LABELS.you[lang]}>{captionTail(userText, max)}</span>
        </span>
      ) : null}
      {airaText ? (
        <span class="caption caption--aira">
          <span class="caption__text" data-who={LABELS.aira[lang]}>{captionTail(airaText, max)}</span>
        </span>
      ) : null}
    </span>
  );
}
