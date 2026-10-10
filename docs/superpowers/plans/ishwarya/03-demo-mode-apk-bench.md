# AIRA — Judge Demo Mode, Installable App, APK and Benchmark Runner (Ishwarya · 03)

> **Addendum (2026-10-10): guidance trial log.** The bench runner must also write hand-guidance trials to `bench/data/guidance/trials.json`, in exactly this format. It is consumed by Sarmitha's `bench/score_guidance.py`; see `2026-10-10-00-interfaces.md`, "Additions from the written plans".
> `{"trials":[{"trial":1,"product":"sakthi_curd_1l","startedAtMs":0,"touchedAtMs":4200,"correctItem":true}]}`
> - `startedAtMs`: the moment `product_find` begins. Use the time the first `target` message is received.
> - `touchedAtMs`: when the app sends `fingertip_in_target`. Use `null` if there was no touch within 30 s.
> - `correctItem`: taken from the next `transcript` that contains the ✓ / mismatch verdict.
>
> Add a vitest test for the serializer.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let sighted judges try AIRA on a laptop without signing in, with every scenario running through the real pipeline. Also make the app installable (PWA + Android APK) and measure the latencies that go on the deck's benchmark slide.

**Architecture:**
- **Demo mode** (`/try`):
  - Swaps the camera for a bundled `<video>`/`<img>` through a frame-source override that `captureFrame` reads from.
  - Plays the UPI soundbox clip into the same 16 kHz `audio` stream the microphone uses.
  - Sends scenario prompts as `text` messages to `aira-live` on the anonymous **demo shop**.
  - The server logic is identical to a real session. Only the input source changes.
- **Installable app and APK:** a hand-written service worker (cache-first for the app shell and MediaPipe models; never for WebSocket or Firestore traffic) and a Bubblewrap TWA that wraps the same URL.
- **Benchmarks:**
  - A client-side `BenchCollector` watches the socket traffic and records four latencies.
  - A battery logger computes drain per hour.
  - Results export as NDJSON, then `bench/publish.py` writes a p50/p95 CSV and loads the raw rows into BigQuery `aira.latency_metrics` with `bq load`.

**Tech Stack:** TypeScript 5, Preact, Vite, Vitest (jsdom), Firebase JS SDK (`firebase/auth`), Bubblewrap CLI (Node 18+, JDK 17), Python 3.12 + uv + pytest, `bq` CLI.

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§7 Testing & benchmarks, §8 Demo & submission) · **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`

**Prerequisites (merged before starting):**
- the foundation plan: `apps/app` skeleton, `AiraSocket`, `@aira/contracts`, Firebase Hosting, the seeded `demo` shop
- `ishwarya/01-app-shell-voice`: `app`, voice playback, Firebase init
- `ishwarya/02-camera-guidance`: `captureFrame`, `startContinuous`, the `request_frame` handler, `trackFingertip`, `guideTo`

## Global Constraints

- **Names:** the product is called **AIRA** in UI copy. The demo shop id is exactly `"demo"`. The TWA package id is exactly `in.techjays.aira`.
- **Contracts:** use `ClientMsg`/`ServerMsg` from `@aira/contracts` exactly as in the interfaces file. `FramePurpose` values are `"ask" | "count" | "point" | "identify" | "invoice" | "cash" | "guide"`.
- **Audio:** sent to the server as 16 kHz mono PCM16, base64, in 40 ms chunks (640 samples). This is the same format as the microphone.
- **Demo media:** fictional invoices only (no real GSTIN or phone numbers), no faces, no real customer data. Each file is at most 8 MB.
- **Service worker:** must **bypass** `ws:`/`wss:`, every `*.googleapis.com` host except `storage.googleapis.com/mediapipe-models/`, and `/api/` and `/webhooks/`. Only GET requests are ever cached.
- **Benchmarks:** `latency_metrics` rows are exactly `{metric: STRING, value: FLOAT, unit: STRING, device: STRING, build: STRING, ts: TIMESTAMP (ISO-8601)}`.
- **Commands:** TS tests run with `npm test -w apps/app`; Python bench tests with `cd bench && uv run pytest -q`.
- **Git:** work on branch `ishwarya/demo-pwa-bench`, with a small PR to `main` after each task.

## Review Focus

1. **A demo source left active** after leaving `/try` must not replace the real camera. `setFrameSource(null)` restores the camera (test in Task 1).
2. **A 48 kHz stereo soundbox clip** must be downmixed and resampled to 16 kHz mono with no clipping wrap-around (tests in Task 1).
3. **The service worker** must never cache `wss://` or Firestore/Auth traffic, or a stale app shell would mask an outage (test in Task 3).
4. **Interleaved sales and out-of-order `state` messages** must be paired by sale id. Unmatched or voided sales produce no sample (test in Task 5).
5. **No Battery Status API** (iOS, Firefox desktop) means the logger becomes a no-op and never throws (test in Task 5).

---

## File Structure

```
apps/app/
  src/camera/source.ts            frame-source override (demo media instead of camera)
  src/camera/capture.ts           MODIFY: draw from pickSource()
  src/demo/audioInject.ts         decode → mono → 16 kHz → PCM16 chunks → `audio` msgs
  src/demo/scenarios.ts           scenario cards + exact demo media list
  src/demo/describe.ts            ServerMsg → one readable line for the judge panel
  src/demo/connectDemo.ts         anonymous auth + hello on shop "demo"
  src/demo/TryAira.tsx            landing page + cards + transcript panel + typed fallback
  src/demo/try.css
  src/router.ts                   "/try" | "/bench" | "/" routing
  src/main.tsx                    MODIFY: route to TryAira / BenchPage / app
  src/pwa/register.ts             service worker registration (production only)
  src/bench/collector.ts          BenchCollector (4 latency metrics)
  src/bench/battery.ts            battery logger + drainPerHour
  src/bench/export.ts             percentile + NDJSON export
  src/bench/install.ts            wires collector to app.socket / app.send
  src/bench/BenchPage.tsx         /bench page: live p50, device name, export
  public/manifest.webmanifest     REPLACE
  public/icons/aira-192.png, aira-512.png   (copied from assets/logo)
  public/sw.js, public/sw-strategy.js
  public/demo/README.md           exact recording specs for every demo file
  public/demo/*.mp4|jpg|wav       recorded media (Task 2, Step 6)
  public/.well-known/assetlinks.json
  twa/twa-manifest.json, twa/write-assetlinks.mjs
  test/source.test.ts, test/audioInject.test.ts, test/scenarios.test.ts, test/describe.test.ts,
  test/router.test.ts, test/pwa.test.ts, test/assetlinks.test.ts, test/bench.test.ts
bench/
  pyproject.toml, publish.py, tests/test_publish.py, out/ (git-ignored)
```

---

### Task 1: Demo frame source and soundbox audio injection

**Files:**
- Create: `apps/app/src/camera/source.ts`, `apps/app/src/demo/audioInject.ts`
- Modify: `apps/app/src/camera/capture.ts` (the `drawImage` call inside `captureFrame`)
- Test: `apps/app/test/source.test.ts`, `apps/app/test/audioInject.test.ts`

**Interfaces:**
- Consumes:
  - `ClientMsg` from `@aira/contracts`
  - `captureFrame(purpose)` (from `ishwarya/02`, which reads a `<video>` element internally)
- Produces:
  - `setFrameSource(el: FrameSourceEl | null): void`, `getFrameSource(): FrameSourceEl | null`
  - `pickSource(camera: HTMLVideoElement): { el: CanvasImageSource; w: number; h: number }`
  - `downmixToMono(channels: Float32Array[]): Float32Array`
  - `resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array`
  - `floatToPcm16(input: Float32Array): Int16Array`
  - `chunkPcm(pcm: Int16Array, samplesPerChunk: number): Int16Array[]`
  - `int16ToBase64(chunk: Int16Array): string`
  - `injectAudioFile(url: string, send: (m: ClientMsg) => boolean, opts?: { chunkMs?: number; realtime?: boolean; ctx?: BaseAudioContext; fetchImpl?: typeof fetch }): Promise<number>` (returns the number of chunks sent)

- [ ] **Step 1: Write the failing tests**

```ts
// apps/app/test/source.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { setFrameSource, getFrameSource, pickSource } from "../src/camera/source";

function video(w: number, h: number) {
  const v = document.createElement("video");
  Object.defineProperty(v, "videoWidth", { value: w });
  Object.defineProperty(v, "videoHeight", { value: h });
  return v;
}
function image(w: number, h: number) {
  const i = document.createElement("img");
  Object.defineProperty(i, "naturalWidth", { value: w });
  Object.defineProperty(i, "naturalHeight", { value: h });
  return i;
}

describe("frame source override", () => {
  afterEach(() => setFrameSource(null));
  it("uses the camera when no override is set", () => {
    const cam = video(1280, 720);
    expect(pickSource(cam)).toEqual({ el: cam, w: 1280, h: 720 });
  });
  it("uses the demo image when set, with its natural size", () => {
    const img = image(1600, 1200);
    setFrameSource(img);
    expect(getFrameSource()).toBe(img);
    expect(pickSource(video(1280, 720))).toEqual({ el: img, w: 1600, h: 1200 });
  });
  it("setFrameSource(null) restores the real camera (Review Focus #1)", () => {
    setFrameSource(video(640, 480));
    setFrameSource(null);
    const cam = video(1280, 720);
    expect(pickSource(cam).el).toBe(cam);
  });
});
```

