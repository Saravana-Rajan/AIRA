// apps/app/src/bench/install.ts
import { app } from "../core/app";
import { attachBench, benchEnabled } from "./attach";

export { loadSaved } from "./attach";

/**
 * Observes all socket traffic (incoming via app.socket.on, outgoing via app.socket.send, which
 * app.send goes through) without changing it, and logs battery level. Idempotent.
 * Call after wireApp()/initApp() has filled `app`; before that it is a no-op.
 */
export function installBench(): () => void {
  if (!app.socket) return () => {};
  return attachBench(app);
}

/** Call once at app startup: records only when bench mode was switched on at /bench. */
export function installBenchIfEnabled(): () => void {
  return benchEnabled() ? installBench() : () => {};
}
