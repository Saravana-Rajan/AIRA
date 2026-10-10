# AIRA: Camera Capture and Hand Guidance (Ishwarya · 02) Implementation Plan

> **Addendum (2026-10-10): Shop-floor bump guard (spec §2, "always on"). Add it as an extra task.** While Murugan walks inside the shop, the chest camera watches the path ahead ON THE PHONE (no network):
> - **Detector:** MediaPipe Object Detector (EfficientDet-Lite0 int8, ~5–10 Hz in a Web Worker). Add a simple "near-floor obstacle" check on the lower third of the frame, for crates on the floor.
> - **Trigger:** an object is in the walking path (centre 40% of the frame) and either its box grows fast (time-to-contact < 1.2 s) or it fills more than 35% of the frame height. Then:
>   1. vibrate pattern `warn` (`navigator.vibrate([80,40,80])`)
>   2. sharp warn earcon
>   3. short phrase `t("bump.stop")` ("Stop") or `t("bump.left_right")` ("Obstacle left / right")
> - **Rules:**
>   - Alert within **150 ms** of the frame.
>   - Cooldown 1.5 s per object.
>   - Muted while hand guidance is active and the hand is near the target.
>   - The server may only RAISE urgency, never silence it.
> - **Pure functions with vitest tests:** `timeToContact(boxes[], ts[])`, `inWalkingPath(box)`, `shouldWarn(state, detection)`.
> - **Bench:** log bump-warning latency (frame timestamp → vibrate call) for the benchmark slide.

> **Addendum (2026-10-10): open links sent by the server** (from `kanish/02`). When a `state` message's `summary` starts with `OPEN_URL ` (the click-to-chat WhatsApp link or a `tel:` call link for the supplier):
> 1. Speak "Opening WhatsApp" or "Calling the vendor".
> 2. Open that URL with `window.open(url, "_blank")`, or `location.href` for `tel:`.
>
> Only allow `https://wa.me/`, `https://api.whatsapp.com/` and `tel:` URLs. Add a vitest test for the allowlist.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The chest-mounted phone camera sends frames when the server asks for them. Server `target` messages are turned into hand guidance: live fingertip tracking, stereo guidance tones and short spoken cues ("left… down… stop"). Live delivery counts are spoken back.

**Architecture:**
- `camera/` owns the single rear-camera stream:
  - one-shot capture
  - bursts
  - continuous capture
  - quality and tilt hints
- `guidance/` owns:
  - MediaPipe fingertip tracking
  - pure geometry (vector, tone mapping, micro-cue, direction hold, dwell)
  - a Web Audio tone generator
  - a session that reacts to `target` messages
- `count/` speaks `count` messages.
- `camera/wire.ts` subscribes all of these to `app.socket` once. Everything testable is a pure function or uses injected dependencies.

**Tech Stack:**
- TypeScript 5, Preact app from the foundation
- Vitest + jsdom
- `@mediapipe/tasks-vision@1.1.0` (Hand Landmarker)
- Web Audio API (`OscillatorNode`, `StereoPannerNode`, `GainNode`)
- `getUserMedia`, `DeviceOrientationEvent`

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md`. Exact cross-team names: `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

**Prereq:** the foundation plan (`2026-10-10-00-foundation.md`) is merged. The `apps/app` workspace, `AiraSocket` and `@aira/contracts` must exist. `ishwarya/01-app-shell-voice.md` provides `apps/app/src/core/app.ts` (`app.speak`, `app.earcon`, `app.send`, `app.socket`, `app.settings.lang`). If `ishwarya/01` is not merged yet, create a temporary stub `apps/app/src/core/app.ts` so imports resolve. The tests here mock it with `vi.mock("../src/core/app")`, but Vitest still needs the file to exist. The stub:

```ts
// TEMP stub — replaced by ishwarya/01-app-shell-voice
import { AiraSocket } from "../net/socket";
export const app = {
  socket: new AiraSocket("ws://localhost:8080/ws"),
  settings: { lang: "en" as "en" | "ta" | "hi" },
  speak: (_s: string) => {}, earcon: (_n: string) => {}, haptic: (_n: string) => {},
  send: (m: any) => app.socket.send(m),
};
```

**Branch:** `ishwarya/camera-guidance`. Open a PR to `main` after each task group (Tasks 1–3, then 4–6).

## Global Constraints

- **Coordinates:**
  - Server `target.point` is `[y, x]`; `box` is `[ymin, xmin, ymax, xmax]`, normalised 0–1000.
  - The fingertip is `{ x1000, y1000 }`.
  - Never swap them.
- **Frames:**
  - JPEG, longest side ≤ **640 px**, quality 0.8.
  - Base64 length must be ≤ **1,500,000**; the foundation server rejects anything larger.
  - Frame messages are exactly `{t:"frame", id, jpeg, w, h, ts, purpose}`.
- **FramePurpose values:** only `"ask" | "count" | "point" | "identify" | "invoice" | "cash" | "guide"`.
- **Client events:** guidance sends only `{t:"event", name:"fingertip_in_target", data:{frameId, label}}`, at most once per target.
- **Timing:**
  - Direction hold ≥ **400 ms** before a spoken micro-cue changes; `"stop"` is immediate.
  - Dwell inside the target ≥ **500 ms**, with a 150 ms grace for fingertip flicker.
- **Spoken hints:** throttled to one every 4 s per kind. All user-facing strings exist in `en`, `ta` and `hi`. A native speaker on the team must proof-read the `ta` and `hi` strings before the demo.
- **Target device:** Android Chrome (PWA/TWA), rear camera, portrait chest mount. MediaPipe runs GPU (WebGL2) first, then falls back to CPU.
- **No other cross-team contract changes.** This plan only adds files under `apps/app/src/camera/`, `guidance/`, `count/`, `i18n/camera.ts` and `dev/`.

## Review Focus

1. **x/y swap.** A target `[200, 800]` (y=200, x=800) with the fingertip at `x1000=200, y1000=200` must produce cue `"right"` and a positive pan. Tested in Task 4.
2. **Camera unavailable.** Permission denied or the app hidden: the frame request handler speaks the `no_camera` hint once and sends nothing. It must not throw or loop. Tested in Task 3.
3. **Burst while continuous capture is running.** A burst still delivers exactly `count` frames, and `stop:true` stops only the continuous stream. Tested in Task 3.
4. **Fingertip flicker.** Losing the tip for ≤150 ms inside the box must not reset the 500 ms dwell, and the event fires exactly once per target. Tested in Task 4.
5. **Duplicate count messages.** The same `{sku, counted}` repeated must not be spoken twice; the final read-back is spoken once. Tested in Task 6.

---

## File Structure

```
apps/app/src/
  i18n/camera.ts              ta/hi/en strings for hints, micro-cues, count read-back (pure)
  camera/quality.ts           toGray, meanLuma, laplacianVariance, qualityHint (pure)
  camera/tilt.ts              tiltHint(beta) (pure) + startTiltHints(listener)
  camera/hints.ts             createHintSpeaker (throttle, pure with injected clock)
  camera/capture.ts           camera stream, captureFrame, startContinuous, fitWithin, CameraError
  camera/requests.ts          createFrameRequestHandler (request_frame: single / burst / continuous / stop)
  camera/wire.ts              wireCameraAndGuidance(): subscribes everything to app.socket
  guidance/geometry.ts        vectorTo, toneParams, microCue, insideBox, DirectionHold, DwellTimer (pure)
  guidance/tones.ts           guideTo(target, tip), stopTone() — Web Audio
  guidance/hands.ts           trackFingertip(onTip) — MediaPipe Hand Landmarker
  guidance/session.ts         createGuidanceSession (target msg → tracking → tones + cues + dwell event)
  count/countUx.ts            createCountUx (count msg → number + earcon; final read-back once)
  dev/fake.ts                 DEV-only window.__aira.dispatch(serverMsg) for manual phone tests
apps/app/test/
  camera.quality.test.ts  camera.capture.test.ts  camera.requests.test.ts
  guidance.geometry.test.ts  guidance.session.test.ts  count.ux.test.ts
```

