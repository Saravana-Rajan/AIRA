import type { ServerMsg } from "@aira/contracts";

export interface SpeakCtx { mock: boolean; msSinceLiveAudio: number; screenReaderMode: boolean }

/** A `state` summary that hands a link to the app (click-to-chat, tel:). Never read aloud. */
export const OPEN_URL_PREFIX = "OPEN_URL ";
const isOpenUrl = (msg: ServerMsg) => msg.t === "state" && msg.summary.startsWith(OPEN_URL_PREFIX);

/** Live audio that ended this recently counts as "AIRA already said it". */
export const LIVE_AUDIO_GRACE_MS = 1000;

/**
 * No double speech: Gemini Live audio is the primary voice, so the client only fills gaps.
 * - Only `confirm`, `error` and `state` can ever be spoken by the client.
 * - Mock sessions have no Live audio, so the client speaks them all.
 * - In real sessions Live narrates state changes; confirm/error are spoken only when
 *   no Live audio arrived in the last second.
 * `screenReaderMode` does not change WHETHER we speak, only HOW (aria-live region vs TTS).
 */
export function shouldClientSpeak(msg: ServerMsg, ctx: SpeakCtx): boolean {
  if (msg.t !== "confirm" && msg.t !== "error" && msg.t !== "state") return false;
  if (isOpenUrl(msg)) return false;
  if (ctx.mock) return true;
  if (msg.t === "state") return false;
  return ctx.msSinceLiveAudio > LIVE_AUDIO_GRACE_MS;
}

export function textFor(msg: ServerMsg): string | null {
  switch (msg.t) {
    case "confirm": return msg.prompt;
    case "error": return msg.say;
    case "state": return isOpenUrl(msg) ? null : msg.summary.slice(0, 120);
    default: return null;
  }
}
