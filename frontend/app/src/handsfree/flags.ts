// Hands-free start flags. All three are OFF by default (shared/docs/ops/handsfree.md).
//
// How a flag reaches the phone: the WebSocket contract is frozen (`ready` carries no feature list, and changing
// it needs the three-file contracts PR), so these are BUILD-TIME flags, the same way the app already takes
// VITE_AIRA_LIVE_WS and VITE_AIRA_APP_KEY (core/wire.ts appKey / wsDefault; shared/scripts/build_hosting.sh).
// The backend names AIRA_MEDIA_BUTTON / AIRA_VOLUME_TRIGGER / AIRA_WAKEWORD are the same flags seen from the
// server side; the app reads the VITE_-prefixed twin because Vite only exposes VITE_* to the bundle.
//
// In the browser only import.meta.env exists; the process.env fallback is the test seam (vi.stubEnv).

export interface HandsfreeFlags {
  /** VITE_AIRA_MEDIA_BUTTON=1: an earphone play/pause button talks / stops (handsfree/mediaButton.ts). */
  mediaButton: boolean;
  /**
   * VITE_AIRA_VOLUME_TRIGGER=1: the volume up + down double-press chord (handsfree/volumeChord.ts). The web app
   * cannot see hardware volume keys; the flag is read here so a native shell that injects key events into the
   * page (shared/docs/proposals/native-android-shell.md) and the app agree on one switch.
   */
  volumeTrigger: boolean;
  /** VITE_AIRA_WAKEWORD=1: the "Hello AIRA" controller may be enabled, still only after the owner confirms. */
  wakeWord: boolean;
}

export const FLAG_ENV: Record<keyof HandsfreeFlags, string> = {
  mediaButton: "VITE_AIRA_MEDIA_BUTTON",
  volumeTrigger: "VITE_AIRA_VOLUME_TRIGGER",
  wakeWord: "VITE_AIRA_WAKEWORD",
};

export const FLAGS_OFF: Readonly<HandsfreeFlags> = { mediaButton: false, volumeTrigger: false, wakeWord: false };

/** "1" or "true" (any case, trimmed) is on; everything else, including unset, is off. */
export function flagOn(raw: string | undefined | null): boolean {
  const v = String(raw ?? "").trim().toLowerCase();
  return v === "1" || v === "true";
}

function readEnv(name: string): string | undefined {
  try {
    const meta = (import.meta as { env?: Record<string, string | undefined> }).env?.[name];
    const proc = typeof process !== "undefined" ? process.env?.[name] : undefined;
    return meta || proc || undefined;
  } catch {
    return undefined;
  }
}

/** The flags built into this bundle. Never throws; anything unreadable is off. */
export function handsfreeFlags(read: (name: string) => string | undefined = readEnv): HandsfreeFlags {
  const safe = (name: string) => { try { return read(name); } catch { return undefined; } };
  return {
    mediaButton: flagOn(safe(FLAG_ENV.mediaButton)),
    volumeTrigger: flagOn(safe(FLAG_ENV.volumeTrigger)),
    wakeWord: flagOn(safe(FLAG_ENV.wakeWord)),
  };
}
