// Local, client-side strings only. Everything AIRA says about the shop comes from the server.
// Every string exists in ta, hi and en; keep sentences short.
import type { Lang } from "@aira/contracts";

type S = Record<Lang, string>;

/** Spoken (or written to the screen-reader region) for local status the server cannot know. */
export const STRINGS: Record<
  "offline" | "online" | "reconnecting" | "paused" | "listening" | "confirmPending" | "micBlocked" | "notSent" | "greeting" | "tapToStart",
  S
> = {
  // hands-free start (core/autostart.ts): AIRA's first line when the app opened straight into listening, and
  // the one-line instruction when the browser wanted a tap first.
  greeting:       { en: "AIRA is ready. Just talk.", ta: "AIRA தயார். பேசுங்கள்.", hi: "AIRA तैयार है। बोलिए।" },
  tapToStart:     { en: "Tap anywhere to start.", ta: "தொடங்க எங்காவது தட்டுங்கள்.", hi: "शुरू करने के लिए कहीं भी टैप करें।" },
  micBlocked:     { en: "Microphone is blocked. Allow it in settings.", ta: "மைக் அனுமதி இல்லை. அமைப்பில் அனுமதியுங்கள்.", hi: "माइक बंद है। सेटिंग में अनुमति दें।" },
  notSent:        { en: "Not sent. No connection. Try again.", ta: "அனுப்பவில்லை. இணையம் இல்லை. மீண்டும் முயலுங்கள்.", hi: "नहीं भेजा। कनेक्शन नहीं है। फिर कोशिश करें।" },
  offline:        { en: "Offline. Sales are saved and will sync.", ta: "இணையம் இல்லை. விற்பனை சேமிக்கப்படுகிறது.", hi: "ऑफ़लाइन। बिक्री सेव हो रही है।" },
  online:         { en: "Back online.", ta: "இணையம் மீண்டும் வந்தது.", hi: "फिर से ऑनलाइन।" },
  reconnecting:   { en: "Reconnecting.", ta: "மீண்டும் இணைக்கிறது.", hi: "फिर से जुड़ रहा है।" },
  paused:         { en: "AIRA paused.", ta: "AIRA நிறுத்தப்பட்டது.", hi: "AIRA रुका है।" },
  listening:      { en: "Listening.", ta: "கேட்கிறேன்.", hi: "सुन रहा हूँ।" },
  confirmPending: { en: "Double-tap for yes, long-press for no.", ta: "ஆம் என்றால் இருமுறை தட்டுங்கள், இல்லை என்றால் அழுத்திப் பிடியுங்கள்.", hi: "हाँ के लिए दो बार टैप, ना के लिए दबाकर रखें।" },
};

/**
 * Short labels drawn on the screen (status chip, task card, caption speakers, idle hint). The screen reducer
 * writes the `en` text; AppShell.localizeStatus looks it up here by its English text (or by the `{n}` pattern
 * for the parameterised ones) and draws the language the speaker last used (English for one without labels).
 */
