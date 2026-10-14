// Who may watch AIRA Live: the viewer's Google sign-in (Firebase Authentication), as the Live page sees it.
// This module is pure (no Firebase import) so the hook and the page are tested with a fake; lib/firebaseAuth
// is the browser implementation, loaded on demand. The server (aira-live /monitor) verifies the ID token and
// checks the account against its own allowlist; nothing here decides access, it only carries the credential.
import { missingFirebaseConfig } from "./firebaseConfig";

export interface ViewerUser { uid: string; email: string | null; name: string | null }

/** The sign-in the Live page needs: who is signed in, how to sign in and out, and a fresh ID token. */
export interface ViewerAuth {
  /** Called with the current user at once and on every change; null while signed out. Returns unsubscribe. */
  subscribe(cb: (u: ViewerUser | null) => void): () => void;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  /** The signed-in user's Firebase ID token; `force` asks for a freshly minted one. Rejects when signed out. */
  idToken(force?: boolean): Promise<string>;
}

/** unconfigured: no Firebase web config in the build · loading: the SDK has not said yet · signed_out · signed_in. */
export type AuthStatus = "unconfigured" | "loading" | "signed_out" | "signed_in";

/** True when the build carries the Firebase web config sign-in needs (VITE_FIREBASE_PROJECT_ID and API key). */
export function authConfigured(env: Record<string, unknown>): boolean {
  return missingFirebaseConfig(env).length === 0;
}

/** A token is refreshed this long before it expires. */
export const REFRESH_EARLY_MS = 5 * 60_000;
/** Never refresh sooner than this after a token was sent (a token already near its end still gets one try). */
export const REFRESH_MIN_MS = 10_000;

function b64urlJson(part: string): Record<string, unknown> | null {
  try {
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const v: unknown = JSON.parse(atob(b64));
    return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/**
 * The `exp` claim of a JWT as epoch ms, read without verifying anything: the page only uses it to schedule
 * its refresh (the server is the one that checks the token). Null when the token is not decodable.
 */
export function tokenExp(idToken: string): number | null {
  const parts = idToken.split(".");
  if (parts.length !== 3) return null;
  const payload = b64urlJson(parts[1]);
  const exp = payload?.exp;
  return typeof exp === "number" && Number.isFinite(exp) && exp > 0 ? exp * 1000 : null;
}

/** How long to wait before refreshing a token that expires at `expMs`: REFRESH_EARLY_MS before, never under REFRESH_MIN_MS. */
export function refreshDelayMs(expMs: number, nowMs: number): number {
  return Math.max(REFRESH_MIN_MS, expMs - nowMs - REFRESH_EARLY_MS);
}

type AuthErr = { code?: unknown; message?: unknown };
const codeOf = (err: unknown): string => {
  const c = (err as AuthErr | null)?.code;
  return typeof c === "string" ? c : "";
};

/** Firebase error codes after which a redirect sign-in may still work (a popup the browser would not open). */
export const POPUP_FALLBACK_CODES = Object.freeze([
  "auth/popup-blocked",
  "auth/operation-not-supported-in-this-environment",
  "auth/web-storage-unsupported",
] as const);

/** Mobile Safari (and every iOS browser, all WebKit) blocks popups opened after an await: redirect there. */
export function prefersRedirect(userAgent: string): boolean {
  return /iPhone|iPad|iPod/i.test(userAgent) || (/Safari/i.test(userAgent) && /Mobile/i.test(userAgent) && !/Chrome|CriOS|FxiOS/i.test(userAgent));
}

/**
 * Sign in with a popup, and with a redirect where a popup cannot work: on a mobile-Safari user agent at once,
 * otherwise when the popup fails with one of POPUP_FALLBACK_CODES. Any other error is rethrown (a closed
 * popup, a disabled provider, a domain that is not authorized).
 */
export async function signInWithFallback(popup: () => Promise<unknown>, redirect: () => Promise<unknown>, userAgent = ""): Promise<void> {
  if (prefersRedirect(userAgent)) {
    await redirect();
    return;
  }
  try {
    await popup();
  } catch (err) {
    if ((POPUP_FALLBACK_CODES as readonly string[]).includes(codeOf(err))) {
      await redirect();
      return;
    }
    throw err;
  }
}

/** What to tell the viewer when sign-in fails. Nothing from the error reaches the page except its code's meaning. */
export function authErrorText(err: unknown): string {
  switch (codeOf(err)) {
    case "auth/configuration-not-found":
    case "auth/operation-not-allowed":
      return "Google sign-in isn't enabled for this project yet. Ask the shop owner to enable it.";
    case "auth/unauthorized-domain":
      return "This address isn't authorized for sign-in. Ask the shop owner to add it in Firebase Authentication.";
    case "auth/popup-closed-by-user":
    case "auth/cancelled-popup-request":
      return "Sign-in was cancelled.";
    case "auth/network-request-failed":
      return "Sign-in failed: no connection. Check the internet, then try again.";
    default:
      return "Sign-in failed. Try again.";
  }
}
