# AIRA App Shell + Voice I/O Implementation Plan (Ishwarya · 01)

> **Addendum (2026-10-10): family phones get alerts.** When the app is opened with role `family` (Settings → "I'm family"), it requests notification permission and calls `POST /fcm/register` with `{idToken, fcmToken}`. This is served by aira-live (`saravana/02`) and uses the Firebase Messaging web SDK + a service worker. Low-stock, expiry and day-summary alerts then arrive as push notifications. Add one vitest test for the register-request builder.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the AIRA phone app core. Murugan, who is blind and wears the phone on his chest, should be able to:
- start the app with one tap
- talk to AIRA by double-tap
- hear AIRA through his earphone, and interrupt it
- confirm orders, payments and sales with a double-tap
- always know whether he is online.

All of it must work with TalkBack on or off.

**Architecture:**
- `apps/app` (Vite + TypeScript + Preact, created by the foundation plan) gets a `core/` layer:
  - `app.ts`: the `app` singleton from the interfaces file
  - settings, haptics, a thinking timer, an announcer, an offline queue, auth
- An `audio/` layer:
  - a 16 kHz PCM16 mic pipeline through an AudioWorklet
  - 24 kHz playback with flush on barge-in
  - synthesized earcons
- `ui/` components: onboarding, screen-reader region, gesture surface.
- Every decision is a **pure function**, unit-tested with Vitest. Browser APIs are thin adapters injected into `createApp(deps)`.

**Tech Stack:** TypeScript 5, Preact 10, Vite 5, Vitest 2 (jsdom), Web Audio API (AudioWorklet), MediaDevices, Screen Wake Lock, Vibration API, Web Speech `speechSynthesis` (fallback only), IndexedDB, Firebase JS SDK v10+ (Auth).

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md`. Interfaces: `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

**Prerequisite:** `docs/superpowers/plans/2026-10-10-00-foundation.md` is merged. This plan assumes it created:
- `apps/app` with `src/net/socket.ts`, exporting `AiraSocket` (`connect(hello)`, `send(msg): boolean`, `on(handler): () => void`, `close()`) and `backoffMs`
- `src/ui/GestureSurface.tsx` (props `label`, `onTalk`, `onStop`)
- `@aira/contracts`, exporting `ClientMsg`, `ServerMsg`, `CueName`, `Lang`, `Verbosity`, `FramePurpose` and `isServerMsg`

If a name differs, use the foundation's name and keep everything else the same.

## Global Constraints

- Product name in all user-facing copy: **AIRA**. Languages: `"ta" | "hi" | "en"`. Every user-facing string exists in all three.
- **Audio formats:**
  - Mic → server: **16 kHz mono PCM16, little-endian, base64**, in chunks of **20–40 ms** (we use 32 ms = 512 samples).
  - Server → playback: **24 kHz mono PCM16 base64**.
- **Always use the built-in microphone** (never a Bluetooth headset mic), so Bluetooth earphones stay in stereo A2DP mode.
- **Gestures:**
  - Double-tap (< 350 ms between taps) = talk, or **yes** when a confirmation is pending.
  - Long-press (≥ 600 ms) = hard stop, or **no** when a confirmation is pending.
  - No swipe gestures.
- **Timing:**
  - Acknowledge every trigger within **150 ms** (earcon + haptic).
  - A soft thinking sound starts after **1.5 s** without a server response.
  - On cue `interrupted`, flush all queued and playing AIRA audio immediately.
- **No double speech.** Gemini Live audio is the primary voice. The client speaks (or writes to the aria-live region in screen-reader mode) only for local status (network, offline), in mock sessions, or when an `error` or `confirm` arrives with no Live audio in the last 1 s.
- `app` matches the interfaces file exactly: `{ socket, settings, speak(text), earcon(n: CueName), haptic(n: "ack"|"warn"|"done"|"match"|"mismatch"), send(m) }`.
- **Never commit secrets.** Firebase web config comes from `VITE_FIREBASE_*` env vars.

## Review Focus

1. **Barge-in while audio is still queued:** after cue `interrupted`, no buffered AIRA audio may play, including chunks that arrive in the same tick. A test in Task 3 pins this.
2. **Double-tap during a pending confirmation:** it must send `{t:"confirm", id, answer:"yes"}` exactly once and must **not** start a new talk turn. Long-press must send `answer:"no"`. Tested in Task 5.
3. **No Bluetooth mic when only a headset mic and a built-in mic exist:** the picker must return the built-in one, whatever the device order. If there's no clearly built-in device, it returns `"default"`. Tested in Task 2.
4. **The offline queue must never drop or duplicate a pending op** across reload or replay. Each op keeps its `idemKey`, so the server dedupes. Tested in Task 6.
5. **Thinking sound must not play over AIRA's speech:** any `audio`, `transcript(role:"aira")`, `cue` or `error` disarms the 1.5 s timer. Tested in Task 4.

---

## File Structure

```
apps/app/
  src/core/types.ts          Settings, HapticName, AppDeps, AppApi
  src/core/settings.ts       load/save Settings (localStorage), defaults, validation
  src/core/app.ts            `app` singleton + createApp(deps) + initApp(deps)
  src/core/haptics.ts        haptic pattern table + vibrate adapter (no-op fallback)
  src/core/thinking.ts       ThinkingTimer (1.5 s arm/disarm)
  src/core/announce.ts       shouldClientSpeak(), textFor(msg, lang) — pure
  src/core/confirm.ts        PendingConfirm state + gesture → ClientMsg mapping — pure
  src/core/offline-queue.ts  OfflineQueue (pure logic) + IndexedDB adapter
  src/core/auth.ts           Firebase anonymous auth → idToken
  src/core/dispatch.ts       routes ServerMsg to playback/earcons/announcer/confirm
  src/i18n/strings.ts        local status strings (ta/hi/en)
  src/audio/pcm.ts           resampling + PCM16 + base64 helpers — pure
  src/audio/mic-worklet.ts   AudioWorkletProcessor (posts Float32 frames)
  src/audio/mic.ts           pickBuiltInMic() (pure) + startMic()
  src/audio/playback.ts      PlaybackQueue (24 kHz) with flush()
  src/audio/earcons.ts       EARCONS table (pure) + playEarcon()
  src/ui/ScreenReaderRegion.tsx   single aria-live polite region
  src/ui/Onboarding.tsx      one "Start" tap: unlock + permissions + language + shop
  src/main.tsx               (modify) wire everything
  test/settings.test.ts  test/pcm.test.ts  test/mic.test.ts  test/playback.test.ts
  test/earcons-thinking.test.ts  test/announce-confirm.test.ts  test/offline-queue.test.ts
```

---

### Task 1: Settings, core types and the `app` singleton

**Files:**
- Create: `apps/app/src/core/types.ts`, `apps/app/src/core/settings.ts`, `apps/app/src/core/app.ts`, `apps/app/src/core/haptics.ts`
- Test: `apps/app/test/settings.test.ts`

**Interfaces:**
- Consumes: `AiraSocket` (foundation), `ClientMsg`, `CueName`, `Lang`, `Verbosity` from `@aira/contracts`.
- Produces:
  - `Settings`: `{ lang: Lang; verbosity: Verbosity; screenReaderMode: boolean; speechRate: number; shopId: string; onboarded: boolean }`
  - `HapticName = "ack" | "warn" | "done" | "match" | "mismatch"`
  - `loadSettings(store?: Storage): Settings`, `saveSettings(s: Settings, store?: Storage): void`
  - `app: AppApi` exactly as in the interfaces file, plus `initApp(deps: AppDeps): AppApi`
  - `HAPTIC_PATTERNS: Record<HapticName, number[]>`, `vibrate(name, nav?)`

