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
  const timer = ctrl ? setTimeout(() => ctrl.abort(), o.timeoutMs ?? 20_000) : null;  // first call may wake the server
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