```ts
// apps/app/test/audioInject.test.ts
import { describe, it, expect, vi } from "vitest";
import { downmixToMono, resampleLinear, floatToPcm16, chunkPcm, int16ToBase64, injectAudioFile } from "../src/demo/audioInject";

describe("audio conversion", () => {
  it("downmixes stereo by averaging", () => {
    const out = downmixToMono([new Float32Array([1, 0, -1]), new Float32Array([0, 0, 1])]);
    expect(Array.from(out)).toEqual([0.5, 0, 0]);
  });
  it("resamples 48 kHz to 16 kHz with the right length (Review Focus #2)", () => {
    const src = new Float32Array(48000).map((_, i) => Math.sin(i / 10));
    expect(resampleLinear(src, 48000, 16000).length).toBe(16000);
  });
  it("clamps instead of wrapping around when converting to PCM16 (Review Focus #2)", () => {
    expect(Array.from(floatToPcm16(new Float32Array([1.5, -1.5, 0, 0.5])))).toEqual([32767, -32768, 0, 16384]);
  });
  it("chunks into 640-sample (40 ms) pieces, keeping the remainder", () => {
    const chunks = chunkPcm(new Int16Array(1500), 640);
    expect(chunks.map((c) => c.length)).toEqual([640, 640, 220]);
  });
  it("base64-encodes little-endian bytes", () => {
    expect(int16ToBase64(new Int16Array([1, -1]))).toBe(btoa(String.fromCharCode(1, 0, 255, 255)));
  });
});

describe("injectAudioFile", () => {
  it("decodes, converts and sends one `audio` message per 40 ms chunk", async () => {
    const ctx = {
      decodeAudioData: vi.fn(async () => ({
        numberOfChannels: 2, sampleRate: 48000, length: 4800,
        getChannelData: (_: number) => new Float32Array(4800).fill(0.25),
      })),
    } as unknown as BaseAudioContext;
    const fetchImpl = vi.fn(async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) })) as unknown as typeof fetch;
    const sent: any[] = [];
    const n = await injectAudioFile("/demo/soundbox_upi_60.wav", (m) => { sent.push(m); return true; },
      { ctx, fetchImpl, realtime: false });
    // 4800 samples @48k = 0.1 s = 1600 samples @16k = 3 chunks (640, 640, 320)
    expect(n).toBe(3);
    expect(sent.every((m) => m.t === "audio" && typeof m.pcm16 === "string")).toBe(true);
  });
  it("throws a readable error when the file is missing", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false, status: 404 })) as unknown as typeof fetch;
    await expect(injectAudioFile("/demo/nope.wav", () => true, { fetchImpl, realtime: false, ctx: {} as BaseAudioContext }))
      .rejects.toThrow("demo audio 404: /demo/nope.wav");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test -w apps/app -- source audioInject`
Expected: FAIL with "Failed to resolve import ../src/camera/source" and "…/src/demo/audioInject".

- [ ] **Step 3: Implement `src/camera/source.ts`**

```ts
export type FrameSourceEl = HTMLVideoElement | HTMLImageElement | HTMLCanvasElement;

let override: FrameSourceEl | null = null;

export function setFrameSource(el: FrameSourceEl | null): void {
  override = el;
}

export function getFrameSource(): FrameSourceEl | null {
  return override;
}

function sizeOf(el: FrameSourceEl): { w: number; h: number } {
  if (el instanceof HTMLVideoElement) return { w: el.videoWidth, h: el.videoHeight };
  if (el instanceof HTMLImageElement) return { w: el.naturalWidth, h: el.naturalHeight };
  return { w: el.width, h: el.height };
}

/** The element captureFrame() must draw from: the demo override if set, otherwise the live camera. */
export function pickSource(camera: HTMLVideoElement): { el: CanvasImageSource; w: number; h: number } {
  const el = override ?? camera;
  return { el, ...sizeOf(el) };
}
```

- [ ] **Step 4: Wire `pickSource` into `captureFrame` in `src/camera/capture.ts`**

`captureFrame` is from `ishwarya/02`, and it draws the camera `<video>` into a canvas. Find the single line in that function that draws the camera video (`ctx.drawImage(video, …)`) and replace the source-size lookup and the draw with:

```ts
import { pickSource } from "./source";
// …inside captureFrame, where the camera <video> element is called `video`:
const src = pickSource(video);
const scale = Math.min(1, 640 / Math.max(src.w, src.h));
const outW = Math.round(src.w * scale);
const outH = Math.round(src.h * scale);
canvas.width = outW;
canvas.height = outH;
ctx.drawImage(src.el, 0, 0, outW, outH);
```

Keep 02's existing JPEG encoding and frame-message construction below this unchanged. If 02 already scales to ≤640 px, keep its scaling and only swap `video` for `src.el` and the width/height for `src.w`/`src.h`.

- [ ] **Step 5: Implement `src/demo/audioInject.ts`**

If `ishwarya/01` already exports identical resample or PCM helpers from `src/voice/`, import those instead and delete the duplicates here.

```ts
import type { ClientMsg } from "@aira/contracts";

export function downmixToMono(channels: Float32Array[]): Float32Array {
  const n = channels[0]?.length ?? 0;
  const out = new Float32Array(n);
  for (const ch of channels) for (let i = 0; i < n; i++) out[i] += ch[i] / channels.length;
  return out;
}

export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input.slice();
  const outLen = Math.floor((input.length * toRate) / fromRate);
  const out = new Float32Array(outLen);
  const ratio = fromRate / toRate;
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

export function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out;
}

export function chunkPcm(pcm: Int16Array, samplesPerChunk: number): Int16Array[] {
  const chunks: Int16Array[] = [];
  for (let i = 0; i < pcm.length; i += samplesPerChunk) chunks.push(pcm.subarray(i, i + samplesPerChunk));
  return chunks;
}

export function int16ToBase64(chunk: Int16Array): string {
  const bytes = new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Plays a demo audio file into the SAME `audio` stream the microphone uses (16 kHz mono PCM16). */
export async function injectAudioFile(
  url: string,
  send: (m: ClientMsg) => boolean,
  opts: { chunkMs?: number; realtime?: boolean; ctx?: BaseAudioContext; fetchImpl?: typeof fetch } = {},
): Promise<number> {
  const chunkMs = opts.chunkMs ?? 40;
  const realtime = opts.realtime ?? true;
  const res = await (opts.fetchImpl ?? fetch)(url);
  if (!res.ok) throw new Error(`demo audio ${(res as Response).status}: ${url}`);
  const ctx = opts.ctx ?? new OfflineAudioContext(1, 1, 48000);
  const buf = await ctx.decodeAudioData(await res.arrayBuffer());
  const channels = Array.from({ length: buf.numberOfChannels }, (_, c) => buf.getChannelData(c));
  const pcm = floatToPcm16(resampleLinear(downmixToMono(channels), buf.sampleRate, 16000));
  const chunks = chunkPcm(pcm, (16000 * chunkMs) / 1000);
  for (const c of chunks) {
    send({ t: "audio", pcm16: int16ToBase64(c) });
    if (realtime) await sleep(chunkMs); // paced like live speech, so server-side VAD behaves normally
  }
  return chunks.length;
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm test -w apps/app -- source audioInject`
Expected: PASS, 10 tests.

- [ ] **Step 7: Commit**

```bash
git checkout -b ishwarya/demo-pwa-bench
git add apps/app/src/camera/source.ts apps/app/src/camera/capture.ts apps/app/src/demo/audioInject.ts apps/app/test/source.test.ts apps/app/test/audioInject.test.ts
git commit -m "feat(demo): frame-source override and soundbox audio injection into the live pipeline"
```

---

### Task 2: "Try AIRA" landing page, scenario cards, judge panel, typed fallback

**Files:**
- Create: `apps/app/src/demo/scenarios.ts`, `src/demo/describe.ts`, `src/demo/connectDemo.ts`, `src/demo/TryAira.tsx`, `src/demo/try.css`, `src/router.ts`, `public/demo/README.md`
- Modify: `apps/app/src/main.tsx`
- Test: `apps/app/test/scenarios.test.ts`, `test/describe.test.ts`, `test/router.test.ts`

**Interfaces:**
- Consumes:
  - `app` (`app.socket`, `app.send`) from `src/core/app.ts` (ishwarya/01)
  - `startContinuous(purpose, fps)` and the `request_frame` handler (ishwarya/02)
  - `setFrameSource`, `injectAudioFile` (Task 1)
  - the Firebase app initialised by ishwarya/01
  - the server-side demo shop `demo`, seeded by the foundation from `content/catalog/murugan_dairy.json`