---

### Task 1: Pure quality, tilt and hint text

**Files:**
- Create: `apps/app/src/i18n/camera.ts`, `apps/app/src/camera/quality.ts`, `apps/app/src/camera/tilt.ts`, `apps/app/src/camera/hints.ts`
- Test: `apps/app/test/camera.quality.test.ts`

**Interfaces:**
- Consumes: `Lang` from `@aira/contracts`.
- Produces:
  - `HintKey`, `hintText(key: HintKey, lang: Lang): string`
  - `CueWord`, `cueText(cue: CueWord, lang: Lang): string`
  - `finalReadback(lang: Lang, counted: number, expected: number | null): string`
  - `toGray(rgba, w, h): Float32Array`, `meanLuma(gray): number`, `laplacianVariance(gray, w, h): number`
  - `qualityHint(gray, w, h): "dark" | "blurry" | null`
  - `tiltHint(betaDeg: number | null): "tilt_down" | "tilt_up" | null`
  - `startTiltHints(onHint: (h: "tilt_down" | "tilt_up") => void): () => void`
  - `createHintSpeaker(speak: (s: string) => void, lang: () => Lang, now?: () => number, minGapMs?: number): (key: HintKey) => boolean`

- [ ] **Step 1: Write the failing test**

```ts
// apps/app/test/camera.quality.test.ts
import { describe, it, expect } from "vitest";
import { toGray, meanLuma, laplacianVariance, qualityHint, DARK_LUMA, BLUR_VAR } from "../src/camera/quality";
import { tiltHint } from "../src/camera/tilt";
import { createHintSpeaker } from "../src/camera/hints";
import { hintText, cueText, finalReadback } from "../src/i18n/camera";

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
  });
});

describe("tilt", () => {
  it("upright phone (beta≈90) must tilt down; pointing at floor must tilt up", () => {
    expect(tiltHint(90)).toBe("tilt_down");
    expect(tiltHint(70)).toBeNull();
    expect(tiltHint(40)).toBe("tilt_up");
    expect(tiltHint(null)).toBeNull();
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
});

describe("i18n", () => {
  it("has every language for hints, cues and read-back", () => {
    for (const lang of ["en", "ta", "hi"] as const) {
      expect(hintText("no_camera", lang).length).toBeGreaterThan(0);
      expect(cueText("stop", lang).length).toBeGreaterThan(0);
    }
    expect(finalReadback("en", 28, 30)).toBe("28 counted, expected 30.");
    expect(finalReadback("en", 30, null)).toBe("30 counted.");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- camera.quality`
Expected: FAIL with `Failed to resolve import "../src/camera/quality"`.

- [ ] **Step 3: Implement the four modules**

```ts
// apps/app/src/i18n/camera.ts
import type { Lang } from "@aira/contracts";

export type HintKey = "tilt_down" | "tilt_up" | "dark" | "blurry" | "no_camera" | "show_hand";
export type CueWord = "left" | "right" | "up" | "down" | "stop";

const HINTS: Record<HintKey, Record<Lang, string>> = {
  tilt_down: { en: "Tilt the phone down a little.", ta: "போனை கொஞ்சம் கீழே சாய்க்கவும்.", hi: "फ़ोन को थोड़ा नीचे झुकाइए।" },
  tilt_up: { en: "Tilt the phone up a little.", ta: "போனை கொஞ்சம் மேலே சாய்க்கவும்.", hi: "फ़ोन को थोड़ा ऊपर झुकाइए।" },
  dark: { en: "Too dark. Turn on a light.", ta: "இருட்டாக உள்ளது. விளக்கை போடுங்கள்.", hi: "बहुत अँधेरा है। लाइट जलाइए।" },
  blurry: { en: "Hold still.", ta: "அசையாமல் இருங்கள்.", hi: "स्थिर रहिए।" },
  no_camera: { en: "Camera is not available.", ta: "கேமரா கிடைக்கவில்லை.", hi: "कैमरा उपलब्ध नहीं है।" },
  show_hand: { en: "Show your hand to the camera.", ta: "கையை கேமராவுக்கு காட்டுங்கள்.", hi: "अपना हाथ कैमरे को दिखाइए।" },
};

const CUES: Record<CueWord, Record<Lang, string>> = {
  left: { en: "Left", ta: "இடது", hi: "बाएँ" },
  right: { en: "Right", ta: "வலது", hi: "दाएँ" },
  up: { en: "Up", ta: "மேலே", hi: "ऊपर" },
  down: { en: "Down", ta: "கீழே", hi: "नीचे" },
  stop: { en: "Stop", ta: "நிறுத்து", hi: "रुकिए" },
};

export const hintText = (k: HintKey, lang: Lang) => HINTS[k][lang] ?? HINTS[k].en;
export const cueText = (c: CueWord, lang: Lang) => CUES[c][lang] ?? CUES[c].en;

export function finalReadback(lang: Lang, counted: number, expected: number | null): string {
  if (expected === null) {
    return { en: `${counted} counted.`, ta: `${counted} எண்ணப்பட்டது.`, hi: `${counted} गिने गए।` }[lang];
  }
  return {
    en: `${counted} counted, expected ${expected}.`,
    ta: `${counted} எண்ணப்பட்டது, எதிர்பார்த்தது ${expected}.`,
    hi: `${counted} गिने गए, अपेक्षित ${expected}।`,
  }[lang];
}
```

```ts
// apps/app/src/camera/quality.ts
export const DARK_LUMA = 40;   // tune with bench/ clips on the team's test phone
export const BLUR_VAR = 60;

export function toGray(rgba: Uint8ClampedArray, w: number, h: number): Float32Array {
  const g = new Float32Array(w * h);
  for (let i = 0, p = 0; i < g.length; i++, p += 4) g[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  return g;
}

export function meanLuma(gray: Float32Array): number {
  if (gray.length === 0) return 0;
  let s = 0;
  for (let i = 0; i < gray.length; i++) s += gray[i];
  return s / gray.length;
}

/** Variance of the 4-neighbour Laplacian over interior pixels; low = blurry. */
export function laplacianVariance(gray: Float32Array, w: number, h: number): number {
  if (w < 3 || h < 3) return 0;
  let n = 0, sum = 0, sumSq = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const l = gray[i - w] + gray[i + w] + gray[i - 1] + gray[i + 1] - 4 * gray[i];
      n++; sum += l; sumSq += l * l;
    }
  }
  const mean = sum / n;
  return Math.max(0, sumSq / n - mean * mean);
}

export function qualityHint(gray: Float32Array, w: number, h: number): "dark" | "blurry" | null {
  if (meanLuma(gray) < DARK_LUMA) return "dark";
  if (laplacianVariance(gray, w, h) < BLUR_VAR) return "blurry";
  return null;
}
```

