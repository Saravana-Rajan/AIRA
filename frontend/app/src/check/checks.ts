// frontend/app/src/check/checks.ts
// The start-up device check, minus the screen: is the mic hearing a voice, and can the AI see through the
// camera? DeviceCheck.tsx drives these; every browser API comes in as a parameter so tests run with fakes.
import type { Lang } from "@aira/contracts";
import { CHECK } from "../i18n/strings";

export type DeviceKind = "phone" | "laptop";
export type MicProblem = "blocked" | "missing" | "silent" | "suspended";
export type MicResult = { ok: true } | { ok: false; problem: MicProblem };
export type CameraProblem =
  | "dark" | "blurry" | "covered" | "blank" | "unclear"     // the AI looked and said no
  | "blocked" | "offline" | "busy" | "unavailable";           // it never got to look
export type CameraResult = { ok: true; seen: string } | { ok: false; problem: CameraProblem };

/** A voice is this loud (RMS of the -1..1 signal); room hiss on a laptop mic sits well below it. */
export const SPEECH_RMS = 0.02;
/** How much speech, in total, counts as "the mic hears you". */
export const SPEECH_MS = 300;
export const MIC_TIMEOUT_MS = 10_000;
const MIC_POLL_MS = 50;

type NavLike = { userAgent?: string; userAgentData?: { mobile?: boolean } };

/** Phone or laptop, for the camera instruction: a laptop camera faces the user, a chest phone faces the shop. */
export function deviceKind(nav: NavLike | undefined = typeof navigator === "undefined" ? undefined : navigator): DeviceKind {
  if (nav?.userAgentData?.mobile !== undefined) return nav.userAgentData.mobile ? "phone" : "laptop";
  return /Android|iPhone|iPad|iPod|Mobile/i.test(nav?.userAgent ?? "") ? "phone" : "laptop";
}

/** Adds up the time a level stays above SPEECH_RMS; `passed` once there is SPEECH_MS of it. */
export class SpeechDetector {
  private loudMs = 0;
  push(rms: number, dtMs: number): boolean {
    if (rms >= SPEECH_RMS) this.loudMs += dtMs;
    return this.passed;
  }
  get passed(): boolean {
    return this.loudMs >= SPEECH_MS;
  }
}

export function rmsOf(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return samples.length ? Math.sqrt(sum / samples.length) : 0;
}

/** "wss://host/ws" -> "https://host", "ws://localhost:8080/ws" -> "http://localhost:8080". */
export function httpBase(wsUrl: string): string {
  try {
    const u = new URL(wsUrl);
    u.protocol = u.protocol === "wss:" ? "https:" : "http:";
    return u.origin;
  } catch {
    return "";
  }
}

export interface MicDeps {
  getUserMedia(c: MediaStreamConstraints): Promise<MediaStream>;
  makeContext(): AudioContext;
  onLevel?(rms: number): void;
  sleep?(ms: number): Promise<void>;
  now?(): number;
  timeoutMs?: number;
}

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Opens the mic and listens for up to MIC_TIMEOUT_MS until it has heard SPEECH_MS of voice. The mic is
 * always closed again: the voice app opens its own stream later (the permission is already granted by then).
 */
export async function testMicrophone(d: MicDeps): Promise<MicResult> {
  let stream: MediaStream;
  try {
    stream = await d.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
  } catch (e) {
    const name = (e as { name?: string } | undefined)?.name ?? "";
    return { ok: false, problem: name === "NotFoundError" || name === "OverconstrainedError" ? "missing" : "blocked" };
  }
  const sleep = d.sleep ?? realSleep;
  const now = d.now ?? (() => Date.now());
  let ctx: AudioContext | null = null;
  try {
    ctx = d.makeContext();
    if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
    if (ctx.state === "suspended") return { ok: false, problem: "suspended" };  // needs a tap first
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const speech = new SpeechDetector();
    const start = now();
    let last = start;
    while (now() - start < (d.timeoutMs ?? MIC_TIMEOUT_MS)) {
      await sleep(MIC_POLL_MS);
      analyser.getFloatTimeDomainData(buf);
      const rms = rmsOf(buf);
      const t = now();
      d.onLevel?.(rms);
      if (speech.push(rms, t - last)) return { ok: true };
      last = t;
    }
    return { ok: false, problem: "silent" };
  } finally {
    stream.getTracks().forEach((t) => t.stop());
    void ctx?.close().catch(() => undefined);
  }
}