- Produces:
  - `SCENARIOS: Scenario[]`, `DEMO_MEDIA_FILES: string[]`
  - `describe(m: ServerMsg): string | null`
  - `connectDemo(appVersion: string): Promise<void>`
  - `routeFor(pathname: string): "try" | "bench" | "app"`
  - the `<TryAira/>` component

- [ ] **Step 1: Write the failing tests**

```ts
// apps/app/test/scenarios.test.ts
import { describe, it, expect } from "vitest";
import { SCENARIOS, DEMO_MEDIA_FILES } from "../src/demo/scenarios";

describe("demo scenarios", () => {
  it("cover the whole Murugan Dairy loop, in order", () => {
    expect(SCENARIOS.map((s) => s.id)).toEqual([
      "order", "delivery-one", "delivery-crate", "invoice-ok", "invoice-wrong",
      "pay-vendor", "sale-guide", "sale-cash", "sale-upi", "summary",
    ]);
  });
  it("list exactly the media files that must be recorded", () => {
    expect(DEMO_MEDIA_FILES).toEqual([
      "/demo/delivery_one_by_one.mp4", "/demo/crate_sakthi_curd.jpg", "/demo/invoice_ok.jpg",
      "/demo/invoice_wrong_qty.jpg", "/demo/cash_vendor_500x2.jpg", "/demo/sale_guidance.mp4",
      "/demo/cash_customer_100.jpg", "/demo/soundbox_upi_60.wav",
    ]);
  });
  it("every card has a spoken-style prompt", () => {
    for (const s of SCENARIOS) expect(s.prompt.length).toBeGreaterThan(8);
  });
});
```

```ts
// apps/app/test/describe.test.ts
import { describe as d, it, expect } from "vitest";
import { describe } from "../src/demo/describe";

d("judge panel lines", () => {
  it("formats AIRA and user transcripts (final only)", () => {
    expect(describe({ t: "transcript", role: "aira", text: "28 counted. 2 short?", final: true })).toBe("AIRA: 28 counted. 2 short?");
    expect(describe({ t: "transcript", role: "user", text: "count", final: false })).toBeNull();
  });
  it("formats live counts and business states", () => {
    expect(describe({ t: "count", sku: "sakthi-curd-1l", counted: 7, expected: 10, final: false, confidence: 0.9 }))
      .toBe("Counting sakthi-curd-1l: 7 / 10");
    expect(describe({ t: "state", entity: "sale", id: "s1", state: "SALE_COMPLETED", summary: "2 L curd ₹120" }))
      .toBe("sale s1 → SALE_COMPLETED: 2 L curd ₹120");
  });
  it("flags mismatch cues and errors", () => {
    expect(describe({ t: "cue", cue: "mismatch" })).toBe("⚠ Mismatch");
    expect(describe({ t: "error", code: "camera", say: "Too dark" })).toBe("Error (camera): Too dark");
    expect(describe({ t: "audio", pcm24: "AAAA" })).toBeNull();
  });
});
```

```ts
// apps/app/test/router.test.ts
import { describe, it, expect } from "vitest";
import { routeFor } from "../src/router";

describe("routeFor", () => {
  it("routes /try and /bench, everything else to the app", () => {
    expect(routeFor("/try")).toBe("try");
    expect(routeFor("/try/")).toBe("try");
    expect(routeFor("/bench")).toBe("bench");
    expect(routeFor("/")).toBe("app");
    expect(routeFor("/trying")).toBe("app");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test -w apps/app -- scenarios describe router`
Expected: FAIL with unresolved imports for `scenarios`, `describe` and `router`.

- [ ] **Step 3: Implement `scenarios.ts`, `describe.ts`, `router.ts`**

```ts
// apps/app/src/demo/scenarios.ts
export interface Scenario {
  id: string;
  title: string;
  blurb: string;
  prompt: string; // sent as a `text` message, exactly as Murugan would say it
  media?: { kind: "video" | "image" | "audio"; src: string };
}

export const SCENARIOS: Scenario[] = [
  { id: "order", title: "1 · Order stock by voice", blurb: "AIRA reads the order back, then sends it to the vendor on WhatsApp after you confirm.",
    prompt: "Order 10 litres of Sakthi curd from the Sakthi vendor." },
  { id: "delivery-one", title: "2 · Count the delivery, one by one", blurb: "Each pack is counted live; only verified stock is recorded.",
    prompt: "The Sakthi curd delivery has arrived. Count it one by one.", media: { kind: "video", src: "/demo/delivery_one_by_one.mp4" } },
  { id: "delivery-crate", title: "3 · Count a crate", blurb: "One photo of a crate; AIRA asks for another view if unsure.",
    prompt: "Count the Sakthi curd crate.", media: { kind: "image", src: "/demo/crate_sakthi_curd.jpg" } },
  { id: "invoice-ok", title: "4 · Check the invoice (correct)", blurb: "Two independent reads, then code compares them with the order and the count.",
    prompt: "Check the Sakthi invoice.", media: { kind: "image", src: "/demo/invoice_ok.jpg" } },
  { id: "invoice-wrong", title: "5 · Check the invoice (wrong)", blurb: "The bill says 12 litres but 10 arrived. AIRA catches it.",
    prompt: "Check this Sakthi invoice.", media: { kind: "image", src: "/demo/invoice_wrong_qty.jpg" } },
  { id: "pay-vendor", title: "6 · Pay the vendor in cash", blurb: "Notes in hand are read twice and totalled in code.",
    prompt: "Pay the Sakthi vendor in cash.", media: { kind: "image", src: "/demo/cash_vendor_500x2.jpg" } },
  { id: "sale-guide", title: "7 · Guide the hand to the product", blurb: "Location memory plus live pointing guide the hand to the curd.",
    prompt: "A customer wants 2 litres of Sakthi curd. Guide me.", media: { kind: "video", src: "/demo/sale_guidance.mp4" } },
  { id: "sale-cash", title: "8 · Customer pays cash", blurb: "AIRA reads the note and gives the exact change.",
    prompt: "The customer is paying cash for the curd.", media: { kind: "image", src: "/demo/cash_customer_100.jpg" } },
  { id: "sale-upi", title: "9 · Customer pays by UPI", blurb: "AIRA listens to the shop's payment soundbox; a screenshot is never trusted.",
    prompt: "The customer is paying by UPI.", media: { kind: "audio", src: "/demo/soundbox_upi_60.wav" } },
  { id: "summary", title: "10 · End of day", blurb: "Sales, top seller, low stock and tomorrow's suggested order.",
    prompt: "How was today?" },
];

export const DEMO_MEDIA_FILES: string[] = SCENARIOS.flatMap((s) => (s.media ? [s.media.src] : []));
```

```ts
// apps/app/src/demo/describe.ts
import type { ServerMsg } from "@aira/contracts";

/** One human-readable line per server message for the judge panel; null = don't show. */
export function describe(m: ServerMsg): string | null {
  switch (m.t) {
    case "transcript":
      if (!m.final) return null;
      return `${m.role === "aira" ? "AIRA" : "You"}: ${m.text}`;
    case "count":
      return `Counting ${m.sku}: ${m.counted}${m.expected === null ? "" : ` / ${m.expected}`}${m.final ? " (final)" : ""}`;
    case "state":
      return `${m.entity} ${m.id} → ${m.state}: ${m.summary}`;
    case "confirm":
      return `Confirm? ${m.prompt}`;
    case "target":
      return `Guiding to ${m.label}`;
    case "cue":
      return { mismatch: "⚠ Mismatch", match: "✓ Match", unsure: "? Not sure", warn: "⚠ Warning" }[m.cue as string] ?? null;
    case "error":
      return `Error (${m.code}): ${m.say}`;
    default:
      return null;
  }
}
```

The `count` line is `Counting <sku>: <counted> / <expected>`, with ` (final)` appended only when `final` is true. The test above uses `final: false`.

```ts
// apps/app/src/router.ts
export function routeFor(pathname: string): "try" | "bench" | "app" {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/try") return "try";
  if (p === "/bench") return "bench";
  return "app";
}
```

- [ ] **Step 4: Implement `connectDemo.ts`, `TryAira.tsx`, `try.css`, and route in `main.tsx`**

```ts
// apps/app/src/demo/connectDemo.ts
import { getAuth, signInAnonymously } from "firebase/auth";
import { app } from "../core/app";

/** Reconnects the shared socket as an anonymous judge on the seeded demo shop. */
export async function connectDemo(appVersion: string): Promise<void> {
  const cred = await signInAnonymously(getAuth());
  const idToken = await cred.user.getIdToken();
  app.socket.close();
  app.socket.connect({ t: "hello", uid: cred.user.uid, idToken, shopId: "demo", lang: "en", verbosity: "short", appVersion });
}
```

