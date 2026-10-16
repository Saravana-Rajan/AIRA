// Android APK (Bubblewrap Trusted Web Activity): checks the checked-in TWA config and build guide.
// Paths resolve from this file, so the test works whatever folder vitest runs from.
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const appPath = (p: string) => resolve(HERE, "..", p);
const readJson = (p: string) => JSON.parse(readFileSync(appPath(p), "utf8"));

const TWA_PATH = "twa/twa-manifest.json";
const README_PATH = "twa/README.md";
const GITIGNORE_PATH = "twa/.gitignore";
const ASSETLINKS_PATH = "public/.well-known/assetlinks.json";

describe("TWA config", () => {
  it("twa-manifest targets AIRA with custom-tabs fallback", () => {
    const t = readJson(TWA_PATH);
    expect(t.packageId).toBe("in.techjays.aira");
    expect(t.fallbackType).toBe("customtabs");
    expect(t.display).toBe("standalone");
    expect(t.startUrl).toBe("/");
    expect(t.host).toMatch(/\.(web\.app|firebaseapp\.com)$/);
    expect(t.name).toBe("AIRA");
    expect(t.launcherName).toBe("AIRA");
    expect(t.orientation).toBe("any"); // the app rotates with the phone; the dashboard follows it
  });

  it("every URL points at the Hosting site over https", () => {
    const t = readJson(TWA_PATH);
    const origin = `https://${t.host}`;
    expect(t.host).not.toMatch(/^https?:|\//);
    for (const key of ["webManifestUrl", "iconUrl", "maskableIconUrl", "fullScopeUrl"]) {
      expect(t[key], key).toMatch(new RegExp(`^${origin.replace(/\./g, "\\.")}/`));
    }
    expect(t.fullScopeUrl).toBe(`${origin}/`);
    expect(t.webManifestUrl).toBe(`${origin}/manifest.webmanifest`);
  });

  it("matches the web app manifest the APK wraps (name, icons, display, light colours)", () => {
    const t = readJson(TWA_PATH);
    const m = readJson("public/manifest.webmanifest");
    expect(t.name).toBe(m.name);
    expect(t.launcherName).toBe(m.short_name);
    expect(t.display).toBe(m.display);
    expect(t.orientation).toBe(m.orientation);
    expect(t.startUrl).toBe(m.start_url);
    expect(t.themeColor.toUpperCase()).toBe(m.theme_color.toUpperCase());
    expect(t.backgroundColor.toUpperCase()).toBe(m.background_color.toUpperCase());

    // Icons: the URLs must name a 512 icon the manifest lists and public/ actually serves.
    const origin = `https://${t.host}`;
    const listed: string[] = m.icons.map((i: { src: string }) => i.src);
    for (const key of ["iconUrl", "maskableIconUrl"]) {
      const path = String(t[key]).slice(origin.length);
      expect(listed, key).toContain(path);
      expect(existsSync(appPath(`public${path}`)), `${key} file`).toBe(true);
    }
    const maskable = m.icons.find((i: { purpose?: string }) => /maskable/.test(i.purpose ?? ""));
    expect(String(t.maskableIconUrl).slice(origin.length)).toBe(maskable.src);
  });

  it("uses the light theme only (overrides §6)", () => {
    const t = readJson(TWA_PATH);
    for (const key of [
      "themeColor",
      "themeColorDark",
      "backgroundColor",
      "navigationColor",
      "navigationColorDark",
    ]) {
      expect(String(t[key]).toUpperCase(), key).toBe("#FFFFFF");
    }
    for (const key of ["navigationDividerColor", "navigationDividerColorDark"]) {
      expect(String(t[key]).toUpperCase(), key).toBe("#E3E9F4"); // --border token
    }
  });

  it("keeps the minimal setup: no notifications, Play Billing, location or extra origins", () => {
    const t = readJson(TWA_PATH);
    expect(t.enableNotifications).toBe(false);
    expect(t.features ?? {}).toEqual({});
    expect(t.additionalTrustedOrigins).toEqual([]);
    expect(t.shortcuts).toEqual([]);
    expect(t.alphaDependencies).toEqual({ enabled: false });
  });

  it("signs with ./aira.keystore alias aira and holds no secrets", () => {
    const raw = readFileSync(appPath(TWA_PATH), "utf8");
    const t = JSON.parse(raw);
    expect(t.signingKey).toEqual({ path: "./aira.keystore", alias: "aira" });
    expect(raw).not.toMatch(/password/i);
    // Fingerprints are public (they go into assetlinks.json), so `bubblewrap fingerprint add` may
    // record real ones here. Each must be a full SHA-256, never a placeholder.
    expect(Array.isArray(t.fingerprints)).toBe(true);
    for (const f of t.fingerprints) {
      expect(f.value).toMatch(/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/);
    }
    expect(Number.isInteger(t.appVersionCode) && t.appVersionCode >= 1).toBe(true);
    expect(t.appVersionName).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("README explains the build, asset links and install steps", () => {
    const md = readFileSync(appPath(README_PATH), "utf8");
    for (const needle of [
      "in.techjays.aira",
      "bubblewrap update",
      "bubblewrap build",
      "keytool",
      "assetlinks.json",
      "delegate_permission/common.handle_all_urls",
      "adb install",
      "customtabs",
    ]) {
      expect(md, needle).toContain(needle);
    }
    expect(md).not.toMatch(/udhaar|kaggle/i);
  });

  it("keeps the keystore and build output out of git", () => {
    const ignore = readFileSync(appPath(GITIGNORE_PATH), "utf8")
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"));
    for (const pattern of ["*.keystore", "*.jks", "*.apk", "*.aab", "*.idsig", "app/", "build/"]) {
      expect(ignore, pattern).toContain(pattern);
    }
    expect(ignore).not.toContain("twa-manifest.json");
    expect(ignore).not.toContain("README.md");
  });

  // assetlinks.json needs the real SHA-256 of the signing key, so it only exists after the
  // keystore is made (see twa/README.md). Never commit a made-up fingerprint.
  it.skipIf(!existsSync(appPath(ASSETLINKS_PATH)))(
    "assetlinks.json delegates handle_all_urls to the signed APK",
    () => {
      const a = readJson(ASSETLINKS_PATH);
      expect(a[0].relation).toEqual(["delegate_permission/common.handle_all_urls"]);
      expect(a[0].target.namespace).toBe("android_app");
      expect(a[0].target.package_name).toBe("in.techjays.aira");
      expect(a[0].target.sha256_cert_fingerprints[0]).toMatch(/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/);
    },
  );
});
