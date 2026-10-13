import type { ServerMsg } from "@aira/contracts";

export interface SpeakCtx { mock: boolean; msSinceLiveAudio: number; screenReaderMode: boolean }

/** A `state` summary that hands a link to the app (click-to-chat, tel:). Never read aloud. */
export const OPEN_URL_PREFIX = "OPEN_URL ";
const isOpenUrl = (msg: ServerMsg) => msg.t === "state" && msg.summary.startsWith(OPEN_URL_PREFIX);

/**
 * No double speech: Gemini Live audio is the primary voice, so the client only fills gaps.
 * - Only `confirm`, `error` and `state` can ever be spoken by the client.
 * - Mock sessions have no Live audio, so the client speaks them all.
 * - In real sessions Live narrates state changes and asks every confirm question itself (prompt rule 1), so
 *   the client never speaks them: the confirm message usually lands before Live's audio for the same turn,
 *   and speaking it here meant the owner heard the question twice.
 * - An `error` is not relayed by Live, so it is always spoken. It used to be dropped outright when Live audio
 *   had played in the last second, so the owner never heard why something failed. Timing is now the speaker's
 *   job (core/wire.ts): the line waits until Live playback is idle (never over Live audio), a waiting error is
 *   not displaced by a lesser line, and a repeat within SPEAK_DEDUPE_MS is spoken once.
 * `screenReaderMode` does not change WHETHER we speak, only HOW (aria-live region vs TTS).
 */
export function shouldClientSpeak(msg: ServerMsg, ctx: SpeakCtx): boolean {
  if (msg.t !== "confirm" && msg.t !== "error" && msg.t !== "state") return false;
  if (isOpenUrl(msg)) return false;
  if (ctx.mock) return true;
  return msg.t === "error";
}

export function textFor(msg: ServerMsg): string | null {
  switch (msg.t) {
    case "confirm": return msg.prompt;
    case "error": return msg.say;
    case "state": return isOpenUrl(msg) ? null : msg.summary.slice(0, 120);
    default: return null;
  }
}
