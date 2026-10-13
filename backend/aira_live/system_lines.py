"""The few lines the PHONE speaks with its own voice, when no Gemini Live session can speak for AIRA.

Every answer, notice and confirmation goes through Gemini Live as facts, in whatever language the owner is
speaking (``prompt`` rule 20). Only these lines have fixed wording, because Live is not there to say them:
connection and permission errors (Live is down, refused or not started yet), the mock-mode echo, and the
device check screen before the first conversation (``device_check``; the same words as
frontend/app/src/i18n/strings.ts CHECK). They exist in Tamil, Hindi and English; any other language gets the
English line.
"""

LANGS = ("ta", "hi", "en")

LINES: dict[str, dict[str, str]] = {
    "auth.denied": {
        "en": "This phone is not linked to this shop.",
        "ta": "இந்த போன் இந்த கடையுடன் இணைக்கப்படவில்லை.",
        "hi": "यह फ़ोन इस दुकान से जुड़ा नहीं है।",
    },
    "auth.busy": {
        "en": "AIRA is busy with other phones. Please try again in a minute.",
        "ta": "AIRA வேறு போன்களில் பிஸியாக உள்ளது. ஒரு நிமிடம் கழித்து முயலுங்கள்.",
        "hi": "AIRA दूसरे फ़ोन में व्यस्त है। एक मिनट बाद फिर कोशिश करें।",
    },
    "live.unavailable": {
        "en": "I can't reach the AI right now. Please try again in a minute.",
        "ta": "இப்போது AI-ஐ அணுக முடியவில்லை. ஒரு நிமிடம் கழித்து மீண்டும் முயலுங்கள்.",
        "hi": "अभी AI से संपर्क नहीं हो पा रहा। एक मिनट बाद फिर कोशिश करें।",
    },
    "error.unsupported": {
        "en": "Sorry, I didn't understand that.",
        "ta": "மன்னிக்கவும், எனக்கு புரியவில்லை.",
        "hi": "माफ़ कीजिए, मैं समझ नहीं पाया।",
    },
    "error.too_large": {
        "en": "That image is too large.",
        "ta": "அந்த படம் மிகப் பெரியது.",
        "hi": "वह तस्वीर बहुत बड़ी है।",
    },
    "error.internal": {
        "en": "Something went wrong. Please try again.",
        "ta": "ஏதோ தவறு நடந்தது. மீண்டும் முயற்சிக்கவும்.",
        "hi": "कुछ गड़बड़ हो गई। फिर से कोशिश कीजिए।",
    },
    "mock.echo": {
        "en": "(mock) you said: {text}",
        "ta": "(mock) நீங்கள் சொன்னது: {text}",
        "hi": "(mock) आपने कहा: {text}",
    },
    "check.allGood": {
        "en": "Microphone and camera work. Tap anywhere to start talking.",
        "ta": "மைக்கும் கேமராவும் வேலை செய்கின்றன. பேசத் தொடங்க எங்கும் தட்டவும்.",
        "hi": "माइक और कैमरा ठीक हैं। बात शुरू करने के लिए कहीं भी टैप करें।",
    },
    "check.camBlank": {
        "en": "The camera shows nothing. Close other apps using the camera, then try again.",
        "ta": "கேமராவில் எதுவும் தெரியவில்லை. கேமராவைப் பயன்படுத்தும் மற்ற செயலிகளை மூடி மீண்டும் முயலுங்கள்.",
        "hi": "कैमरे में कुछ नहीं दिख रहा। कैमरा इस्तेमाल कर रहे दूसरे ऐप बंद करें, फिर दोबारा कोशिश करें।",
    },
    "check.camBlocked": {
        "en": "The camera is blocked. Allow the camera for this site, then try again.",
        "ta": "கேமரா தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு கேமராவை அனுமதித்து மீண்டும் முயலுங்கள்.",
        "hi": "कैमरा बंद है। इस साइट के लिए कैमरे की अनुमति दें, फिर दोबारा कोशिश करें।",
    },
    "check.camBlurry": {
        "en": "The picture is blurred. Hold still and wipe the lens, then try again.",
        "ta": "படம் மங்கலாக உள்ளது. அசையாமல் பிடித்து, லென்ஸைத் துடைத்து மீண்டும் முயலுங்கள்.",
        "hi": "तस्वीर धुंधली है। स्थिर रखें और लेंस पोंछें, फिर दोबारा कोशिश करें।",
    },
    "check.camBusy": {
        "en": "Too many checks. Wait a minute, then try again.",
        "ta": "அதிக முறை சோதித்துவிட்டீர்கள். ஒரு நிமிடம் கழித்து முயலுங்கள்.",
        "hi": "बहुत बार जाँच हो गई। एक मिनट रुककर दोबारा कोशिश करें।",
    },
    "check.camCovered": {
        "en": "Something is covering the camera. Uncover the lens, then try again.",
        "ta": "கேமராவை ஏதோ மறைக்கிறது. லென்ஸைத் திறந்து மீண்டும் முயலுங்கள்.",
        "hi": "कैमरे के आगे कुछ है। लेंस खोलें, फिर दोबारा कोशिश करें।",
    },
    "check.camDark": {
        "en": "The picture is too dark. Turn on a light, then try again.",
        "ta": "படம் மிகவும் இருட்டாக உள்ளது. விளக்கைப் போட்டு மீண்டும் முயலுங்கள்.",
        "hi": "तस्वीर बहुत अँधेरी है। रोशनी करें, फिर दोबारा कोशिश करें।",
    },
    "check.camLookLaptop": {
        "en": "Checking the camera. Look at the laptop camera.",
        "ta": "கேமரா சரிபார்க்கிறேன். லேப்டாப் கேமராவைப் பாருங்கள்.",
        "hi": "कैमरा जाँच रहा हूँ। लैपटॉप कैमरे की ओर देखिए।",
    },
    "check.camLookPhone": {
        "en": "Checking the camera. Point the phone at what is in front of you.",
        "ta": "கேமரா சரிபார்க்கிறேன். உங்கள் முன் உள்ளதை நோக்கி போனைப் பிடியுங்கள்.",
        "hi": "कैमरा जाँच रहा हूँ। फ़ोन को अपने सामने की चीज़ की ओर रखें।",
    },
    "check.camOffline": {
        "en": "I could not reach AIRA to check the camera. Check the internet, then try again.",
        "ta": "கேமராவைச் சரிபார்க்க AIRA-வை அடைய முடியவில்லை. இணையத்தைப் பார்த்து மீண்டும் முயலுங்கள்.",
        "hi": "कैमरा जाँचने के लिए AIRA तक नहीं पहुँच पाया। इंटरनेट देखें, फिर दोबारा कोशिश करें।",
    },
    "check.camOk": {
        "en": "Camera works. I can see {seen}.",
        "ta": "கேமரா வேலை செய்கிறது. எனக்கு {seen} தெரிகிறது.",
        "hi": "कैमरा ठीक है। मुझे {seen} दिख रहा है।",
    },
    "check.camUnavailable": {
        "en": "AIRA's camera check is not answering. Try again in a moment.",
        "ta": "AIRA கேமரா சோதனை பதில் தரவில்லை. சிறிது நேரம் கழித்து முயலுங்கள்.",
        "hi": "AIRA की कैमरा जाँच जवाब नहीं दे रही। थोड़ी देर में दोबारा कोशिश करें।",
    },
    "check.camUnclear": {
        "en": "I could not make out anything. Point the camera at something, then try again.",
        "ta": "எதையும் அடையாளம் காண முடியவில்லை. கேமராவை ஏதாவது ஒன்றை நோக்கி வைத்து மீண்டும் முயலுங்கள்.",
        "hi": "कुछ पहचान नहीं पाया। कैमरा किसी चीज़ की ओर करें, फिर दोबारा कोशिश करें।",
    },
    "check.micBlocked": {
        "en": "The microphone is blocked. Allow the microphone for this site, then try again.",
        "ta": "மைக் தடுக்கப்பட்டுள்ளது. இந்த தளத்திற்கு மைக்கை அனுமதித்து, மீண்டும் முயலுங்கள்.",
        "hi": "माइक बंद है। इस साइट के लिए माइक की अनुमति दें, फिर दोबारा कोशिश करें।",
    },
    "check.micMissing": {
        "en": "No microphone found. Connect one, then try again.",
        "ta": "மைக் இல்லை. மைக்கை இணைத்து மீண்டும் முயலுங்கள்.",
        "hi": "कोई माइक नहीं मिला। माइक जोड़ें, फिर दोबारा कोशिश करें।",
    },
    "check.micOk": {
        "en": "Microphone works.",
        "ta": "மைக் வேலை செய்கிறது.",
        "hi": "माइक ठीक है।",
    },
    "check.micSay": {
        "en": "Checking the microphone. Say: Hello AIRA.",
        "ta": "மைக் சரிபார்க்கிறேன். ஹலோ AIRA என்று சொல்லுங்கள்.",
        "hi": "माइक जाँच रहा हूँ। बोलिए: हैलो AIRA।",
    },
    "check.micSilent": {
        "en": "I heard nothing. Check the microphone is not muted, then try again.",
        "ta": "எதுவும் கேட்கவில்லை. மைக் அணைக்கப்படவில்லையா என்று பார்த்து மீண்டும் முயலுங்கள்.",
        "hi": "कुछ सुनाई नहीं दिया। देखें कि माइक म्यूट न हो, फिर दोबारा कोशिश करें।",
    },
    "check.micTap": {
        "en": "Tap to test the microphone.",
        "ta": "மைக்கைச் சோதிக்க தட்டவும்.",
        "hi": "माइक जाँचने के लिए टैप करें।",
    },
}


def line(key: str, lang: str | None, **kw) -> str:
    """The line for ``key`` in ``lang`` (English for any other language, then the key). Never raises."""
    row = LINES.get(key) or {}
    text = row.get(str(lang or "en")) or row.get("en") or key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text
