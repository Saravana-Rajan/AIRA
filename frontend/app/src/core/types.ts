import type { ClientMsg, CueName, Lang, Verbosity } from "@aira/contracts";
import type { AiraSocket } from "../net/socket";
import type { EarconName } from "../voice/earcons";

export type HapticName = "ack" | "warn" | "done" | "match" | "mismatch";

export interface Settings {
  /**
   * "auto" (the default, and the only value the app itself ever saves): AIRA follows whoever speaks, turn by
   * turn, in any language. A label language here is only a starting point for the first line and the labels
   * before anyone has spoken (tests and tools use it); it never fixes the conversation's language.
   */
  lang: Lang | "auto";
  verbosity: Verbosity;
  screenReaderMode: boolean;
  /** 0.5–2.0. Applies only to text the client speaks itself (local status, fallbacks). */
  speechRate: number;
  /** "sunrise_dairy" by default (the demo shop). */
  shopId: string;
  onboarded: boolean;
  /** Draw the MediaPipe and ER 2 boxes over a small camera preview (for sighted helpers and the video). Off by default. */
  showBoxes: boolean;
}

export interface AppDeps {
  socket: Pick<AiraSocket, "send">;
  settings: Settings;
  speakImpl: (text: string, s: Settings) => void;
  earconImpl: (n: EarconName) => void;
  hapticImpl: (n: HapticName) => void;
  /** The label language right now (the one the server last detected). Default: from `settings.lang`. */
  langImpl?: () => Lang;
}

/** The `app` singleton shape from the interfaces file. */
export interface AppApi {
  socket: AiraSocket;
  settings: Settings;
  speak(text: string): void;
  earcon(n: EarconName): void;
  haptic(n: HapticName): void;
  send(m: ClientMsg): boolean;
  /** The language of the labels and local TTS right now: what the speaker last used, as the server heard it. */
  lang(): Lang;
}
