// apps/app/src/camera/wire.ts — subscribes camera frames, hand guidance, live counts and OPEN_URL links to
// app.socket (wireCameraAndGuidance), and joins them to the wired voice app with the bump guard and the
// gesture hooks (wireCameraIntoApp). main.tsx is not edited here: the integrator passes
// `onWired={(w) => wireCameraIntoApp(w)}` to <AppShell />.
/// <reference types="vite/client" />
import type { ServerMsg } from "@aira/contracts";
import { app } from "../core/app";
import type { WiredApp } from "../core/wire";
import { captureFrame, setQualityListener, startContinuous, stopCamera } from "./capture";
import { createFrameRequestHandler, defaultSleep } from "./requests";
import { createHintSpeaker } from "./hints";
import { startTiltHints } from "./tilt";
import { createGuidanceSession } from "../guidance/session";
import { trackFingertip } from "../guidance/hands";
import { guideTo, stopTone } from "../guidance/tones";
import { COOLDOWN_MS } from "../walk/bump";
import { startBumpGuard, type RunningBumpGuard } from "../walk/start";
import { createCountUx, type CountProgress } from "./countUx";
import { createOpenUrlHandler, defaultDial, defaultOpen } from "./openUrl";
import { createCameraWiring } from "./wiring";

export interface WireOptions {
  /** Live count updates ("Counting 12 / 30"). The core wiring already drives the chip; this is optional. */
  onCountProgress?: (p: CountProgress) => void;
  /** More server-message handlers on the same subscription. */
  extraHandlers?: Array<(m: ServerMsg) => void>;
  /** Force the dev dispatch hook on/off. Default: Vite dev server, or any build opened with `?devtools`. */
  devtools?: boolean;
}

export interface CameraWiring {
  guidance: ReturnType<typeof createGuidanceSession>;
  count: ReturnType<typeof createCountUx>;
  openUrl: ReturnType<typeof createOpenUrlHandler>;
  /** Feed a server message by hand (same path as the socket). */
  dispatch: (m: ServerMsg) => void;
  /** Hand guidance is running and the fingertip is near the target: the bump guard stays muted. */
  isHandNearTarget: () => boolean;
  /** Told when the hand gets near / leaves the guidance target. Returns an unsubscribe fn. */
  onHandNear: (fn: (near: boolean) => void) => () => void;
  unsubscribe: () => void;
}

let current: { wiring: CameraWiring; socket: unknown } | null = null;

function devtoolsDefault(): boolean {
  if (import.meta.env?.DEV) return true;
  const search = (globalThis as unknown as { location?: { search?: string } }).location?.search ?? "";
  return new URLSearchParams(search).has("devtools");
}

const report = (what: string) => (e: unknown) => console.error(`[aira] ${what}`, e);

/**
 * Call once after the onboarding "Start" tap (that user gesture unlocked audio and the camera prompt).
 * A second call returns the live wiring, unless the app was re-wired onto a new socket.
 */
export function wireCameraAndGuidance(opts: WireOptions = {}): CameraWiring {
  if (current) {
    if (current.socket === app.socket) return current.wiring;
    current.wiring.unsubscribe(); // a new WiredApp: never keep listening to the old socket
  }

  const lang = () => app.settings.lang;
  const speak = (s: string) => app.speak(s);
  const hint = createHintSpeaker(speak, lang);
  setQualityListener((h) => hint(h));
  const stopTilt = startTiltHints((h) => hint(h));

  const handleFrame = createFrameRequestHandler({
    capture: captureFrame,
    startContinuous,
    send: (m) => app.send(m),
    hint,
    sleep: defaultSleep,
  });

  let near = false;
  const nearListeners = new Set<(near: boolean) => void>();
  const guidance = createGuidanceSession({
    track: trackFingertip,
    guide: guideTo,
    stopTone,
    speak,
    send: (m) => app.send(m),
    lang,
    now: () => performance.now(),
    onNear: (n) => {
      near = n;
      for (const fn of nearListeners) {
        try { fn(n); } catch (e) { report("hand-near listener failed")(e); }
      }
    },
  });
  const count = createCountUx({ speak, earcon: (n) => app.earcon(n), lang, onProgress: opts.onCountProgress });
  const openUrl = createOpenUrlHandler({ speak, lang, open: defaultOpen, dial: defaultDial });

  let disposed = false;
  let uninstallDev: (() => void) | null = null;
  const socket = app.socket;
  const wiring = createCameraWiring({
    socket,
    handleFrame,
    handlers: [
      (m) => guidance.onServerMsg(m),
      (m) => count.onServerMsg(m),
      (m) => openUrl.onServerMsg(m),
      ...(opts.extraHandlers ?? []),
    ],
    cleanup: [
      stopTilt,
      () => setQualityListener(null),
      () => guidance.end(),
      () => { disposed = true; uninstallDev?.(); uninstallDev = null; nearListeners.clear(); },
    ],
    installDev: (opts.devtools ?? devtoolsDefault())
      ? (dispatch) => {
          void import("./devDispatch").then((d) => {
            if (!disposed) uninstallDev = d.installFake(dispatch);
          });
        }
      : undefined,
  });

  const self: CameraWiring = {
    guidance,
    count,
    openUrl,
    dispatch: wiring.dispatch,
    isHandNearTarget: () => near,
    onHandNear: (fn) => {
      nearListeners.add(fn);
      return () => { nearListeners.delete(fn); };
    },
    unsubscribe: () => {
      wiring.unsubscribe();
      if (current?.wiring === self) current = null;
    },
  };
  current = { wiring: self, socket };
  return self;
}

