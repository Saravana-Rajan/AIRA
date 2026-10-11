// frontend/app/test/camera.quality.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { toGray, meanLuma, laplacianVariance, qualityHint, noteFrameQuality, frameLooksGood, DARK_LUMA, BLUR_VAR, FRAME_GOOD_MS } from "../src/camera/quality";
import { tiltHint, createTiltJudge, startTiltHints, TILT_HOLD_MS, TILT_HINT_GAP_MS } from "../src/camera/tilt";
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
  // Chest mount, phone upright in portrait, back camera forward: beta ≈ 90, gamma ≈ 0.
  it("upright chest mount (beta 90, roll 5) is fine; only clearly out-of-band readings get a direction", () => {
    expect(tiltHint(90, 5)).toBeNull();
    expect(tiltHint(70, 0)).toBeNull();
    expect(tiltHint(110, -10)).toBeNull();
    expect(tiltHint(60, 25)).toBeNull();
    expect(tiltHint(120, -25)).toBeNull();
    expect(tiltHint(10, 0)).toBe("tilt_up");     // lying flat, camera at the ceiling/floor
    expect(tiltHint(140, 0)).toBe("tilt_down");  // leaning back over the shoulder
  });
  it("no sensor, no permission or a large roll means no tilt hint", () => {
    expect(tiltHint(null, null)).toBeNull();
    expect(tiltHint(Number.NaN, 0)).toBeNull();
    expect(tiltHint(10, null)).toBe("tilt_up"); // gamma unknown is treated as small roll
    expect(tiltHint(10, 40)).toBeNull();        // rolled sideways: reading unreliable, no key to say "straighten"
  });

  describe("createTiltJudge", () => {
    it("upright chest pitch 90 / roll 5 never hints", () => {
      let t = 0;
      const j = createTiltJudge({ now: () => t });
      const got: string[] = [];
      for (; t <= 10_000; t += 100) { const h = j.sample(90, 5); if (h) got.push(h); }
      expect(got).toEqual([]);
    });
    it("flat (pitch 10) for 2 s gives exactly one hint, repeated only after the gap", () => {
      let t = 0;
      const j = createTiltJudge({ now: () => t });
      const got: Array<[number, string]> = [];
      for (; t <= 2000; t += 100) { const h = j.sample(10, 0); if (h) got.push([t, h]); }
      expect(got.length).toBe(1);
      expect(got[0][1]).toBe("tilt_up");
      expect(got[0][0]).toBeGreaterThanOrEqual(TILT_HOLD_MS);
      for (; t <= TILT_HINT_GAP_MS - 1; t += 100) { expect(j.sample(10, 0)).toBeNull(); }
      let again: string | null = null;
      for (; t <= TILT_HINT_GAP_MS + TILT_HOLD_MS + 2000 && !again; t += 100) again = j.sample(10, 0);
      expect(again).toBe("tilt_up");
    });
    it("a brief 0.5 s spike out of band gives no hint", () => {
      let t = 0;
      const j = createTiltJudge({ now: () => t });
      const got: string[] = [];
      for (; t < 3000; t += 100) { const h = j.sample(90, 0); if (h) got.push(h); }
      for (; t < 3500; t += 100) { const h = j.sample(5, 0); if (h) got.push(h); }
      for (; t < 8000; t += 100) { const h = j.sample(90, 0); if (h) got.push(h); }
      expect(got).toEqual([]);
    });
    it("good frame quality suppresses a slight out-of-band tilt", () => {
      let t = 0;
      const j = createTiltJudge({ now: () => t, frameLooksGood: () => true });
      const got: string[] = [];
      for (; t <= 5000; t += 100) { const h = j.sample(50, 0); if (h) got.push(h); }
      expect(got).toEqual([]);
      // the same reading without frame evidence does hint
      t = 0;
      const k = createTiltJudge({ now: () => t, frameLooksGood: () => false });
      const got2: string[] = [];
      for (; t <= 5000; t += 100) { const h = k.sample(50, 0); if (h) got2.push(h); }
      expect(got2).toEqual(["tilt_up"]);
    });
    it("no sensor reading gives no hint, however long", () => {
      let t = 0;
      const j = createTiltJudge({ now: () => t });
      for (; t <= 5000; t += 100) expect(j.sample(null, null)).toBeNull();
    });
  });

  describe("startTiltHints", () => {
    afterEach(() => { vi.useRealTimers(); });
    const orient = (beta: number | null, gamma: number | null = 0) => {
      const e = new Event("deviceorientation") as Event & { beta: number | null; gamma: number | null };
      Object.defineProperty(e, "beta", { value: beta });
      Object.defineProperty(e, "gamma", { value: gamma });
      window.dispatchEvent(e);
    };

    it("hints only after a held bad tilt, at most once per gap, and stops after unsubscribe", () => {
      vi.useFakeTimers();
      vi.setSystemTime(0);
      const got: string[] = [];
      const stop = startTiltHints((h) => got.push(h));
      orient(90);
      orient(10);                                   // a single flat sample is not enough
      expect(got).toEqual([]);
      for (let t = 100; t <= 2000; t += 100) { vi.setSystemTime(t); orient(10); }
      expect(got).toEqual(["tilt_up"]);
      for (let t = 2100; t <= 4000; t += 100) { vi.setSystemTime(t); orient(10); }
      expect(got).toEqual(["tilt_up"]);             // still inside the gap
      stop();
      for (let t = 20_000; t <= 30_000; t += 100) { vi.setSystemTime(t); orient(10); }
      expect(got).toEqual(["tilt_up"]);
    });
    it("never hints without a sensor (no events) or with null readings", () => {
      vi.useFakeTimers();
      vi.setSystemTime(0);
      const got: string[] = [];
      const stop = startTiltHints((h) => got.push(h));
      for (let t = 0; t <= 5000; t += 100) { vi.setSystemTime(t); orient(null, null); }
      expect(got).toEqual([]);
      stop();
    });
  });
});

describe("frame evidence", () => {
  it("a recent sharp, bright frame counts as good; dark or blurry does not; evidence expires", () => {
    noteFrameQuality(null, 1000);
    expect(frameLooksGood(1000)).toBe(true);
    expect(frameLooksGood(1000 + FRAME_GOOD_MS)).toBe(true);
    expect(frameLooksGood(1001 + FRAME_GOOD_MS)).toBe(false);
    noteFrameQuality("dark", 5000);
    expect(frameLooksGood(5000)).toBe(false);
    noteFrameQuality(null, 6000);
    noteFrameQuality("blurry", 6500);
    expect(frameLooksGood(6500)).toBe(false);
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
