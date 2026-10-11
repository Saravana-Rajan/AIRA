import type { Lang } from "@aira/contracts";
import type { AppApi, AppDeps } from "./types";

/** The interfaces-file singleton. Empty until initApp() runs (from wireApp in main.tsx, or a test). */
export const app = {} as AppApi;

const settingsLang = (): Lang => (app.settings.lang === "auto" ? "en" : app.settings.lang);

/**
 * Fills the `app` singleton. `speak`, `send` and `lang` read `app.settings`, `app.socket` and the wiring's
 * current language when called, so a settings change, a socket swap or a language switch takes effect
 * without calling initApp again.
 */
export function initApp(deps: AppDeps): AppApi {
  const api: AppApi = {
    socket: deps.socket as AppApi["socket"],
    settings: deps.settings,
    speak: (text) => deps.speakImpl(text, app.settings),
    earcon: (n) => deps.earconImpl(n),
    haptic: (n) => deps.hapticImpl(n),
    send: (m) => app.socket.send(m),
    lang: () => deps.langImpl?.() ?? settingsLang(),
  };
  return Object.assign(app, api);
}
