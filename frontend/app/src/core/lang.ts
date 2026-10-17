// AIRA has no default and no fixed language: it answers in whatever language the speaker used last.
// The app only has to pick which set of on-screen labels (and which local TTS voice) fits that language.
import { LANGS, type Lang } from "@aira/contracts";

const hasLabels = (code: string): code is Lang => (LANGS as readonly string[]).includes(code);

/**
 * The hint for AIRA's very first line, before anyone has spoken: the first of the phone's locales the app
 * has labels for, English otherwise. Sent as `hello.lang`; it never fixes the language of the conversation.
 */
export function langFromPhone(languages: readonly string[] = []): Lang {
  for (const l of languages) {
    const base = l.toLowerCase().split("-")[0];
    if (hasLabels(base)) return base;
  }
  return "en";
}

/**
 * The label language for a code the server detected from the speaker's last utterance (`transcript.lang`).
 * Labels exist in Tamil, Hindi and English; any other language (Japanese, Vietnamese, ...) gets the English
 * labels while AIRA itself keeps answering in that language. No code keeps the current language.
 */
export function uiLangFor(code: string | undefined, current: Lang = "en"): Lang {
  if (!code) return current;
  const base = code.toLowerCase().split("-")[0];
  return hasLabels(base) ? base : "en";
}

/** The phone's locale list, when a browser is around (tests inject their own). */
export function phoneLanguages(): readonly string[] {
  try {
    return navigator.languages?.length ? navigator.languages : [navigator.language];
  } catch {
    return [];
  }
}
