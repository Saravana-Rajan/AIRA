// The dashboard only reads the "aira" database. Build overrides §1: no Firebase Auth, Storage,
// Remote Config, Messaging or App Check on the client, and no client writes (rules deny them anyway).
import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>("../src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });

describe("dashboard is read-only and minimal", () => {
  it("has source files to check", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10);
  });

  it("never uses Firestore write APIs", () => {
    const offenders = Object.entries(sources)
      .filter(([, t]) => /\b(setDoc|updateDoc|addDoc|deleteDoc|writeBatch|runTransaction)\b/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
  });

  it("never imports Firebase Auth, Storage, Remote Config, Messaging or App Check", () => {
    const offenders = Object.entries(sources)
      .filter(([, t]) => /from\s+["']firebase\/(auth|storage|remote-config|messaging|app-check)["']/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
  });

  it("opens Firestore only through lib/firebase.ts (database aira)", () => {
    const offenders = Object.entries(sources)
      .filter(([p, t]) => !p.endsWith("/lib/firebase.ts") && /\bgetFirestore\s*\(/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
  });
});