- [ ] **Step 1: Write the failing test** `apps/app/test/settings.test.ts`

```ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { loadSettings, saveSettings, DEFAULT_SETTINGS } from "../src/core/settings";
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
  });

  it("round-trips and clamps speechRate to 0.5–2", () => {
    saveSettings({ ...DEFAULT_SETTINGS, lang: "ta", speechRate: 9 }, store);
    const s = loadSettings(store);
    expect(s.lang).toBe("ta");
    expect(s.speechRate).toBe(2);
  });

  it("ignores corrupt JSON and unknown languages", () => {
    store.setItem("aira.settings", "{not json");
    expect(loadSettings(store)).toEqual(DEFAULT_SETTINGS);
    store.setItem("aira.settings", JSON.stringify({ lang: "fr" }));
    expect(loadSettings(store).lang).toBe("en");
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
});

describe("app singleton", () => {
  it("initApp fills the exported `app` object with the interface methods", () => {
    const sent: unknown[] = [];
    initApp({
      socket: { send: (m: unknown) => { sent.push(m); return true; } } as any,
      settings: { ...DEFAULT_SETTINGS },
      speakImpl: vi.fn(), earconImpl: vi.fn(), hapticImpl: vi.fn(),
    });
    expect(app.send({ t: "stop" })).toBe(true);
    expect(sent).toEqual([{ t: "stop" }]);
    app.earcon("match"); app.haptic("done"); app.speak("hello");
    expect(typeof app.settings.lang).toBe("string");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/app -- settings`
Expected: FAIL with `Failed to resolve import "../src/core/settings"`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/app/src/core/types.ts
import type { ClientMsg, CueName, Lang, Verbosity } from "@aira/contracts";
import type { AiraSocket } from "../net/socket";

export type HapticName = "ack" | "warn" | "done" | "match" | "mismatch";

export interface Settings {
  lang: Lang;
  verbosity: Verbosity;
  screenReaderMode: boolean;
  speechRate: number; // 0.5–2.0, applies to client-spoken text
  shopId: string;     // "demo-murugan-dairy" by default
  onboarded: boolean;
}

export interface AppDeps {
  socket: Pick<AiraSocket, "send">;
  settings: Settings;
  speakImpl: (text: string, s: Settings) => void;
  earconImpl: (n: CueName) => void;
  hapticImpl: (n: HapticName) => void;
}

export interface AppApi {
  socket: AiraSocket;
  settings: Settings;
  speak(text: string): void;
  earcon(n: CueName): void;
  haptic(n: HapticName): void;
  send(m: ClientMsg): boolean;
}
```

```ts
// apps/app/src/core/settings.ts
import type { Settings } from "./types";

const KEY = "aira.settings";
const LANGS = ["ta", "hi", "en"] as const;

export const DEFAULT_SETTINGS: Settings = {
  lang: "en",
  verbosity: "short",
  screenReaderMode: false,
  speechRate: 1,
  shopId: "demo-murugan-dairy",
  onboarded: false,
};

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

export function loadSettings(store: Storage = localStorage): Settings {
  let raw: unknown;
  try { raw = JSON.parse(store.getItem(KEY) ?? "null"); } catch { return { ...DEFAULT_SETTINGS }; }
  if (!raw || typeof raw !== "object") return { ...DEFAULT_SETTINGS };
  const r = raw as Partial<Settings>;
  return {
    lang: LANGS.includes(r.lang as any) ? (r.lang as Settings["lang"]) : DEFAULT_SETTINGS.lang,
    verbosity: r.verbosity === "detail" ? "detail" : "short",
    screenReaderMode: r.screenReaderMode === true,
    speechRate: typeof r.speechRate === "number" ? clamp(r.speechRate, 0.5, 2) : 1,
    shopId: typeof r.shopId === "string" && r.shopId ? r.shopId : DEFAULT_SETTINGS.shopId,
    onboarded: r.onboarded === true,
  };
}

export function saveSettings(s: Settings, store: Storage = localStorage): void {
  store.setItem(KEY, JSON.stringify({ ...s, speechRate: clamp(s.speechRate, 0.5, 2) }));
}
```

```ts
// apps/app/src/core/haptics.ts
import type { HapticName } from "./types";

export const HAPTIC_PATTERNS: Record<HapticName, number[]> = {
  ack: [15],
  warn: [80, 60, 80],
  done: [30, 40, 30],
  match: [20, 30, 20],
  mismatch: [200, 100, 200],
};

/** Returns false (no-op) where the Vibration API is missing, e.g. iOS Safari. */
export function vibrate(name: HapticName, nav: Navigator = navigator): boolean {
  const v = (nav as Navigator & { vibrate?: (p: number[]) => boolean }).vibrate;
  if (typeof v !== "function") return false;
  return v.call(nav, HAPTIC_PATTERNS[name]);
}
```

```ts
// apps/app/src/core/app.ts
import type { AppApi, AppDeps } from "./types";

/** The interfaces-file singleton. Empty until initApp() runs (in main.tsx or a test). */
export const app = {} as AppApi;