```tsx
// apps/app/src/demo/TryAira.tsx
import { useEffect, useRef, useState } from "preact/hooks";
import type { ServerMsg } from "@aira/contracts";
import { app } from "../core/app";
import { SCENARIOS, type Scenario } from "./scenarios";
import { describe } from "./describe";
import { connectDemo } from "./connectDemo";
import { setFrameSource } from "../camera/source";
import { injectAudioFile } from "./audioInject";
import "./try.css";

const APP_VERSION = "0.1.0";

export function TryAira() {
  const [lines, setLines] = useState<string[]>([]);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [active, setActive] = useState<Scenario | null>(null);
  const [typed, setTyped] = useState("");
  const mediaRef = useRef<HTMLVideoElement & HTMLImageElement>(null);

  useEffect(() => {
    connectDemo(APP_VERSION).catch((e) => setLines((l) => [...l, `Error: ${String(e)}`]));
    const off = app.socket.on((m: ServerMsg) => {
      const line = describe(m);
      if (line) setLines((l) => [...l.slice(-60), line]);
      if (m.t === "confirm") setConfirmId(m.id);
    });
    return () => { off(); setFrameSource(null); }; // Review Focus #1
  }, []);

  async function run(s: Scenario) {
    setActive(s);
    setFrameSource(null);
    app.send({ t: "text", text: s.prompt });
    if (s.media?.kind === "audio") {
      setTimeout(() => injectAudioFile(s.media!.src, (m) => app.send(m)).catch((e) => setLines((l) => [...l, String(e)])), 2500);
    }
  }

  function onMediaReady(el: HTMLVideoElement | HTMLImageElement) {
    setFrameSource(el); // the server's request_frame now gets frames from this media
  }

  function answer(a: "yes" | "no") {
    if (confirmId) app.send({ t: "confirm", id: confirmId, answer: a });
    setConfirmId(null);
  }

  return (
    <main class="try">
      <header>
        <img src="/icons/aira-192.png" alt="AIRA logo" width={72} height={72} />
        <div>
          <h1>AIRA — try it</h1>
          <p>A blind shopkeeper runs a dairy shop alone. Pick a moment from Murugan's day; it runs through the real AIRA pipeline on a demo shop.</p>
        </div>
      </header>
      <section class="cards" aria-label="Demo scenarios">
        {SCENARIOS.map((s) => (
          <button class={`card ${active?.id === s.id ? "on" : ""}`} onClick={() => run(s)}>
            <strong>{s.title}</strong><span>{s.blurb}</span>
          </button>
        ))}
      </section>
      <section class="stage">
        {active?.media?.kind === "video" && (
          <video key={active.id} ref={mediaRef} src={active.media.src} autoPlay muted loop playsInline
            onLoadedData={(e) => onMediaReady(e.currentTarget)} />
        )}
        {active?.media?.kind === "image" && (
          <img key={active.id} ref={mediaRef} src={active.media.src} alt={active.title}
            onLoad={(e) => onMediaReady(e.currentTarget)} />
        )}
        {active?.media?.kind === "audio" && <p class="hint">Playing the payment soundbox into AIRA's microphone stream…</p>}
      </section>
      <section class="panel" aria-live="polite">
        {lines.map((l) => <p>{l}</p>)}
        {confirmId && (
          <div class="confirm"><button onClick={() => answer("yes")}>Yes</button><button onClick={() => answer("no")}>No</button></div>
        )}
      </section>
      <form class="typed" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { app.send({ t: "text", text: typed.trim() }); setTyped(""); } }}>
        <label for="typed">No microphone? Type what Murugan would say:</label>
        <input id="typed" value={typed} onInput={(e) => setTyped(e.currentTarget.value)} placeholder="e.g. How much Sakthi curd is left?" />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}
```

```css
/* apps/app/src/demo/try.css */
.try { max-width: 1100px; margin: 0 auto; padding: 24px 16px; font-family: system-ui, sans-serif; color: #0b1b3f; background: #fff; }
.try header { display: flex; gap: 16px; align-items: center; }
.try h1 { margin: 0; font-size: 1.8rem; }
.try .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; margin: 20px 0; }
.try .card { text-align: left; padding: 14px; border: 2px solid #d6e2ff; border-radius: 12px; background: #f7f9ff; cursor: pointer; display: grid; gap: 6px; }
.try .card.on { border-color: #1a73e8; background: #e8f0fe; }
.try .stage video, .try .stage img { max-width: 100%; max-height: 360px; border-radius: 12px; }
.try .panel { min-height: 160px; max-height: 320px; overflow: auto; background: #0b1b3f; color: #e8f0fe; padding: 12px; border-radius: 12px; font-family: ui-monospace, monospace; }
.try .confirm button { margin-right: 8px; padding: 8px 18px; font-size: 1rem; }
.try .typed { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-top: 12px; }
.try .typed label { grid-column: 1 / -1; }
.try .typed input { padding: 10px; font-size: 1rem; }
@media (prefers-color-scheme: dark) { .try { background: #0b1220; color: #e8f0fe; } .try .card { background: #111a2e; border-color: #2a3a5e; } }
```

In `apps/app/src/main.tsx`, keep everything `ishwarya/01` renders at `/` as `<App/>` and wrap it:

```tsx
import { render } from "preact";
import { routeFor } from "./router";
import { TryAira } from "./demo/TryAira";
import { BenchPage } from "./bench/BenchPage";
import { registerServiceWorker } from "./pwa/register";
// existing import of the ishwarya/01 root component stays, e.g. `import { App } from "./App";`

const route = routeFor(location.pathname);
render(route === "try" ? <TryAira /> : route === "bench" ? <BenchPage /> : <App />, document.getElementById("root")!);
registerServiceWorker();
```

`BenchPage` and `registerServiceWorker` are created in Tasks 5 and 3. Until those land, comment out the two lines that use them.

- [ ] **Step 5: Write `public/demo/README.md` (exact recording specs)**

```markdown
# Demo media for "Try AIRA" (judges)

Record on the test phone in the demo dairy shop.
- Use fictional invoices only, with no real GSTIN or phone numbers.
- No faces in frame.
- Each file must be ≤ 8 MB.

| File | Content | Spec |
|---|---|---|
| `delivery_one_by_one.mp4` | Chest-camera view: 10 Sakthi curd 1 L pouches. Each is held towards the camera ~1.5 s, then placed into the fridge | 1280×720, H.264, ≤25 s, no audio |
| `crate_sakthi_curd.jpg` | Top-down photo of one crate, 12 pouches visible, none overlapping more than half | ≤1600 px long side |
| `invoice_ok.jpg` | Printed mock invoice, "Sakthi Dairy Distributor": 10 × Sakthi curd 1 L, unit price and total consistent | Flat, well lit, ≤1600 px |
| `invoice_wrong_qty.jpg` | The same invoice but qty **12** (total adjusted), to trigger a discrepancy against the 10 counted | Same as above |
| `cash_vendor_500x2.jpg` | Two ₹500 notes fanned in an open hand, chest-camera view | ≤1600 px |
| `sale_guidance.mp4` | Chest-camera view reaching into fridge 2, middle shelf, left, taking one Sakthi curd 1 L pouch | 1280×720, ≤15 s, no audio |
| `cash_customer_100.jpg` | One ₹100 note in an open hand | ≤1600 px |
| `soundbox_upi_60.wav` | Recording of a UPI payment soundbox announcing a ₹60 receipt, as it is heard in the shop | WAV PCM16, mono or stereo, 16–48 kHz, ≤6 s |
```

- [ ] **Step 6: Record and add the 8 media files**

Record the 8 files listed in `public/demo/README.md` and copy them to `apps/app/public/demo/` with those exact names. Then run this check:

```bash
cd apps/app && node -e "const f=['delivery_one_by_one.mp4','crate_sakthi_curd.jpg','invoice_ok.jpg','invoice_wrong_qty.jpg','cash_vendor_500x2.jpg','sale_guidance.mp4','cash_customer_100.jpg','soundbox_upi_60.wav'];const fs=require('fs');const miss=f.filter(x=>!fs.existsSync('public/demo/'+x));const big=f.filter(x=>fs.existsSync('public/demo/'+x)&&fs.statSync('public/demo/'+x).size>8e6);console.log(JSON.stringify({miss,big}))"
```

Expected: `{"miss":[],"big":[]}`

- [ ] **Step 7: Run the tests and build**

Run: `npm test -w apps/app -- scenarios describe router && npm run build -w apps/app`
Expected: PASS, 8 tests; the build succeeds.

Then run a manual check with `aira-live` running in mock or real mode: open `http://localhost:5173/try` and click card 3. The panel shows `AIRA: …` lines, and once the server sends `request_frame`, frames come from the crate photo.

- [ ] **Step 8: Commit**

```bash
git add apps/app/src/demo apps/app/src/router.ts apps/app/src/main.tsx apps/app/public/demo apps/app/test/scenarios.test.ts apps/app/test/describe.test.ts apps/app/test/router.test.ts
git commit -m "feat(demo): Try AIRA landing page with 10 scenario cards, judge panel and typed fallback"
```

---

### Task 3: Installable app — manifest, icons, service worker

**Files:**
- Create: `apps/app/public/sw.js`, `apps/app/public/sw-strategy.js`, `apps/app/src/pwa/register.ts`, `apps/app/public/icons/aira-192.png`, `apps/app/public/icons/aira-512.png`
- Replace: `apps/app/public/manifest.webmanifest`
- Test: `apps/app/test/pwa.test.ts`