export const LABELS: Record<
  | "connecting" | "ready" | "listening" | "thinking" | "counting" | "warning" | "offline" | "stop" | "you" | "aira"
  | "hintTalk"
  | "mismatch" | "unsure" | "reconnecting" | "yesOrNo" | "short" | "extra" | "counted"
  | "order" | "delivery" | "sale" | "payment" | "stock"
  | "confirmOrder" | "confirmPayment" | "confirmSale" | "confirmDiscrepancy" | "confirm",
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
  // chip states the reducer emits for cues, confirms and the count outcome
  mismatch:     { en: "Mismatch", ta: "பொருந்தவில்லை", hi: "मेल नहीं खाता" },
  unsure:       { en: "Not sure", ta: "உறுதியில்லை", hi: "पक्का नहीं" },
  reconnecting: { en: "Reconnecting", ta: "மீண்டும் இணைக்கிறது", hi: "फिर से जुड़ रहा है" },
  yesOrNo:      { en: "Yes or no?", ta: "ஆமா இல்லையா?", hi: "हाँ या ना?" },
  short:        { en: "{n} short", ta: "{n} குறைவு", hi: "{n} कम" },
  extra:        { en: "{n} extra", ta: "{n} கூடுதல்", hi: "{n} ज़्यादा" },
  counted:      { en: "{n} counted", ta: "{n} எண்ணப்பட்டது", hi: "{n} गिने" },
  // task card titles (state messages and confirms)
  order:    { en: "Order", ta: "ஆர்டர்", hi: "ऑर्डर" },
  delivery: { en: "Delivery", ta: "டெலிவரி", hi: "डिलीवरी" },
  sale:     { en: "Sale", ta: "விற்பனை", hi: "बिक्री" },
  payment:  { en: "Payment", ta: "பணம்", hi: "भुगतान" },
  stock:    { en: "Stock", ta: "சரக்கு", hi: "स्टॉक" },
  confirmOrder:       { en: "Confirm order", ta: "ஆர்டரை உறுதி செய்யவா?", hi: "ऑर्डर पक्का करें?" },
  confirmPayment:     { en: "Confirm payment", ta: "பணம் உறுதி செய்யவா?", hi: "भुगतान पक्का करें?" },
  confirmSale:        { en: "Confirm sale", ta: "விற்பனை உறுதி செய்யவா?", hi: "बिक्री पक्का करें?" },
  confirmDiscrepancy: { en: "Confirm discrepancy", ta: "வித்தியாசம் உறுதி செய்யவா?", hi: "अंतर पक्का करें?" },
  confirm:            { en: "Confirm", ta: "உறுதி செய்யவா?", hi: "पक्का करें?" },
  hintTalk:   {
    en: "Double-tap anywhere to talk. Long-press to stop.",
    ta: "பேச எங்கும் இருமுறை தட்டுங்கள். நிறுத்த அழுத்திப் பிடியுங்கள்.",
    hi: "बोलने के लिए कहीं भी दो बार टैप करें। रोकने के लिए दबाकर रखें।",
  },
};

/**
 * Onboarding screen (the one Start tap). There is no language picker: AIRA follows whoever speaks. The labels
 * here are written in the phone's language (only a hint for the first line), English for any other.
 */
export const ONBOARDING: Record<"howTo" | "anyLanguage" | "talkback" | "talkbackHint" | "start" | "tapToStart" | "startHint", S> = {
  howTo:        { en: "Tap Start, then just talk.", ta: "தொடங்கு அழுத்தி, பேசுங்கள்.", hi: "शुरू करें दबाएँ, फिर बोलें।" },
  anyLanguage:  { en: "Speak any language. AIRA answers in yours.", ta: "எந்த மொழியிலும் பேசலாம். AIRA அதே மொழியில் பதில் சொல்லும்.", hi: "किसी भी भाषा में बोलें। AIRA उसी भाषा में जवाब देगा।" },
  talkback:     { en: "I use TalkBack", ta: "நான் TalkBack பயன்படுத்துகிறேன்", hi: "मैं TalkBack इस्तेमाल करता हूँ" },
  talkbackHint: { en: "AIRA's messages go to TalkBack.", ta: "AIRA செய்திகளை TalkBack படிக்கும்.", hi: "AIRA के संदेश TalkBack पढ़ेगा।" },
  start:        { en: "Start", ta: "தொடங்கு", hi: "शुरू करें" },
  tapToStart:   { en: "Tap to start", ta: "தொடங்க தட்டவும்", hi: "शुरू करने के लिए टैप करें" },
  startHint:    { en: "Turns on the microphone and camera.", ta: "மைக், கேமரா இயக்கப்படும்.", hi: "माइक और कैमरा चालू होंगे।" },
};

/** The start-up device check (check/DeviceCheck.tsx): spoken and shown before the conversation opens. */
export const CHECK: Record<
  | "title" | "intro" | "micStep" | "camStep" | "waiting" | "checking" | "passed" | "failed"
  | "micSay" | "micOk" | "micBlocked" | "micMissing" | "micSilent" | "micTap"
  | "camLookLaptop" | "camLookPhone" | "camOk" | "camDark" | "camBlurry" | "camCovered" | "camBlank" | "camUnclear"
  | "camBlocked" | "camOffline" | "camBusy" | "camUnavailable" | "allGood" | "startTalking" | "tryAgain",
  S
