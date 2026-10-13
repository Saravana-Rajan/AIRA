// frontend/app/src/camera/wire.ts — subscribes camera frames, hand guidance, live counts and OPEN_URL links to
// app.socket (wireCameraAndGuidance), and joins them to the wired voice app with the bump guard and the
// gesture hooks (wireCameraIntoApp). main.tsx is not edited here: the integrator passes
// `onWired={(w) => wireCameraIntoApp(w)}` to <AppShell />.
/// <reference types="vite/client" />
import type { ServerMsg } from "@aira/contracts";
import { app } from "../core/app";
import type { WiredApp } from "../core/wire";
import {
  backgroundHeld, captureFrame, captureFrameIf, getVideo, holdBackground, setQualityListener, startCamera, startContinuous,
  stopCamera, toolCaptureBusy, toolCaptureCount, encodedInWorker, type FrameKeep,
} from "./capture";
import { createWorkingCue } from "./working";
import { startLiveVideo } from "./liveVideo";
import { overrideSource } from "./source";
import { createSceneGate } from "./sceneGate";
import { createFrameRequestHandler, defaultSleep } from "./requests";
import { createRtcSender } from "./rtcSender";
import { createHintSpeaker } from "./hints";
import { startTiltHints } from "./tilt";
import { createGuidanceSession } from "../guidance/session";
import { trackFingertip } from "../guidance/hands";
import { guideTo, stopTone } from "../guidance/tones";
import { boxStore } from "../walk/boxes";
import { parseWalkingModeCommand, walkingMode } from "../walk/mode";
import { startBumpGuard, type RunningBumpGuard } from "../walk/start";
import { walkingModeText } from "../walk/strings";
import { createCountUx, type CountProgress } from "./countUx";
import { createOpenUrlHandler, defaultDial, defaultOpen } from "./openUrl";
import { createCameraWiring } from "./wiring";
import { freshObjects, noteBumpWarning, startHoldUpWatch } from "../ambient/watch";

export interface WireOptions {
  /** Live count updates ("Counting 12 / 30"). The core wiring already drives the chip; this is optional. */
  onCountProgress?: (p: CountProgress) => void;
  /** More server-message handlers on the same subscription. */
  extraHandlers?: Array<(m: ServerMsg) => void>;
  /** Force the dev dispatch hook on/off. Default: Vite dev server, or any build opened with `?devtools`. */
  devtools?: boolean;
  /** The accelerometer says the owner walks: describe frames go even when the picture looks the same. */
  isWalking?: () => boolean;
}

/**
 * Continuous streams: guide/identify frames go as they come; `describe` frames (describe mode, 1 per 3 s) pass
 * a scene-change gate so an unchanged view costs no upload and no ER 2 call; `identify_held` (ambient mode) is
 * not a timer at all but the hold-up watcher, which sends one frame per object the owner holds up.
 */