export function initApp(deps: AppDeps): AppApi {
  const api: AppApi = {
    socket: deps.socket as AppApi["socket"],
    settings: deps.settings,
    speak: (text) => deps.speakImpl(text, api.settings),
    earcon: (n) => deps.earconImpl(n),
    haptic: (n) => deps.hapticImpl(n),
    send: (m) => deps.socket.send(m),
  };
  return Object.assign(app, api);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -w apps/app -- settings`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/core/types.ts apps/app/src/core/settings.ts apps/app/src/core/app.ts apps/app/src/core/haptics.ts apps/app/test/settings.test.ts
git commit -m "feat(app): settings store, haptics and the app singleton"
```

---

### Task 2: Mic pipeline: built-in mic picker, 16 kHz PCM16 chunks, AudioWorklet

**Files:**
- Create: `apps/app/src/audio/pcm.ts`, `apps/app/src/audio/mic.ts`, `apps/app/src/audio/mic-worklet.ts`
- Test: `apps/app/test/pcm.test.ts`, `apps/app/test/mic.test.ts`

**Interfaces:**
- Consumes: `app.send` (Task 1)
- Produces:
  - `resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array`
  - `floatToPcm16(f: Float32Array): Int16Array`, `int16ToBase64(i: Int16Array): string`, `base64ToInt16(b64: string): Int16Array`, `int16ToFloat32(i: Int16Array): Float32Array`
  - `class Chunker { constructor(samplesPerChunk: number); push(f: Float32Array): Float32Array[]; flush(): Float32Array | null }`
  - `pickBuiltInMic(devices: MediaDeviceInfo[]): string`
  - `startMic(opts: { onChunk(b64: string): void }): Promise<{ stop(): void; ctx: AudioContext }>`

- [ ] **Step 1: Write the failing tests**

```ts
// apps/app/test/pcm.test.ts
import { describe, it, expect } from "vitest";
import { resampleLinear, floatToPcm16, int16ToBase64, base64ToInt16, int16ToFloat32, Chunker } from "../src/audio/pcm";

describe("pcm helpers", () => {
  it("resamples 48 kHz to 16 kHz with the right length", () => {
    const one = new Float32Array(48000).fill(0.5);
    const out = resampleLinear(one, 48000, 16000);
    expect(out.length).toBe(16000);
    expect(out[100]).toBeCloseTo(0.5, 5);
  });

  it("is identity when rates match", () => {
    const a = new Float32Array([0.1, -0.2, 0.3]);
    expect(Array.from(resampleLinear(a, 16000, 16000))).toEqual(Array.from(a));
  });

  it("clips floats into PCM16 range", () => {
    const pcm = floatToPcm16(new Float32Array([1.5, -1.5, 0, 0.5]));
    expect(Array.from(pcm)).toEqual([32767, -32768, 0, 16384]);
  });

  it("base64 round-trips little-endian int16", () => {
    const i = new Int16Array([1, -1, 32767, -32768, 1234]);
    expect(Array.from(base64ToInt16(int16ToBase64(i)))).toEqual(Array.from(i));
  });

  it("int16ToFloat32 maps back to [-1, 1)", () => {
    const f = int16ToFloat32(new Int16Array([32767, -32768, 0]));
    expect(f[0]).toBeCloseTo(0.99997, 4);
    expect(f[1]).toBe(-1);
    expect(f[2]).toBe(0);
  });

  it("Chunker emits exact 512-sample chunks and keeps the remainder", () => {
    const c = new Chunker(512);
    expect(c.push(new Float32Array(300))).toHaveLength(0);
    const out = c.push(new Float32Array(800)); // 1100 total → 2 chunks, 76 left
    expect(out.map((x) => x.length)).toEqual([512, 512]);
    expect(c.flush()!.length).toBe(76);
    expect(c.flush()).toBeNull();
  });
});
```

```ts
// apps/app/test/mic.test.ts
import { describe, it, expect } from "vitest";
import { pickBuiltInMic } from "../src/audio/mic";

const dev = (deviceId: string, label: string): MediaDeviceInfo =>
  ({ deviceId, label, kind: "audioinput", groupId: "g", toJSON() { return {}; } }) as MediaDeviceInfo;

describe("pickBuiltInMic", () => {
  it("prefers the built-in mic even when the Bluetooth headset comes first", () => {
    const id = pickBuiltInMic([dev("bt1", "boAt Rockerz 255 (Bluetooth)"), dev("int1", "Built-in microphone")]);
    expect(id).toBe("int1");
  });
  it("recognises common Android labels for the phone mic", () => {
    expect(pickBuiltInMic([dev("h", "Headset earpiece"), dev("p", "Phone microphone")])).toBe("p");
    expect(pickBuiltInMic([dev("b", "Bluetooth SCO"), dev("s", "Speakerphone")])).toBe("s");
  });
  it("never picks a bluetooth/headset device; falls back to 'default'", () => {
    expect(pickBuiltInMic([dev("bt", "Bluetooth headset")])).toBe("default");
    expect(pickBuiltInMic([])).toBe("default");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -w apps/app -- pcm mic`
Expected: FAIL with `Failed to resolve import "../src/audio/pcm"`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/app/src/audio/pcm.ts
export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input.slice();
  const ratio = fromRate / toRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

export function floatToPcm16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) {
    const s = Math.max(-1, Math.min(1, f[i]));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out;
}

export function int16ToFloat32(i: Int16Array): Float32Array {
  const out = new Float32Array(i.length);
  for (let k = 0; k < i.length; k++) out[k] = i[k] < 0 ? i[k] / 32768 : i[k] / 32767;
  return out;
}

export function int16ToBase64(i: Int16Array): string {
  const bytes = new Uint8Array(i.length * 2);
  const view = new DataView(bytes.buffer);
  i.forEach((v, k) => view.setInt16(k * 2, v, true));
  let bin = "";
  for (let k = 0; k < bytes.length; k++) bin += String.fromCharCode(bytes[k]);
  return btoa(bin);
}

export function base64ToInt16(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
  const view = new DataView(bytes.buffer);
  const out = new Int16Array(Math.floor(bytes.length / 2));
  for (let k = 0; k < out.length; k++) out[k] = view.getInt16(k * 2, true);
  return out;
}

/** Accumulates float samples and emits fixed-size chunks (512 samples @16 kHz = 32 ms). */
export class Chunker {
  private buf: number[] = [];
  constructor(private samplesPerChunk: number) {}
  push(f: Float32Array): Float32Array[] {
    for (let i = 0; i < f.length; i++) this.buf.push(f[i]);
    const out: Float32Array[] = [];
    while (this.buf.length >= this.samplesPerChunk) {
      out.push(Float32Array.from(this.buf.splice(0, this.samplesPerChunk)));
    }
    return out;
  }
  flush(): Float32Array | null {
    if (!this.buf.length) return null;
    const out = Float32Array.from(this.buf);
    this.buf = [];
    return out;
  }
}
```

```ts
// apps/app/src/audio/mic-worklet.ts — runs in AudioWorkletGlobalScope
// Posts each 128-sample render quantum (Float32, at ctx.sampleRate) to the main thread.
declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor { readonly port: MessagePort; constructor(); }

class MicTap extends AudioWorkletProcessor {
  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (ch && ch.length) this.port.postMessage({ samples: ch.slice(0), rate: sampleRate });
    return true;
  }
}
registerProcessor("aira-mic-tap", MicTap);
```

```ts
// apps/app/src/audio/mic.ts
import { Chunker, floatToPcm16, int16ToBase64, resampleLinear } from "./pcm";
// Vite bundles the worklet as a separate module URL. Verify the `?worker&url` suffix against
// https://vitejs.dev/guide/features.html#import-with-query-suffixes for your Vite version.
import workletUrl from "./mic-worklet.ts?worker&url";

const BAD = /(bluetooth|headset|sco|hands-?free|airpods|buds|rockerz)/i;
const GOOD = /(built-?in|phone|internal|speakerphone|bottom|microphone)/i;

/** Pure: choose the phone's own mic so Bluetooth earphones stay in stereo (A2DP). */
export function pickBuiltInMic(devices: MediaDeviceInfo[]): string {
  const inputs = devices.filter((d) => d.kind === "audioinput" && !BAD.test(d.label));
  const good = inputs.find((d) => GOOD.test(d.label));
  return good ? good.deviceId : "default";
}

export async function startMic(opts: { onChunk(b64: string): void }): Promise<{ stop(): void; ctx: AudioContext }> {
  // Labels are empty until permission is granted, so ask once and then enumerate.
  const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
  probe.getTracks().forEach((t) => t.stop());
  const deviceId = pickBuiltInMic(await navigator.mediaDevices.enumerateDevices());
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      deviceId: deviceId === "default" ? undefined : { exact: deviceId },
      echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
    },
  });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule(workletUrl);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "aira-mic-tap");
  const chunker = new Chunker(512);
  node.port.onmessage = (e: MessageEvent<{ samples: Float32Array; rate: number }>) => {
    const down = resampleLinear(e.data.samples, e.data.rate, 16000);
    for (const c of chunker.push(down)) opts.onChunk(int16ToBase64(floatToPcm16(c)));
  };
  src.connect(node);
  return {
    ctx,
    stop() {
      node.port.onmessage = null;
      src.disconnect(); node.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      const rest = chunker.flush();
      if (rest) opts.onChunk(int16ToBase64(floatToPcm16(rest)));
    },
  };
}
```

Resampling each 128-sample quantum on its own causes boundary error of at most one sample per quantum. That's inaudible for speech recognition and keeps the worklet stateless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -w apps/app -- pcm mic`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/audio/pcm.ts apps/app/src/audio/mic.ts apps/app/src/audio/mic-worklet.ts apps/app/test/pcm.test.ts apps/app/test/mic.test.ts
git commit -m "feat(app): 16 kHz PCM16 mic pipeline with built-in mic selection"
```

---

### Task 3: Playback queue (24 kHz) with barge-in flush

**Files:**
- Create: `apps/app/src/audio/playback.ts`
- Test: `apps/app/test/playback.test.ts`

**Interfaces:**
- Consumes: `base64ToInt16`, `int16ToFloat32` (Task 2)
- Produces: `class PlaybackQueue { constructor(ctx: AudioContext); enqueue(pcm24b64: string): void; flush(): void; get playing(): boolean; lastAudioAt: number }`

- [ ] **Step 1: Write the failing test** `apps/app/test/playback.test.ts`

```ts
import { describe, it, expect } from "vitest";
import { PlaybackQueue } from "../src/audio/playback";
import { int16ToBase64 } from "../src/audio/pcm";

