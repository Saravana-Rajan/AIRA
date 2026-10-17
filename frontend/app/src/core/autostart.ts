// Hands-free start. A blind owner should not have to find the Start button every time he opens AIRA from the
// app icon, from "Hey Google, open AIRA" (an Android intent for the installed app) or from the "Talk to AIRA"
// shortcut (manifest.webmanifest → /?start=1). ui/AppShell then wires, connects and opens the mic on load.
//
// Why the FIRST launch still shows Start: a browser needs one user gesture on the origin before it will open
// the microphone, unlock audio output and start speech. The mic permission a user grants inside that first
// tap is persisted for the origin (and for an installed app it survives restarts), so from the next launch
// on getUserMedia works without a tap. We remember "the owner tapped Start once" as `Settings.onboarded`,
// which core/settings keeps in localStorage inside try/catch (private mode or blocked site data fall back
// to `false`, which only costs one extra tap). If the browser still refuses the gesture-less mic, AppShell
// shows the Start screen and says "Tap anywhere to start".

/** True when the page URL asks for a hands-free start: `/?start=1` (the manifest shortcut, a deep link). */
export function wantsStart(search: string): boolean {
  try {
    return new URLSearchParams(search).get("start") === "1";
  } catch {
    return false;
  }
}

/**
 * True when AIRA was opened as an installed app rather than in a browser tab: a PWA or TWA window
 * (display-mode standalone / fullscreen / minimal-ui), an iOS home-screen app (`navigator.standalone`), or an
 * Android app intent such as the Assistant's (an `android-app://` referrer). Never throws.
 */
export function isInstalledLaunch(win: Window = window): boolean {
  try {
    for (const mode of ["standalone", "fullscreen", "minimal-ui"]) {
      if (win.matchMedia?.(`(display-mode: ${mode})`)?.matches) return true;
    }
    if ((win.navigator as Navigator & { standalone?: boolean })?.standalone === true) return true;
    if (win.document?.referrer?.startsWith("android-app://")) return true;
  } catch {
    /* no matchMedia, or a sandbox that throws: treat as a browser tab */
  }
  return false;
}

/**
 * True on an iPhone or iPad (Safari, a home-screen app, or any iOS browser: all run WebKit). iPadOS reports a Mac
 * user agent, so a touch "Macintosh" counts too. iOS unlocks Web Audio, speech and the motion sensors only inside a
 * user gesture, so there the Start tap is always needed. Never throws.
 */
export function isIOS(nav: Pick<Navigator, "userAgent" | "maxTouchPoints"> | undefined = globalThis.navigator): boolean {
  try {
    const ua = String(nav?.userAgent ?? "");
    if (/iPhone|iPad|iPod/.test(ua)) return true;
    return /Macintosh/.test(ua) && (nav?.maxTouchPoints ?? 0) > 1;
  } catch {
    return false;
  }
}

/**
 * Skip the Start tap only for an owner who has tapped it once before (see the note above), and only when the
 * app was opened from the icon / the Assistant (installed) or the `?start=1` shortcut. A plain browser tab
 * keeps the one tap: that is where a judge, a developer or an e2e run looks at the Start screen.
 * Never on iOS (`ios`): without a tap AIRA's voice, the earcons and the motion sensors stay locked there.
 */
export function shouldAutoStart(o: { onboarded: boolean; search: string; installed: boolean; ios?: boolean }): boolean {
  if (!o.onboarded || o.ios) return false;
  return o.installed || wantsStart(o.search);
}