/** How long the red "Stop!" chip stays up after a bump warning (one warning cooldown). */
export const BUMP_CHIP_MS = COOLDOWN_MS;

/** The parts of the wired voice app this joins to. `WiredApp` (core/wire.ts) satisfies it. */
export type WiredHooks = Pick<WiredApp, "onTalk" | "onStop" | "dispose" | "local" | "confirm">;

export interface PhoneWireOptions extends WireOptions {
  /** false on the demo page (/try): no detector download and no walking. Default true. */
  bumpGuard?: boolean;
}

export interface PhoneWiring {
  cam: CameraWiring;
  /** The running bump guard; null while it starts, when it is off, or when it failed to start. */
  guard: () => RunningBumpGuard | null;
  /** Settles once the bump guard has started, failed or been skipped. Never rejects. */
  ready: Promise<void>;
  /** Stops the camera wiring, the bump guard and the camera stream. The app's own dispose also runs this. */
  dispose: () => void;
}

/**
 * Joins the camera to the wired voice app. Call it from AppShell's `onWired` hook, still inside the Start tap:
 * - answers request_frame, runs hand guidance, speaks live counts, opens allowlisted OPEN_URL links
 * - starts the always-on bump guard; it stays muted while the hand is near a guidance target, and the
 *   server may only raise its urgency; each warning shows the red "Stop!" chip
 * - long-press also ends hand guidance; a double-tap opens a link the browser blocked
 *   (unless a confirm waits for its "yes")
 * - the app's dispose also stops all of this
 * It replaces `onTalk`, `onStop` and `dispose` on `w`, so call it before those are handed to the screen.
 */
export function wireCameraIntoApp(w: WiredHooks, opts: PhoneWireOptions = {}): PhoneWiring {
  const { bumpGuard = true, ...camOpts } = opts;
  const cam = wireCameraAndGuidance(camOpts);
  let guard: RunningBumpGuard | null = null;
  let disposed = false;
  let chipTimer: ReturnType<typeof setTimeout> | null = null;

  const offNear = cam.onHandNear((n) => guard?.setHandNearTarget(n));
  // The server may only RAISE urgency (`cue: "warn"`); nothing it sends can mute the guard.
  const offServer = app.socket.on((m) => guard?.onServerMsg(m));

  const onWarn = () => {
    w.local("bump");
    if (chipTimer) clearTimeout(chipTimer);
    chipTimer = setTimeout(() => {
      chipTimer = null;
      if (!disposed) w.local("bump_clear");
    }, BUMP_CHIP_MS);
  };

  const ready: Promise<void> = bumpGuard
    ? startBumpGuard({ onWarn }).then(
        (g) => {
          if (disposed) { g.dispose(); return; }
          guard = g;
          g.setHandNearTarget(cam.isHandNearTarget());
        },
        report("bump guard did not start; camera and guidance keep working"),
      )
    : Promise.resolve();

  const appTalk = w.onTalk;
  const appStop = w.onStop;
  const appDispose = w.dispose;

  const dispose = () => {
    if (disposed) return;
    disposed = true;
    if (chipTimer) clearTimeout(chipTimer);
    chipTimer = null;
    offNear();
    offServer();
    try { guard?.dispose(); } catch (e) { report("bump guard dispose failed")(e); }
    guard = null;
    cam.unsubscribe();
    try { stopCamera(); } catch (e) { report("camera stop failed")(e); }
  };

  w.onTalk = () => {
    if (!w.confirm.pending && cam.openUrl.openPending()) return; // the double-tap is the user gesture
    appTalk();
  };
  w.onStop = () => {
    cam.guidance.end();
    appStop();
  };
  w.dispose = () => {
    dispose();
    appDispose();
  };

  return { cam, guard: () => guard, ready, dispose };
}
