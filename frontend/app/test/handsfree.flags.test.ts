import { describe, it, expect, vi, afterEach } from "vitest";
import { FLAGS_OFF, FLAG_ENV, flagOn, handsfreeFlags } from "../src/handsfree/flags";
import { HANDSFREE_STRINGS } from "../src/handsfree/strings";
import { LANGS } from "@aira/contracts";

afterEach(() => vi.unstubAllEnvs());

describe("handsfree flags", () => {
  it("all three are off by default", () => {
    expect(handsfreeFlags(() => undefined)).toEqual(FLAGS_OFF);
    expect(handsfreeFlags()).toEqual(FLAGS_OFF); // nothing set in the test env
  });

  it("only '1' or 'true' turn a flag on", () => {
    for (const v of ["1", "true", "TRUE", " 1 "]) expect(flagOn(v)).toBe(true);
    for (const v of ["0", "", "false", "yes", "on", undefined, null]) expect(flagOn(v)).toBe(false);
  });

  it("reads each flag from its own VITE_ variable (the build-time pattern of VITE_AIRA_APP_KEY)", () => {
    vi.stubEnv(FLAG_ENV.mediaButton, "1");
    expect(handsfreeFlags()).toEqual({ ...FLAGS_OFF, mediaButton: true });
    const read = (n: string) => (n === "VITE_AIRA_WAKEWORD" || n === "VITE_AIRA_VOLUME_TRIGGER" ? "true" : undefined);
    expect(handsfreeFlags(read)).toEqual({ mediaButton: false, volumeTrigger: true, wakeWord: true });
  });

  it("never throws when the env reader does", () => {
    expect(handsfreeFlags(() => { throw new Error("no env"); })).toEqual(FLAGS_OFF);
  });
});

describe("handsfree strings", () => {
  it("every line exists in ta, hi and en and is short", () => {
    for (const [key, s] of Object.entries(HANDSFREE_STRINGS)) {
      for (const lang of LANGS) {
        expect(s[lang], `${key}.${lang}`).toBeTruthy();
        expect(s[lang].length, `${key}.${lang}`).toBeLessThan(140);
      }
    }
  });
});