```ts
// apps/app/src/camera/tilt.ts
// DeviceOrientation beta: phone upright portrait ≈ 90° (rear camera looks straight ahead).
// A chest mount should look slightly down at the counter/fridge: 55°–85°.
export const TILT_MIN = 55;
export const TILT_MAX = 85;

export function tiltHint(betaDeg: number | null): "tilt_down" | "tilt_up" | null {
  if (betaDeg === null || Number.isNaN(betaDeg)) return null;
  if (betaDeg > TILT_MAX) return "tilt_down";
  if (betaDeg < TILT_MIN) return "tilt_up";
  return null;
}

/** Listens to deviceorientation; calls onHint at most once per 3 s. Returns an unsubscribe fn. */
export function startTiltHints(onHint: (h: "tilt_down" | "tilt_up") => void): () => void {
  let last = 0;
  const handler = (e: DeviceOrientationEvent) => {
    const h = tiltHint(e.beta);
    const now = Date.now();
    if (h && now - last > 3000) { last = now; onHint(h); }
  };
  window.addEventListener("deviceorientation", handler);
  return () => window.removeEventListener("deviceorientation", handler);
}
```

```ts
// apps/app/src/camera/hints.ts
import type { Lang } from "@aira/contracts";
import { hintText, type HintKey } from "../i18n/camera";

export function createHintSpeaker(
  speak: (s: string) => void,
  lang: () => Lang,
  now: () => number = () => Date.now(),
  minGapMs = 4000,
): (key: HintKey) => boolean {
  const lastAt = new Map<HintKey, number>();
  return (key) => {
    const t = now();
    const prev = lastAt.get(key);
    if (prev !== undefined && t - prev < minGapMs) return false;
    lastAt.set(key, t);
    speak(hintText(key, lang()));
    return true;
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -w apps/app -- camera.quality`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/i18n/camera.ts apps/app/src/camera/quality.ts apps/app/src/camera/tilt.ts apps/app/src/camera/hints.ts apps/app/test/camera.quality.test.ts
git commit -m "feat(app): camera quality, tilt hints and ta/hi/en guidance strings"
```

---

### Task 2: Camera capture (`captureFrame`, `startContinuous`)

**Files:**
- Create: `apps/app/src/camera/capture.ts`
- Test: `apps/app/test/camera.capture.test.ts`

**Interfaces:**
- Consumes: `ClientMsg`, `FramePurpose` from `@aira/contracts`; `app.send` from `core/app.ts`; `qualityHint`, `toGray` (Task 1).
- Produces (exact interface names):
  - `captureFrame(purpose: FramePurpose): Promise<Extract<ClientMsg,{t:"frame"}>>`
  - `startContinuous(purpose: FramePurpose, fps: number): () => void`
- Also:
  - `startCamera(): Promise<HTMLVideoElement>`, `stopCamera(): void`
  - `getVideo(): HTMLVideoElement | null`, used by `guidance/hands.ts` so the camera stream is shared
  - `fitWithin(w, h, max?)`
  - `class CameraError extends Error { code: "permission" | "no_frame" | "no_canvas" | "too_large" }`
  - `setQualityListener(fn: ((h: "dark" | "blurry") => void) | null)`
  - `__setCaptureDeps(partial)`, for tests only

- [ ] **Step 1: Write the failing test**

```ts
// apps/app/test/camera.capture.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
vi.mock("../src/core/app", () => ({ app: { send: vi.fn(() => true), speak: vi.fn(), earcon: vi.fn(), settings: { lang: "en" } } }));
import { app } from "../src/core/app";
import { captureFrame, startContinuous, fitWithin, CameraError, __setCaptureDeps, MAX_B64 } from "../src/camera/capture";

const fakeCanvas = () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(4 * 4 * 4).fill(200) }) } as unknown as CanvasRenderingContext2D });

beforeEach(() => {
  vi.useRealTimers();
  (app.send as any).mockClear();
  __setCaptureDeps({
    source: { width: () => 1280, height: () => 720, draw: () => {} },
    encoder: async () => "AAAA",
    makeCanvas: fakeCanvas,
  });
});

describe("fitWithin", () => {
  it("scales the longest side to 640 and never upscales", () => {
    expect(fitWithin(1280, 720)).toEqual({ w: 640, h: 360 });
    expect(fitWithin(720, 1280)).toEqual({ w: 360, h: 640 });
    expect(fitWithin(320, 240)).toEqual({ w: 320, h: 240 });
  });
});

describe("captureFrame", () => {
  it("builds an exact frame message", async () => {
    const f = await captureFrame("count");
    expect(f).toMatchObject({ t: "frame", jpeg: "AAAA", w: 640, h: 360, purpose: "count" });
    expect(typeof f.id).toBe("string");
    expect(typeof f.ts).toBe("number");
  });
  it("retries at lower quality then rejects oversize frames", async () => {
    const big = "A".repeat(MAX_B64 + 1);
    const enc = vi.fn(async () => big);
    __setCaptureDeps({ encoder: enc });
    await expect(captureFrame("invoice")).rejects.toMatchObject({ code: "too_large" });
    expect(enc).toHaveBeenCalledTimes(2);
  });
  it("rejects with no_frame when the video has no size yet", async () => {
    __setCaptureDeps({ source: { width: () => 0, height: () => 0, draw: () => {} } });
    await expect(captureFrame("ask")).rejects.toBeInstanceOf(CameraError);
  });
});