export function continuousStarter(isWalking?: () => boolean): typeof startContinuous {
  return (purpose, fps, keep?: FrameKeep) => {
    if (purpose === "identify_held") return startHoldUpWatch();
    if (purpose !== "describe" || keep) return startContinuous(purpose, fps, keep);
    return startContinuous(purpose, fps, createSceneGate({ isWalking }).keep);
  };
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

  const lang = () => app.lang();
  const speak = (s: string) => app.speak(s);
  const hint = createHintSpeaker(speak, lang);
  setQualityListener((h) => hint(h));
  const stopTilt = startTiltHints((h) => hint(h));

  const handleFrame = createFrameRequestHandler({
    capture: captureFrame,
    startContinuous: continuousStarter(opts.isWalking),
    send: (m) => app.send(m),
    hint,
    sleep: defaultSleep,
    hold: holdBackground,
    viaWorker: encodedInWorker,
    buffered: () => { try { return app.socket?.bufferedAmount?.() ?? 0; } catch { return 0; } },
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
  // A soft word-free tick the moment AIRA asks for a photo, every 2 s until AIRA speaks (or 30 s).
  const working = createWorkingCue({ play: () => app.earcon("working") });
  const count = createCountUx({ speak, earcon: (n) => app.earcon(n), lang, onProgress: opts.onCountProgress });
  const openUrl = createOpenUrlHandler({ speak, lang, open: defaultOpen, dial: defaultDial });
  // Live video for the dashboard over WebRTC: the same camera stream the frames come from, never a second prompt.
  const rtc = createRtcSender({
    send: (m) => app.send(m),
    getStream: async () => ((getVideo() ?? (await startCamera())).srcObject as MediaStream | null),
  });

  // The camera as Gemini Live video, 1 fps, while the Live session is up (`ready`, not mock) and the camera is on.
  let liveReady = false;
  const stopLive = startLiveVideo({
    capture: (keep) => captureFrameIf("live", keep),
    send: (m) => app.send(m),
    active: () => liveReady && ((app.socket as { isOpen?: () => boolean }).isOpen?.() ?? true),
    cameraOn: () => getVideo() !== null || overrideSource() !== null,
    hidden: () => typeof document !== "undefined" && document.hidden,
    buffered: () => { try { return app.socket?.bufferedAmount?.() ?? 0; } catch { return 0; } },
    toolBusy: () => toolCaptureBusy() || backgroundHeld(),
    toolCount: toolCaptureCount,
    labels: () => { try { return freshObjects().map((o) => o.label); } catch { return []; } },
  });
  const onReady = (m: ServerMsg) => { if (m.t === "ready") liveReady = !m.mock; };

  let disposed = false;
  let uninstallDev: (() => void) | null = null;
  const socket = app.socket;
  const wiring = createCameraWiring({
    socket,
    handleFrame,
    handlers: [
      (m) => working.onServerMsg(m),
      (m) => guidance.onServerMsg(m),
      (m) => count.onServerMsg(m),
      (m) => openUrl.onServerMsg(m),
      (m) => boxStore.onServerMsg(m),   // ER 2 boxes for the "Show boxes" overlay
      (m) => rtc.onServerMsg(m),        // WebRTC signalling from the dashboard
      onReady,                          // live video starts once the Live session is up
      ...(opts.extraHandlers ?? []),
    ],
    cleanup: [
      () => working.stop(),
      stopLive,
      () => rtc.dispose(),
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

/** How long the red "Stop!" chip stays up after a bump warning: short, so it clears before the next look. */
export const BUMP_CHIP_MS = 1200;

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
  /** Walking mode (the persisted bump-guard switch, default on). Off ⇒ no "Stop!" at all. */
  walkingMode: () => boolean;
  /** Switches walking mode, persists it, tells the guard and says so (settings toggle, voice command). */
  setWalkingMode: (on: boolean) => void;
  /** Stops the camera wiring, the bump guard and the camera stream. The app's own dispose also runs this. */
  dispose: () => void;
}

/**
 * Joins the camera to the wired voice app. Call it from AppShell's `onWired` hook, still inside the Start tap:
 * - answers request_frame, runs hand guidance, speaks live counts, opens allowlisted OPEN_URL links
 * - starts the bump guard; it warns only while the accelerometer says the owner is walking (approach-only
 *   without a sensor), stays muted while the hand is near a guidance target, and the server may only raise
 *   its urgency; each warning shows the red "Stop!" chip. Walking mode (persisted, default on) switches it
 *   off entirely: `setWalkingMode`, or a server `state` message with summary "WALKING_MODE on|off"
 * - long-press also ends hand guidance; a double-tap opens a link the browser blocked
 *   (unless a confirm waits for its "yes")
 * - the app's dispose also stops all of this
 * It replaces `onTalk`, `onStop` and `dispose` on `w`, so call it before those are handed to the screen.
 */
export function wireCameraIntoApp(w: WiredHooks, opts: PhoneWireOptions = {}): PhoneWiring {
  const { bumpGuard = true, ...camOpts } = opts;
  let guard: RunningBumpGuard | null = null;
  const cam = wireCameraAndGuidance({
    isWalking: () => guard?.motion?.available === true && guard.motion.isWalking(performance.now()),
    ...camOpts,
  });
  let disposed = false;
  let chipTimer: ReturnType<typeof setTimeout> | null = null;

  const offNear = cam.onHandNear((n) => guard?.setHandNearTarget(n));
  // The server may only RAISE urgency (`cue: "warn"`); nothing it sends can mute the guard, except the
  // owner's own walking-mode command relayed as a `state` message ("WALKING_MODE on|off", like OPEN_URL).
  const offServer = app.socket.on((m) => {
    guard?.onServerMsg(m);
    if (m.t === "state") {
      const on = parseWalkingModeCommand(m.summary);
      if (on !== null) walkingMode.set(on);
    }
  });
  // Every switch (server, settings, voice) is confirmed aloud once; the guard follows the store itself.
  const offMode = walkingMode.subscribe((on) => app.speak(walkingModeText(on, app.lang())));

  const onWarn = () => {
    noteBumpWarning(performance.now());   // danger beats a product name: hold-up frames pause
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
    offMode();
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

  return {
    cam,
    guard: () => guard,
    ready,
    walkingMode: () => walkingMode.get(),
    setWalkingMode: (on) => walkingMode.set(on),
    dispose,
  };
}
