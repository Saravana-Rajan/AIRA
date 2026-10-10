// apps/app/test/camera.quality.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { toGray, meanLuma, laplacianVariance, qualityHint, DARK_LUMA, BLUR_VAR } from "../src/camera/quality";
import { tiltHint, startTiltHints } from "../src/camera/tilt";
import { createHintSpeaker } from "../src/camera/hints";
import { hintText, cueText, finalReadback, type HintKey, type CueWord } from "../src/i18n/camera";

function rgbaFill(w: number, h: number, v: number) {
  const a = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < a.length; i += 4) { a[i] = v; a[i + 1] = v; a[i + 2] = v; a[i + 3] = 255; }
  return a;
}
function rgbaChecker(w: number, h: number) {
  const a = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const v = (x + y) % 2 === 0 ? 0 : 255; const p = (y * w + x) * 4;
    a[p] = v; a[p + 1] = v; a[p + 2] = v; a[p + 3] = 255;
  }
  return a;
}

describe("quality", () => {
  it("flat mid-grey is blurry, not dark", () => {
    const g = toGray(rgbaFill(16, 16, 128), 16, 16);
    expect(meanLuma(g)).toBeCloseTo(128, 0);
    expect(laplacianVariance(g, 16, 16)).toBe(0);
    expect(qualityHint(g, 16, 16)).toBe("blurry");
  });
  it("very dark image is dark (dark wins over blurry)", () => {
    const g = toGray(rgbaFill(16, 16, 10), 16, 16);
    expect(meanLuma(g)).toBeLessThan(DARK_LUMA);
    expect(qualityHint(g, 16, 16)).toBe("dark");
  });
  it("sharp checkerboard is fine", () => {
    const g = toGray(rgbaChecker(16, 16), 16, 16);
    expect(laplacianVariance(g, 16, 16)).toBeGreaterThan(BLUR_VAR);
    expect(qualityHint(g, 16, 16)).toBeNull();
  });
  it("tiny images do not crash", () => {
    expect(laplacianVariance(new Float32Array(4), 2, 2)).toBe(0);
    expect(meanLuma(new Float32Array(0))).toBe(0);
  });
  it("uses Rec.601 luma weights per pixel", () => {
    const g = toGray(new Uint8ClampedArray([255, 0, 0, 255, 0, 255, 0, 255]), 2, 1);
    expect(g[0]).toBeCloseTo(0.299 * 255, 3);
    expect(g[1]).toBeCloseTo(0.587 * 255, 3);
  });
});

describe("tilt", () => {
  it("upright phone (beta≈90) must tilt down; pointing at floor must tilt up", () => {
    expect(tiltHint(90)).toBe("tilt_down");
    expect(tiltHint(70)).toBeNull();
    expect(tiltHint(40)).toBe("tilt_up");
    expect(tiltHint(null)).toBeNull();
    expect(tiltHint(Number.NaN)).toBeNull();
  });

  describe("startTiltHints", () => {
    afterEach(() => { vi.useRealTimers(); });
    const orient = (beta: number | null) => {
      const e = new Event("deviceorientation") as Event & { beta: number | null };
      Object.defineProperty(e, "beta", { value: beta });
      window.dispatchEvent(e);
    };

    it("reports a bad tilt at most once per 3 s and stops after unsubscribe", () => {
      vi.useFakeTimers();
      vi.setSystemTime(0);
      const got: string[] = [];
      const stop = startTiltHints((h) => got.push(h));
      orient(90);                      // first hint fires even at clock 0
      orient(40);                      // throttled
      orient(70);                      // fine, nothing to say
      vi.setSystemTime(3001);
      orient(40);
      expect(got).toEqual(["tilt_down", "tilt_up"]);
      stop();
      vi.setSystemTime(10_000);
      orient(90);
      expect(got).toEqual(["tilt_down", "tilt_up"]);
    });
  });
});

describe("hint speaker", () => {
  it("throttles each hint kind to once per 4 s", () => {
    let t = 0; const said: string[] = [];
    const speak = createHintSpeaker((s) => said.push(s), () => "en", () => t, 4000);
    expect(speak("dark")).toBe(true);
    t = 1000; expect(speak("dark")).toBe(false);
    expect(speak("blurry")).toBe(true);
    t = 4001; expect(speak("dark")).toBe(true);
    expect(said).toEqual(["Too dark. Turn on a light.", "Hold still.", "Too dark. Turn on a light."]);
  });
  it("speaks in the current language at the time of the hint", () => {
    let lang: "en" | "ta" | "hi" = "en"; const said: string[] = [];
    const speak = createHintSpeaker((s) => said.push(s), () => lang, () => 0);
    lang = "ta";
    speak("no_camera");
    expect(said).toEqual([hintText("no_camera", "ta")]);
  });
});

describe("i18n", () => {
  const HINT_KEYS: HintKey[] = ["tilt_down", "tilt_up", "dark", "blurry", "no_camera", "show_hand"];
  const CUE_WORDS: CueWord[] = ["left", "right", "up", "down", "stop"];

  it("has every language for hints, cues and read-back", () => {
    for (const lang of ["en", "ta", "hi"] as const) {
      for (const k of HINT_KEYS) expect(hintText(k, lang).length).toBeGreaterThan(0);
      for (const c of CUE_WORDS) expect(cueText(c, lang).length).toBeGreaterThan(0);
      expect(finalReadback(lang, 28, 30)).toContain("28");
      expect(finalReadback(lang, 28, 30)).toContain("30");
      expect(finalReadback(lang, 30, null)).toContain("30");
    }
    expect(finalReadback("en", 28, 30)).toBe("28 counted, expected 30.");
    expect(finalReadback("en", 30, null)).toBe("30 counted.");
  });
  it("ta and hi are real translations, not English copies", () => {
    for (const lang of ["ta", "hi"] as const) {
      for (const k of HINT_KEYS) expect(hintText(k, lang)).not.toBe(hintText(k, "en"));
      for (const c of CUE_WORDS) expect(cueText(c, lang)).not.toBe(cueText(c, "en"));
    }
  });
  it("falls back to English for an unknown language instead of returning undefined", () => {
    const bad = "fr" as unknown as "en";
    expect(hintText("dark", bad)).toBe("Too dark. Turn on a light.");
    expect(cueText("stop", bad)).toBe("Stop");
    expect(finalReadback(bad, 5, 6)).toBe("5 counted, expected 6.");
    expect(finalReadback(bad, 5, null)).toBe("5 counted.");
  });
});