describe("startContinuous", () => {
  it("sends frames at the requested fps until stopped", async () => {
    vi.useFakeTimers();
    const stop = startContinuous("guide", 2);
    await vi.advanceTimersByTimeAsync(1100);
    stop();
    const n = (app.send as any).mock.calls.length;
    expect(n).toBeGreaterThanOrEqual(2);
    await vi.advanceTimersByTimeAsync(2000);
    expect((app.send as any).mock.calls.length).toBe(n);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- camera.capture`
Expected: FAIL with `Failed to resolve import "../src/camera/capture"`.

- [ ] **Step 3: Implement `camera/capture.ts`**

```ts
// apps/app/src/camera/capture.ts
import type { ClientMsg, FramePurpose } from "@aira/contracts";
import { app } from "../core/app";
import { toGray, qualityHint } from "./quality";

export type FrameMsg = Extract<ClientMsg, { t: "frame" }>;
export const MAX_SIDE = 640;
export const JPEG_QUALITY = 0.8;
export const MAX_B64 = 1_500_000;

export class CameraError extends Error {
  constructor(public code: "permission" | "no_frame" | "no_canvas" | "too_large") { super(`camera:${code}`); }
}

export interface FrameSource { width(): number; height(): number; draw(ctx: CanvasRenderingContext2D, w: number, h: number): void }
export type Encoder = (canvas: HTMLCanvasElement, quality: number) => Promise<string>; // base64, no data: prefix

const defaultEncoder: Encoder = (canvas, q) =>
  new Promise((resolve, reject) => {
    canvas.toBlob((b) => {
      if (!b) return reject(new CameraError("no_canvas"));
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
      r.onerror = () => reject(r.error);
      r.readAsDataURL(b);
    }, "image/jpeg", q);
  });

const defaultMakeCanvas = (w: number, h: number) => {
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  return { canvas, ctx: canvas.getContext("2d", { willReadFrequently: true }) };
};

interface CaptureDeps {
  source: FrameSource | null;
  encoder: Encoder;
  makeCanvas: (w: number, h: number) => { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null };
}
const deps: CaptureDeps = { source: null, encoder: defaultEncoder, makeCanvas: defaultMakeCanvas };
export function __setCaptureDeps(p: Partial<CaptureDeps>) { Object.assign(deps, p); }

let stream: MediaStream | null = null;
let video: HTMLVideoElement | null = null;
let qualityListener: ((h: "dark" | "blurry") => void) | null = null;
let qualityCounter = 0;
let seq = 0;

export const setQualityListener = (fn: ((h: "dark" | "blurry") => void) | null) => { qualityListener = fn; };
export const getVideo = () => video;

export function fitWithin(w: number, h: number, max = MAX_SIDE) {
  const s = Math.min(1, max / Math.max(w, h));
  return { w: Math.round(w * s), h: Math.round(h * s) };
}

const nextId = () => `f${Date.now().toString(36)}${(++seq).toString(36)}`;

export async function startCamera(): Promise<HTMLVideoElement> {
  if (video && stream?.active) return video;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
  } catch {
    throw new CameraError("permission");
  }
  video = document.createElement("video");
  video.setAttribute("playsinline", "true");
  video.muted = true;
  video.srcObject = stream;
  await video.play();
  const v = video;
  deps.source = { width: () => v.videoWidth, height: () => v.videoHeight, draw: (ctx, w, h) => ctx.drawImage(v, 0, 0, w, h) };
  return video;
}

export function stopCamera() {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null; video = null; deps.source = null;
}

export async function captureFrame(purpose: FramePurpose): Promise<FrameMsg> {
  if (!deps.source) await startCamera();
  const src = deps.source!;
  const sw = src.width(), sh = src.height();
  if (!sw || !sh) throw new CameraError("no_frame");
  const { w, h } = fitWithin(sw, sh);
  const { canvas, ctx } = deps.makeCanvas(w, h);
  if (!ctx) throw new CameraError("no_canvas");
  src.draw(ctx, w, h);
  // quality check every 3rd frame (cheap enough at 640 px)
  if (qualityListener && ++qualityCounter % 3 === 0) {
    const img = ctx.getImageData(0, 0, w, h);
    const hint = qualityHint(toGray(img.data, w, h), w, h);
    if (hint) qualityListener(hint);
  }
  let jpeg = await deps.encoder(canvas, JPEG_QUALITY);
  if (jpeg.length > MAX_B64) jpeg = await deps.encoder(canvas, 0.6);
  if (jpeg.length > MAX_B64) throw new CameraError("too_large");
  return { t: "frame", id: nextId(), jpeg, w, h, ts: Date.now(), purpose };
}

/** Sends frames via app.send at `fps` until the returned stop fn is called. Never overlaps captures. */
export function startContinuous(purpose: FramePurpose, fps: number): () => void {
  const period = Math.max(100, Math.round(1000 / Math.max(0.1, fps)));
  let stopped = false, busy = false;
  const tick = async () => {
    if (stopped || busy) return;
    busy = true;
    try {
      const f = await captureFrame(purpose);
      if (!stopped) app.send(f);
    } catch {
      /* camera errors are surfaced by the request handler / hint speaker */
    } finally {
      busy = false;
    }
  };
  const id = setInterval(tick, period);
  void tick();
  return () => { stopped = true; clearInterval(id); };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -w apps/app -- camera.capture`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/camera/capture.ts apps/app/test/camera.capture.test.ts
git commit -m "feat(app): rear-camera captureFrame/startContinuous (≤640px JPEG, size guard)"
```

---

### Task 3: `request_frame` handler (single, burst, continuous, stop) and spoken hints

**Files:**
- Create: `apps/app/src/camera/requests.ts`
- Test: `apps/app/test/camera.requests.test.ts`

**Interfaces:**
- Consumes: `captureFrame`, `startContinuous`, `CameraError` (Task 2); `createHintSpeaker`'s `(key: HintKey) => boolean` (Task 1).
- Produces:
  - `CONTINUOUS_FPS = 2`
  - `createFrameRequestHandler(deps: RequestDeps): (m: Extract<ServerMsg,{t:"request_frame"}>) => Promise<void>`
  - `RequestDeps = { capture, startContinuous, send: (m: ClientMsg) => boolean, hint: (k: HintKey) => boolean, sleep: (ms: number) => Promise<void> }`

- [ ] **Step 1: Write the failing test**

```ts
// apps/app/test/camera.requests.test.ts
import { describe, it, expect, vi } from "vitest";
import { createFrameRequestHandler, CONTINUOUS_FPS } from "../src/camera/requests";
import { CameraError } from "../src/camera/capture";

vi.mock("../src/core/app", () => ({ app: { send: vi.fn(), speak: vi.fn(), settings: { lang: "en" } } }));

function setup(over: Partial<Parameters<typeof createFrameRequestHandler>[0]> = {}) {
  const sent: any[] = []; const hints: string[] = []; const stops: string[] = [];
  let n = 0;
  const deps = {
    capture: vi.fn(async (purpose: any) => ({ t: "frame", id: `f${++n}`, jpeg: "A", w: 1, h: 1, ts: n, purpose }) as any),
    startContinuous: vi.fn((purpose: any) => () => { stops.push(purpose); }),
    send: vi.fn((m: any) => { sent.push(m); return true; }),
    hint: vi.fn((k: any) => { hints.push(k); return true; }),
    sleep: vi.fn(async () => {}),
    ...over,
  };
  return { handle: createFrameRequestHandler(deps), deps, sent, hints, stops };
}

describe("request_frame handler", () => {
  it("single request sends one frame with the requested purpose", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "invoice" });
    expect(s.sent.map((f) => f.purpose)).toEqual(["invoice"]);
  });

  it("burst sends exactly count frames, sleeping between them", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "count", burst: { count: 3, intervalMs: 400 } });
    expect(s.sent).toHaveLength(3);
    expect(s.deps.sleep).toHaveBeenCalledTimes(2);
    expect(s.deps.sleep).toHaveBeenCalledWith(400);
  });

  it("continuous starts at CONTINUOUS_FPS; burst during continuous still works; stop stops continuous only", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    expect(s.deps.startContinuous).toHaveBeenCalledWith("guide", CONTINUOUS_FPS);
    await s.handle({ t: "request_frame", purpose: "identify", burst: { count: 2, intervalMs: 100 } });
    expect(s.sent).toHaveLength(2);
    await s.handle({ t: "request_frame", purpose: "guide", stop: true });
    expect(s.stops).toEqual(["guide"]);
  });

  it("a second continuous request replaces the first", async () => {
    const s = setup();
    await s.handle({ t: "request_frame", purpose: "guide", continuous: true });
    await s.handle({ t: "request_frame", purpose: "count", continuous: true });
    expect(s.stops).toEqual(["guide"]);
  });

  it("camera failure speaks no_camera once and sends nothing", async () => {
    const s = setup({ capture: vi.fn(async () => { throw new CameraError("permission"); }) });
    await s.handle({ t: "request_frame", purpose: "cash", burst: { count: 3, intervalMs: 10 } });
    expect(s.sent).toHaveLength(0);
    expect(s.hints).toEqual(["no_camera"]);
  });

  it("oversize frame speaks the blurry/hold-still hint", async () => {
    const s = setup({ capture: vi.fn(async () => { throw new CameraError("too_large"); }) });
    await s.handle({ t: "request_frame", purpose: "invoice" });
    expect(s.hints).toEqual(["blurry"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- camera.requests`
Expected: FAIL with `Failed to resolve import "../src/camera/requests"`.

- [ ] **Step 3: Implement `camera/requests.ts`**

```ts
// apps/app/src/camera/requests.ts
import type { ClientMsg, FramePurpose, ServerMsg } from "@aira/contracts";
import { CameraError, type FrameMsg } from "./capture";
import type { HintKey } from "../i18n/camera";

export const CONTINUOUS_FPS = 2;
type FrameRequest = Extract<ServerMsg, { t: "request_frame" }>;

export interface RequestDeps {
  capture: (purpose: FramePurpose) => Promise<FrameMsg>;
  startContinuous: (purpose: FramePurpose, fps: number) => () => void;
  send: (m: ClientMsg) => boolean;
  hint: (k: HintKey) => boolean;
  sleep: (ms: number) => Promise<void>;
}

export function createFrameRequestHandler(deps: RequestDeps) {
  let stopContinuous: (() => void) | null = null;

  return async function handle(m: FrameRequest): Promise<void> {
    if (m.stop) {
      stopContinuous?.();
      stopContinuous = null;
      return;
    }
    if (m.continuous) {
      stopContinuous?.();
      stopContinuous = deps.startContinuous(m.purpose, CONTINUOUS_FPS);
      return;
    }
    const count = Math.max(1, m.burst?.count ?? 1);
    const interval = m.burst?.intervalMs ?? 0;
    for (let i = 0; i < count; i++) {
      try {
        deps.send(await deps.capture(m.purpose));
      } catch (e) {
        deps.hint(e instanceof CameraError && e.code === "too_large" ? "blurry" : "no_camera");
        return; // one hint, no retry loop
      }
      if (i < count - 1 && interval > 0) await deps.sleep(interval);
    }
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -w apps/app -- camera.requests`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/camera/requests.ts apps/app/test/camera.requests.test.ts
git commit -m "feat(app): request_frame handler — single, burst, continuous, stop"
```

---

### Task 4: Guidance geometry (pure)

**Files:**
- Create: `apps/app/src/guidance/geometry.ts`
- Test: `apps/app/test/guidance.geometry.test.ts`

**Interfaces:**
- Produces:
  - `Tip = { x1000: number; y1000: number }`
  - `Point = [number, number]` as `[y, x]`
  - `Box = [number, number, number, number]`
  - `vectorTo(target: Point, tip: Tip): { dx: number; dy: number; dist: number }`
  - `toneParams(v): { pan: number; freqHz: number; beepHz: number }`
  - `microCue(v, inBox: boolean): CueWord`
  - `insideBox(tip: Tip, box: Box | undefined, point: Point, radius?: number): boolean`
  - `class DirectionHold { update(cue: CueWord, now: number): CueWord | null; reset(): void }`
  - `class DwellTimer { update(inside: boolean, now: number): boolean; reset(): void }`
  - `STOP_RADIUS = 40`

- [ ] **Step 1: Write the failing test**

```ts
// apps/app/test/guidance.geometry.test.ts
import { describe, it, expect } from "vitest";
import { vectorTo, toneParams, microCue, insideBox, DirectionHold, DwellTimer } from "../src/guidance/geometry";

describe("vector + tones (target is [y, x])", () => {
  it("target to the right of the tip ⇒ cue right, positive pan", () => {
    const v = vectorTo([200, 800], { x1000: 200, y1000: 200 });
    expect(v.dx).toBe(600); expect(v.dy).toBe(0);
    expect(microCue(v, false)).toBe("right");
    expect(toneParams(v).pan).toBeGreaterThan(0);
  });
  it("target above the tip ⇒ cue up, higher pitch than level", () => {
    const up = toneParams(vectorTo([100, 500], { x1000: 500, y1000: 500 }));
    const level = toneParams(vectorTo([500, 700], { x1000: 500, y1000: 500 }));
    expect(microCue(vectorTo([100, 500], { x1000: 500, y1000: 500 }), false)).toBe("up");
    expect(up.freqHz).toBeGreaterThan(level.freqHz);
  });
  it("beeps faster when closer, clamped 1..8 Hz", () => {
    const far = toneParams({ dx: 0, dy: 900, dist: 900 });
    const near = toneParams({ dx: 0, dy: 10, dist: 10 });
    expect(far.beepHz).toBe(1);
    expect(near.beepHz).toBeGreaterThan(7);
  });
  it("in box ⇒ stop", () => {
    expect(microCue({ dx: 300, dy: 0, dist: 300 }, true)).toBe("stop");
  });
});

describe("insideBox", () => {
  it("uses [ymin, xmin, ymax, xmax]", () => {
    expect(insideBox({ x1000: 520, y1000: 410 }, [390, 505, 435, 555], [412, 530])).toBe(true);
    expect(insideBox({ x1000: 410, y1000: 520 }, [390, 505, 435, 555], [412, 530])).toBe(false);
  });
  it("falls back to a radius around the point when no box", () => {
    expect(insideBox({ x1000: 530, y1000: 420 }, undefined, [412, 530])).toBe(true);
  });
});

describe("DirectionHold", () => {
  it("speaks the first cue immediately, then needs 400 ms before switching", () => {
    const h = new DirectionHold();
    expect(h.update("left", 0)).toBe("left");
    expect(h.update("down", 100)).toBeNull();
    expect(h.update("down", 450)).toBeNull();   // held only 350 ms
    expect(h.update("down", 501)).toBe("down");
    expect(h.update("down", 900)).toBeNull();   // unchanged ⇒ silent
  });
  it("stop is immediate", () => {
    const h = new DirectionHold();
    h.update("left", 0);
    expect(h.update("stop", 50)).toBe("stop");
  });
});

describe("DwellTimer", () => {
  it("fires once after 500 ms inside, tolerating ≤150 ms flicker", () => {
    const d = new DwellTimer();
    expect(d.update(true, 0)).toBe(false);
    expect(d.update(false, 100)).toBe(false);   // 100 ms flicker (≤150 ms grace)
    expect(d.update(true, 150)).toBe(false);
    expect(d.update(true, 520)).toBe(true);
    expect(d.update(true, 900)).toBe(false);    // only once
  });
  it("resets after leaving for more than 150 ms", () => {
    const d = new DwellTimer();
    d.update(true, 0);
    d.update(false, 100);
    d.update(false, 400);                        // gone 300 ms
    expect(d.update(true, 450)).toBe(false);
    expect(d.update(true, 900)).toBe(false);    // only 450 ms since re-entry
    expect(d.update(true, 951)).toBe(true);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- guidance.geometry`
Expected: FAIL with `Failed to resolve import "../src/guidance/geometry"`.

- [ ] **Step 3: Implement `guidance/geometry.ts`**

```ts
// apps/app/src/guidance/geometry.ts
import type { CueWord } from "../i18n/camera";

export type Tip = { x1000: number; y1000: number };
export type Point = [number, number];                    // [y, x] 0–1000 (Gemini convention)
export type Box = [number, number, number, number];      // [ymin, xmin, ymax, xmax]
export const STOP_RADIUS = 40;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function vectorTo(target: Point, tip: Tip) {
  const dx = target[1] - tip.x1000;      // + ⇒ move right
  const dy = target[0] - tip.y1000;      // + ⇒ move down (image y grows downward)
  return { dx, dy, dist: Math.hypot(dx, dy) };
}

/** pan: left/right; freqHz: higher when target is above; beepHz: faster when closer. */
export function toneParams(v: { dx: number; dy: number; dist: number }) {
  return {
    pan: clamp(v.dx / 300, -1, 1),
    freqHz: clamp(660 - v.dy * 0.8, 330, 1100),
    beepHz: clamp(8 - v.dist / 100, 1, 8),
  };
}

export function microCue(v: { dx: number; dy: number; dist: number }, inBox: boolean): CueWord {
  if (inBox || v.dist <= STOP_RADIUS) return "stop";
  if (Math.abs(v.dx) >= Math.abs(v.dy)) return v.dx > 0 ? "right" : "left";
  return v.dy > 0 ? "down" : "up";
}

export function insideBox(tip: Tip, box: Box | undefined, point: Point, radius = STOP_RADIUS): boolean {
  if (box) {
    const [ymin, xmin, ymax, xmax] = box;
    return tip.y1000 >= ymin && tip.y1000 <= ymax && tip.x1000 >= xmin && tip.x1000 <= xmax;
  }
  return Math.hypot(point[1] - tip.x1000, point[0] - tip.y1000) <= radius;
}

/** Speak a micro-cue only when it changes and has been stable for minMs (stop is immediate). */
export class DirectionHold {
  private current: CueWord | null = null;
  private candidate: CueWord | null = null;
  private since = 0;
  constructor(private minMs = 400) {}

  update(cue: CueWord, now: number): CueWord | null {
    if (cue === this.current) { this.candidate = null; return null; }
    if (this.current === null || cue === "stop") { this.current = cue; this.candidate = null; return cue; }
    if (cue !== this.candidate) { this.candidate = cue; this.since = now; return null; }
    if (now - this.since >= this.minMs) { this.current = cue; this.candidate = null; return cue; }
    return null;
  }

  reset() { this.current = null; this.candidate = null; this.since = 0; }
}

/** True exactly once, after `dwellMs` continuously inside (gaps ≤ graceMs are tolerated). */
export class DwellTimer {
  private enteredAt: number | null = null;
  private lastInside = 0;
  private fired = false;
  constructor(private dwellMs = 500, private graceMs = 150) {}

  update(inside: boolean, now: number): boolean {
    if (this.fired) return false;
    if (inside) {
      // new window if we were never inside, or the last inside sample is older than the grace period
      if (this.enteredAt === null || now - this.lastInside > this.graceMs) this.enteredAt = now;
      this.lastInside = now;
      if (now - this.enteredAt >= this.dwellMs) { this.fired = true; return true; }
      return false;
    }
    if (this.enteredAt !== null && now - this.lastInside > this.graceMs) this.enteredAt = null;
    return false;
  }

  reset() { this.enteredAt = null; this.lastInside = 0; this.fired = false; }
}
```

> **Note on `DwellTimer`.** A new dwell window starts whenever the previous inside sample is older than `graceMs`. That covers both an explicit `update(false)` gap and frames that simply stopped arriving, and the second test checks it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -w apps/app -- guidance.geometry`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/guidance/geometry.ts apps/app/test/guidance.geometry.test.ts
git commit -m "feat(app): pure guidance geometry — vector, tones, micro-cues, hold, dwell"
```

---

### Task 5: Tones, MediaPipe fingertip tracking, guidance session

**Files:**
- Create: `apps/app/src/guidance/tones.ts`, `apps/app/src/guidance/hands.ts`, `apps/app/src/guidance/session.ts`
- Test: `apps/app/test/guidance.session.test.ts`

**Interfaces:**
- Consumes:
  - `getVideo`, `startCamera` (Task 2)
  - geometry (Task 4)
  - `cueText`, `hintText` (Task 1)
  - `app.speak`, `app.send`, `app.settings.lang`
- Produces (exact interface names):
  - `guideTo(target: [number, number], tip: { x1000: number; y1000: number } | null): void`
  - `trackFingertip(onTip: (tip: { x1000: number; y1000: number } | null) => void): Promise<() => void>`
- Also:
  - `stopTone(): void`
  - `createGuidanceSession(deps: GuidanceDeps): { onServerMsg(m: ServerMsg): void; end(): void; _onTip(tip): void }`
  - `GuidanceDeps = { track, guide, stopTone, speak, send, lang: () => Lang, now: () => number }`

- [ ] **Step 1: Write the failing test (session logic with fakes)**

```ts
// apps/app/test/guidance.session.test.ts
import { describe, it, expect, vi } from "vitest";
vi.mock("../src/core/app", () => ({ app: { send: vi.fn(), speak: vi.fn(), settings: { lang: "en" } } }));
import { createGuidanceSession } from "../src/guidance/session";

function setup() {
  let t = 0; let tipCb: ((tip: any) => void) | null = null;
  const sent: any[] = []; const said: string[] = [];
  const stopTrack = vi.fn();
  const deps = {
    track: vi.fn(async (cb: any) => { tipCb = cb; return stopTrack; }),
    guide: vi.fn(), stopTone: vi.fn(),
    speak: (s: string) => said.push(s),
    send: (m: any) => { sent.push(m); return true; },
    lang: () => "en" as const,
    now: () => t,
  };
  const s = createGuidanceSession(deps);
  return { s, deps, sent, said, stopTrack, tip: (x: number, y: number | null, at: number) => { t = at; tipCb!(y === null ? null : { x1000: x, y1000: y }); } };
}

const target = { t: "target" as const, frameId: "f1", label: "Sakthi curd 1 L", point: [412, 530] as [number, number], box: [390, 505, 435, 555] as [number, number, number, number], confidence: 0.9 };

describe("guidance session", () => {
  it("a target msg starts tracking once and drives guideTo with the [y,x] point", async () => {
    const g = setup();
    g.s.onServerMsg(target);
    await Promise.resolve();
    expect(g.deps.track).toHaveBeenCalledTimes(1);
    g.tip(200, 412, 0);
    expect(g.deps.guide).toHaveBeenCalledWith([412, 530], { x1000: 200, y1000: 412 });
    expect(g.said).toEqual(["Right"]);
  });

  it("sends fingertip_in_target once after 500 ms in the box, despite a short flicker", async () => {
    const g = setup();
    g.s.onServerMsg(target); await Promise.resolve();
    g.tip(530, 410, 0);
    g.tip(0, null, 80);           // flicker: tip lost for one frame
    g.tip(530, 410, 140);         // back within the 150 ms grace
    g.tip(530, 410, 520);         // ≥ 500 ms since entering ⇒ event
    g.tip(530, 410, 900);         // no second event
    const events = g.sent.filter((m) => m.t === "event");
    expect(events).toEqual([{ t: "event", name: "fingertip_in_target", data: { frameId: "f1", label: "Sakthi curd 1 L" } }]);
    expect(g.said).toContain("Stop");
  });

  it("a new target resets dwell so the event can fire again for it", async () => {
    const g = setup();
    g.s.onServerMsg(target); await Promise.resolve();
    for (const t of [0, 120, 240, 360, 520]) g.tip(530, 410, t);          // continuous samples ⇒ event f1
    g.s.onServerMsg({ ...target, frameId: "f2" }); await Promise.resolve();
    for (const t of [700, 820, 940, 1060, 1220]) g.tip(530, 410, t);      // ⇒ event f2
    expect(g.sent.filter((m) => m.t === "event").map((m) => m.data.frameId)).toEqual(["f1", "f2"]);
    expect(g.deps.track).toHaveBeenCalledTimes(1);   // tracking reused
  });

  it("done / match / interrupted cue ends guidance (stops tracking and tone)", async () => {
    const g = setup();
    g.s.onServerMsg(target); await Promise.resolve();
    g.s.onServerMsg({ t: "cue", cue: "match" });
    expect(g.stopTrack).toHaveBeenCalledTimes(1);
    expect(g.deps.stopTone).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- guidance.session`
Expected: FAIL with `Failed to resolve import "../src/guidance/session"`.

- [ ] **Step 3: Install MediaPipe and implement `hands.ts` (verify the options against https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/web_js)**

Run: `npm install -w apps/app @mediapipe/tasks-vision@1.1.0`
Expected: `added 1 package` (the version may print as `1.1.0`).

```ts
// apps/app/src/guidance/hands.ts
import { FilesetResolver, HandLandmarker } from "@mediapipe/tasks-vision";
import { getVideo, startCamera } from "../camera/capture";

const WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/wasm";
// verify the current model URL on the Hand Landmarker web guide; "latest" tracks the newest float16 model
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task";

let landmarker: HandLandmarker | null = null;

async function load(): Promise<HandLandmarker> {
  if (landmarker) return landmarker;
  const files = await FilesetResolver.forVisionTasks(WASM);
  const preferred = (localStorage.getItem("aira.handsDelegate") as "GPU" | "CPU" | null) ?? "GPU";
  const make = (delegate: "GPU" | "CPU") =>
    HandLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: MODEL, delegate },
      runningMode: "VIDEO",
      numHands: 1,
    });
  try {
    landmarker = await make(preferred);
  } catch {
    landmarker = await make("CPU"); // WebGL2 unavailable ⇒ CPU fallback (A/B: set localStorage aira.handsDelegate)
  }
  return landmarker;
}

/** Streams the index fingertip (landmark 8) in 0–1000 image coords; null when no hand. */
export async function trackFingertip(onTip: (tip: { x1000: number; y1000: number } | null) => void): Promise<() => void> {
  const video = getVideo() ?? (await startCamera());
  const hl = await load();
  let running = true;
  let lastTs = -1;
  const loop = () => {
    if (!running) return;
    const ts = performance.now();
    if (video.readyState >= 2 && ts !== lastTs) {
      lastTs = ts;
      const res = hl.detectForVideo(video, ts);
      const lm = res.landmarks?.[0]?.[8];
      onTip(lm ? { x1000: Math.round(lm.x * 1000), y1000: Math.round(lm.y * 1000) } : null);
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  return () => { running = false; };
}
```

- [ ] **Step 4: Implement `tones.ts` and `session.ts`**

```ts
// apps/app/src/guidance/tones.ts
import { app } from "../core/app";
import { hintText } from "../i18n/camera";
import { toneParams, vectorTo } from "./geometry";

let ctx: AudioContext | null = null;
let osc: OscillatorNode | null = null;
let pan: StereoPannerNode | null = null;
let gain: GainNode | null = null;
let beepTimer: ReturnType<typeof setInterval> | null = null;
let beepPeriodMs = 0;
let lastShowHand = 0;

export function __setAudioContext(c: AudioContext | null) { ctx = c; osc = pan = gain = null; }

function ensure() {
  ctx ??= new AudioContext();
  if (!osc) {
    osc = ctx.createOscillator();
    pan = ctx.createStereoPanner();
    gain = ctx.createGain();
    gain.gain.value = 0;
    osc.type = "sine";
    osc.connect(pan).connect(gain).connect(ctx.destination);
    osc.start();
  }
}

function setBeepRate(hz: number) {
  const period = Math.round(1000 / hz);
  if (beepTimer && Math.abs(period - beepPeriodMs) < 20) return;
  if (beepTimer) clearInterval(beepTimer);
  beepPeriodMs = period;
  beepTimer = setInterval(() => {
    if (!ctx || !gain) return;
    const t = ctx.currentTime;
    gain.gain.cancelScheduledValues(t);
    gain.gain.setValueAtTime(0.25, t);
    gain.gain.setTargetAtTime(0, t + 0.06, 0.02);
  }, period);
}

/** Stereo pan = left/right, pitch = up/down, beep rate = distance. null tip ⇒ silence + "show your hand" (≤ 1 / 3 s). */
export function guideTo(target: [number, number], tip: { x1000: number; y1000: number } | null): void {
  if (!tip) {
    stopTone();
    const now = Date.now();
    if (now - lastShowHand > 3000) { lastShowHand = now; app.speak(hintText("show_hand", app.settings.lang)); }
    return;
  }
  ensure();
  const p = toneParams(vectorTo(target, tip));
  const t = ctx!.currentTime;
  osc!.frequency.setTargetAtTime(p.freqHz, t, 0.03);
  pan!.pan.setTargetAtTime(p.pan, t, 0.03);
  setBeepRate(p.beepHz);
}

export function stopTone(): void {
  if (beepTimer) { clearInterval(beepTimer); beepTimer = null; beepPeriodMs = 0; }
  if (ctx && gain) gain.gain.setTargetAtTime(0, ctx.currentTime, 0.02);
}
```

```ts
// apps/app/src/guidance/session.ts
import type { ClientMsg, Lang, ServerMsg } from "@aira/contracts";
import { cueText } from "../i18n/camera";
import { DirectionHold, DwellTimer, insideBox, microCue, vectorTo, type Box, type Point, type Tip } from "./geometry";

export interface GuidanceDeps {
  track: (onTip: (tip: Tip | null) => void) => Promise<() => void>;
  guide: (target: Point, tip: Tip | null) => void;
  stopTone: () => void;
  speak: (s: string) => void;
  send: (m: ClientMsg) => boolean;
  lang: () => Lang;
  now: () => number;
}

type TargetMsg = Extract<ServerMsg, { t: "target" }>;

export function createGuidanceSession(deps: GuidanceDeps) {
  let target: { frameId: string; label: string; point: Point; box?: Box } | null = null;
  let stopTrack: (() => void) | null = null;
  let starting = false;
  const hold = new DirectionHold();
  const dwell = new DwellTimer();

  function onTip(tip: Tip | null) {
    if (!target) return;
    deps.guide(target.point, tip);
    const now = deps.now();
    if (!tip) { dwell.update(false, now); return; }
    const inBox = insideBox(tip, target.box, target.point);
    const cue = hold.update(microCue(vectorTo(target.point, tip), inBox), now);
    if (cue) deps.speak(cueText(cue, deps.lang()));
    if (dwell.update(inBox, now)) {
      deps.send({ t: "event", name: "fingertip_in_target", data: { frameId: target.frameId, label: target.label } });
    }
  }

  async function onTarget(m: TargetMsg) {
    target = { frameId: m.frameId, label: m.label, point: m.point, box: m.box };
    hold.reset();
    dwell.reset();
    if (!stopTrack && !starting) {
      starting = true;
      try { stopTrack = await deps.track(onTip); } finally { starting = false; }
    }
  }

  function end() {
    target = null;
    stopTrack?.();
    stopTrack = null;
    deps.stopTone();
    hold.reset();
    dwell.reset();
  }

  return {
    onServerMsg(m: ServerMsg) {
      if (m.t === "target") void onTarget(m);
      else if (m.t === "cue" && (m.cue === "done" || m.cue === "match" || m.cue === "interrupted")) end();
    },
    end,
    _onTip: onTip,
  };
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w apps/app -- guidance`
Expected: PASS (`guidance.geometry` 10 tests + `guidance.session` 4 tests).

- [ ] **Step 6: Commit**

```bash
git add apps/app/src/guidance/tones.ts apps/app/src/guidance/hands.ts apps/app/src/guidance/session.ts apps/app/test/guidance.session.test.ts apps/app/package.json package-lock.json
git commit -m "feat(app): fingertip tracking (MediaPipe), guidance tones and target-driven session"
```

---

### Task 6: Live count UX, wiring and phone check

**Files:**
- Create: `apps/app/src/count/countUx.ts`, `apps/app/src/camera/wire.ts`, `apps/app/src/dev/fake.ts`
- Modify: `apps/app/src/main.tsx`. Call `wireCameraAndGuidance()` once after the onboarding "Start" tap, which `ishwarya/01` creates. Call `guidance.end()` from the existing long-press stop handler.
- Test: `apps/app/test/count.ux.test.ts`

**Interfaces:**
- Consumes: everything above; `app.socket.on(handler) => () => void` (foundation `AiraSocket`); `app.earcon`, `app.speak`.
- Produces:
  - `createCountUx(deps: { speak; earcon: (n: CueName) => void; lang: () => Lang }): { onServerMsg(m: ServerMsg): void }`
  - `wireCameraAndGuidance(): { guidance: ReturnType<typeof createGuidanceSession>; unsubscribe: () => void }`
  - DEV only: `window.__aira.dispatch(m: ServerMsg)`

- [ ] **Step 1: Write the failing test**

```ts
// apps/app/test/count.ux.test.ts
import { describe, it, expect } from "vitest";
import { createCountUx } from "../src/count/countUx";

function setup() {
  const said: string[] = []; const earcons: string[] = [];
  const ux = createCountUx({ speak: (s) => said.push(s), earcon: (n) => earcons.push(n), lang: () => "en" });
  return { ux, said, earcons };
}
const c = (counted: number, final = false, expected: number | null = 30) =>
  ({ t: "count" as const, sku: "sakthi-curd-1l", counted, expected, final, confidence: 0.9 });

describe("count UX", () => {
  it("speaks each new number with a tick (ack earcon)", () => {
    const g = setup();
    g.ux.onServerMsg(c(1)); g.ux.onServerMsg(c(2));
    expect(g.said).toEqual(["1", "2"]);
    expect(g.earcons).toEqual(["ack", "ack"]);
  });
  it("does not repeat a duplicate count", () => {
    const g = setup();
    g.ux.onServerMsg(c(5)); g.ux.onServerMsg(c(5));
    expect(g.said).toEqual(["5"]);
  });
  it("reads back the final count once, with expected", () => {
    const g = setup();
    g.ux.onServerMsg(c(28, true)); g.ux.onServerMsg(c(28, true));
    expect(g.said).toEqual(["28 counted, expected 30."]);
    expect(g.earcons).toEqual(["done"]);
  });
  it("ignores non-count messages", () => {
    const g = setup();
    g.ux.onServerMsg({ t: "cue", cue: "ack" });
    expect(g.said).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -w apps/app -- count.ux`
Expected: FAIL with `Failed to resolve import "../src/count/countUx"`.

- [ ] **Step 3: Implement `countUx.ts`, `wire.ts` and `dev/fake.ts`**

```ts
// apps/app/src/count/countUx.ts
import type { CueName, Lang, ServerMsg } from "@aira/contracts";
import { finalReadback } from "../i18n/camera";

export function createCountUx(deps: { speak: (s: string) => void; earcon: (n: CueName) => void; lang: () => Lang }) {
  let last: { sku: string; counted: number } | null = null;
  const finalsSpoken = new Set<string>();
  return {
    onServerMsg(m: ServerMsg) {
      if (m.t !== "count") return;
      if (m.final) {
        const key = `${m.sku}:${m.counted}:${m.expected ?? "-"}`;
        if (finalsSpoken.has(key)) return;
        finalsSpoken.add(key);
        last = null;
        deps.earcon("done");
        deps.speak(finalReadback(deps.lang(), m.counted, m.expected));
        return;
      }
      if (last && last.sku === m.sku && last.counted === m.counted) return;
      last = { sku: m.sku, counted: m.counted };
      deps.earcon("ack");
      deps.speak(String(m.counted));
    },
  };
}
```

```ts
// apps/app/src/camera/wire.ts
/// <reference types="vite/client" />
import type { ServerMsg } from "@aira/contracts";
import { app } from "../core/app";
import { captureFrame, startContinuous, setQualityListener } from "./capture";
import { createFrameRequestHandler } from "./requests";
import { createHintSpeaker } from "./hints";
import { startTiltHints } from "./tilt";
import { createGuidanceSession } from "../guidance/session";
import { trackFingertip } from "../guidance/hands";
import { guideTo, stopTone } from "../guidance/tones";
import { createCountUx } from "../count/countUx";

/** Call once after the onboarding "Start" tap (user gesture already unlocked audio + camera). */
export function wireCameraAndGuidance() {
  const lang = () => app.settings.lang;
  const hint = createHintSpeaker((s) => app.speak(s), lang);
  setQualityListener((h) => hint(h));
  const stopTilt = startTiltHints((h) => hint(h));

  const handleFrame = createFrameRequestHandler({
    capture: captureFrame,
    startContinuous,
    send: (m) => app.send(m),
    hint,
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  });
  const guidance = createGuidanceSession({
    track: trackFingertip, guide: guideTo, stopTone,
    speak: (s) => app.speak(s), send: (m) => app.send(m),
    lang, now: () => performance.now(),
  });
  const count = createCountUx({ speak: (s) => app.speak(s), earcon: (n) => app.earcon(n), lang });

  const dispatch = (m: ServerMsg) => {
    if (m.t === "request_frame") void handleFrame(m);
    guidance.onServerMsg(m);
    count.onServerMsg(m);
  };
  const off = app.socket.on(dispatch);
  // dev hook: local dev server, or any build opened with ?devtools (for phone tests on a preview channel)
  if (import.meta.env.DEV || new URLSearchParams(location.search).has("devtools")) {
    void import("../dev/fake").then((d) => d.installFake(dispatch));
  }

  return {
    guidance,
    unsubscribe: () => { off(); stopTilt(); setQualityListener(null); guidance.end(); },
  };
}
```

```ts
// apps/app/src/dev/fake.ts — DEV only; lets you drive the UI from chrome://inspect on the phone
import type { ServerMsg } from "@aira/contracts";
export function installFake(dispatch: (m: ServerMsg) => void) {
  (window as unknown as { __aira: { dispatch: (m: ServerMsg) => void } }).__aira = { dispatch };
}
```

In `apps/app/src/main.tsx`, add the following after the onboarding "Start" handler has run:

```tsx
import { wireCameraAndGuidance } from "./camera/wire";
// inside the Start tap handler, after permissions are granted:
const cam = wireCameraAndGuidance();
// inside the existing long-press stop handler:
cam.guidance.end();
```

- [ ] **Step 4: Run all app tests and typecheck**

Run: `npm test -w apps/app && npm run typecheck -w apps/app`
Expected: all test files PASS (`camera.quality`, `camera.capture`, `camera.requests`, `guidance.geometry`, `guidance.session`, `count.ux`, plus the foundation's socket test), and no type errors.

- [ ] **Step 5: Phone check on a real Android device (HTTPS is required for the camera)**

Run: `npm run build -w apps/app && npx firebase-tools hosting:channel:deploy cam-test --only app --expires 2d`
Expected: a preview URL `https://<project>--cam-test-<hash>.web.app`.

On the Android phone, mounted on the chest:
1. Open the preview URL and tap Start; allow the camera and motion permissions.
2. Hold the phone upright. Expected: "Tilt the phone down a little." Tilt it about 20° down and the hint stops.
3. Reopen the preview URL with `?devtools` appended (e.g. `https://<project>--cam-test-<hash>.web.app/?devtools`). On the laptop, open `chrome://inspect` → the phone tab → Console, then run:
   `__aira.dispatch({t:"target",frameId:"x",label:"curd",point:[500,500],box:[440,440,560,560],confidence:0.9})`
   Expected: the guidance tone starts. Moving the right index finger toward the centre of the view changes the pan and pitch and makes the beeps faster. You hear "Stop" in the box, and the Network tab's WebSocket frames show one `fingertip_in_target` event.
4. Run `__aira.dispatch({t:"count",sku:"s",counted:1,expected:3,final:false,confidence:0.9})`, then counted 2, then `final:true` with 3. Expected: "1", "2", then "3 counted, expected 3."
5. Record the hand-tracking FPS for GPU and CPU (toggle `localStorage.aira.handsDelegate = "CPU"` and reload) in `bench/notes/hands-fps.md`, with the phone model name.

- [ ] **Step 6: Commit**

```bash
git add apps/app/src/count/countUx.ts apps/app/src/camera/wire.ts apps/app/src/dev/fake.ts apps/app/src/main.tsx apps/app/test/count.ux.test.ts bench/notes/hands-fps.md
git commit -m "feat(app): live count read-back, camera+guidance wiring, dev dispatch for phone tests"
```
