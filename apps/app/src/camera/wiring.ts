// apps/app/src/camera/wiring.ts — the pure subscription behind wireCameraAndGuidance() (no browser or lane imports).
import type { ServerMsg } from "@aira/contracts";

type FrameRequest = Extract<ServerMsg, { t: "request_frame" }>;
type Handler = (m: ServerMsg) => unknown;

export interface CameraWiringDeps {
  socket: { on(h: (m: ServerMsg) => void): () => void };
  handleFrame: (m: FrameRequest) => Promise<void> | void;
  handlers: Handler[];
  cleanup?: Array<() => void>;
  /** Receives the same dispatch fn the socket uses (dev hook). May return an uninstall fn. */
  installDev?: (dispatch: (m: ServerMsg) => void) => void | (() => void);
}

const report = (e: unknown) => console.error("[aira] server message handler failed", e);

/** Subscribes every handler to the socket once. One failing handler never blocks the others. */
export function createCameraWiring(deps: CameraWiringDeps) {
  const dispatch = (m: ServerMsg) => {
    if (m.t === "request_frame") {
      try {
        const p = deps.handleFrame(m);
        if (p && typeof p.catch === "function") p.catch(report);
      } catch (e) {
        report(e);
      }
    }
    for (const h of deps.handlers) {
      try {
        h(m);
      } catch (e) {
        report(e);
      }
    }
  };

  const off = deps.socket.on(dispatch);
  const uninstallDev = deps.installDev?.(dispatch);
  let live = true;

  return {
    dispatch,
    unsubscribe(): void {
      if (!live) return;
      live = false;
      off();
      for (const c of [...(deps.cleanup ?? []), ...(uninstallDev ? [uninstallDev] : [])]) {
        try {
          c();
        } catch (e) {
          report(e);
        }
      }
    },
  };
}
