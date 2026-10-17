import { LANGS, type Lang } from "@aira/contracts";
import type { Settings } from "./types";

export const SETTINGS_KEY = "aira.settings";
/** The one shop this build serves, as shown on screen. Its id lives on the server (config demo_shop_id). */
export const SHOP_NAME = "AIRA Cricket Store";

/** Treat as read-only: copy it (`{ ...DEFAULT_SETTINGS }`) before changing anything. */
export const DEFAULT_SETTINGS: Readonly<Settings> = {
  lang: "auto", // no default language: AIRA follows whoever speaks, turn by turn

  verbosity: "short",
  screenReaderMode: false,
  speechRate: 1,
  onboarded: false,
  showBoxes: false,
};

const clampRate = (n: unknown): number =>
  typeof n === "number" && Number.isFinite(n) ? Math.min(2, Math.max(0.5, n)) : DEFAULT_SETTINGS.speechRate;

const defaultStore = (): Storage | null => {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null; // some browsers throw on access when site data is blocked
  }
};

/** Reads settings, falling back to the default for any missing or invalid field. Never throws. */
export function loadSettings(store: Storage | null = defaultStore()): Settings {
  let raw: unknown = null;
  try {
    raw = JSON.parse(store?.getItem(SETTINGS_KEY) ?? "null");
  } catch {
    raw = null;
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { ...DEFAULT_SETTINGS };
  const r = raw as Partial<Record<keyof Settings, unknown>>;
  return {
    lang: (LANGS as readonly unknown[]).includes(r.lang) ? (r.lang as Lang) : "auto",
    verbosity: r.verbosity === "detail" ? "detail" : "short",
    screenReaderMode: r.screenReaderMode === true,
    speechRate: clampRate(r.speechRate),
    // an older build's shopId is dropped: there is one shop and the server picks it
    onboarded: r.onboarded === true,
    showBoxes: r.showBoxes === true,
  };
}

/** Persists settings (speechRate clamped, no shopId). Storage failures are ignored: settings stay in memory. */
export function saveSettings(s: Settings, store: Storage | null = defaultStore()): void {
  try {
    const { shopId: _dropped, ...rest } = s;
    store?.setItem(SETTINGS_KEY, JSON.stringify({ ...rest, speechRate: clampRate(s.speechRate) }));
  } catch {
    /* private mode or quota: keep going with in-memory settings */
  }
}
