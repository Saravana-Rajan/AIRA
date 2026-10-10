// Camera hints, hand-guidance micro-cues and the count read-back. Pure, client-side only.
// Every string exists in ta, hi and en; keep sentences short.
// The ta and hi text must be proof-read by a native speaker on the team before the demo.
import type { Lang } from "@aira/contracts";

export type HintKey = "tilt_down" | "tilt_up" | "dark" | "blurry" | "no_camera" | "show_hand";
export type CueWord = "left" | "right" | "up" | "down" | "stop";

type S = Record<Lang, string>;

const HINTS: Record<HintKey, S> = {
  tilt_down: { en: "Tilt the phone down a little.", ta: "போனை கொஞ்சம் கீழே சாய்க்கவும்.", hi: "फ़ोन को थोड़ा नीचे झुकाइए।" },
  tilt_up:   { en: "Tilt the phone up a little.", ta: "போனை கொஞ்சம் மேலே சாய்க்கவும்.", hi: "फ़ोन को थोड़ा ऊपर झुकाइए।" },
  dark:      { en: "Too dark. Turn on a light.", ta: "இருட்டாக உள்ளது. விளக்கை போடுங்கள்.", hi: "बहुत अँधेरा है। लाइट जलाइए।" },
  blurry:    { en: "Hold still.", ta: "அசையாமல் இருங்கள்.", hi: "स्थिर रहिए।" },
  no_camera: { en: "Camera is not available.", ta: "கேமரா கிடைக்கவில்லை.", hi: "कैमरा उपलब्ध नहीं है।" },
  show_hand: { en: "Show your hand to the camera.", ta: "கையை கேமராவுக்கு காட்டுங்கள்.", hi: "अपना हाथ कैमरे को दिखाइए।" },
};

const CUES: Record<CueWord, S> = {
  left:  { en: "Left", ta: "இடது", hi: "बाएँ" },
  right: { en: "Right", ta: "வலது", hi: "दाएँ" },
  up:    { en: "Up", ta: "மேலே", hi: "ऊपर" },
  down:  { en: "Down", ta: "கீழே", hi: "नीचे" },
  stop:  { en: "Stop", ta: "நிறுத்து", hi: "रुकिए" },
};

const pick = (s: S, lang: Lang): string => s[lang] ?? s.en;

export const hintText = (k: HintKey, lang: Lang): string => pick(HINTS[k], lang);
export const cueText = (c: CueWord, lang: Lang): string => pick(CUES[c], lang);

/** Spoken once when a live count finishes. Never invents an expected number: null means "none given". */
export function finalReadback(lang: Lang, counted: number, expected: number | null): string {
  if (expected === null) {
    return pick({ en: `${counted} counted.`, ta: `${counted} எண்ணப்பட்டது.`, hi: `${counted} गिने गए।` }, lang);
  }
  return pick(
    {
      en: `${counted} counted, expected ${expected}.`,
      ta: `${counted} எண்ணப்பட்டது, எதிர்பார்த்தது ${expected}.`,
      hi: `${counted} गिने गए, अपेक्षित ${expected}।`,
    },
    lang,
  );
}
