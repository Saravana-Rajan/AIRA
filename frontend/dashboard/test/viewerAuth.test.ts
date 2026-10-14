// The pure side of the Live page's sign-in: reading a token's expiry, the refresh schedule, the popup ->
// redirect fallback and the words for a failed sign-in. No Firebase here.
import { describe, expect, it, vi } from "vitest";
import {
  REFRESH_EARLY_MS, REFRESH_MIN_MS, authConfigured, authErrorText, prefersRedirect, refreshDelayMs, signInWithFallback, tokenExp,
} from "../src/lib/viewerAuth";

import { b64url, jwt } from "./helpers/jwt";

describe("tokenExp", () => {
  it("reads exp (seconds) as epoch ms without verifying anything", () => {
    expect(tokenExp(jwt({ sub: "u1", exp: 1_760_003_600 }))).toBe(1_760_003_600_000);
  });
  it("is null for junk, a token without exp, or a non-numeric exp", () => {
    expect(tokenExp("")).toBeNull();
    expect(tokenExp("a.b")).toBeNull();
    expect(tokenExp("a.!!.c")).toBeNull();
    expect(tokenExp(jwt({ sub: "u1" }))).toBeNull();
    expect(tokenExp(jwt({ exp: "soon" }))).toBeNull();
    expect(tokenExp(`x.${b64url([1, 2])}.y`)).toBeNull();
  });
});

describe("refreshDelayMs", () => {
  it("refreshes five minutes before expiry, never sooner than ten seconds from now", () => {
    expect(REFRESH_EARLY_MS).toBe(5 * 60_000);
    expect(REFRESH_MIN_MS).toBe(10_000);
    expect(refreshDelayMs(1_000_000 + 60 * 60_000, 1_000_000)).toBe(55 * 60_000);
    expect(refreshDelayMs(1_000_000 + 4 * 60_000, 1_000_000)).toBe(10_000);
    expect(refreshDelayMs(1_000_000 - 1, 1_000_000)).toBe(10_000);
  });
});

describe("signInWithFallback", () => {
  const err = (code: string) => Object.assign(new Error(code), { code });
  it("uses the popup when it works", async () => {
    const popup = vi.fn(async () => undefined);
    const redirect = vi.fn(async () => undefined);
    await signInWithFallback(popup, redirect, "Mozilla/5.0 (Macintosh) Chrome/130");
    expect(popup).toHaveBeenCalledTimes(1);
    expect(redirect).not.toHaveBeenCalled();
  });
  it.each(["auth/popup-blocked", "auth/operation-not-supported-in-this-environment", "auth/web-storage-unsupported"])(
    "falls back to a redirect when the popup fails with %s", async (code) => {
      const popup = vi.fn(async () => { throw err(code); });
      const redirect = vi.fn(async () => undefined);
      await signInWithFallback(popup, redirect);
      expect(redirect).toHaveBeenCalledTimes(1);
    },
  );
  it("rethrows any other popup error (a closed popup, a disabled provider)", async () => {
    const redirect = vi.fn(async () => undefined);
    await expect(signInWithFallback(async () => { throw err("auth/popup-closed-by-user"); }, redirect)).rejects.toMatchObject({ code: "auth/popup-closed-by-user" });
    await expect(signInWithFallback(async () => { throw err("auth/configuration-not-found"); }, redirect)).rejects.toMatchObject({ code: "auth/configuration-not-found" });
    expect(redirect).not.toHaveBeenCalled();
  });
  it("goes straight to a redirect on mobile Safari / iOS, where popups do not work", async () => {
    const ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
    expect(prefersRedirect(ios)).toBe(true);
    expect(prefersRedirect("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")).toBe(false);
    expect(prefersRedirect("Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile Safari/537.36")).toBe(false);
    const popup = vi.fn(async () => undefined);
    const redirect = vi.fn(async () => undefined);
    await signInWithFallback(popup, redirect, ios);
    expect(popup).not.toHaveBeenCalled();
    expect(redirect).toHaveBeenCalledTimes(1);
  });
});

describe("authErrorText", () => {
  const err = (code: string) => ({ code, message: "secret internals" });
  it("says when Google sign-in is not enabled for the project, and when the domain is not authorized", () => {
    expect(authErrorText(err("auth/configuration-not-found"))).toBe("Google sign-in isn't enabled for this project yet. Ask the shop owner to enable it.");
    expect(authErrorText(err("auth/operation-not-allowed"))).toBe("Google sign-in isn't enabled for this project yet. Ask the shop owner to enable it.");
    expect(authErrorText(err("auth/unauthorized-domain"))).toContain("isn't authorized for sign-in");
    expect(authErrorText(err("auth/popup-closed-by-user"))).toBe("Sign-in was cancelled.");
  });
  it("is generic for anything else and never repeats the error's own text", () => {
    expect(authErrorText(err("auth/internal-error"))).toBe("Sign-in failed. Try again.");
    expect(authErrorText(new Error("boom"))).toBe("Sign-in failed. Try again.");
    expect(authErrorText(null)).toBe("Sign-in failed. Try again.");
    expect(authErrorText(err("auth/internal-error"))).not.toContain("secret");
  });
});

describe("authConfigured", () => {
  it("needs the Firebase project id and API key", () => {
    expect(authConfigured({})).toBe(false);
    expect(authConfigured({ VITE_FIREBASE_PROJECT_ID: "p" })).toBe(false);
    expect(authConfigured({ VITE_FIREBASE_PROJECT_ID: "p", VITE_FIREBASE_API_KEY: " " })).toBe(false);
    expect(authConfigured({ VITE_FIREBASE_PROJECT_ID: "p", VITE_FIREBASE_API_KEY: "k" })).toBe(true);
  });
});