export interface CameraCheckOpts {
  base: string;
  lang: Lang;
  device: DeviceKind;
  key?: string;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

/** Sends one camera picture to aira-live (POST /api/device-check/camera) and maps every outcome. */
export async function askAiAboutCamera(jpegB64: string, o: CameraCheckOpts): Promise<CameraResult> {
  const f = o.fetch ?? fetch;
  const ctrl = typeof AbortController === "undefined" ? null : new AbortController();
  // The server may need to wake up, and its AI gets two tries of 12 s each.
  const timer = ctrl ? setTimeout(() => ctrl.abort(), o.timeoutMs ?? 30_000) : null;
  try {
    const r = await f(`${o.base}/api/device-check/camera`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ image: jpegB64, lang: o.lang, device: o.device, ...(o.key ? { key: o.key } : {}) }),
      signal: ctrl?.signal,
    });
    if (r.status === 429) return { ok: false, problem: "busy" };
    if (r.status === 503) return { ok: false, problem: "unavailable" };
    if (!r.ok) return { ok: false, problem: "unavailable" };
    const body = (await r.json()) as { ok?: unknown; seen?: unknown; problem?: unknown };
    if (body.ok === true && typeof body.seen === "string" && body.seen.trim()) return { ok: true, seen: body.seen.trim() };
    const p = body.problem;
    return { ok: false, problem: p === "dark" || p === "blurry" || p === "covered" || p === "blank" ? p : "unclear" };
  } catch {
    return { ok: false, problem: "offline" };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

const MIC_LINE: Record<MicProblem, keyof typeof CHECK> = {
  blocked: "micBlocked", missing: "micMissing", silent: "micSilent", suspended: "micTap",
};
const CAM_LINE: Record<CameraProblem, keyof typeof CHECK> = {
  dark: "camDark", blurry: "camBlurry", covered: "camCovered", blank: "camBlank", unclear: "camUnclear",
  blocked: "camBlocked", offline: "camOffline", busy: "camBusy", unavailable: "camUnavailable",
};

/** The server could not judge the picture (busy, down, unreachable): not a camera fault, so it must not lock the owner out. */
export function serverSideProblem(r: CameraResult): boolean {
  return !r.ok && (r.problem === "busy" || r.problem === "unavailable" || r.problem === "offline");
}

/** What the owner hears and reads for a mic result. */
export function micLine(r: MicResult, lang: Lang): string {
  return r.ok ? CHECK.micOk[lang] : CHECK[MIC_LINE[r.problem]][lang];
}

/** What the owner hears and reads for a camera result; a pass repeats what the AI saw. */
export function cameraLine(r: CameraResult, lang: Lang): string {
  return r.ok ? CHECK.camOk[lang].replace("{seen}", r.seen) : CHECK[CAM_LINE[r.problem]][lang];
}

export function cameraInstruction(device: DeviceKind, lang: Lang): string {
  return (device === "laptop" ? CHECK.camLookLaptop : CHECK.camLookPhone)[lang];
}

/** The lines the server can read in AIRA's voice (backend i18n "check.<key>", the same words as CHECK). */
export type SpokenKey =
  | "micSay" | "micOk" | "micBlocked" | "micMissing" | "micSilent" | "micTap" | "camLookLaptop" | "camLookPhone"
  | "camOk" | "camDark" | "camBlurry" | "camCovered" | "camBlank" | "camUnclear" | "camBlocked" | "camOffline"
  | "camBusy" | "camUnavailable" | "allGood";
/** One spoken line: its key for AIRA's voice, its words for the screen and the device's own voice. */
export type VoiceLine = { key: SpokenKey; text: string; seen?: string };

export function voiceLine(key: SpokenKey, lang: Lang): VoiceLine {
  return { key, text: CHECK[key][lang] };
}

export function instructionVoice(device: DeviceKind, lang: Lang): VoiceLine {
  return voiceLine(device === "laptop" ? "camLookLaptop" : "camLookPhone", lang);
}

export function micVoice(r: MicResult, lang: Lang): VoiceLine {
  return r.ok ? voiceLine("micOk", lang) : voiceLine(MIC_LINE[r.problem] as SpokenKey, lang);
}

export function cameraVoice(r: CameraResult, lang: Lang): VoiceLine {
  return r.ok ? { key: "camOk", text: cameraLine(r, lang), seen: r.seen } : voiceLine(CAM_LINE[r.problem] as SpokenKey, lang);
}

// ---------------------------------------------------------------- the check's lines in AIRA's own voice

export interface VoiceDeps {
  base: string;
  key?: string;
  fetch?: typeof fetch;
  /** Plays the audio and resolves when it has finished. */
  play(blob: Blob): Promise<void>;
  /** The device's own voice, used when AIRA's voice is unavailable. Resolves when it has finished. */
  fallback(text: string, lang: Lang, rate: number): Promise<void>;
  timeoutMs?: number;
}

/**
 * The check screen speaks in AIRA's own voice: aira-live reads each line with the same voice model as the
 * conversation (POST /api/device-check/say). The app names the line; the server holds the words, so the address can
 * only ever read the check's own lines. Each line is fetched once per page (prefetch() warms the next ones); when the
 * server cannot (mock mode, an error, a slow answer) the device's own voice reads it instead. speak() resolves only
 * when the line has finished, so nothing listens to the mic while AIRA is still talking.
 */
export function airaVoice(d: VoiceDeps) {
  const f = d.fetch ?? fetch;
  const lines = new Map<string, Promise<Blob | null>>();
  const load = (line: VoiceLine, lang: Lang): Promise<Blob | null> => {
    const id = `${lang}|${line.key}|${line.seen ?? ""}`;
    let p = lines.get(id);
    if (!p) {
      p = (async () => {
        const ctrl = typeof AbortController === "undefined" ? null : new AbortController();
        const timer = ctrl ? setTimeout(() => ctrl.abort(), d.timeoutMs ?? 12_000) : null;
        try {
          const r = await f(`${d.base}/api/device-check/say`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ line: line.key, lang, ...(line.seen ? { seen: line.seen } : {}), ...(d.key ? { key: d.key } : {}) }),
            signal: ctrl?.signal,
          });
          if (r.status !== 200) return null;
          const blob = await r.blob();
          return blob.size ? blob : null;
        } catch {
          return null;
        } finally {
          if (timer) clearTimeout(timer);
        }
      })();
      lines.set(id, p);
    }
    return p;
  };
  return {
    prefetch(line: VoiceLine, lang: Lang): void {
      void load(line, lang);
    },
    async speak(line: VoiceLine, lang: Lang, rate: number): Promise<void> {
      const blob = await load(line, lang);
      if (blob) {
        try {
          await d.play(blob);
          return;
        } catch {
          /* autoplay refused or a broken clip: read it with the device's voice */
        }
      }
      await d.fallback(line.text, lang, rate);
    },
  };
}

/** Plays a clip; resolves when it ends, rejects when the browser will not play it. */
export function playBlob(blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    const finish = (ok: boolean, err?: unknown) => {
      URL.revokeObjectURL(url);
      if (ok) resolve();
      else reject(err ?? new Error("audio failed"));
    };
    audio.onended = () => finish(true);
    audio.onerror = () => finish(false);
    audio.play().catch((e) => finish(false, e));
  });
}

/** The device's own voice; resolves when the line ends (or after a generous cap, if the browser never says). */
export function speakLocalAsync(text: string, lang: Lang, rate: number): Promise<void> {
  return new Promise((resolve) => {
    const cap = setTimeout(resolve, Math.min(15_000, 1500 + text.length * 90));
    const done = () => { clearTimeout(cap); resolve(); };
    try {
      if (typeof speechSynthesis === "undefined") return done();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = { ta: "ta-IN", hi: "hi-IN", en: "en-IN" }[lang];
      u.rate = rate;
      u.onend = done;
      u.onerror = done;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch {
      done(); // blocked before a gesture: the role=status line is still read by TalkBack
    }
  });
}
