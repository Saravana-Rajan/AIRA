import type { ClientMsg } from "@aira/contracts";
import { langFromPhone, phoneLanguages } from "../core/lang";
import { appKey } from "../core/wire";

type Hello = Extract<ClientMsg, { t: "hello" }>;
type UidStore = Pick<Storage, "getItem" | "setItem">;

const UID_KEY = "aira.demo.uid";

function freshUid(): string {
  return `judge-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

function tabStore(): UidStore | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null; // some browsers throw on access when site data is blocked
  }
}

/** One stable judge uid per browser tab; works when storage is missing or blocked. */
export function demoUid(store: UidStore | null = tabStore()): string {
  try {
    const existing = store?.getItem(UID_KEY);
    if (existing) return existing;
  } catch { /* storage blocked: fall through to a fresh uid */ }
  const uid = freshUid();
  try { store?.setItem(UID_KEY, uid); } catch { /* not persisted; fine for one page view */ }
  return uid;
}

/**
 * The `hello` a judge sends: no user token (overrides §1: no Firebase Auth), only the built-in demo key, and no
 * shop: there is one, and the server picks it. `lang` is the browser's locale, a hint for AIRA's first line only:
 * it then follows whoever speaks.
 */
export function demoHello(appVersion: string, uid: string = demoUid(), languages: readonly string[] = phoneLanguages()): Hello {
  const key = appKey();
  return {
    t: "hello", uid, ...(key ? { idToken: key } : {}), lang: langFromPhone(languages),
    verbosity: "short", appVersion,
  };
}
