// frontend/app/src/camera/openUrl.ts — OPEN_URL hand-off (interfaces: a `state` msg whose summary starts with
// "OPEN_URL <url>"). Only WhatsApp click-to-chat and tel: links are ever opened.
import type { ServerMsg, Lang } from "@aira/contracts";
import { OPEN_URL_PREFIX } from "../core/announce";
import { openUrlText, type OpenUrlPhrase } from "../i18n/camera";

export { OPEN_URL_PREFIX, openUrlText, type OpenUrlPhrase };

const HTTPS_ALLOWED = ["https://wa.me/", "https://api.whatsapp.com/"] as const;
const TEL_RE = /^tel:\+?[0-9][0-9\-().]{2,19}$/;

export type OpenKind = "whatsapp" | "call";

/** Normalised URL when allowed, else null. https links must parse to exactly wa.me / api.whatsapp.com (no creds, no port). */
function normalise(url: string): string | null {
  if (!url || /\s/.test(url)) return null;
  if (url.startsWith("tel:")) return TEL_RE.test(url) ? url : null;
  let href: string;
  try {
    href = new URL(url).href;
  } catch {
    return null;
  }
  return HTTPS_ALLOWED.some((p) => href.startsWith(p)) ? href : null;
}

export const isAllowedOpenUrl = (url: string): boolean => normalise(url) !== null;

export function parseOpenUrl(summary: string): { url: string; kind: OpenKind } | null {
  if (!summary.startsWith(OPEN_URL_PREFIX)) return null;
  const token = summary.slice(OPEN_URL_PREFIX.length).trim().split(/\s+/)[0] ?? "";
  const url = normalise(token);
  if (!url) return null;
  return { url, kind: url.startsWith("tel:") ? "call" : "whatsapp" };
}

export interface OpenUrlDeps {
  speak: (s: string) => void;
  lang: () => Lang;
  /** Opens an https link in a new tab; returns false when the browser blocked it. */
  open: (url: string) => boolean;
  /** Starts a phone call for a tel: link. */
  dial: (url: string) => boolean;
}

/** Browser default for `open`: a new tab, detached from this page. */
export function defaultOpen(url: string): boolean {
  const g = globalThis as unknown as { open?: (u: string, t: string) => { opener: unknown } | null };
  const w = g.open?.(url, "_blank") ?? null;
  if (!w) return false;
  try { w.opener = null; } catch { /* cross-origin: already detached */ }
  return true;
}

/** Browser default for `dial`: tel: links hand off to the dialler without leaving the app. */
export function defaultDial(url: string): boolean {
  const g = globalThis as unknown as { location?: { href: string } };
  if (!g.location) return false;
  g.location.href = url;
  return true;
}

export function createOpenUrlHandler(deps: OpenUrlDeps) {
  const done = new Set<string>(); // `${entity}:${id}:${url}` already handled (resent state msgs)
  let pendingUrl: string | null = null;

  const launch = (url: string) => (url.startsWith("tel:") ? deps.dial(url) : deps.open(url));

  return {
    /** Returns true when the message was an allowed OPEN_URL hand-off. */
    onServerMsg(m: ServerMsg): boolean {
      if (m.t !== "state") return false;
      const link = parseOpenUrl(m.summary);
      if (!link) {
        if (m.summary.startsWith(OPEN_URL_PREFIX)) console.warn("[aira] OPEN_URL refused (not on the allowlist)");
        return false;
      }
      const key = `${m.entity}:${m.id}:${link.url}`;
      if (done.has(key)) return true;
      done.add(key);
      deps.speak(openUrlText(link.kind, deps.lang()));
      if (launch(link.url)) {
        pendingUrl = null;
      } else {
        pendingUrl = link.url;
        deps.speak(openUrlText("blocked", deps.lang()));
      }
      return true;
    },
    /** Link kept after the browser blocked it (no user gesture); null when none. */
    pending: (): string | null => pendingUrl,
    /** Call from a user gesture to retry a blocked link. True when it opened. */
    openPending(): boolean {
      if (!pendingUrl) return false;
      if (!launch(pendingUrl)) return false;
      pendingUrl = null;
      return true;
    },
  };
}
