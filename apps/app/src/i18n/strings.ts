// Local, client-side strings only. Everything AIRA says about the shop comes from the server.
// Every string exists in ta, hi and en; keep sentences short.
import type { Lang } from "@aira/contracts";

type S = Record<Lang, string>;

/** Spoken (or written to the screen-reader region) for local status the server cannot know. */
export const STRINGS: Record<"offline" | "online" | "reconnecting" | "paused" | "listening" | "confirmPending" | "micBlocked" | "notSent", S> = {
  micBlocked:     { en: "Microphone is blocked. Allow it in settings.", ta: "மைக் அனுமதி இல்லை. அமைப்பில் அனுமதியுங்கள்.", hi: "माइक बंद है। सेटिंग में अनुमति दें।" },
  notSent:        { en: "Not sent. No connection. Try again.", ta: "அனுப்பவில்லை. இணையம் இல்லை. மீண்டும் முயலுங்கள்.", hi: "नहीं भेजा। कनेक्शन नहीं है। फिर कोशिश करें।" },
  offline:        { en: "Offline. Sales are saved and will sync.", ta: "இணையம் இல்லை. விற்பனை சேமிக்கப்படுகிறது.", hi: "ऑफ़लाइन। बिक्री सेव हो रही है।" },
  online:         { en: "Back online.", ta: "இணையம் மீண்டும் வந்தது.", hi: "फिर से ऑनलाइन।" },
  reconnecting:   { en: "Reconnecting.", ta: "மீண்டும் இணைக்கிறது.", hi: "फिर से जुड़ रहा है।" },
  paused:         { en: "AIRA paused.", ta: "AIRA நிறுத்தப்பட்டது.", hi: "AIRA रुका है।" },
  listening:      { en: "Listening.", ta: "கேட்கிறேன்.", hi: "सुन रहा हूँ।" },
  confirmPending: { en: "Double-tap for yes, long-press for no.", ta: "ஆம் என்றால் இருமுறை தட்டுங்கள், இல்லை என்றால் அழுத்திப் பிடியுங்கள்.", hi: "हाँ के लिए दो बार टैप, ना के लिए दबाकर रखें।" },
};

/** Short labels drawn on the screen (status chip, caption speakers, idle hint). */
export const LABELS: Record<
  "connecting" | "ready" | "listening" | "thinking" | "counting" | "warning" | "offline" | "stop" | "you" | "aira" | "hintTalk",
  S
> = {
  connecting: { en: "Connecting", ta: "இணைக்கிறது", hi: "जुड़ रहा है" },
  ready:      { en: "Ready", ta: "தயார்", hi: "तैयार" },
  listening:  { en: "Listening", ta: "கேட்கிறேன்", hi: "सुन रहा हूँ" },
  thinking:   { en: "Thinking", ta: "யோசிக்கிறேன்", hi: "सोच रहा हूँ" },
  counting:   { en: "Counting", ta: "எண்ணுகிறேன்", hi: "गिन रहा हूँ" },
  warning:    { en: "Warning", ta: "கவனம்", hi: "ध्यान दें" },
  offline:    { en: "Offline", ta: "இணையம் இல்லை", hi: "ऑफ़लाइन" },
  stop:       { en: "Stop!", ta: "நில்லுங்கள்!", hi: "रुकिए!" },
  you:        { en: "You", ta: "நீங்கள்", hi: "आप" },
  aira:       { en: "AIRA", ta: "AIRA", hi: "AIRA" },
  hintTalk:   {
    en: "Double-tap anywhere to talk. Long-press to stop.",
    ta: "பேச எங்கும் இருமுறை தட்டுங்கள். நிறுத்த அழுத்திப் பிடியுங்கள்.",
    hi: "बोलने के लिए कहीं भी दो बार टैप करें। रोकने के लिए दबाकर रखें।",
  },
};

/** Onboarding screen (the one Start tap). The labels follow the language picked on that screen. */
export const ONBOARDING: Record<"howTo" | "language" | "talkback" | "talkbackHint" | "start" | "startHint", S> = {
  howTo:        { en: "Pick a language, then tap Start.", ta: "மொழியைத் தேர்ந்தெடுத்து, தொடங்கு அழுத்துங்கள்.", hi: "भाषा चुनें, फिर शुरू करें दबाएँ।" },
  language:     { en: "Language", ta: "மொழி", hi: "भाषा" },
  talkback:     { en: "I use TalkBack", ta: "நான் TalkBack பயன்படுத்துகிறேன்", hi: "मैं TalkBack इस्तेमाल करता हूँ" },
  talkbackHint: { en: "AIRA's messages go to TalkBack.", ta: "AIRA செய்திகளை TalkBack படிக்கும்.", hi: "AIRA के संदेश TalkBack पढ़ेगा।" },
  start:        { en: "Start", ta: "தொடங்கு", hi: "शुरू करें" },
  startHint:    { en: "Turns on the microphone and camera.", ta: "மைக், கேமரா இயக்கப்படும்.", hi: "माइक और कैमरा चालू होंगे।" },
};

/** Each language in its own script, with the English name for a sighted helper. */
export const LANG_NAMES: Record<Lang, { native: string; english: string }> = {
  ta: { native: "தமிழ்", english: "Tamil" },
  hi: { native: "हिन्दी", english: "Hindi" },
  en: { native: "English", english: "English" },
};
