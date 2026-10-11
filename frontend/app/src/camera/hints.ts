// Speaks camera hints, throttled per kind (default once every 4 s). Pure apart from the injected deps.
import type { Lang } from "@aira/contracts";
import { hintText, type HintKey } from "../i18n/camera";

export const HINT_GAP_MS = 4000;

export function createHintSpeaker(
  speak: (s: string) => void,
  lang: () => Lang,
  now: () => number = () => Date.now(),
  minGapMs = HINT_GAP_MS,
): (key: HintKey) => boolean {
  const lastAt = new Map<HintKey, number>();
  return (key) => {
    const t = now();
    const prev = lastAt.get(key);
    if (prev !== undefined && t - prev < minGapMs) return false;
    lastAt.set(key, t);
    speak(hintText(key, lang()));
    return true;
  };
}