**Interfaces:**
- Consumes: `assets/logo/aira-logo-192.png`, `assets/logo/aira-logo-512.png`
- Produces:
  - `strategyFor(url: string, mode: string): "cache-first" | "network-first" | "bypass"` (in `sw-strategy.js`, CommonJS-compatible)
  - `registerServiceWorker(nav?: Navigator, isProd?: boolean): Promise<ServiceWorkerRegistration | null>`

- [ ] **Step 1: Copy the icons**

```bash
mkdir -p apps/app/public/icons
cp assets/logo/aira-logo-192.png apps/app/public/icons/aira-192.png
cp assets/logo/aira-logo-512.png apps/app/public/icons/aira-512.png
```

- [ ] **Step 2: Write the failing tests**

```ts
// apps/app/test/pwa.test.ts
import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { registerServiceWorker } from "../src/pwa/register";

const require = createRequire(import.meta.url);
const { strategyFor, PRECACHE } = require("../public/sw-strategy.js");

describe("manifest", () => {
  const m = JSON.parse(readFileSync("public/manifest.webmanifest", "utf8"));
  it("is installable as AIRA", () => {
    expect(m.name).toBe("AIRA");
    expect(m.short_name).toBe("AIRA");
    expect(m.start_url).toBe("/");
    expect(m.display).toBe("standalone");
  });
  it("references icons that exist", () => {
    for (const i of m.icons) expect(existsSync(`public${i.src}`)).toBe(true);
  });
});

describe("service worker strategy (Review Focus #3)", () => {
  it("never caches websockets, Firestore, Auth, API or webhooks", () => {
    expect(strategyFor("wss://aira-live-x.a.run.app/ws", "websocket")).toBe("bypass");
    expect(strategyFor("https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen", "cors")).toBe("bypass");
    expect(strategyFor("https://identitytoolkit.googleapis.com/v1/accounts", "cors")).toBe("bypass");
    expect(strategyFor("https://demo.web.app/api/x", "cors")).toBe("bypass");
    expect(strategyFor("https://demo.web.app/webhooks/whatsapp", "cors")).toBe("bypass");
  });
  it("caches MediaPipe models, wasm, demo media and static assets first", () => {
    expect(strategyFor("https://storage.googleapis.com/mediapipe-models/hand_landmarker/x/hand_landmarker.task", "cors")).toBe("cache-first");
    expect(strategyFor("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/wasm/vision_wasm_internal.wasm", "cors")).toBe("cache-first");
    expect(strategyFor("https://demo.web.app/demo/invoice_ok.jpg", "no-cors")).toBe("cache-first");
    expect(strategyFor("https://demo.web.app/assets/index-abc.js", "cors")).toBe("cache-first");
  });
  it("uses network-first for page navigations", () => {
    expect(strategyFor("https://demo.web.app/try", "navigate")).toBe("network-first");
  });
  it("precaches the app shell and icons", () => {
    expect(PRECACHE).toEqual(["/", "/index.html", "/manifest.webmanifest", "/icons/aira-192.png", "/icons/aira-512.png"]);
  });
});

describe("registerServiceWorker", () => {
  it("does nothing in development", async () => {
    expect(await registerServiceWorker({ serviceWorker: { register: () => { throw new Error("no"); } } } as any, false)).toBeNull();
  });
  it("registers /sw.js at scope / in production", async () => {
    const calls: any[] = [];
    const nav = { serviceWorker: { register: async (...a: any[]) => { calls.push(a); return { scope: "/" }; } } } as any;
    expect(await registerServiceWorker(nav, true)).toEqual({ scope: "/" });
    expect(calls[0]).toEqual(["/sw.js", { scope: "/" }]);
  });
  it("returns null when service workers are unsupported", async () => {
    expect(await registerServiceWorker({} as any, true)).toBeNull();
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npm test -w apps/app -- pwa`
Expected: FAIL with "Cannot find module '../public/sw-strategy.js'".

- [ ] **Step 4: Implement the manifest, strategy, service worker and registration**

```json
{
  "name": "AIRA",
  "short_name": "AIRA",
  "description": "Run your shop with your voice — an AI shop assistant for blind shopkeepers.",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#000000",
  "theme_color": "#0b1b3f",
  "icons": [
    { "src": "/icons/aira-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable" },
    { "src": "/icons/aira-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
```

```js
// apps/app/public/sw-strategy.js — plain JS, loaded by sw.js via importScripts and by tests via require
(function (root) {
  var VERSION = "aira-v1";
  var PRECACHE = ["/", "/index.html", "/manifest.webmanifest", "/icons/aira-192.png", "/icons/aira-512.png"];
  function strategyFor(url, mode) {
    var u = new URL(url);
    if (u.protocol === "ws:" || u.protocol === "wss:") return "bypass";
    if (u.hostname === "storage.googleapis.com" && u.pathname.indexOf("/mediapipe-models/") === 0) return "cache-first";
    if (/(^|\.)googleapis\.com$/.test(u.hostname)) return "bypass"; // Firestore, Auth, App Check, FCM, Storage API
    if (u.hostname === "cdn.jsdelivr.net" && u.pathname.indexOf("/npm/@mediapipe/") === 0) return "cache-first";
    if (u.pathname.indexOf("/api/") === 0 || u.pathname.indexOf("/webhooks/") === 0) return "bypass";
    if (u.pathname.indexOf("/demo/") === 0 || u.pathname.indexOf("/models/") === 0) return "cache-first";
    if (mode === "navigate") return "network-first";
    if (/\.(js|css|png|jpg|jpeg|svg|webmanifest|wasm|tflite|task|mp4|wav)$/.test(u.pathname)) return "cache-first";
    return "network-first";
  }
  var api = { VERSION: VERSION, PRECACHE: PRECACHE, strategyFor: strategyFor };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.swStrategy = api;
})(typeof self !== "undefined" ? self : globalThis);
```

```js
// apps/app/public/sw.js
importScripts("/sw-strategy.js");
var S = self.swStrategy;

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(S.VERSION).then(function (c) { return c.addAll(S.PRECACHE); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== S.VERSION; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function put(req, res) {
  if (res && (res.ok || res.type === "opaque")) {
    var copy = res.clone();
    caches.open(S.VERSION).then(function (c) { c.put(req, copy); });
  }
  return res;
}

self.addEventListener("fetch", function (e) {
  if (e.request.method !== "GET") return;
  var s = S.strategyFor(e.request.url, e.request.mode);
  if (s === "bypass") return;
  if (s === "cache-first") {
    e.respondWith(caches.match(e.request).then(function (hit) { return hit || fetch(e.request).then(function (r) { return put(e.request, r); }); }));
    return;
  }
  e.respondWith(fetch(e.request).then(function (r) { return put(e.request, r); }).catch(function () {
    return caches.match(e.request).then(function (hit) { return hit || caches.match("/index.html"); });
  }));
});
```

```ts
// apps/app/src/pwa/register.ts
export function registerServiceWorker(
  nav: Navigator = navigator,
  isProd: boolean = (import.meta as any).env?.PROD === true,
): Promise<ServiceWorkerRegistration | null> {
  if (!isProd || !nav || !("serviceWorker" in nav)) return Promise.resolve(null);
  return nav.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => null);
}
```

Then uncomment the `registerServiceWorker();` line in `main.tsx` (from Task 2).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w apps/app -- pwa`
Expected: PASS, 10 tests.

- [ ] **Step 6: Run the installability check on a phone**

Run: `npm run build -w apps/app && npx firebase-tools deploy --only hosting:app`

Open the Hosting URL in Chrome on the Android test phone. "Add to Home screen" or "Install app" should appear. Then Chrome DevTools → Application → Service Workers should show `sw.js` "activated and is running", and Cache Storage `aira-v1` should list the 5 precached URLs.

- [ ] **Step 7: Commit**

```bash
git add apps/app/public/manifest.webmanifest apps/app/public/icons apps/app/public/sw.js apps/app/public/sw-strategy.js apps/app/src/pwa/register.ts apps/app/src/main.tsx apps/app/test/pwa.test.ts
git commit -m "feat(pwa): installable AIRA — manifest, logo icons, service worker that never caches live traffic"
```

---

### Task 4: Android APK via Bubblewrap TWA

**Files:**
- Create: `apps/app/twa/twa-manifest.json` (generated, then checked), `apps/app/twa/write-assetlinks.mjs`, `apps/app/public/.well-known/assetlinks.json`
- Modify: `.gitignore` (append three lines; tell Saravana in the PR)
- Test: `apps/app/test/assetlinks.test.ts`

**Interfaces:**
- Consumes:
  - the deployed Hosting URL from Task 3
  - the `firebase.json` header for `/.well-known/assetlinks.json` (foundation)
- Produces:
  - `apps/app/twa/app-release-signed.apk` (not committed)
  - TWA package `in.techjays.aira`

- [ ] **Step 1: Install the toolchain and get the Hosting domain**

```bash
node --version            # must be ≥ 18
java -version             # must be 17
npm i -g @bubblewrap/cli
npx firebase-tools hosting:sites:list   # note the DEFAULT URL host, e.g. <project-id>.web.app
```

- [ ] **Step 2: Generate the TWA project**

```bash
mkdir -p apps/app/twa && cd apps/app/twa
bubblewrap init --manifest https://<the host from Step 1>/manifest.webmanifest
```

Use the host from Step 1 in that URL. Answer the prompts:

| Prompt | Answer |
|---|---|
| Domain | the host from Step 1 |
| Application ID | `in.techjays.aira` |
| Name | `AIRA` |
| Launcher name | `AIRA` |
| Display mode | `standalone` |
| Orientation | `portrait` |
| Theme color | `#0b1b3f` |
| Background color | `#000000` |
| Start URL | `/` |
| Icon | the manifest's 512 icon |
| Include support for Play Billing | No |
| Request geolocation permission | No |
| Key store location | `./aira.keystore` |
| Key name | `aira` (choose and record the passwords in the team password manager) |

