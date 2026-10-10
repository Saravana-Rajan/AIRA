import type { AppApi, AppDeps } from "./types";

/** The interfaces-file singleton. Empty until initApp() runs (from wireApp in main.tsx, or a test). */
export const app = {} as AppApi;

/**
 * Fills the `app` singleton. `speak` and `send` read `app.settings` and `app.socket` when called,
 * so a settings change or a socket swap takes effect without calling initApp again.
 */
export function initApp(deps: AppDeps): AppApi {
  const api: AppApi = {
    socket: deps.socket as AppApi["socket"],
    settings: deps.settings,
    speak: (text) => deps.speakImpl(text, app.settings),
    earcon: (n) => deps.earconImpl(n),
    haptic: (n) => deps.hapticImpl(n),
    send: (m) => app.socket.send(m),
  };
  return Object.assign(app, api);
}
