// frontend/app/test/walk.mode.test.ts — persisted walking-mode switch and its server / voice command
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  WALKING_MODE_KEY, WALKING_MODE_PREFIX, loadWalkingMode, saveWalkingMode, parseWalkingModeCommand, walkingMode,
} from "../src/walk/mode";
import { walkingModeText } from "../src/walk/strings";

function memStore(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    clear: () => m.clear(),
    key: () => null,
    get length() { return m.size; },
  };
}

describe("walking mode persistence", () => {
  it("defaults to on; a saved off is read back; garbage means on", () => {
    const s = memStore();
    expect(loadWalkingMode(s)).toBe(true);
    saveWalkingMode(false, s);
    expect(s.getItem(WALKING_MODE_KEY)).toBe("off");
    expect(loadWalkingMode(s)).toBe(false);
    s.setItem(WALKING_MODE_KEY, "banana");
    expect(loadWalkingMode(s)).toBe(true);
    expect(loadWalkingMode(null)).toBe(true);
  });

  it("a broken store never throws", () => {
    const bad = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("quota"); } } as unknown as Storage;
    expect(loadWalkingMode(bad)).toBe(true);
    expect(() => saveWalkingMode(false, bad)).not.toThrow();
  });
});

describe("walking mode command", () => {
  it("parses WALKING_MODE on/off (OPEN_URL-style summary) and a few spoken forms", () => {
    expect(WALKING_MODE_PREFIX).toBe("WALKING_MODE ");
    expect(parseWalkingModeCommand("WALKING_MODE on")).toBe(true);
    expect(parseWalkingModeCommand("WALKING_MODE off")).toBe(false);
    expect(parseWalkingModeCommand("WALKING_MODE OFF extra")).toBe(false);
    expect(parseWalkingModeCommand("WALKING_MODE maybe")).toBeNull();
    expect(parseWalkingModeCommand("OPEN_URL https://wa.me/1")).toBeNull();
    expect(parseWalkingModeCommand("walking mode off")).toBe(false);
    expect(parseWalkingModeCommand("turn walking mode on")).toBe(true);
    expect(parseWalkingModeCommand("stop walking mode")).toBe(false);
    expect(parseWalkingModeCommand("hello")).toBeNull();
  });

  it("has short confirmations in ta / hi / en", () => {
    expect(walkingModeText(true, "en")).toBe("Walking mode on.");
    expect(walkingModeText(false, "en")).toBe("Walking mode off.");
    for (const lang of ["ta", "hi"] as const) {
      expect(walkingModeText(true, lang)).not.toBe(walkingModeText(false, lang));
      expect(walkingModeText(true, lang).length).toBeGreaterThan(0);
    }
  });
});

describe("walkingMode store", () => {
  beforeEach(() => { walkingMode.reset(memStore()); });

  it("get/set persist and notify subscribers once per change", () => {
    const s = memStore();
    walkingMode.reset(s);
    const seen: boolean[] = [];
    const off = walkingMode.subscribe((on) => seen.push(on));
    expect(walkingMode.get()).toBe(true);
    walkingMode.set(false);
    walkingMode.set(false);
    walkingMode.set(true);
    expect(seen).toEqual([false, true]);
    expect(loadWalkingMode(s)).toBe(true);
    off();
    walkingMode.set(false);
    expect(seen).toEqual([false, true]);
  });

  it("a failing subscriber does not stop the others", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const seen: boolean[] = [];
    walkingMode.subscribe(() => { throw new Error("ui"); });
    walkingMode.subscribe((on) => seen.push(on));
    walkingMode.set(false);
    expect(seen).toEqual([false]);
    err.mockRestore();
  });
});