Then open `twa-manifest.json` and set `"fallbackType": "customtabs"`. A WebView fallback would lose Media Session and speechSynthesis, which the voice UI needs.

- [ ] **Step 3: Write the failing test**

```ts
// apps/app/test/assetlinks.test.ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

describe("TWA config", () => {
  it("twa-manifest targets AIRA with custom-tabs fallback", () => {
    const t = JSON.parse(readFileSync("twa/twa-manifest.json", "utf8"));
    expect(t.packageId).toBe("in.techjays.aira");
    expect(t.fallbackType).toBe("customtabs");
    expect(t.display).toBe("standalone");
    expect(t.startUrl).toBe("/");
    expect(t.host).toMatch(/\.(web\.app|firebaseapp\.com)$/);
  });
  it("assetlinks.json delegates handle_all_urls to the signed APK", () => {
    const a = JSON.parse(readFileSync("public/.well-known/assetlinks.json", "utf8"));
    expect(a[0].relation).toEqual(["delegate_permission/common.handle_all_urls"]);
    expect(a[0].target.namespace).toBe("android_app");
    expect(a[0].target.package_name).toBe("in.techjays.aira");
    expect(a[0].target.sha256_cert_fingerprints[0]).toMatch(/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `npm test -w apps/app -- assetlinks`
Expected: FAIL with "ENOENT … public/.well-known/assetlinks.json".

- [ ] **Step 5: Create `write-assetlinks.mjs`, read the real fingerprint, and write the file**

```js
// apps/app/twa/write-assetlinks.mjs — usage: node apps/app/twa/write-assetlinks.mjs "AA:BB:…(32 pairs)"
import { writeFileSync, mkdirSync } from "node:fs";
const fp = (process.argv[2] || "").trim().toUpperCase();
if (!/^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(fp)) {
  console.error("Pass the SHA-256 fingerprint (32 colon-separated hex pairs).");
  process.exit(1);
}
const links = [{
  relation: ["delegate_permission/common.handle_all_urls"],
  target: { namespace: "android_app", package_name: "in.techjays.aira", sha256_cert_fingerprints: [fp] },
}];
mkdirSync("apps/app/public/.well-known", { recursive: true });
writeFileSync("apps/app/public/.well-known/assetlinks.json", JSON.stringify(links, null, 2) + "\n");
console.log("wrote apps/app/public/.well-known/assetlinks.json");
```

```bash
keytool -list -v -keystore apps/app/twa/aira.keystore -alias aira | grep "SHA256:" | awk '{print $2}'
node apps/app/twa/write-assetlinks.mjs "<the fingerprint printed above>"
```

- [ ] **Step 6: Keep secrets and build output out of git**

Append to the root `.gitignore`:

```
apps/app/twa/*.keystore
apps/app/twa/*.apk
apps/app/twa/app/build/
```

- [ ] **Step 7: Run the tests, build the APK, deploy asset links, install**

```bash
npm test -w apps/app -- assetlinks          # Expected: PASS (2 tests)
cd apps/app/twa && bubblewrap build && cd ../../..
npm run build -w apps/app && npx firebase-tools deploy --only hosting:app
curl -s https://<the host from Step 1>/.well-known/assetlinks.json   # Expected: the JSON with in.techjays.aira
adb install -r apps/app/twa/app-release-signed.apk
```

Expected: the AIRA icon opens **full-screen with no URL bar**, which proves the asset links are verified. If a URL bar shows, the fingerprint doesn't match the signing key, so repeat Step 5.

- [ ] **Step 8: Commit**

```bash
git add apps/app/twa/twa-manifest.json apps/app/twa/write-assetlinks.mjs apps/app/public/.well-known/assetlinks.json apps/app/test/assetlinks.test.ts .gitignore
git commit -m "feat(apk): Bubblewrap TWA for AIRA (in.techjays.aira) with verified asset links"
```

---

### Task 5: In-app benchmark collector, battery logger, export page

**Files:**
- Create: `apps/app/src/bench/collector.ts`, `src/bench/battery.ts`, `src/bench/export.ts`, `src/bench/install.ts`, `src/bench/BenchPage.tsx`
- Test: `apps/app/test/bench.test.ts`

**Interfaces:**
- Consumes:
  - `app.socket.on`, `app.send` (ishwarya/01)
  - the `ClientMsg`/`ServerMsg` types
  - the `fingertip_in_target` event emitted by the guidance flow (ishwarya/02)
- Produces:
  - `type Metric = "first_audio_ms" | "count_update_ms" | "guidance_time_to_touch_ms" | "sale_e2e_ms" | "battery_drain_pct_per_hour"`
  - `class BenchCollector { onOutgoing(m: ClientMsg, now: number): void; onIncoming(m: ServerMsg, now: number): void; readonly samples: Sample[] }`
  - `drainPerHour(s: BatterySample[]): number | null`
  - `startBatteryLogger(nav?, everyMs?, onSample?): Promise<() => void>`
  - `percentile(values: number[], p: number): number | null`
  - `toNdjson(samples: Sample[], device: string, build: string): string`
  - `installBench(): () => void`

Metric definitions (the deck uses these exact meanings):
- `first_audio_ms`: from the server's final user `transcript` (end of the user's turn) to the first `audio` chunk after it.
- `count_update_ms`: from the last `frame` sent with purpose `count` to the next `count` message.
- `guidance_time_to_touch_ms`: from the first `target` message of a guidance run to the client event `fingertip_in_target`.
- `sale_e2e_ms`: from the `state` sale `SALE_PENDING` to `SALE_COMPLETED` **for the same sale id**.

- [ ] **Step 1: Write the failing tests**

```ts
// apps/app/test/bench.test.ts
import { describe, it, expect } from "vitest";
import { BenchCollector } from "../src/bench/collector";
import { drainPerHour, startBatteryLogger } from "../src/bench/battery";
import { percentile, toNdjson } from "../src/bench/export";

describe("BenchCollector", () => {
  it("measures first audio after the user's final transcript, once per turn", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "transcript", role: "user", text: "stock?", final: true }, 1000);
    c.onIncoming({ t: "audio", pcm24: "A" }, 1650);
    c.onIncoming({ t: "audio", pcm24: "B" }, 1700);
    expect(c.samples).toEqual([{ metric: "first_audio_ms", value: 650, ts: 1650 }]);
  });
  it("measures count update from the most recent count frame", () => {
    const c = new BenchCollector();
    c.onOutgoing({ t: "frame", id: "f1", jpeg: "x", w: 1, h: 1, ts: 0, purpose: "count" }, 100);
    c.onOutgoing({ t: "frame", id: "f2", jpeg: "x", w: 1, h: 1, ts: 0, purpose: "count" }, 1100);
    c.onIncoming({ t: "count", sku: "s", counted: 1, expected: 10, final: false, confidence: 0.9 }, 1900);
    expect(c.samples).toEqual([{ metric: "count_update_ms", value: 800, ts: 1900 }]);
  });
  it("measures guidance time-to-touch and resets on stop", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "target", frameId: "f", label: "curd", point: [500, 500], confidence: 0.8 }, 0);
    c.onIncoming({ t: "target", frameId: "g", label: "curd", point: [510, 490], confidence: 0.8 }, 900);
    c.onOutgoing({ t: "event", name: "fingertip_in_target", data: {} }, 4200);
    expect(c.samples).toEqual([{ metric: "guidance_time_to_touch_ms", value: 4200, ts: 4200 }]);
    c.onIncoming({ t: "target", frameId: "h", label: "milk", point: [1, 1], confidence: 0.8 }, 5000);
    c.onOutgoing({ t: "stop" }, 5100);
    c.onOutgoing({ t: "event", name: "fingertip_in_target", data: {} }, 9000);
    expect(c.samples.length).toBe(1);
  });
  it("pairs sales by id; void and unmatched sales produce nothing (Review Focus #4)", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "state", entity: "sale", id: "a", state: "SALE_PENDING", summary: "" }, 0);
    c.onIncoming({ t: "state", entity: "sale", id: "b", state: "SALE_PENDING", summary: "" }, 1000);
    c.onIncoming({ t: "state", entity: "sale", id: "b", state: "SALE_COMPLETED", summary: "" }, 21000);
    c.onIncoming({ t: "state", entity: "sale", id: "a", state: "SALE_VOID", summary: "" }, 22000);
    c.onIncoming({ t: "state", entity: "sale", id: "z", state: "SALE_COMPLETED", summary: "" }, 23000);
    expect(c.samples).toEqual([{ metric: "sale_e2e_ms", value: 20000, ts: 21000 }]);
  });
});

describe("battery", () => {
  it("computes %/hour over the trailing non-charging run of ≥15 minutes", () => {
    const h = 3_600_000;
    expect(drainPerHour([
      { level: 1.0, charging: true, ts: 0 },
      { level: 0.9, charging: false, ts: 0.1 * h },
      { level: 0.84, charging: false, ts: 1.1 * h },
    ])).toBe(6);
  });
  it("returns null for under 15 minutes of data", () => {
    expect(drainPerHour([{ level: 1, charging: false, ts: 0 }, { level: 0.99, charging: false, ts: 60_000 }])).toBeNull();
  });
  it("is a no-op where the Battery API is missing (Review Focus #5)", async () => {
    const stop = await startBatteryLogger({} as Navigator, 10, () => { throw new Error("should not sample"); });
    expect(typeof stop).toBe("function");
    stop();
  });
});

describe("export", () => {
  it("nearest-rank percentiles", () => {
    expect(percentile([5, 1, 3, 2, 4], 50)).toBe(3);
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95)).toBe(10);
    expect(percentile([], 50)).toBeNull();
  });
  it("NDJSON rows match the aira.latency_metrics schema", () => {
    const out = toNdjson([{ metric: "first_audio_ms", value: 650, ts: Date.UTC(2026, 9, 15, 10, 0, 0) }], "Redmi Note 13", "0.1.0");
    expect(JSON.parse(out.trim())).toEqual({
      metric: "first_audio_ms", value: 650, unit: "ms", device: "Redmi Note 13", build: "0.1.0", ts: "2026-10-15T10:00:00.000Z",
    });
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm test -w apps/app -- bench`
Expected: FAIL with unresolved imports for `collector`, `battery` and `export`.

- [ ] **Step 3: Implement `collector.ts`, `battery.ts`, `export.ts`**

```ts
// apps/app/src/bench/collector.ts
import type { ClientMsg, ServerMsg } from "@aira/contracts";

export type Metric = "first_audio_ms" | "count_update_ms" | "guidance_time_to_touch_ms" | "sale_e2e_ms" | "battery_drain_pct_per_hour";
export interface Sample { metric: Metric; value: number; ts: number }

export class BenchCollector {
  readonly samples: Sample[] = [];
  private userTurnEndAt: number | null = null;
  private lastCountFrameAt: number | null = null;
  private guidanceStart: number | null = null;
  private salePending = new Map<string, number>();

  private push(metric: Metric, value: number, ts: number) {
    this.samples.push({ metric, value, ts });
  }

  onOutgoing(m: ClientMsg, now: number): void {
    if (m.t === "frame" && m.purpose === "count") this.lastCountFrameAt = now;
    else if (m.t === "event" && m.name === "fingertip_in_target" && this.guidanceStart !== null) {
      this.push("guidance_time_to_touch_ms", now - this.guidanceStart, now);
      this.guidanceStart = null;
    } else if (m.t === "stop") {
      this.guidanceStart = null;
      this.userTurnEndAt = null;
    }
  }

  onIncoming(m: ServerMsg, now: number): void {
    switch (m.t) {
      case "transcript":
        if (m.role === "user" && m.final) this.userTurnEndAt = now;
        break;
      case "audio":
        if (this.userTurnEndAt !== null) {
          this.push("first_audio_ms", now - this.userTurnEndAt, now);
          this.userTurnEndAt = null;
        }
        break;
      case "count":
        if (this.lastCountFrameAt !== null) {
          this.push("count_update_ms", now - this.lastCountFrameAt, now);
          this.lastCountFrameAt = null;
        }
        break;
      case "target":
        if (this.guidanceStart === null) this.guidanceStart = now;
        break;
      case "state":
        if (m.entity !== "sale") break;
        if (m.state === "SALE_PENDING") this.salePending.set(m.id, now);
        else if (m.state === "SALE_COMPLETED") {
          const start = this.salePending.get(m.id);
          if (start !== undefined) this.push("sale_e2e_ms", now - start, now);
          this.salePending.delete(m.id);
        } else if (m.state === "SALE_VOID") this.salePending.delete(m.id);
        break;
      case "cue":
        if (m.cue === "interrupted") this.userTurnEndAt = null;
        break;
    }
  }
}
```

```ts
// apps/app/src/bench/battery.ts
export interface BatterySample { level: number; charging: boolean; ts: number }

/** % drained per hour over the trailing run of non-charging samples; needs ≥ 15 minutes. */
export function drainPerHour(samples: BatterySample[]): number | null {
  let i = samples.length - 1;
  while (i >= 0 && !samples[i].charging) i--;
  const run = samples.slice(i + 1);
  if (run.length < 2) return null;
  const first = run[0];
  const last = run[run.length - 1];
  const hours = (last.ts - first.ts) / 3_600_000;
  if (hours < 0.25) return null;
  return Math.round(((first.level - last.level) * 100 / hours) * 100) / 100;
}

