# Hands-free start: earphone button, volume buttons, "Hello AIRA"

How the owner starts AIRA without finding the screen. Three features, three flags, **all off by default**:

| Feature | Flag (backend name / app build name) | Where it runs | Status |
|---|---|---|---|
| Earphone play/pause button talks / stops | `AIRA_MEDIA_BUTTON` / `VITE_AIRA_MEDIA_BUTTON` | The web app (PWA and the TWA) | Built. Not yet tested on a phone |
| Volume up + down double press | `AIRA_VOLUME_TRIGGER` / `VITE_AIRA_VOLUME_TRIGGER` | Needs a native shell (not the TWA) | Detector built and tested; no shell yet |
| "Hello AIRA" wake word | `AIRA_WAKEWORD` / `VITE_AIRA_WAKEWORD` | Needs a native shell with a foreground service | Controller built and tested; no engine, no shell yet |

Code: `frontend/app/src/handsfree/` (`flags.ts`, `mediaButton.ts`, `volumeChord.ts`, `wakeWord.ts`, `strings.ts`),
wired in `frontend/app/src/core/wire.ts`. Tests: `frontend/app/test/handsfree.*.test.ts` (offline, fakes).
The native shell is a proposal only: [`../proposals/native-android-shell.md`](../proposals/native-android-shell.md).

The zero-code fallback that works today on both platforms is in the README: "Hey Google, open AIRA" and
"Hey Siri, open AIRA".

## 1. How a flag reaches the phone

The WebSocket contract is frozen (`shared/contracts`; the `ready` message carries no feature list), so the
app does not learn these flags from the server. They are **build-time** flags, the pattern the app already uses
for `VITE_AIRA_LIVE_WS` and `VITE_AIRA_APP_KEY` (`core/wire.ts`, `shared/scripts/build_hosting.sh`): set
`VITE_AIRA_MEDIA_BUTTON=1` in the environment of `npm run build -w app` (CI: the hosting build step) and the
flag is baked into the bundle. Vite only exposes `VITE_*` names to the bundle, so the `AIRA_*` names in the
table are the same switches as seen from the server side; the backend does not read them today.

Values: `1` or `true` (any case) = on; anything else, including unset, = off (`handsfree/flags.ts`). Tests
and the demo page can pass `wireApp({ handsfree: { mediaButton: true } })` instead.

If the team later wants to flip these without a rebuild, the clean route is one contracts PR adding an optional
`features` field to `ready` (three files + fixtures, Saravana reviews). Not done here on purpose.

## 2. Earphone button (built)

**What it does.** With `VITE_AIRA_MEDIA_BUTTON=1`, the play/pause button on wired or Bluetooth earphones does
exactly what the screen does: one press = the double-tap (talk; or "yes" to a pending confirm), the next press =
the long-press (stop; or "no" to a pending confirm). After the hands-free start AIRA says once, in the owner's
language: "Press the earphone button to talk or stop." (ta / hi / en in `handsfree/strings.ts`).

**How.** The Media Session API: `navigator.mediaSession.setActionHandler("play" | "pause" | "stop", …)`. When a
page registers handlers, the browser hands it the headset's media keys instead of acting on them. A single
toggle button produces `play` or `pause` depending on `mediaSession.playbackState`, so the app mirrors its own
listening state into it (`"playing"` while the mic session is live, `"paused"` otherwise): one button alternates
talk / stop. `nexttrack` / `previoustrack` (a double or triple press on many headsets) are deliberately not
registered, so the owner's music app keeps them.

