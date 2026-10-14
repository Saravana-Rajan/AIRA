import { describe, it, expect, beforeEach, vi } from "vitest";
import { loadSettings, saveSettings, DEFAULT_SETTINGS, SHOP_NAME, SETTINGS_KEY } from "../src/core/settings";
import { HAPTIC_PATTERNS, vibrate } from "../src/core/haptics";
import { app, initApp } from "../src/core/app";

class MemStore implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  key(i: number) { return [...this.m.keys()][i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}

describe("settings", () => {
  let store: MemStore;
  beforeEach(() => { store = new MemStore(); });

  it("returns defaults when nothing is stored", () => {
    expect(loadSettings(store)).toEqual(DEFAULT_SETTINGS);
    expect(DEFAULT_SETTINGS).not.toHaveProperty("shopId"); // one shop: the server picks it, the app never names it
    expect(SHOP_NAME).toBe("Sunrise Dairy");
  });

  it("drops an older build's saved shopId without error and never writes one", () => {
    store.setItem(SETTINGS_KEY, JSON.stringify({ lang: "ta", shopId: "sunrise_dairy", onboarded: true }));
    const s = loadSettings(store);
    expect(s).toEqual({ ...DEFAULT_SETTINGS, lang: "ta", onboarded: true });
    expect(s).not.toHaveProperty("shopId");
    saveSettings({ ...s, shopId: "legacy" }, store);
    expect(JSON.parse(store.getItem(SETTINGS_KEY)!)).not.toHaveProperty("shopId");
    expect(loadSettings(store)).not.toHaveProperty("shopId");
  });

  it("returns a fresh copy so callers cannot mutate the defaults", () => {
    const s = loadSettings(store);
    s.lang = "ta";
    expect(DEFAULT_SETTINGS.lang).toBe("auto"); // no default language: AIRA follows the speaker
  });

  it("round-trips and clamps speechRate to 0.5–2", () => {
    saveSettings({ ...DEFAULT_SETTINGS, lang: "ta", speechRate: 9 }, store);
    const s = loadSettings(store);
    expect(s.lang).toBe("ta");
    expect(s.speechRate).toBe(2);
    saveSettings({ ...DEFAULT_SETTINGS, speechRate: 0.1 }, store);
    expect(loadSettings(store).speechRate).toBe(0.5);
  });

  it("round-trips every field", () => {
    const s = { lang: "hi" as const, verbosity: "detail" as const, screenReaderMode: true, speechRate: 1.25, onboarded: true, showBoxes: true };
    saveSettings(s, store);
    expect(loadSettings(store)).toEqual(s);
    expect(store.getItem(SETTINGS_KEY)).not.toBeNull();
  });

  it("'Show boxes' is off by default and only a real true turns it on", () => {
    expect(DEFAULT_SETTINGS.showBoxes).toBe(false);
    store.setItem("aira.settings", JSON.stringify({ showBoxes: "yes" }));
    expect(loadSettings(store).showBoxes).toBe(false);
    store.setItem("aira.settings", JSON.stringify({ showBoxes: true }));
    expect(loadSettings(store).showBoxes).toBe(true);
  });

  it("ignores corrupt JSON and unknown languages", () => {
    store.setItem("aira.settings", "{not json");
    expect(loadSettings(store)).toEqual(DEFAULT_SETTINGS);
    store.setItem("aira.settings", JSON.stringify({ lang: "fr" }));
    expect(loadSettings(store).lang).toBe("auto");
    store.setItem("aira.settings", JSON.stringify({ lang: "auto" }));
    expect(loadSettings(store).lang).toBe("auto");
  });

  it("falls back per field for bad types and non-finite rates", () => {
    store.setItem("aira.settings", JSON.stringify({ verbosity: "loud", screenReaderMode: "yes", speechRate: "fast", shopId: "", onboarded: 1 }));  // shopId: an old build's field, ignored
    expect(loadSettings(store)).toEqual(DEFAULT_SETTINGS);
    store.setItem("aira.settings", JSON.stringify([1, 2]));
    expect(loadSettings(store)).toEqual(DEFAULT_SETTINGS);
    saveSettings({ ...DEFAULT_SETTINGS, speechRate: Number.NaN }, store);
    expect(loadSettings(store).speechRate).toBe(DEFAULT_SETTINGS.speechRate);
  });

  it("survives a storage that throws (private mode, blocked site data)", () => {
    const broken = {
      getItem() { throw new Error("SecurityError"); },
      setItem() { throw new Error("QuotaExceededError"); },
    } as unknown as Storage;
    expect(loadSettings(broken)).toEqual(DEFAULT_SETTINGS);
    expect(() => saveSettings(DEFAULT_SETTINGS, broken)).not.toThrow();
  });
});

describe("haptics", () => {
  it("has a pattern for every HapticName", () => {
    expect(Object.keys(HAPTIC_PATTERNS).sort()).toEqual(["ack", "done", "match", "mismatch", "warn"]);
  });
  it("is a no-op returning false when vibrate is unsupported", () => {
    expect(vibrate("ack", {} as Navigator)).toBe(false);
  });
  it("calls navigator.vibrate with the pattern", () => {
    const nav = { vibrate: vi.fn(() => true) } as unknown as Navigator;
    expect(vibrate("mismatch", nav)).toBe(true);
    expect((nav as any).vibrate).toHaveBeenCalledWith(HAPTIC_PATTERNS.mismatch);
  });
  it("returns false instead of throwing when vibrate throws", () => {
    const nav = { vibrate: () => { throw new Error("blocked"); } } as unknown as Navigator;
    expect(vibrate("ack", nav)).toBe(false);
  });
});

describe("app singleton", () => {
  it("app.lang() follows what the wiring reports (the language the server last detected)", () => {
    let current: "ta" | "hi" | "en" = "ta";
    initApp({
      socket: { send: () => false } as any,
      settings: { ...DEFAULT_SETTINGS },
      speakImpl: vi.fn(), earconImpl: vi.fn(), hapticImpl: vi.fn(),
      langImpl: () => current,
    });
    expect(app.lang()).toBe("ta");
    current = "en";
    expect(app.lang()).toBe("en");
  });

  it("initApp fills the exported `app` object with the interface methods", () => {
    const sent: unknown[] = [];
    const speakImpl = vi.fn(), earconImpl = vi.fn(), hapticImpl = vi.fn();
    const ret = initApp({
      socket: { send: (m: unknown) => { sent.push(m); return true; } } as any,
      settings: { ...DEFAULT_SETTINGS },
      speakImpl, earconImpl, hapticImpl,
    });
    expect(ret).toBe(app);
    expect(app.send({ t: "stop" })).toBe(true);
    expect(sent).toEqual([{ t: "stop" }]);
    app.earcon("match"); app.haptic("done"); app.speak("hello");
    expect(earconImpl).toHaveBeenCalledWith("match");
    expect(hapticImpl).toHaveBeenCalledWith("done");
    expect(speakImpl).toHaveBeenCalledWith("hello", expect.objectContaining({ lang: "auto" }));
    expect(typeof app.settings.lang).toBe("string");
    expect(app.lang()).toBe("en"); // "auto" with no speaker yet: English labels
  });

  it("reads the current settings and socket at call time", () => {
    const speakImpl = vi.fn();
    initApp({
      socket: { send: () => false } as any,
      settings: { ...DEFAULT_SETTINGS },
      speakImpl, earconImpl: vi.fn(), hapticImpl: vi.fn(),
    });
    app.settings = { ...app.settings, lang: "hi" };
    app.speak("namaste");
    expect(speakImpl).toHaveBeenCalledWith("namaste", expect.objectContaining({ lang: "hi" }));
    expect(app.lang()).toBe("hi");

    const sent: unknown[] = [];
    app.socket = { send: (m: unknown) => { sent.push(m); return true; } } as any;
    expect(app.send({ t: "stop" })).toBe(true);
    expect(sent).toEqual([{ t: "stop" }]);
  });
});