class FakeSource {
  buffer: any = null; started = false; stopped = false; startAt = -1; onended: (() => void) | null = null;
  connect() {} disconnect() {}
  start(t: number) { this.started = true; this.startAt = t; }
  stop() { this.stopped = true; }
}
class FakeCtx {
  currentTime = 0; destination = {}; sources: FakeSource[] = [];
  createBuffer(_ch: number, len: number, rate: number) {
    return { duration: len / rate, getChannelData: () => new Float32Array(len) };
  }
  createBufferSource() { const s = new FakeSource(); this.sources.push(s); return s; }
}
const chunk = (ms: number) => int16ToBase64(new Int16Array(Math.round((24000 * ms) / 1000)));

describe("PlaybackQueue", () => {
  it("schedules chunks back-to-back without gaps", () => {
    const ctx = new FakeCtx();
    const q = new PlaybackQueue(ctx as unknown as AudioContext);
    q.enqueue(chunk(100)); q.enqueue(chunk(100));
    expect(ctx.sources[0].startAt).toBeCloseTo(0, 5);
    expect(ctx.sources[1].startAt).toBeCloseTo(0.1, 5);
    expect(q.playing).toBe(true);
  });

  it("flush() stops every scheduled source and resets the clock (barge-in)", () => {
    const ctx = new FakeCtx();
    const q = new PlaybackQueue(ctx as unknown as AudioContext);
    q.enqueue(chunk(200)); q.enqueue(chunk(200)); q.enqueue(chunk(200));
    q.flush();
    expect(ctx.sources.every((s) => s.stopped)).toBe(true);
    expect(q.playing).toBe(false);
    ctx.currentTime = 0.05;
    q.enqueue(chunk(100)); // new turn after the interruption starts "now", not after old audio
    expect(ctx.sources.at(-1)!.startAt).toBeCloseTo(0.05, 5);
  });

  it("drops chunks that arrive in the same tick as an interruption until resume()", () => {
    const ctx = new FakeCtx();
    const q = new PlaybackQueue(ctx as unknown as AudioContext);
    q.flush({ holdUntilResume: true });
    q.enqueue(chunk(100));
    expect(ctx.sources).toHaveLength(0);
    q.resume();
    q.enqueue(chunk(100));
    expect(ctx.sources).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/app -- playback`
Expected: FAIL with `Failed to resolve import "../src/audio/playback"`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/app/src/audio/playback.ts
import { base64ToInt16, int16ToFloat32 } from "./pcm";

const RATE = 24000;

export class PlaybackQueue {
  private nextAt = 0;
  private live = new Set<AudioBufferSourceNode>();
  private held = false;
  lastAudioAt = 0; // ctx time of the most recent enqueue (used by the announcer to avoid double speech)

  constructor(private ctx: AudioContext, private out: AudioNode = ctx.destination) {}

  enqueue(pcm24b64: string): void {
    if (this.held) return;
    const f = int16ToFloat32(base64ToInt16(pcm24b64));
    if (!f.length) return;
    const buf = this.ctx.createBuffer(1, f.length, RATE);
    buf.getChannelData(0).set(f);
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.out);
    const startAt = Math.max(this.ctx.currentTime, this.nextAt);
    src.start(startAt);
    this.nextAt = startAt + buf.duration;
    this.lastAudioAt = this.ctx.currentTime;
    this.live.add(src);
    src.onended = () => this.live.delete(src);
  }

  /** Barge-in: stop everything immediately. holdUntilResume drops late chunks of the old turn. */
  flush(opts: { holdUntilResume?: boolean } = {}): void {
    for (const s of this.live) { try { s.stop(); } catch { /* already stopped */ } s.disconnect(); }
    this.live.clear();
    this.nextAt = 0;
    this.held = !!opts.holdUntilResume;
  }

  resume(): void { this.held = false; }

  get playing(): boolean { return this.live.size > 0; }
}
```

The dispatcher (Task 5) calls `flush({holdUntilResume:true})` on cue `interrupted` and `resume()` on the next `transcript(role:"user")`. That handles late chunks of the interrupted turn.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -w apps/app -- playback`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/audio/playback.ts apps/app/test/playback.test.ts
git commit -m "feat(app): 24 kHz playback queue with barge-in flush"
```

---

### Task 4: Earcons for every CueName, plus the 1.5 s thinking timer

**Files:**
- Create: `apps/app/src/audio/earcons.ts`, `apps/app/src/core/thinking.ts`
- Test: `apps/app/test/earcons-thinking.test.ts`

**Interfaces:**
- Consumes: `CueName` (`@aira/contracts`)
- Produces:
  - `EARCONS: Record<CueName, { tones: { hz: number; ms: number }[]; gain: number }>`
  - `playEarcon(ctx: AudioContext, n: CueName): void`
  - `class ThinkingTimer { constructor(onStart: () => void, onStop: () => void, delayMs = 1500); arm(): void; disarm(): void; get active(): boolean }`

- [ ] **Step 1: Write the failing test** `apps/app/test/earcons-thinking.test.ts`

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EARCONS } from "../src/audio/earcons";
import { ThinkingTimer } from "../src/core/thinking";

const ALL = ["ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline"];

describe("earcons", () => {
  it("defines a short earcon for every CueName", () => {
    expect(Object.keys(EARCONS).sort()).toEqual([...ALL].sort());
    for (const k of ALL) {
      const e = (EARCONS as any)[k];
      const total = e.tones.reduce((s: number, t: any) => s + t.ms, 0);
      expect(total).toBeLessThanOrEqual(600);
      expect(e.gain).toBeGreaterThan(0);
    }
  });
  it("match rises and mismatch falls (distinguishable without words)", () => {
    expect(EARCONS.match.tones.at(-1)!.hz).toBeGreaterThan(EARCONS.match.tones[0].hz);
    expect(EARCONS.mismatch.tones.at(-1)!.hz).toBeLessThan(EARCONS.mismatch.tones[0].hz);
  });
});

describe("ThinkingTimer", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("starts the thinking sound only after 1.5 s", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm();
    vi.advanceTimersByTime(1499);
    expect(start).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(start).toHaveBeenCalledOnce();
    expect(t.active).toBe(true);
  });

  it("disarm before 1.5 s means no sound at all", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm(); vi.advanceTimersByTime(800); t.disarm(); vi.advanceTimersByTime(5000);
    expect(start).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("disarm after start stops the sound exactly once", () => {
    const start = vi.fn(), stop = vi.fn();
    const t = new ThinkingTimer(start, stop);
    t.arm(); vi.advanceTimersByTime(2000); t.disarm(); t.disarm();
    expect(stop).toHaveBeenCalledOnce();
    expect(t.active).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/app -- earcons-thinking`
Expected: FAIL with `Failed to resolve import "../src/audio/earcons"`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/app/src/audio/earcons.ts
import type { CueName } from "@aira/contracts";

type Earcon = { tones: { hz: number; ms: number }[]; gain: number };

export const EARCONS: Record<CueName, Earcon> = {
  ack:         { tones: [{ hz: 880, ms: 60 }], gain: 0.25 },
  thinking:    { tones: [{ hz: 440, ms: 120 }, { hz: 494, ms: 120 }], gain: 0.08 },
  done:        { tones: [{ hz: 660, ms: 80 }, { hz: 990, ms: 120 }], gain: 0.25 },
  warn:        { tones: [{ hz: 330, ms: 150 }, { hz: 330, ms: 150 }], gain: 0.35 },
  match:       { tones: [{ hz: 523, ms: 90 }, { hz: 659, ms: 90 }, { hz: 784, ms: 140 }], gain: 0.3 },
  mismatch:    { tones: [{ hz: 784, ms: 140 }, { hz: 392, ms: 260 }], gain: 0.35 },
  unsure:      { tones: [{ hz: 587, ms: 120 }, { hz: 587, ms: 120 }], gain: 0.25 },
  interrupted: { tones: [{ hz: 700, ms: 40 }], gain: 0.15 },
  reconnect:   { tones: [{ hz: 523, ms: 80 }, { hz: 784, ms: 80 }], gain: 0.2 },
  offline:     { tones: [{ hz: 392, ms: 200 }, { hz: 262, ms: 300 }], gain: 0.3 },
};

/** Synthesized with OscillatorNode, so there are no asset files to download. */
export function playEarcon(ctx: AudioContext, n: CueName): void {
  const e = EARCONS[n];
  let t = ctx.currentTime;
  for (const tone of e.tones) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.frequency.value = tone.hz;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(e.gain, t + 0.01);
    g.gain.linearRampToValueAtTime(0, t + tone.ms / 1000);
    osc.connect(g).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + tone.ms / 1000 + 0.02);
    t += tone.ms / 1000;
  }
}
```

```ts
// apps/app/src/core/thinking.ts
export class ThinkingTimer {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private on = false;
  constructor(private onStart: () => void, private onStop: () => void, private delayMs = 1500) {}

  arm(): void {
    this.disarm();
    this.timer = setTimeout(() => { this.timer = null; this.on = true; this.onStart(); }, this.delayMs);
  }

  disarm(): void {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    if (this.on) { this.on = false; this.onStop(); }
  }

  get active(): boolean { return this.on; }
}
```

In `main.tsx` (Task 6), `onStart` plays the `thinking` earcon on a 1.2 s loop and `onStop` clears the loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -w apps/app -- earcons-thinking`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/audio/earcons.ts apps/app/src/core/thinking.ts apps/app/test/earcons-thinking.test.ts
git commit -m "feat(app): synthesized earcons for every cue and 1.5 s thinking timer"
```

---

### Task 5: Dispatcher, announcer (no double speech), confirm gestures, screen-reader region

**Files:**
- Create: `apps/app/src/core/announce.ts`, `apps/app/src/core/confirm.ts`, `apps/app/src/core/dispatch.ts`, `apps/app/src/i18n/strings.ts`, `apps/app/src/ui/ScreenReaderRegion.tsx`
- Test: `apps/app/test/announce-confirm.test.ts`

**Interfaces:**
- Consumes: `app` (Task 1), `PlaybackQueue` (Task 3), `ThinkingTimer` (Task 4), `ServerMsg` (`@aira/contracts`)
- Produces:
  - `STRINGS: Record<"offline"|"online"|"reconnecting"|"paused"|"listening"|"confirmPending", Record<Lang,string>>`
  - `shouldClientSpeak(msg: ServerMsg, ctx: { mock: boolean; msSinceLiveAudio: number; screenReaderMode: boolean }): boolean`
  - `textFor(msg: ServerMsg): string | null`
  - `class ConfirmState { set(id: string, kind: string): void; clear(): void; get pending(): {id:string;kind:string} | null; onGesture(g: "double"|"long"): ClientMsg | null }`
  - `createDispatcher(d: DispatchDeps): (m: ServerMsg) => void`
  - `ScreenReaderRegion` component + `announceToRegion(text: string)`

- [ ] **Step 1: Write the failing test** `apps/app/test/announce-confirm.test.ts`

```ts
import { describe, it, expect, vi } from "vitest";
import { shouldClientSpeak, textFor } from "../src/core/announce";
import { ConfirmState } from "../src/core/confirm";
import { createDispatcher } from "../src/core/dispatch";
import { STRINGS } from "../src/i18n/strings";

const live = { mock: false, msSinceLiveAudio: 200, screenReaderMode: false };

describe("announcer", () => {
  it("does not speak confirm/error when Live audio just played (no double speech)", () => {
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "order", prompt: "Order 30 L?" }, live)).toBe(false);
    expect(shouldClientSpeak({ t: "error", code: "network", say: "Network lost" }, live)).toBe(false);
  });
  it("speaks confirm/error when no Live audio for >1 s, and always in mock sessions", () => {
    const quiet = { ...live, msSinceLiveAudio: 1500 };
    expect(shouldClientSpeak({ t: "confirm", id: "c1", kind: "order", prompt: "Order?" }, quiet)).toBe(true);
    expect(shouldClientSpeak({ t: "state", entity: "sale", id: "s1", state: "SALE_COMPLETED", summary: "Sale done" }, { ...live, mock: true })).toBe(true);
  });
  it("never speaks raw audio/transcript messages", () => {
    expect(shouldClientSpeak({ t: "transcript", role: "aira", text: "hi", final: true }, { ...live, mock: true })).toBe(false);
  });
  it("textFor picks prompt/say/summary and trims long summaries to 120 chars", () => {
    expect(textFor({ t: "confirm", id: "c", kind: "payment", prompt: "Pay 1000?" })).toBe("Pay 1000?");
    expect(textFor({ t: "error", code: "camera", say: "Camera blocked" })).toBe("Camera blocked");
    const long = "x".repeat(300);
    expect(textFor({ t: "state", entity: "order", id: "o", state: "ORDER_PLACED", summary: long })!.length).toBe(120);
  });
  it("has every local status string in ta, hi and en", () => {
    for (const v of Object.values(STRINGS)) expect(Object.keys(v).sort()).toEqual(["en", "hi", "ta"]);
  });
});

describe("ConfirmState gestures", () => {
  it("double-tap answers yes exactly once, long-press answers no", () => {
    const c = new ConfirmState();
    c.set("c9", "payment");
    expect(c.onGesture("double")).toEqual({ t: "confirm", id: "c9", answer: "yes" });
    expect(c.onGesture("double")).toBeNull(); // cleared — must not send twice
    c.set("c10", "order");
    expect(c.onGesture("long")).toEqual({ t: "confirm", id: "c10", answer: "no" });
  });
  it("returns null when nothing is pending (caller treats it as talk/stop)", () => {
    expect(new ConfirmState().onGesture("double")).toBeNull();
  });
});

describe("dispatcher", () => {
  function deps() {
    return {
      playback: { enqueue: vi.fn(), flush: vi.fn(), resume: vi.fn(), lastAudioAt: 0 },
      thinking: { disarm: vi.fn() },
      earcon: vi.fn(), haptic: vi.fn(), speak: vi.fn(),
      confirm: new ConfirmState(),
      ctx: () => ({ mock: false, msSinceLiveAudio: 5000, screenReaderMode: false }),
    };
  }
  it("interrupted flushes playback with hold, and the next user transcript resumes", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "cue", cue: "interrupted" });
    expect(d.playback.flush).toHaveBeenCalledWith({ holdUntilResume: true });
    dispatch({ t: "transcript", role: "user", text: "wait", final: false });
    expect(d.playback.resume).toHaveBeenCalled();
  });
  it("every server reply disarms the thinking timer", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "audio", pcm24: "AAAA" });
    dispatch({ t: "transcript", role: "aira", text: "ok", final: true });
    dispatch({ t: "cue", cue: "done" });
    dispatch({ t: "error", code: "internal", say: "Sorry" });
    expect(d.thinking.disarm).toHaveBeenCalledTimes(4);
  });
  it("confirm sets pending state, plays warn earcon and speaks the prompt when Live is silent", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "confirm", id: "c1", kind: "order", prompt: "Order 30 litres Sakthi curd?" });
    expect(d.confirm.pending).toEqual({ id: "c1", kind: "order" });
    expect(d.earcon).toHaveBeenCalledWith("warn");
    expect(d.speak).toHaveBeenCalledWith("Order 30 litres Sakthi curd?");
  });
  it("match/mismatch cues also fire matching haptics", () => {
    const d = deps(); const dispatch = createDispatcher(d as any);
    dispatch({ t: "cue", cue: "mismatch" });
    expect(d.earcon).toHaveBeenCalledWith("mismatch");
    expect(d.haptic).toHaveBeenCalledWith("mismatch");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/app -- announce-confirm`
Expected: FAIL with `Failed to resolve import "../src/core/announce"`.

- [ ] **Step 3: Write the implementation**

```ts
// apps/app/src/i18n/strings.ts
import type { Lang } from "@aira/contracts";
type S = Record<Lang, string>;
export const STRINGS: Record<"offline" | "online" | "reconnecting" | "paused" | "listening" | "confirmPending", S> = {
  offline:        { en: "Offline. Sales are saved and will sync.", ta: "இணையம் இல்லை. விற்பனை சேமிக்கப்படுகிறது.", hi: "ऑफ़लाइन। बिक्री सेव हो रही है।" },
  online:         { en: "Back online.", ta: "இணையம் மீண்டும் வந்தது.", hi: "फिर से ऑनलाइन।" },
  reconnecting:   { en: "Reconnecting.", ta: "மீண்டும் இணைக்கிறது.", hi: "फिर से जुड़ रहा है।" },
  paused:         { en: "AIRA paused.", ta: "AIRA நிறுத்தப்பட்டது.", hi: "AIRA रुका है।" },
  listening:      { en: "Listening.", ta: "கேட்கிறேன்.", hi: "सुन रहा हूँ।" },
  confirmPending: { en: "Double-tap for yes, long-press for no.", ta: "ஆம் என்றால் இருமுறை தட்டுங்கள், இல்லை என்றால் அழுத்திப் பிடியுங்கள்.", hi: "हाँ के लिए दो बार टैप, ना के लिए दबाकर रखें।" },
};
```

```ts
// apps/app/src/core/announce.ts
import type { ServerMsg } from "@aira/contracts";

export interface SpeakCtx { mock: boolean; msSinceLiveAudio: number; screenReaderMode: boolean }

/** Gemini Live audio is the primary voice; the client only fills gaps. */
export function shouldClientSpeak(msg: ServerMsg, ctx: SpeakCtx): boolean {
  if (msg.t !== "confirm" && msg.t !== "error" && msg.t !== "state") return false;
  if (ctx.mock) return true;
  if (msg.t === "state") return false; // Live narrates state changes in real sessions
  return ctx.msSinceLiveAudio > 1000;
}

export function textFor(msg: ServerMsg): string | null {
  switch (msg.t) {
    case "confirm": return msg.prompt;
    case "error": return msg.say;
    case "state": return msg.summary.slice(0, 120);
    default: return null;
  }
}
```

```ts
// apps/app/src/core/confirm.ts
import type { ClientMsg } from "@aira/contracts";

export class ConfirmState {
  private p: { id: string; kind: string } | null = null;
  set(id: string, kind: string) { this.p = { id, kind }; }
  clear() { this.p = null; }
  get pending() { return this.p; }
  /** Returns the confirm reply for a gesture, or null if nothing is pending. */
  onGesture(g: "double" | "long"): ClientMsg | null {
    if (!this.p) return null;
    const msg: ClientMsg = { t: "confirm", id: this.p.id, answer: g === "double" ? "yes" : "no" };
    this.p = null;
    return msg;
  }
}
```

```ts
// apps/app/src/core/dispatch.ts
import type { CueName, ServerMsg } from "@aira/contracts";
import type { HapticName } from "./types";
import { ConfirmState } from "./confirm";
import { shouldClientSpeak, textFor, type SpeakCtx } from "./announce";

export interface DispatchDeps {
  playback: { enqueue(b64: string): void; flush(o?: { holdUntilResume?: boolean }): void; resume(): void };
  thinking: { disarm(): void };
  earcon(n: CueName): void;
  haptic(n: HapticName): void;
  speak(text: string): void;
  confirm: ConfirmState;
  ctx(): SpeakCtx;
}

const CUE_HAPTIC: Partial<Record<CueName, HapticName>> = {
  ack: "ack", done: "done", warn: "warn", match: "match", mismatch: "mismatch", unsure: "warn",
};

export function createDispatcher(d: DispatchDeps): (m: ServerMsg) => void {
  return (m) => {
    if (m.t === "audio" || m.t === "cue" || m.t === "error" || (m.t === "transcript" && m.role === "aira")) d.thinking.disarm();
    switch (m.t) {
      case "audio": d.playback.enqueue(m.pcm24); break;
      case "transcript": if (m.role === "user") d.playback.resume(); break;
      case "cue":
        if (m.cue === "interrupted") d.playback.flush({ holdUntilResume: true });
        d.earcon(m.cue);
        if (CUE_HAPTIC[m.cue]) d.haptic(CUE_HAPTIC[m.cue]!);
        break;
      case "confirm":
        d.confirm.set(m.id, m.kind);
        d.earcon("warn");
        break;
      case "error": d.earcon("warn"); break;
      default: break;
    }
    if (shouldClientSpeak(m, d.ctx())) { const t = textFor(m); if (t) d.speak(t); }
  };
}
```

```tsx
// apps/app/src/ui/ScreenReaderRegion.tsx
import { useEffect, useState } from "preact/hooks";

let push: ((t: string) => void) | null = null;
/** Only used when Settings.screenReaderMode is on (TalkBack reads it; we stay silent). */
export function announceToRegion(text: string) { push?.(text); }

export function ScreenReaderRegion() {
  const [text, setText] = useState("");
  useEffect(() => { push = (t) => { setText(""); setTimeout(() => setText(t), 30); }; return () => { push = null; }; }, []);
  return <div aria-live="polite" role="status" style={{ position: "absolute", left: "-9999px" }}>{text}</div>;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -w apps/app -- announce-confirm`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/app/src/core/announce.ts apps/app/src/core/confirm.ts apps/app/src/core/dispatch.ts apps/app/src/i18n/strings.ts apps/app/src/ui/ScreenReaderRegion.tsx apps/app/test/announce-confirm.test.ts
git commit -m "feat(app): server message dispatcher, no-double-speech announcer, confirm gestures"
```

---

### Task 6: Onboarding (one Start tap), Firebase anonymous auth, offline queue, and wiring in `main.tsx`

**Files:**
- Create: `apps/app/src/core/offline-queue.ts`, `apps/app/src/core/auth.ts`, `apps/app/src/ui/Onboarding.tsx`
- Modify: `apps/app/src/main.tsx` (replace the foundation's demo wiring)
- Modify: `apps/app/package.json` (add `firebase`)
- Test: `apps/app/test/offline-queue.test.ts`

**Interfaces:**
- Consumes: everything above; `AiraSocket`, `GestureSurface` (foundation)
- Produces:
  - `interface QueueStore { all(): Promise<QueuedOp[]>; put(op: QueuedOp): Promise<void>; del(idemKey: string): Promise<void> }`
  - `QueuedOp = { idemKey: string; msg: ClientMsg; at: number }`
  - `class OfflineQueue { constructor(store: QueueStore); add(msg: ClientMsg, idemKey: string): Promise<void>; replay(send: (m: ClientMsg) => boolean): Promise<number> }`
  - `idbStore(): QueueStore`, `memStore(): QueueStore`
  - `getIdToken(): Promise<string | undefined>`
  - `Onboarding` props `{ onDone(s: Settings): void }`

- [ ] **Step 1: Write the failing test** `apps/app/test/offline-queue.test.ts`

```ts
import { describe, it, expect } from "vitest";
import { OfflineQueue, memStore } from "../src/core/offline-queue";

describe("OfflineQueue", () => {
  it("replays in insertion order and removes only ops that were sent", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add({ t: "text", text: "sale 1" }, "k1");
    await q.add({ t: "text", text: "sale 2" }, "k2");
    let calls = 0;
    const sent = await q.replay(() => ++calls === 1); // first succeeds, second fails
    expect(sent).toBe(1);
    expect((await store.all()).map((o) => o.idemKey)).toEqual(["k2"]);
  });

  it("never duplicates an op with the same idemKey", async () => {
    const store = memStore();
    const q = new OfflineQueue(store);
    await q.add({ t: "text", text: "a" }, "same");
    await q.add({ t: "text", text: "a" }, "same");
    expect(await store.all()).toHaveLength(1);
  });

  it("survives a 'reload' (new queue over the same store)", async () => {
    const store = memStore();
    await new OfflineQueue(store).add({ t: "stop" }, "k9");
    const replayed: unknown[] = [];
    await new OfflineQueue(store).replay((m) => { replayed.push(m); return true; });
    expect(replayed).toEqual([{ t: "stop" }]);
    expect(await store.all()).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/app -- offline-queue`
Expected: FAIL with `Failed to resolve import "../src/core/offline-queue"`.

- [ ] **Step 3: Write the offline queue and auth**

```ts
// apps/app/src/core/offline-queue.ts
import type { ClientMsg } from "@aira/contracts";

export type QueuedOp = { idemKey: string; msg: ClientMsg; at: number };
export interface QueueStore { all(): Promise<QueuedOp[]>; put(op: QueuedOp): Promise<void>; del(idemKey: string): Promise<void> }

export class OfflineQueue {
  constructor(private store: QueueStore) {}
  async add(msg: ClientMsg, idemKey: string): Promise<void> {
    const existing = await this.store.all();
    if (existing.some((o) => o.idemKey === idemKey)) return;
    await this.store.put({ idemKey, msg, at: Date.now() });
  }
  /** Sends in order; stops at the first failure so ordering is preserved. Returns ops sent. */
  async replay(send: (m: ClientMsg) => boolean): Promise<number> {
    const ops = (await this.store.all()).sort((a, b) => a.at - b.at);
    let n = 0;
    for (const op of ops) {
      if (!send(op.msg)) break;
      await this.store.del(op.idemKey);
      n++;
    }
    return n;
  }
}

export function memStore(): QueueStore {
  const m = new Map<string, QueuedOp>();
  let seq = 0;
  return {
    async all() { return [...m.values()]; },
    async put(op) { m.set(op.idemKey, { ...op, at: op.at + seq++ * 1e-6 }); },
    async del(k) { m.delete(k); },
  };
}

export function idbStore(dbName = "aira", storeName = "pending"): QueueStore {
  const open = () => new Promise<IDBDatabase>((res, rej) => {
    const r = indexedDB.open(dbName, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(storeName, { keyPath: "idemKey" });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  const tx = async <T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>) => {
    const db = await open();
    return new Promise<T>((res, rej) => {
      const req = fn(db.transaction(storeName, mode).objectStore(storeName));
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  };
  return {
    all: () => tx<QueuedOp[]>("readonly", (s) => s.getAll() as IDBRequest<QueuedOp[]>),
    put: async (op) => { await tx("readwrite", (s) => s.put(op)); },
    del: async (k) => { await tx("readwrite", (s) => s.delete(k)); },
  };
}
```

```ts
// apps/app/src/core/auth.ts
import { initializeApp, getApps } from "firebase/app";
import { getAuth, signInAnonymously } from "firebase/auth";

const env = (import.meta as any).env ?? {};
const config = {
  apiKey: env.VITE_FIREBASE_API_KEY,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: env.VITE_FIREBASE_PROJECT_ID,
  appId: env.VITE_FIREBASE_APP_ID,
};

/** Anonymous sign-in (judges and demo need no account). Returns undefined if Firebase isn't configured. */
export async function getIdToken(): Promise<string | undefined> {
  if (!config.apiKey) return undefined;
  const fb = getApps()[0] ?? initializeApp(config);
  const auth = getAuth(fb);
  const cred = auth.currentUser ? { user: auth.currentUser } : await signInAnonymously(auth);
  return cred.user.getIdToken();
}
```

Add the dependency: `npm install -w apps/app firebase@^10`. Add `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID` and `VITE_FIREBASE_APP_ID` to `.env.example` (values come from Saravana).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -w apps/app -- offline-queue`
Expected: PASS, 3 tests.

- [ ] **Step 5: Write Onboarding and wire everything in `main.tsx`**

```tsx
// apps/app/src/ui/Onboarding.tsx
import { useState } from "preact/hooks";
import type { Settings } from "../core/types";
import { DEFAULT_SETTINGS } from "../core/settings";

const SHOPS = [{ id: "demo-murugan-dairy", label: "Murugan Dairy (demo)" }];

/** One big "Start" tap = the user gesture that unlocks audio, camera, mic and wake lock together. */
export function Onboarding(props: { onDone(s: Settings): void }) {
  const [lang, setLang] = useState<Settings["lang"]>("en");
  const [shopId, setShop] = useState(SHOPS[0].id);
  const [srMode, setSr] = useState(false);
  return (
    <main style={{ padding: 24, color: "#fff", background: "#000", minHeight: "100dvh", fontSize: "1.4rem" }}>
      <h1>AIRA</h1>
      <fieldset><legend>Language</legend>
        {(["ta", "hi", "en"] as const).map((l) => (
          <label key={l} style={{ display: "block", padding: 12 }}>
            <input type="radio" name="lang" checked={lang === l} onChange={() => setLang(l)} />
            {l === "ta" ? " தமிழ்" : l === "hi" ? " हिन्दी" : " English"}
          </label>
        ))}
      </fieldset>
      <label style={{ display: "block", padding: 12 }}>Shop{" "}
        <select value={shopId} onChange={(e) => setShop((e.target as HTMLSelectElement).value)}>
          {SHOPS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </label>
      <label style={{ display: "block", padding: 12 }}>
        <input type="checkbox" checked={srMode} onChange={() => setSr(!srMode)} /> I use TalkBack (screen-reader mode)
      </label>
      <button style={{ width: "100%", padding: 32, fontSize: "2rem", marginTop: 24 }}
        onClick={() => props.onDone({ ...DEFAULT_SETTINGS, lang, shopId, screenReaderMode: srMode, onboarded: true })}>
        Start
      </button>
    </main>
  );
}
```

```tsx
// apps/app/src/main.tsx
import { render } from "preact";
import { useState } from "preact/hooks";
import type { ServerMsg } from "@aira/contracts";
import { AiraSocket } from "./net/socket";
import { GestureSurface } from "./ui/GestureSurface";
import { ScreenReaderRegion, announceToRegion } from "./ui/ScreenReaderRegion";
import { Onboarding } from "./ui/Onboarding";
import { initApp, app } from "./core/app";
import { loadSettings, saveSettings } from "./core/settings";
import { vibrate } from "./core/haptics";
import { ThinkingTimer } from "./core/thinking";
import { ConfirmState } from "./core/confirm";
import { createDispatcher } from "./core/dispatch";
import { OfflineQueue, idbStore } from "./core/offline-queue";
import { getIdToken } from "./core/auth";
import { STRINGS } from "./i18n/strings";
import { PlaybackQueue } from "./audio/playback";
import { playEarcon } from "./audio/earcons";
import { startMic } from "./audio/mic";

const WS = (import.meta as any).env?.VITE_AIRA_LIVE_WS ?? "ws://localhost:8080/ws";
const VOICE: Record<string, string> = { ta: "ta-IN", hi: "hi-IN", en: "en-IN" };

async function boot(settings = loadSettings()) {
  saveSettings(settings);
  const ctx = new AudioContext();            // created inside the Start tap → unlocked
  await ctx.resume();
  try { await (navigator as any).wakeLock?.request("screen"); } catch { /* best effort */ }
  try { (await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })).getTracks().forEach((t) => t.stop()); } catch { /* camera plan (02) re-asks */ }

  const socket = new AiraSocket(WS);
  const playback = new PlaybackQueue(ctx);
  let thinkingLoop: ReturnType<typeof setInterval> | null = null;
  const thinking = new ThinkingTimer(
    () => { playEarcon(ctx, "thinking"); thinkingLoop = setInterval(() => playEarcon(ctx, "thinking"), 1200); },
    () => { if (thinkingLoop) clearInterval(thinkingLoop); thinkingLoop = null; },
  );
  const confirm = new ConfirmState();
  const queue = new OfflineQueue(idbStore());
  let mock = false;

  initApp({
    socket, settings,
    speakImpl: (text, s) => {
      if (s.screenReaderMode) return announceToRegion(text);
      const u = new SpeechSynthesisUtterance(text);
      u.lang = VOICE[s.lang]; u.rate = s.speechRate;
      speechSynthesis.cancel(); speechSynthesis.speak(u);
    },
    earconImpl: (n) => playEarcon(ctx, n),
    hapticImpl: (n) => { vibrate(n); },
  });

  const dispatch = createDispatcher({
    playback, thinking, confirm,
    earcon: app.earcon, haptic: app.haptic, speak: app.speak,
    ctx: () => ({ mock, msSinceLiveAudio: (ctx.currentTime - playback.lastAudioAt) * 1000, screenReaderMode: app.settings.screenReaderMode }),
  });
  socket.on((m: ServerMsg) => {
    if (m.t === "ready") { mock = m.mock; app.earcon("reconnect"); void queue.replay((x) => app.send(x)); }
    dispatch(m);
  });

  window.addEventListener("offline", () => { app.earcon("offline"); app.speak(STRINGS.offline[app.settings.lang]); });
  window.addEventListener("online", () => app.speak(STRINGS.online[app.settings.lang]));
  document.addEventListener("visibilitychange", () => {
    app.send({ t: "event", name: "visibility", data: { hidden: document.hidden } });
    if (document.hidden) app.speak(STRINGS.paused[app.settings.lang]);
  });

  socket.connect({ t: "hello", uid: "anon", idToken: await getIdToken(), shopId: settings.shopId, lang: settings.lang, verbosity: settings.verbosity, appVersion: "0.2.0" });

  let mic: { stop(): void } | null = null;
  const talk = async () => {
    app.earcon("ack"); app.haptic("ack");                  // < 150 ms acknowledgement
    if (mic) return;
    mic = await startMic({ onChunk: (b64) => app.send({ t: "audio", pcm16: b64 }) });
    thinking.arm();
    setTimeout(() => { mic?.stop(); mic = null; }, 30_000); // idle mic window; Live VAD ends the turn
  };
  const stop = () => {
    playback.flush(); thinking.disarm(); speechSynthesis.cancel();
    mic?.stop(); mic = null;
    app.send({ t: "stop" }); app.haptic("done");
  };

  return { onTalk: () => { const c = confirm.onGesture("double"); c ? app.send(c) : void talk(); },
           onStop: () => { const c = confirm.onGesture("long"); c ? app.send(c) : stop(); } };
}

function Root() {
  const [handlers, setHandlers] = useState<{ onTalk(): void; onStop(): void } | null>(null);
  if (!handlers) return <Onboarding onDone={async (s) => setHandlers(await boot(s))} />;
  return (
    <>
      <ScreenReaderRegion />
      <GestureSurface label="AIRA. Double-tap to talk or say yes. Long-press to stop or say no."
        onTalk={handlers.onTalk} onStop={handlers.onStop} />
    </>
  );
}

render(<Root />, document.getElementById("root")!);
```

- [ ] **Step 6: Run the full suite and the type check**

Run: `npm test -w apps/app && npm run typecheck -w apps/app`
Expected: all Task 1–6 tests PASS (39 tests), plus the foundation's socket tests. No type errors.

- [ ] **Step 7: Manual check on an Android phone** (Chrome, `npm run dev -w apps/app -- --host`, open the LAN URL over HTTPS or via `chrome://inspect` port-forwarding)

1. Tap **Start** → the language and shop are saved, and there's no permission prompt loop.
2. Connect Bluetooth earphones, then double-tap. You hear the `ack` earcon within about 150 ms, and the mic indicator shows the **built-in** mic. In Chrome remote devtools, `navigator.mediaDevices.enumerateDevices()` lists both, and `getSettings().deviceId` equals the built-in one.
3. Against mock `aira-live` (foundation), send a `confirm` msg. AIRA speaks the prompt; a double-tap sends `{"t":"confirm","answer":"yes"}` once (check the server log).
4. Turn on airplane mode → the offline earcon plays and the spoken status follows. Turn it off → "Back online", and queued ops replay.
5. Turn on TalkBack and screen-reader mode, and repeat step 3 → the prompt is read once, by TalkBack only.

- [ ] **Step 8: Commit**

```bash
git add apps/app/src/core/offline-queue.ts apps/app/src/core/auth.ts apps/app/src/ui/Onboarding.tsx apps/app/src/main.tsx apps/app/test/offline-queue.test.ts apps/app/package.json package-lock.json .env.example
git commit -m "feat(app): one-tap onboarding, anonymous auth, offline queue, voice wiring"
```