**The catch: an active media session is needed.** Known as of mid-2026, to verify on device: a browser routes
media keys only to the page that holds the active media session, and in Chrome that means an `HTMLMediaElement`
that is playing, not muted, and (Chrome's media-notification rule) longer than about 5 s. AIRA's voice is Web
Audio (`voice/playback.ts`), which does not count. So, while the flag is on, the app keeps a looping, detached
`<audio>` playing: a 6 s, 8 kHz, 16-bit WAV built in code (no asset), a 50 Hz tone at about -60 dBFS
(`KEEPALIVE_PEAK = 32` of 32767), below hearing on any earphone but not digital silence. It starts inside the
Start tap (a user gesture, so autoplay allows it) and is restarted on every return to the page or touch
(`wakeAudio`). Whether Chrome needs non-silent samples or only an unmuted element is **uncertain**; the level is a
single constant. Without the flag no element exists at all.

**Side effects to expect (verify):** the OS shows a media notification for AIRA while the app runs ("AIRA,
Listening"); other apps' music pauses when AIRA starts (audio focus); on iOS a playing `<audio>` plus the
`play-and-record` audio session may change which route the Bluetooth headset uses. **Battery impact: not
measured.**

**Fallbacks.** No API, flag off, or anything throwing: nothing is attached and the gestures work as before.
A browser that rejects one action name (`TypeError`) keeps the others.

### Tested on

| Phone / OS | Browser or shell | Headset | play → talk | pause → stop | Confirm yes / no | Keys arrive with the keep-alive | Keys arrive without it | Notes |
|---|---|---|---|---|---|---|---|---|
| none on device yet | | | | | | | | fill this table before turning the flag on |

Suggested matrix: Pixel / Android 14 Chrome (PWA and TWA), a Samsung with One UI, iPhone Safari home-screen app;
wired USB-C / 3.5 mm headset, Bluetooth earbuds; TalkBack on and off.

## 3. Volume buttons (detector built; needs a native shell)

A web page, a PWA or a TWA **cannot see the hardware volume keys** (platform facts, section 5). The detector is
pure TypeScript (`handsfree/volumeChord.ts`), written for a native shell to feed with key events or to mirror in
Kotlin (proposal, section 3).

**Rule.** It fires only on a QUICK double press of volume up + volume down together:

- both keys down within `chordWindowMs` (150 ms),
- both released within `maxHoldMs` (400 ms) of the chord forming, else it is a hold,
- the second such chord completed within `doublePressWindowMs` (800 ms) of the first.

A long hold of both keys (Android's accessibility shortcut, TalkBack on/off, about 3 s) never fires. A single
volume press never fires and is never consumed. The timings are design values, **not measured**; tune on a phone.

**Consume.** The result's `consume` says whether the shell may swallow the event (`dispatchKeyEvent` returns
`true`). It is `true` only for the second key's down of a quick chord and the chord's releases, never for a lone
press and never for anything in a held chord. The first key's down of a chord is never consumed because at that
instant it may still be a normal press, so the volume steps once on the first key. That one step is the price of
never breaking normal volume use.

## 4. "Hello AIRA" (controller built; needs a native shell and an engine)

A closed web app cannot listen (section 5). `handsfree/wakeWord.ts` is the state machine around an injectable
engine (`WakeEngine { start, stop }`): off by default; `enable({ ownerConfirmed: true })` only, the question is
`HANDSFREE_STRINGS.wakeAsk` ("Listen for 'Hello AIRA' all the time? The sound stays on the phone until you say
it. Yes or no?"); on a detection: ack earcon, bring the app to the front, start the voice session (three
callbacks); a 3 s cooldown; `disable()` / `dispose()` always safe; an engine error or an OS kill restarts the
engine after 2 s, at most 3 times per minute, then `failed` and the owner hears `wakeFailed` ("'Hello AIRA'
stopped working. Double-tap to talk."). The controller has no network callback: audio stays on the device until
the wake word fires, by construction. Engine options, licences and the Android rules are in the proposal.

## 5. Platform facts (the owner's list, checked)

Each line says agree / disagree / uncertain and how sure we are. No web search was available when this was
written; everything is from knowledge as of mid-2026 and must be verified on a phone before anyone relies on it.

| # | Claim | Verdict | Why |
|---|---|---|---|
| 1 | A web page / PWA / TWA cannot read the hardware volume buttons | **Agree** (confident) | Android handles `KEYCODE_VOLUME_UP/DOWN` in the system and the browser app; no `keydown` reaches page JS on Chrome Android or iOS Safari. A TWA is Chrome, so the same. The only web signal is the (non-standard, Chrome-only) `volumechange`-style events on a media element, which fire for software volume, not the hardware rocker |
| 2 | A web page / PWA / TWA cannot listen (microphone) when closed | **Agree** (confident) | `getUserMedia` needs a live document; a service worker has no mic. Chrome Android pauses capture in a hidden tab after a while and kills it with the tab. Background mic needs a native foreground service |
| 3 | iPhone does not allow a custom wake word for third-party apps when closed | **Agree** (confident) | Only "Hey Siri" is always-on. A third-party app can listen only while in the foreground, or in the background with the audio background mode while an audio session is active and the app is still alive; iOS suspends and kills it, and an always-on custom hot word in the background is not supported for App Store apps. A web app on iOS gets nothing in the background |
| 4 | Holding volume up + down together is Android's accessibility shortcut (TalkBack) and must not be broken | **Agree** (confident) | Android 8+: hold both volume keys about 3 s to toggle the chosen accessibility service (TalkBack by default when a user enabled it). Handled by the system (`PhoneWindowManager`) **before** the foreground app sees the keys, so a well-behaved app cannot break it from `dispatchKeyEvent`; an AccessibilityService filtering keys sits after that too (uncertain on the exact order, verify with TalkBack on). Our detector ignores any hold anyway |

Extra facts the design relies on (same labelling):

- Media Session action handlers receive headset media keys on Chrome Android and iOS Safari: **known as of
  mid-2026**, verify. Chrome needs a playing, unmuted media element of more than about 5 s to make the page the
  active session: **known, verify**. Whether silent samples are enough: **uncertain, verify**.
- `MediaMetadata` and `playbackState` exist on both: **known**.
- "Hey Google, open AIRA" opens the installed PWA / TWA by its launcher name: **known** (the Assistant resolves
  app names from the launcher). "Hey Siri, open AIRA" needs a Shortcut (section in the README): **known**.
