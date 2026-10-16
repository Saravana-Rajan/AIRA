// Hands-free strings, in ta, hi and en like every other user-facing line (shared/docs/team-rules.md).
// The wake-word controller is pure logic; a native shell speaks these through the app's local speech.
import type { Lang } from "@aira/contracts";

type S = Record<Lang, string>;

export const HANDSFREE_STRINGS: Record<"wakeAsk" | "wakeOn" | "wakeOff" | "wakeFailed" | "earphoneButton", S> = {
  // the owner's confirmation before the wake word is ever enabled (WakeWordController.enable needs a yes)
  wakeAsk: {
    en: "Listen for 'Hello AIRA' all the time? The sound stays on the phone until you say it. Yes or no?",
    ta: "'Hello AIRA' என்று எப்போதும் கேட்கவா? நீங்கள் சொல்லும் வரை ஒலி போனிலேயே இருக்கும். ஆமா இல்லையா?",
    hi: "हमेशा 'Hello AIRA' सुनूँ? आपके कहने तक आवाज़ फ़ोन में ही रहेगी। हाँ या ना?",
  },
  wakeOn: { en: "Say 'Hello AIRA' to start me.", ta: "என்னைத் தொடங்க 'Hello AIRA' என்று சொல்லுங்கள்.", hi: "मुझे शुरू करने के लिए 'Hello AIRA' कहें।" },
  wakeOff: { en: "'Hello AIRA' is off.", ta: "'Hello AIRA' நிறுத்தப்பட்டது.", hi: "'Hello AIRA' बंद है।" },
  wakeFailed: {
    en: "'Hello AIRA' stopped working. Double-tap to talk.",
    ta: "'Hello AIRA' வேலை செய்யவில்லை. பேச இருமுறை தட்டுங்கள்.",
    hi: "'Hello AIRA' काम नहीं कर रहा। बोलने के लिए दो बार टैप करें।",
  },
  // said once when the earphone button is wired (VITE_AIRA_MEDIA_BUTTON) so the owner knows it works
  earphoneButton: {
    en: "Press the earphone button to talk or stop.",
    ta: "பேச அல்லது நிறுத்த இயர்போன் பட்டனை அழுத்துங்கள்.",
    hi: "बोलने या रोकने के लिए ईयरफ़ोन का बटन दबाएँ।",
  },
};