> = {
  title:          { en: "Checking your microphone and camera", ta: "மைக் மற்றும் கேமராவைச் சரிபார்க்கிறோம்", hi: "माइक और कैमरा जाँच रहे हैं" },
  intro:          { en: "AIRA starts talking once both work.", ta: "இரண்டும் வேலை செய்த பிறகு AIRA பேசத் தொடங்கும்.", hi: "दोनों ठीक होने पर AIRA बात शुरू करेगा।" },
  micStep:        { en: "Microphone", ta: "மைக்", hi: "माइक" },
  camStep:        { en: "Camera", ta: "கேமரா", hi: "कैमरा" },
  waiting:        { en: "Waiting", ta: "காத்திருக்கிறது", hi: "इंतज़ार" },
  checking:       { en: "Checking", ta: "சரிபார்க்கிறது", hi: "जाँच हो रही है" },
  passed:         { en: "Works", ta: "சரி", hi: "ठीक" },
  failed:         { en: "Needs attention", ta: "சரிசெய்ய வேண்டும்", hi: "ध्यान दें" },
  micSay:         { en: "Checking the microphone. Say: Hello AIRA.", ta: "மைக் சரிபார்க்கிறேன். ஹலோ AIRA என்று சொல்லுங்கள்.", hi: "माइक जाँच रहा हूँ। बोलिए: हैलो AIRA।" },
  micOk:          { en: "Microphone works.", ta: "மைக் வேலை செய்கிறது.", hi: "माइक ठीक है।" },
  micBlocked:     { en: "The microphone is blocked. Allow the microphone for this site, then try again.", ta: "மைக் தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு மைக்கை அனுமதித்து, மீண்டும் முயலுங்கள்.", hi: "माइक बंद है। इस साइट के लिए माइक की अनुमति दें, फिर दोबारा कोशिश करें।" },
  micMissing:     { en: "No microphone found. Connect one, then try again.", ta: "மைக் இல்லை. மைக்கை இணைத்து மீண்டும் முயலுங்கள்.", hi: "कोई माइक नहीं मिला। माइक जोड़ें, फिर दोबारा कोशिश करें।" },
  micSilent:      { en: "I heard nothing. Check the microphone is not muted, then try again.", ta: "எதுவும் கேட்கவில்லை. மைக் அணைக்கப்படவில்லையா என்று பார்த்து மீண்டும் முயலுங்கள்.", hi: "कुछ सुनाई नहीं दिया। देखें कि माइक म्यूट न हो, फिर दोबारा कोशिश करें।" },
  micTap:         { en: "Tap to test the microphone.", ta: "மைக்கைச் சோதிக்க தட்டவும்.", hi: "माइक जाँचने के लिए टैप करें।" },
  camLookLaptop:  { en: "Checking the camera. Look at the laptop camera.", ta: "கேமரா சரிபார்க்கிறேன். லேப்டாப் கேமராவைப் பாருங்கள்.", hi: "कैमरा जाँच रहा हूँ। लैपटॉप कैमरे की ओर देखिए।" },
  camLookPhone:   { en: "Checking the camera. Point the phone at what is in front of you.", ta: "கேமரா சரிபார்க்கிறேன். உங்கள் முன் உள்ளதை நோக்கி போனைப் பிடியுங்கள்.", hi: "कैमरा जाँच रहा हूँ। फ़ोन को अपने सामने की चीज़ की ओर रखें।" },
  camOk:          { en: "Camera works. I can see {seen}.", ta: "கேமரா வேலை செய்கிறது. எனக்கு {seen} தெரிகிறது.", hi: "कैमरा ठीक है। मुझे {seen} दिख रहा है।" },
  camDark:        { en: "The picture is too dark. Turn on a light, then try again.", ta: "படம் மிகவும் இருட்டாக உள்ளது. விளக்கைப் போட்டு மீண்டும் முயலுங்கள்.", hi: "तस्वीर बहुत अँधेरी है। रोशनी करें, फिर दोबारा कोशिश करें।" },
  camBlurry:      { en: "The picture is blurred. Hold still and wipe the lens, then try again.", ta: "படம் மங்கலாக உள்ளது. அசையாமல் பிடித்து, லென்ஸைத் துடைத்து மீண்டும் முயலுங்கள்.", hi: "तस्वीर धुंधली है। स्थिर रखें और लेंस पोंछें, फिर दोबारा कोशिश करें।" },
  camCovered:     { en: "Something is covering the camera. Uncover the lens, then try again.", ta: "கேமராவை ஏதோ மறைக்கிறது. லென்ஸைத் திறந்து மீண்டும் முயலுங்கள்.", hi: "कैमरे के आगे कुछ है। लेंस खोलें, फिर दोबारा कोशिश करें।" },
  camBlank:       { en: "The camera shows nothing. Close other apps using the camera, then try again.", ta: "கேமராவில் எதுவும் தெரியவில்லை. கேமராவைப் பயன்படுத்தும் மற்ற செயலிகளை மூடி மீண்டும் முயலுங்கள்.", hi: "कैमरे में कुछ नहीं दिख रहा। कैमरा इस्तेमाल कर रहे दूसरे ऐप बंद करें, फिर दोबारा कोशिश करें।" },
  camUnclear:     { en: "I could not make out anything. Point the camera at something, then try again.", ta: "எதையும் அடையாளம் காண முடியவில்லை. கேமராவை ஏதாவது ஒன்றை நோக்கி வைத்து மீண்டும் முயலுங்கள்.", hi: "कुछ पहचान नहीं पाया। कैमरा किसी चीज़ की ओर करें, फिर दोबारा कोशिश करें।" },
  camBlocked:     { en: "The camera is blocked. Allow the camera for this site, then try again.", ta: "கேமரா தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு கேமராவை அனுமதித்து மீண்டும் முயலுங்கள்.", hi: "कैमरा बंद है। इस साइट के लिए कैमरे की अनुमति दें, फिर दोबारा कोशिश करें।" },
  camOffline:     { en: "I could not reach AIRA to check the camera. Check the internet, then try again.", ta: "கேமராவைச் சரிபார்க்க AIRA-வை அடைய முடியவில்லை. இணையத்தைப் பார்த்து மீண்டும் முயலுங்கள்.", hi: "कैमरा जाँचने के लिए AIRA तक नहीं पहुँच पाया। इंटरनेट देखें, फिर दोबारा कोशिश करें।" },
  camBusy:        { en: "Too many checks. Wait a minute, then try again.", ta: "அதிக முறை சோதித்துவிட்டீர்கள். ஒரு நிமிடம் கழித்து முயலுங்கள்.", hi: "बहुत बार जाँच हो गई। एक मिनट रुककर दोबारा कोशिश करें।" },
  camUnavailable: { en: "AIRA's camera check is not answering. Try again in a moment.", ta: "AIRA கேமரா சோதனை பதில் தரவில்லை. சிறிது நேரம் கழித்து முயலுங்கள்.", hi: "AIRA की कैमरा जाँच जवाब नहीं दे रही। थोड़ी देर में दोबारा कोशिश करें।" },
  allGood:        { en: "Microphone and camera work. Tap anywhere to start talking.", ta: "மைக்கும் கேமராவும் வேலை செய்கின்றன. பேசத் தொடங்க எங்கும் தட்டவும்.", hi: "माइक और कैमरा ठीक हैं। बात शुरू करने के लिए कहीं भी टैप करें।" },
  startTalking:   { en: "Start talking", ta: "பேசத் தொடங்கு", hi: "बात शुरू करें" },
  tryAgain:       { en: "Try again", ta: "மீண்டும் முயல்", hi: "दोबारा कोशिश करें" },
};
