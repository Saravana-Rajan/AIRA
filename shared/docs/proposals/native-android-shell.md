# Proposal: a native Android shell for hands-free start

**Status: proposal only. Nothing native is built. Needs the owner's approval on three points (section 7).**

Why: two hands-free starts cannot be done from the web app. The volume-button chord needs hardware key events and
"Hello AIRA" needs a microphone that keeps running with the app closed. A web page, a PWA and the Bubblewrap TWA
(`frontend/app/twa/`) get neither (`../ops/handsfree.md` section 5). The web app stays the single UI; the
shell only adds a trigger and an app-to-front.

Nothing in this document was verified on a device or against current Play policy text while writing it (no web
search available). Platform statements are "known as of mid-2026" unless marked "uncertain, verify".

## 1. Shell options

| Option | What it is | Volume keys | Background mic | Keeps the web app as the one UI | Judges' PWA / TWA keep working | Cost |
|---|---|---|---|---|---|---|
| A. Extend the existing Bubblewrap TWA | The current APK: Chrome renders the site in a Trusted Web Activity | **No.** The TWA's activity is Chrome's; the host app has no `dispatchKeyEvent` on the web content and cannot add services that talk to the page | **No** services with a page bridge | Yes | Yes | None, but it cannot do the job |
| B. Minimal Kotlin WebView wrapper | One `Activity` with a `WebView` loading `https://aira-shop.web.app`, plus a `ForegroundService` for the wake word. A JS bridge (`addJavascriptInterface` or `evaluateJavascript`) calls the page | Yes: `onKeyDown` / `dispatchKeyEvent` in the foreground | Yes: a microphone foreground service | Yes | Yes (the shell is a separate APK or a flavour; the PWA and TWA are untouched) | Small. One developer, a few days. But `twa/README.md` chose `customtabs` fallback precisely because a WebView loses Media Session and `speechSynthesis` behaviour; the WebView route must be measured against that (uncertain: modern Android System WebView does support Media Session and `speechSynthesis`, verify) |
| C. Capacitor | A WebView wrapper with a plugin system and a native project you own | Yes (a small custom plugin for key events) | Yes (a custom plugin for the service) | Yes | Yes | Medium. Larger toolchain, more upgrades, same WebView limits as B |

**Recommendation: B, a minimal Kotlin WebView wrapper, as a *second* APK (`in.techjays.aira.shell` or a build
flavour), only after the owner approves.** Keep the Bubblewrap TWA as the APK judges install: it is Chrome, it is
proven, and nothing here must touch it before the submission. B gives exactly the two hooks we need with the
least surface. C only pays off if more native features follow.

Whatever the shell, the page-side logic is already written and tested: `handsfree/volumeChord.ts` and
`handsfree/wakeWord.ts` can run inside the WebView (the shell injects events through the JS bridge) or be
mirrored in Kotlin line for line. The flags stay `VITE_AIRA_VOLUME_TRIGGER` / `VITE_AIRA_WAKEWORD`.

## 2. How the shell talks to the page

- Shell to page: `webView.evaluateJavascript("window.airaShell.onVolumeKey({key:'up',type:'down',t:…})")` and
  `window.airaShell.onWake()`. The page exposes these only when the matching flag is on.
- Page to shell: `@JavascriptInterface` object `AiraShell` with `wakeWordEnable(confirmed: Boolean)`,
  `wakeWordDisable()`, `bringToFront()`; the page's `WakeWordController` callbacks map onto them.
- The web app's own double-tap / long-press and the earphone button keep working unchanged inside the WebView.

## 3. Volume-button trigger

### Foreground (the app is open and visible)

`Activity.dispatchKeyEvent(event)` (or `onKeyDown` / `onKeyUp`) sees `KEYCODE_VOLUME_UP` and
`KEYCODE_VOLUME_DOWN`. Feed each `ACTION_DOWN` / `ACTION_UP` to `VolumeChordDetector.feed(...)` (repeats are
`ACTION_DOWN` with `repeatCount > 0` and are ignored by the detector). Return the detector's `consume`; otherwise
return `super.dispatchKeyEvent(event)` so the volume still changes. On `fire`, call the page's `onTalk()`.

The TalkBack chord (hold both about 3 s) is handled by the system before the app sees the keys (known;
exact pipeline order uncertain, verify with TalkBack on), and the detector never fires or consumes on a hold.

### Background (screen off or another app in front): only an AccessibilityService

Only an `AccessibilityService` with `android:canRequestFilterKeyEvents="true"` and
`FLAG_REQUEST_FILTER_KEY_EVENTS` receives hardware keys for the whole system (`onKeyEvent`). The user must turn
the service on in Settings > Accessibility. The service feeds the same detector and, on `fire`, starts the
activity (`startActivity` with `FLAG_ACTIVITY_NEW_TASK`; on Android 10+ an accessibility service may start an
activity from the background, verify on 14).

**Google Play policy implications (must be understood before building):**

- Play's Accessibility API policy allows the API only for helping users with disabilities or when the app is an
  accessibility tool; other uses need a declaration. AIRA's stated purpose (a blind owner) fits, but the use here
  is a key-press trigger, which Play reviewers often reject as "not accessibility". Expect questions.
- The Play Console **AccessibilityService declaration form** is mandatory; `isAccessibilityTool="true"` in the
  service metadata when the app is an accessibility tool; otherwise a **prominent in-app disclosure** and consent
  before the service is enabled.
- Review risk is real: apps have been removed for key-event filtering. If the APK is only side-loaded for the
  pilot, Play policy does not apply, but a Play release would.
- The service keeps running and can read every key the user types system-wide. That is a trust statement to the
  owner and must be in the disclosure.

