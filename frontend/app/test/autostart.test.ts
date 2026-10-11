// Hands-free start: the launch rule, the installed-app detection, the local "listening" earcon and the strings.
import { describe, it, expect } from "vitest";
import { CUE_NAMES } from "@aira/contracts";
import { shouldAutoStart, isInstalledLaunch, wantsStart } from "../src/core/autostart";
import { EARCONS, LOCAL_EARCONS, playEarcon } from "../src/voice/earcons";
import { STRINGS } from "../src/i18n/strings";

describe("shouldAutoStart", () => {
  it("never auto-starts before the first Start tap: a browser needs one user gesture before the mic", () => {
    expect(shouldAutoStart({ onboarded: false, search: "?start=1", installed: true })).toBe(false);
    expect(shouldAutoStart({ onboarded: false, search: "", installed: true })).toBe(false);
    expect(shouldAutoStart({ onboarded: false, search: "", installed: false })).toBe(false);
  });

  it("auto-starts an onboarded owner from the app icon, the Assistant and the ?start=1 shortcut", () => {
    expect(shouldAutoStart({ onboarded: true, search: "", installed: true })).toBe(true); // installed app icon / "Hey Google, open AIRA"
    expect(shouldAutoStart({ onboarded: true, search: "?start=1", installed: false })).toBe(true); // manifest shortcut, deep link
    expect(shouldAutoStart({ onboarded: true, search: "?demo=0&start=1", installed: false })).toBe(true);
  });

  it("a plain browser tab still shows Start (one tap), unless ?start=1 asks otherwise", () => {
    expect(shouldAutoStart({ onboarded: true, search: "", installed: false })).toBe(false);
    expect(shouldAutoStart({ onboarded: true, search: "?start=0", installed: false })).toBe(false);
  });

  it("wantsStart reads only start=1", () => {
    expect(wantsStart("?start=1")).toBe(true);
    expect(wantsStart("start=1")).toBe(true);
    expect(wantsStart("?start=yes")).toBe(false);
    expect(wantsStart("")).toBe(false);
  });
});

describe("isInstalledLaunch", () => {
  const win = (o: { modes?: string[]; standalone?: boolean; referrer?: string }) =>
    ({
      matchMedia: (q: string) => ({ matches: (o.modes ?? []).some((m) => q.includes(m)) }),
      navigator: { standalone: o.standalone },
      document: { referrer: o.referrer ?? "" },
    }) as unknown as Window;

  it("is true for a standalone, fullscreen or minimal-ui display mode (installed PWA / TWA)", () => {
    expect(isInstalledLaunch(win({ modes: ["standalone"] }))).toBe(true);
    expect(isInstalledLaunch(win({ modes: ["fullscreen"] }))).toBe(true);
    expect(isInstalledLaunch(win({ modes: ["minimal-ui"] }))).toBe(true);
  });
  it("is true for iOS home-screen apps and for an Android app intent (the Assistant)", () => {
    expect(isInstalledLaunch(win({ standalone: true }))).toBe(true);
    expect(isInstalledLaunch(win({ referrer: "android-app://com.google.android.googlequicksearchbox/" }))).toBe(true);
  });
  it("is false in a browser tab, and never throws without matchMedia", () => {
    expect(isInstalledLaunch(win({}))).toBe(false);
    expect(isInstalledLaunch({} as Window)).toBe(false);
    expect(isInstalledLaunch({ matchMedia: () => { throw new Error("no"); } } as unknown as Window)).toBe(false);
  });
});

describe("the local listening earcon", () => {
  it("is not a contract cue (the CueName list is frozen) but plays like one: short, rising", () => {
    expect(Object.keys(EARCONS)).not.toContain("listening");
    expect([...CUE_NAMES]).not.toContain("listening");
    const e = LOCAL_EARCONS.listening;
    expect(e.tones.reduce((s, t) => s + t.ms, 0)).toBeLessThanOrEqual(600);
    expect(e.tones.at(-1)!.hz).toBeGreaterThan(e.tones[0].hz);
  });
  it("playEarcon knows it", () => {
    const started: number[] = [];
    const ctx = {
      currentTime: 0,
      destination: {},
      createOscillator: () => ({ frequency: { value: 0 }, connect: (n: unknown) => n, start: (t: number) => started.push(t), stop() {} }),
      createGain: () => ({ gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect: (n: unknown) => n }),
    } as unknown as AudioContext;
    playEarcon(ctx, "listening");
    expect(started).toHaveLength(LOCAL_EARCONS.listening.tones.length);
  });
});

describe("hands-free strings", () => {
  it("has a short greeting and the tap-to-start line in ta, hi and en", () => {
    for (const k of ["greeting", "tapToStart"] as const) {
      expect(Object.keys(STRINGS[k]).sort()).toEqual(["en", "hi", "ta"]);
      for (const s of Object.values(STRINGS[k])) expect(s.length).toBeLessThan(60);
    }
    expect(STRINGS.tapToStart.en).toBe("Tap anywhere to start.");
  });
});
