// Speaks camera hints, throttled per kind (default once every 8 s). Pure apart from the injected deps.
// A hint repeats only after the gap; a different hint is new. Aim hints are a loop (every frame can raise
// one), so the gap is what keeps the owner from hearing the same line over and over.
import type { Lang } from "@aira/contracts";
import { hintText, type HintKey } from "../i18n/camera";
import { clientLog } from "../core/clientLog";

export const HINT_GAP_MS = 8000;

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
    if (prev !== undefined && t - prev < minGapMs) {
      // Diagnostics: a hint the gap swallowed (aim hints come every frame, so these lines are spaced out).
      clientLog("hint", { key, dropped: "gap", sinceMs: t - prev }, { minGapMs: 2000 });
      return false;
    }
    lastAt.set(key, t);
    const l = lang();
    speak(hintText(key, l));
    clientLog("hint", { key, lang: l });
    return true;
  };
}
