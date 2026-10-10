// How the dashboard reaches its data: Firestore (loaded on demand, so the first paint does not wait
// for the SDK) and the aira-live forecast endpoint. No Firebase Auth or Storage in this build.
import type { LiveStatus, ShopData } from "./live";
import { fetchForecast, type ForecastItem } from "./forecast";

type Env = Record<string, unknown>;

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** Subscribes to one shop; returns a stop function. */
export type ListenFn = (shopId: string, onChange: (d: ShopData) => void, onStatus: (s: LiveStatus) => void) => () => void;

/**
 * Base URL of aira-live for HTTP calls. Uses VITE_AIRA_LIVE_HTTP when set, otherwise derives it from the
 * app's VITE_AIRA_LIVE_WS (wss://host/ws -> https://host), which is what the deploy workflow sets.
 */
export function liveHttpBase(env: Env): string | null {
  const http = str(env.VITE_AIRA_LIVE_HTTP);
  if (http) return http.replace(/\/+$/, "");
  const ws = str(env.VITE_AIRA_LIVE_WS);
  if (!ws) return null;
  try {
    const u = new URL(ws);
    if (u.protocol !== "wss:" && u.protocol !== "ws:") return null;
    return `${u.protocol === "wss:" ? "https:" : "http:"}//${u.host}`;
  } catch {
    return null;
  }
}

/** Loads reorder suggestions for a shop; resolves null (shown as "Forecast unavailable") when aira-live is not configured. */
export function forecastLoader(env: Env, fetchImpl: typeof fetch = fetch): (shopId: string) => Promise<ForecastItem[] | null> {
  const base = liveHttpBase(env);
  return (shopId) => (base ? fetchForecast(base, shopId, fetchImpl) : Promise.resolve(null));
}

type LiveModule = { listenShop: (shopId: string, onChange: (d: ShopData) => void, nowMs: () => number, onStatus: (s: LiveStatus) => void) => () => void };

/**
 * A ListenFn that imports the Firestore listeners only when first needed. Any start-up failure
 * (missing Firebase config, a chunk that fails to load) is reported as "offline", never a blank page.
 */
export function lazyListen(load: () => Promise<LiveModule> = () => import("./live")): ListenFn {
  return (shopId, onChange, onStatus) => {
    let stop: (() => void) | null = null;
    let cancelled = false;
    load()
      .then((m) => {
        if (!cancelled) stop = m.listenShop(shopId, onChange, Date.now, onStatus);
      })
      .catch(() => {
        if (!cancelled) onStatus("offline");
      });
    return () => {
      cancelled = true;
      stop?.();
      stop = null;
    };
  };
}

/**
 * Evidence links for the audit trail. Only public https URLs are linked. Evidence in a private bucket
 * (gs://) stays private: the dashboard has no sign-in, so it cannot fetch it.
 */
export async function publicEvidenceUrl(uri: string): Promise<string | null> {
  return uri.startsWith("https://") ? uri : null;
}
