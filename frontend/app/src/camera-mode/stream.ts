// frontend/app/src/camera-mode/stream.ts
// Shop-camera mode: this phone sits on a stand and streams one "ask" frame every few seconds over the same
// /ws socket, identified by the camera role (uid "camera:<name>"). No mic, no audio, no Live session: the
// server (aira_live.cameras) reads these frames, gates them on motion and speaks to the owner's phone.
// The wire contracts are unchanged: a normal hello and normal frame messages.
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import type { AiraSocket } from "../net/socket";
import { startRoomBoxes, type RoomBoxes, type RoomBoxesDeps } from "./boxes";
import type { Settings } from "../core/types";
import { langFromPhone, phoneLanguages } from "../core/lang";
import { APP_VERSION } from "../core/wire";

export const CAMERA_UID_PREFIX = "camera:";
export const DEFAULT_INTERVAL_MS = 5000;
export const CAMERA_NAME_KEY = "aira.camera.name";
export const DEFAULT_CAMERA_NAME = "front";

export type Hello = Extract<ClientMsg, { t: "hello" }>;
export type FrameMsg = Extract<ClientMsg, { t: "frame" }>;
export type CameraSocket = Pick<AiraSocket, "connect" | "send" | "on" | "close">;
export type CameraStatus = "connecting" | "streaming" | "camera_error" | "stopped";

/** Same rule as the server (aira_live.cameras.config.safe_name): lower-case, [a-z0-9_-], at most 32 chars. */
export function normalizeCameraName(raw: string): string {
  const name = raw.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32);
  return name || DEFAULT_CAMERA_NAME;
}

/** A hello with the camera role. No idToken (there is no Firebase Auth) and the owner's language. The hello
 * contract carries one of ta/hi/en, so the app's "auto" (follow the speaker) maps to the phone's language, as
 * core/wire.ts does for the owner's own hello. */
export function cameraHello(name: string, settings: Settings): Hello {
  return {
    t: "hello",
    uid: CAMERA_UID_PREFIX + normalizeCameraName(name), // no shopId: the server picks the one shop
    lang: settings.lang === "auto" ? langFromPhone(phoneLanguages()) : settings.lang,
    verbosity: "short",
    appVersion: APP_VERSION,
  };
}

export function loadCameraName(store: Storage | null = safeStorage()): string {
  try {
    return normalizeCameraName(store?.getItem(CAMERA_NAME_KEY) ?? "");
  } catch {
    return DEFAULT_CAMERA_NAME;
  }
}

export function saveCameraName(name: string, store: Storage | null = safeStorage()): void {
  try {
    store?.setItem(CAMERA_NAME_KEY, normalizeCameraName(name));
  } catch {
    /* private mode or quota: keep the in-memory name */
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

export interface ShopCameraDeps {
  socket: CameraSocket;
  /** Grabs one JPEG frame from the rear camera (camera/capture.ts captureFrame). */
  capture: (purpose: "ask") => Promise<FrameMsg>;
  name: string;
  settings: Settings;
  intervalMs?: number;
  onStatus?: (s: CameraStatus) => void;
  /** The on-device detector for live boxes (camera-mode/boxes.ts). Absent: frames only. */
  boxes?: Pick<RoomBoxesDeps, "detect" | "intervalMs">;
}

export interface RunningShopCamera {
  stop(): void;
}

/**
 * Connects with the camera role and, once the server answers `ready`, sends one frame per interval.
 * Captures never overlap; a failed capture reports `camera_error` and the loop keeps going.
 */
export function startShopCamera(deps: ShopCameraDeps): RunningShopCamera {
  const interval = Math.max(1000, deps.intervalMs ?? DEFAULT_INTERVAL_MS);
  const status = (s: CameraStatus) => deps.onStatus?.(s);
  let timer: ReturnType<typeof setInterval> | null = null;
  let boxes: RoomBoxes | null = null;
  let stopped = false;
  let busy = false;

  const tick = async () => {
    if (stopped || busy) return;
    busy = true;
    try {
      const f = await deps.capture("ask");
      if (!stopped) deps.socket.send(f);
    } catch {
      if (!stopped) status("camera_error");
    } finally {
      busy = false;
    }
  };

  const off = deps.socket.on((m: ServerMsg) => {
    if (stopped || m.t !== "ready") return;
    if (timer !== null) clearInterval(timer); // a reconnect: restart the cadence
    status("streaming");
    timer = setInterval(() => { void tick(); }, interval);
    void tick();
    if (deps.boxes) {   // live boxes at a low rate, once the server is ready (a reconnect restarts them too)
      boxes?.stop();
      boxes = startRoomBoxes({ detect: deps.boxes.detect, intervalMs: deps.boxes.intervalMs, send: (m) => deps.socket.send(m) });
    }
  });

  status("connecting");
  deps.socket.connect(cameraHello(deps.name, deps.settings));

  return {
    stop() {
      if (stopped) return;
      stopped = true;
      if (timer !== null) clearInterval(timer);
      timer = null;
      boxes?.stop();
      boxes = null;
      off();
      deps.socket.close();
      status("stopped");
    },
  };
}
