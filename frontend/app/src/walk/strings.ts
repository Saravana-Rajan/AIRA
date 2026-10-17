// frontend/app/src/walk/strings.ts
// Bump-guard phrases (keys bump.stop / bump.left_right) in ta / hi / en. Short on purpose.
// A native speaker must proof-read the ta and hi strings before the demo.
import type { Lang } from "@aira/contracts";
import type { BumpPhrase, Side } from "./bump";

const STOP: Record<Lang, string> = { en: "Stop", ta: "நில்லுங்கள்", hi: "रुकिए" };
const LEFT: Record<Lang, string> = { en: "Obstacle left", ta: "இடது பக்கம் தடை", hi: "बाईं ओर रुकावट" };
const RIGHT: Record<Lang, string> = { en: "Obstacle right", ta: "வலது பக்கம் தடை", hi: "दाईं ओर रुकावट" };

export function bumpText(phrase: BumpPhrase, side: Side, lang: Lang): string {
  const table = phrase === "bump.stop" || side === "centre" ? STOP : side === "left" ? LEFT : RIGHT;
  return table[lang] ?? table.en;
}

const MODE_ON: Record<Lang, string> = { en: "Walking mode on.", ta: "நடை முறை ஆன்.", hi: "वॉकिंग मोड चालू।" };
const MODE_OFF: Record<Lang, string> = { en: "Walking mode off.", ta: "நடை முறை ஆஃப்.", hi: "वॉकिंग मोड बंद।" };

/** Spoken confirmation when walking mode is switched on / off. */
export function walkingModeText(on: boolean, lang: Lang): string {
  const table = on ? MODE_ON : MODE_OFF;
  return table[lang] ?? table.en;
}
