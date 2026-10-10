// apps/app/src/walk/strings.ts
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