export async function startBatteryLogger(
  nav: Navigator = navigator,
  everyMs = 60_000,
  onSample: (s: BatterySample) => void = () => {},
): Promise<() => void> {
  const get = (nav as any).getBattery;
  if (typeof get !== "function") return () => {};
  const battery = await get.call(nav);
  const sample = () => onSample({ level: battery.level, charging: battery.charging, ts: Date.now() });
  sample();
  const id = setInterval(sample, everyMs);
  return () => clearInterval(id);
}
```

```ts
// apps/app/src/bench/export.ts
import type { Sample } from "./collector";

export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.min(sorted.length, Math.max(1, rank)) - 1];
}

/** One JSON row per sample; matches BigQuery aira.latency_metrics (metric, value, unit, device, build, ts). */
export function toNdjson(samples: Sample[], device: string, build: string): string {
  return samples.map((s) => JSON.stringify({
    metric: s.metric,
    value: s.value,
    unit: s.metric === "battery_drain_pct_per_hour" ? "pct_per_hour" : "ms",
    device, build,
    ts: new Date(s.ts).toISOString(),
  })).join("\n") + "\n";
}
```

- [ ] **Step 4: Implement `install.ts` and `BenchPage.tsx`**

```ts
// apps/app/src/bench/install.ts
import { app } from "../core/app";
import { BenchCollector, type Sample } from "./collector";

const KEY = "aira.bench.samples";
export const collector = new BenchCollector();

export function loadSaved(): Sample[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]"); } catch { return []; }
}

function save() {
  try { localStorage.setItem(KEY, JSON.stringify([...loadSaved(), ...collector.samples.splice(0)].slice(-5000))); } catch { /* storage full or blocked: keep in memory */ }
}

/** Observes all socket traffic without changing it. Call once at startup when bench mode is on. */
export function installBench(): () => void {
  const off = app.socket.on((m) => { collector.onIncoming(m, Date.now()); save(); });
  const originalSend = app.send.bind(app);
  app.send = (m) => { collector.onOutgoing(m, Date.now()); return originalSend(m); };
  return () => { off(); app.send = originalSend; };
}
```

The collector uses `Date.now()` for both start and end of each measurement. Millisecond resolution is enough for latencies in the hundreds of milliseconds. It also means each sample's `ts` is already wall-clock time, so samples stay correct across page reloads.

```tsx
// apps/app/src/bench/BenchPage.tsx
import { useEffect, useState } from "preact/hooks";
import { installBench, loadSaved } from "./install";
import { startBatteryLogger, drainPerHour, type BatterySample } from "./battery";
import { percentile, toNdjson } from "./export";
import type { Sample } from "./collector";

const METRICS = ["first_audio_ms", "count_update_ms", "guidance_time_to_touch_ms", "sale_e2e_ms"] as const;

