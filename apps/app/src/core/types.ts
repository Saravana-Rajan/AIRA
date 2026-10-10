import type { ClientMsg, CueName, Lang, Verbosity } from "@aira/contracts";
import type { AiraSocket } from "../net/socket";

export type HapticName = "ack" | "warn" | "done" | "match" | "mismatch";

export interface Settings {
  lang: Lang;
  verbosity: Verbosity;
  screenReaderMode: boolean;
  /** 0.5–2.0. Applies only to text the client speaks itself (local status, fallbacks). */
  speechRate: number;
  /** "murugan_dairy" by default (the demo shop). */
  shopId: string;
  onboarded: boolean;
}

export interface AppDeps {
  socket: Pick<AiraSocket, "send">;
  settings: Settings;
  speakImpl: (text: string, s: Settings) => void;
  earconImpl: (n: CueName) => void;
  hapticImpl: (n: HapticName) => void;
}

/** The `app` singleton shape from the interfaces file. */
export interface AppApi {
  socket: AiraSocket;
  settings: Settings;
  speak(text: string): void;
  earcon(n: CueName): void;
  haptic(n: HapticName): void;
  send(m: ClientMsg): boolean;
}
