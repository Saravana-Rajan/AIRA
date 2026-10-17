import { describe, it, expect } from "vitest";
import dashTokens from "../src/styles/tokens.css?raw";
import appTokens from "../../app/src/ui/tokens.css?raw";
import indexHtml from "../index.html?raw";

const sources = import.meta.glob<string>("../src/**/*.{css,ts,tsx}", { query: "?raw", import: "default", eager: true });

const COLOUR_TOKENS = [
  "--bg", "--surface", "--border", "--text", "--text-muted", "--brand-blue", "--brand-cyan",
  "--brand-purple", "--brand-orange", "--ok", "--danger", "--gradient", "--font-sans",
];

function token(css: string, name: string): string | undefined {
  const m = css.match(new RegExp(`${name}\\s*:\\s*([^;]+);`));
  return m?.[1].trim().replace(/\s+/g, " ");
}

describe("dashboard design tokens (light theme only)", () => {
  it.each(COLOUR_TOKENS)("%s matches the phone app's tokens", (name) => {
    expect(token(dashTokens, name)).toBeDefined();
    expect(token(dashTokens, name)?.toUpperCase()).toBe(token(appTokens, name)?.toUpperCase());
  });

  it("uses 16px body text on the dashboard", () => {
    expect(token(dashTokens, "--text-body")).toBe("16px");
  });

  it("declares a light colour scheme and has no dark-mode styles anywhere", () => {
    expect(dashTokens).toMatch(/color-scheme:\s*light/);
    expect(indexHtml).toMatch(/<meta name="color-scheme" content="light"/);
    expect(Object.keys(sources).length).toBeGreaterThan(3);
    for (const [path, text] of Object.entries(sources)) {
      expect(text, path).not.toMatch(/prefers-color-scheme/);
    }
    expect(indexHtml).not.toMatch(/prefers-color-scheme/);
  });

  it("loads Inter and the Noto Tamil and Devanagari fonts from Google Fonts", () => {
    expect(indexHtml).toContain("https://fonts.googleapis.com/css2?");
    for (const family of ["Inter", "Noto+Sans+Tamil", "Noto+Sans+Devanagari"]) expect(indexHtml).toContain(`family=${family}`);
  });

  it("respects reduced motion and shows keyboard focus rings", () => {
    const css = Object.entries(sources).filter(([p]) => p.endsWith(".css")).map(([, t]) => t).join("\n");
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
    expect(css).toMatch(/:focus-visible/);
  });
});

describe("firebase client", () => {
  it("uses the Firestore database id from VITE_FIRESTORE_DATABASE (aira), never (default)", () => {
    const src = sources["../src/lib/firebase.ts"];
    expect(src).toBeDefined();
    expect(src).toMatch(/getFirestore\(\s*app\s*,\s*firestoreDatabaseId\(\s*env\s*\)\s*\)/);
    expect(src).not.toMatch(/\(default\)/);
  });

  it("builds the web config from VITE_FIREBASE_* and refuses to start without it (shown as Offline)", () => {
    const src = sources["../src/lib/firebase.ts"];
    expect(src).toMatch(/initializeApp\(\s*firebaseOptions\(\s*env\s*\)\s*\)/);
    expect(src).toMatch(/missingFirebaseConfig\(\s*env\s*\)/);
    expect(src).toMatch(/emulatorTarget\(\s*env\s*\)/);
  });

  it("never writes to Firestore (read-only dashboard)", () => {
    for (const [path, text] of Object.entries(sources)) {
      expect(text, path).not.toMatch(/\b(setDoc|updateDoc|addDoc|deleteDoc|writeBatch|runTransaction)\b/);
    }
  });
});
