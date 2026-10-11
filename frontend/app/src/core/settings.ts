import { LANGS, type Lang } from "@aira/contracts";
import type { Settings } from "./types";

export const SETTINGS_KEY = "aira.settings";
export const DEMO_SHOP_ID = "sunrise_dairy";

/** Treat as read-only: copy it (`{ ...DEFAULT_SETTINGS }`) before changing anything. */
export const DEFAULT_SETTINGS: Readonly<Settings> = {
  lang: "auto", // no default language: AIRA follows whoever speaks, turn by turn

  verbosity: "short",
  screenReaderMode: false,
  speechRate: 1,
  shopId: DEMO_SHOP_ID,
  onboarded: false,
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
    shopId: typeof r.shopId === "string" && r.shopId ? r.shopId : DEFAULT_SETTINGS.shopId,
    onboarded: r.onboarded === true,
  };
}

/** Persists settings (speechRate clamped). Storage failures are ignored: settings stay in memory. */
export function saveSettings(s: Settings, store: Storage | null = defaultStore()): void {
  try {
    store?.setItem(SETTINGS_KEY, JSON.stringify({ ...s, speechRate: clampRate(s.speechRate) }));
  } catch {
    /* private mode or quota: keep going with in-memory settings */
  }
}
