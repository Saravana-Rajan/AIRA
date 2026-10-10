import type { ClientMsg } from "@aira/contracts";
import { DEMO_SHOP_ID } from "../core/settings";

type Hello = Extract<ClientMsg, { t: "hello" }>;
type UidStore = Pick<Storage, "getItem" | "setItem">;

/** The seeded demo shop (overrides §1). The server maps a token-less hello to it. */
export { DEMO_SHOP_ID };
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

/** The `hello` a judge sends: no ID token (overrides §1: no Firebase Auth). */
export function demoHello(appVersion: string, uid: string = demoUid()): Hello {
  return { t: "hello", uid, shopId: DEMO_SHOP_ID, lang: "en", verbosity: "short", appVersion };
}