**Requires owner approval.** Default: foreground only, no AccessibilityService.

## 4. "Hello AIRA" wake word

### Service shape

A `ForegroundService` with a persistent notification ("AIRA is listening for 'Hello AIRA'"), declared with
`android:foregroundServiceType="microphone"`, running the wake engine on the device. On a detection: play the
ack earcon, `startActivity` the shell (an app in a foreground service can start its own activity when it was
started by the user; on Android 14 background activity starts are restricted, verify), and tell the page to
start the voice session.

### Android 14+ rules (known as of mid-2026, verify on the target build)

- Permission `FOREGROUND_SERVICE_MICROPHONE` (normal, declared in the manifest) plus runtime `RECORD_AUDIO`.
- A `microphone` foreground service is **while-in-use restricted**: it can be started only while the app is in
  the foreground or from a user interaction (a notification tap, a visible activity, a shortcut). Starting it from
  the background throws `ForegroundServiceStartNotAllowedException`, with a short list of exemptions (an active
  exact alarm, an FCM high-priority message, a few others) that do not fit a wake word.
- **After a reboot:** `BOOT_COMPLETED` is a background start, so the microphone service cannot start from it on
  14+. The owner must open AIRA (or tap the notification/shortcut) once after every reboot. The shell should post a
  normal notification on boot: "Open AIRA to turn 'Hello AIRA' back on." (ta / hi / en).
- The notification is mandatory and shows the mic indicator; the privacy indicator shows the mic in use all day.
- The OS may still kill the service under memory pressure or battery policies (Samsung and Xiaomi are
  aggressive). `WakeWordController.onKilled` applies the restart budget; past it, the owner hears `wakeFailed`
  and `START_STICKY` is not relied on.
- Doze and app standby do not stop a running foreground service, but the OEM battery optimiser may; ask the owner
  to exempt AIRA (`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` has its own Play policy; prefer the settings page).
- **Battery impact: not measured.** On-device wake engines usually claim a few percent per day; we quote nothing
  until measured on the pilot phone.

### Engine options and licences

| Engine | Licence | Custom "Hello AIRA" | On device | Notes |
|---|---|---|---|---|
| openWakeWord | Code Apache-2.0; the bundled models carry their own terms (vary, check each) | Needs training a custom model (synthetic data pipeline provided by the project) | Yes (ONNX / TFLite) | Python-first; Android needs the TFLite model and our own audio front end. Tamil / Hindi accents need our own training clips |
| Picovoice Porcupine | Proprietary SDK; free tier for personal / evaluation with an AccessKey, commercial use needs a paid plan | Yes, in the Picovoice Console (custom keyword, per platform) | Yes | Best-documented Android SDK; the licence and the AccessKey in the app are the catch |
| Vosk (keyword spotting / grammar) | Apache-2.0 | Yes, as a grammar-constrained recogniser (not a true hot-word model) | Yes | Models are large (tens of MB); higher CPU than a hot-word model; Tamil / Hindi models exist with varying quality |
| Android `SpeechRecognizer` continuous | Platform | No real hot word; restart loop, beeps, often cloud-backed | No (often cloud) | Not suitable: privacy (audio may leave the device), restarts, beeps, and it fights the Live mic |

Whatever the engine, audio stays on the device until the wake word fires: the engine sees PCM only, the
controller has no network path, and the Gemini Live mic opens only after the detection.

## 5. Behaviour summary

| Situation | Volume chord | "Hello AIRA" |
|---|---|---|
| AIRA open, screen on | Works (Activity) | Works (service or in-app engine) |
| AIRA in the background, screen on or off | Only with the AccessibilityService (owner approval) | Works while the foreground service lives |
| After a reboot (Android 14+) | Only with the AccessibilityService | Off until the owner opens AIRA once |
| OS killed the service | n/a | Restart budget, then "stopped working, double-tap to talk" |
| TalkBack on | Hold = TalkBack as before; double quick chord = AIRA | Unchanged |
| iPhone | Not possible | Not possible; use "Hey Siri, open AIRA" |

## 6. Test plan (on device, before any flag goes on)

1. Volume keys, TalkBack **off**: single up, single down, long hold each (volume changes as normal, never fires);
   quick chord once (no fire; the first key stepped the volume once); quick double chord (fires once); a hold of
   both for 3 s (no fire).
2. Volume keys, TalkBack **on**: hold both 3 s toggles TalkBack exactly as before, with the shell in front and in
   the background; the quick double chord fires and TalkBack announces AIRA's state; TalkBack's own gestures on
   the WebView still work.
3. Wake word: the confirmation question is asked once; "no" leaves it off; "yes" shows the notification and the
   mic indicator; "Hello AIRA" from 1 m and 3 m in a shop with a fridge compressor running (count hits and false
   triggers over an hour, write the numbers down before quoting any); detection while music plays; detection with
   the screen off; after a reboot the notification says to open AIRA; force-stop the service and watch the
   restart budget then the spoken failure line.
4. Battery: a full shop day with the service on, then the same day with it off, on the same phone. Until then:
   **not measured**.
5. Judges: the PWA at `aira-shop.web.app` and the TWA APK behave exactly as before, with every flag off.

## 7. Decisions that need the owner

1. **Build a native shell at all** (option B recommended), as a separate APK, after the submission freeze.
2. **A background AccessibilityService** for the volume chord: Play policy risk and system-wide key access.
   Default: no.
3. **The wake engine**: Porcupine (easiest, proprietary, AccessKey) vs openWakeWord (open, needs training) vs
   Vosk (open, heavier). The owner's confirmation question in the app is required either way.