export function BenchPage() {
  const [device, setDevice] = useState(() => localStorage.getItem("aira.bench.device") ?? "");
  const [samples, setSamples] = useState<Sample[]>(loadSaved());
  const [battery, setBattery] = useState<BatterySample[]>([]);

  useEffect(() => {
    const off = installBench();
    const tick = setInterval(() => setSamples(loadSaved()), 2000);
    let stopBattery = () => {};
    startBatteryLogger(navigator, 60_000, (s) => setBattery((b) => [...b, s])).then((f) => (stopBattery = f));
    return () => { off(); clearInterval(tick); stopBattery(); };
  }, []);

  function exportFile() {
    localStorage.setItem("aira.bench.device", device);
    const drain = drainPerHour(battery);
    const all: Sample[] = [...samples,
      ...(drain === null ? [] : [{ metric: "battery_drain_pct_per_hour" as const, value: drain, ts: Date.now() }])];
    const blob = new Blob([toNdjson(all, device || "unknown", "0.1.0")], { type: "application/x-ndjson" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `aira-bench-${(device || "unknown").replace(/\W+/g, "-")}-${Date.now()}.ndjson`;
    a.click();
  }

  return (
    <main style={{ padding: 16, fontFamily: "system-ui" }}>
      <h1>AIRA bench</h1>
      <label>Device name <input value={device} onInput={(e) => setDevice(e.currentTarget.value)} placeholder="Redmi Note 13" /></label>
      <table>
        <thead><tr><th>metric</th><th>n</th><th>p50</th><th>p95</th></tr></thead>
        <tbody>
          {METRICS.map((m) => {
            const v = samples.filter((s) => s.metric === m).map((s) => s.value);
            return <tr><td>{m}</td><td>{v.length}</td><td>{percentile(v, 50) ?? "-"}</td><td>{percentile(v, 95) ?? "-"}</td></tr>;
          })}
          <tr><td>battery_drain_pct_per_hour</td><td>{battery.length}</td><td colSpan={2}>{drainPerHour(battery) ?? "need ≥15 min unplugged"}</td></tr>
        </tbody>
      </table>
      <button onClick={exportFile}>Export NDJSON</button>
      <button onClick={() => { localStorage.removeItem("aira.bench.samples"); setSamples([]); }}>Clear</button>
    </main>
  );
}
```

To measure:
1. On the test phone, open `/bench` in one tab and keep it open. The bench page installs the collector on the shared socket; run the scenarios in the main app in the same tab session (use the in-app link back to `/`).
2. Repeat each metric at least 20 times.
3. Unplug the phone for at least 15 minutes for battery.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npm test -w apps/app -- bench`
Expected: PASS, 10 tests.

- [ ] **Step 6: Commit**

```bash
git add apps/app/src/bench apps/app/test/bench.test.ts apps/app/src/main.tsx
git commit -m "feat(bench): in-app latency collector (first audio, count, guidance, sale), battery drain, NDJSON export"
```

---

### Task 6: `bench/publish.py` — p50/p95 CSV and BigQuery load

**Decision:** benchmark rows go to BigQuery with **`bq load` into `aira.latency_metrics`**, not through Pub/Sub. The Pub/Sub topics in the interfaces file (`sales`, `inventory-movements`, `orders`, `discrepancies`, `alerts`) carry live business events. A batch file load is simpler, free of streaming cost, and keeps bench data out of the alert pipeline.

**Files:**
- Create: `bench/pyproject.toml`, `bench/publish.py`, `bench/tests/test_publish.py`
- Modify: `.gitignore` (append `bench/out/`)

**Interfaces:**
- Consumes:
  - NDJSON from Task 5
  - BigQuery dataset `aira` (created by `saravana/02-cloud-data-analytics`)
- Produces:
  - `load_ndjson(paths: list[str]) -> tuple[list[dict], int]` (rows, skipped)
  - `summarize(rows: list[dict]) -> list[dict]`
  - `write_csv(summary: list[dict], path: str) -> None`
  - `bq_load_cmd(path: str, table: str = "aira.latency_metrics") -> list[str]`
  - CLI `uv run python publish.py <files…> [--csv out/summary.csv] [--load]`

- [ ] **Step 1: Create `bench/pyproject.toml`**

```toml
[project]
name = "aira-bench"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["pytest>=8.3"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 2: Write the failing tests**

```python
# bench/tests/test_publish.py
import csv
import json
from publish import load_ndjson, summarize, write_csv, bq_load_cmd, percentile

def _write(tmp_path, rows):
    p = tmp_path / "a.ndjson"
    p.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n")
    return str(p)

ROW = {"metric": "first_audio_ms", "value": 650, "unit": "ms", "device": "Redmi Note 13", "build": "0.1.0", "ts": "2026-10-15T10:00:00.000Z"}

def test_percentile_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile(list(range(1, 11)), 95) == 10
    assert percentile([], 50) is None

def test_load_skips_invalid_rows(tmp_path):
    path = _write(tmp_path, [ROW, {"metric": "x"}, "not json", {**ROW, "value": "fast"}])
    rows, skipped = load_ndjson([path])
    assert rows == [ROW] and skipped == 3

def test_summarize_per_metric_and_device(tmp_path):
    rows = [{**ROW, "value": v} for v in [600, 700, 800, 900, 1000]] + [{**ROW, "metric": "sale_e2e_ms", "value": 20000}]
    s = summarize(rows)
    fa = next(x for x in s if x["metric"] == "first_audio_ms")
    assert fa == {"metric": "first_audio_ms", "device": "Redmi Note 13", "unit": "ms", "n": 5, "p50": 800, "p95": 1000, "mean": 800.0}

def test_write_csv(tmp_path):
    out = tmp_path / "out" / "summary.csv"
    write_csv([{"metric": "first_audio_ms", "device": "d", "unit": "ms", "n": 1, "p50": 1, "p95": 1, "mean": 1.0}], str(out))
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["metric"] == "first_audio_ms" and rows[0]["p95"] == "1"

def test_bq_load_command():
    assert bq_load_cmd("bench/out/run.ndjson") == [
        "bq", "load", "--source_format=NEWLINE_DELIMITED_JSON", "aira.latency_metrics", "bench/out/run.ndjson"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd bench && uv sync && uv run pytest -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'publish'`.

- [ ] **Step 4: Implement `bench/publish.py`**

```python
"""Summarize AIRA bench NDJSON (p50/p95 per metric+device) and load raw rows into BigQuery aira.latency_metrics."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
from datetime import datetime

REQUIRED = {"metric": str, "unit": str, "device": str, "build": str, "ts": str}

def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    rank = max(1, min(len(s), math.ceil(p / 100 * len(s))))
    return s[rank - 1]

def _valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for k, t in REQUIRED.items():
        if not isinstance(row.get(k), t):
            return False
    if isinstance(row.get("value"), bool) or not isinstance(row.get("value"), (int, float)):
        return False
    try:
        datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return True

def load_ndjson(paths: list[str]) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if _valid(row):
                    rows.append(row)
                else:
                    skipped += 1
    return rows, skipped

def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[float]] = {}
    for r in rows:
        groups.setdefault((r["metric"], r["device"], r["unit"]), []).append(float(r["value"]))
    out = []
    for (metric, device, unit), vals in sorted(groups.items()):
        p50, p95 = percentile(vals, 50), percentile(vals, 95)
        out.append({
            "metric": metric, "device": device, "unit": unit, "n": len(vals),
            "p50": int(p50) if p50 is not None and p50.is_integer() else p50,
            "p95": int(p95) if p95 is not None and p95.is_integer() else p95,
            "mean": round(statistics.fmean(vals), 2),
        })
    return out

def write_csv(summary: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["metric", "device", "unit", "n", "p50", "p95", "mean"])
        w.writeheader()
        w.writerows(summary)

def bq_load_cmd(path: str, table: str = "aira.latency_metrics") -> list[str]:
    return ["bq", "load", "--source_format=NEWLINE_DELIMITED_JSON", table, path]

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--csv", default="out/summary.csv")
    ap.add_argument("--load", action="store_true", help="bq load the valid rows into aira.latency_metrics")
    args = ap.parse_args(argv)

    rows, skipped = load_ndjson(args.files)
    summary = summarize(rows)
    write_csv(summary, args.csv)
    for s in summary:
        print(f"{s['metric']:<28} {s['device']:<18} n={s['n']:<4} p50={s['p50']} p95={s['p95']} {s['unit']}")
    print(f"valid={len(rows)} skipped={skipped} csv={args.csv}")

    if args.load and rows:
        clean = os.path.join(os.path.dirname(args.csv) or ".", "load.ndjson")
        with open(clean, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        return subprocess.call(bq_load_cmd(clean))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd bench && uv run pytest -q`
Expected: `5 passed`.

- [ ] **Step 6: Create the table if missing and load a real run**

```bash
bq show aira.latency_metrics >/dev/null 2>&1 || \
  bq mk --table aira.latency_metrics metric:STRING,value:FLOAT,unit:STRING,device:STRING,build:STRING,ts:TIMESTAMP
cd bench && uv run python publish.py ~/Downloads/aira-bench-*.ndjson --csv out/summary.csv --load
```

Expected: one line per metric with `n`, `p50` and `p95`; `valid=… skipped=0`; and `bq` prints `Upload complete` and `DONE`. `bench/out/summary.csv` is the source for the deck's benchmark slide.

Append `bench/out/` to the root `.gitignore`.

- [ ] **Step 7: Commit**

```bash
git add bench/pyproject.toml bench/uv.lock bench/publish.py bench/tests/test_publish.py .gitignore
git commit -m "feat(bench): publish.py — p50/p95 CSV summary and bq load into aira.latency_metrics"
```

---

## Done when

- **Laptop demo:** `https://<host>/try` loads on a laptop with no sign-in, and all 10 cards drive the real `aira-live` demo shop. The invoice-wrong card produces a discrepancy line, and the UPI card produces a matched soundbox payment.
- **Install:** the app installs from Chrome on Android, and the APK opens full-screen with no URL bar.
- **Benchmarks:** `bench/out/summary.csv` holds at least 20 samples each for the four latency metrics on the named test phone, plus the battery drain line, and the same rows are in BigQuery `aira.latency_metrics`.
