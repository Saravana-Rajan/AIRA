// The dashboard only reads the "aira" database. No Firebase Storage, Remote Config, Messaging or App Check on
// the client, and no client writes (rules deny them anyway). Firebase Auth is used in exactly one place:
// lib/firebaseAuth.ts, the Google sign-in the Live page needs (loaded on demand, only there) so aira-live can
// verify who is watching the owner's camera; nothing else on the dashboard signs in.
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

  it("never imports Firebase Storage, Remote Config, Messaging or App Check", () => {
    const offenders = Object.entries(sources)
      .filter(([, t]) => /from\s+["']firebase\/(storage|remote-config|messaging|app-check)["']/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
  });

  it("imports Firebase Auth only in lib/firebaseAuth.ts (the Live page's Google sign-in), and loads that only on demand", () => {
    const offenders = Object.entries(sources)
      .filter(([p, t]) => !p.endsWith("/lib/firebaseAuth.ts") && /from\s+["']firebase\/auth["']/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
    const eager = Object.entries(sources)
      .filter(([, t]) => /^\s*import\b[^;]*["']\.\/firebaseAuth["']/m.test(t))
      .map(([p]) => p);
    expect(eager).toEqual([]); // only `import("./firebaseAuth")` from useMonitor
  });

  it("opens Firestore only through lib/firebase.ts (database aira)", () => {
    const offenders = Object.entries(sources)
      .filter(([p, t]) => !p.endsWith("/lib/firebase.ts") && /\bgetFirestore\s*\(/.test(t))
      .map(([p]) => p);
    expect(offenders).toEqual([]);
  });
});
