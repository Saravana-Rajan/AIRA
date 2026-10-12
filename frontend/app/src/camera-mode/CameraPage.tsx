// frontend/app/src/camera-mode/CameraPage.tsx
// /camera: shop-camera mode. A second phone on a stand becomes a fixed camera for the shop (shared/docs/ops/cctv.md).
// Light theme, big controls, every state change announced through a polite live region. No microphone.
import { useEffect, useRef, useState } from "preact/hooks";
import { AiraSocket } from "../net/socket";
import { captureFrame, stopCamera } from "../camera/capture";
import { loadSettings } from "../core/settings";
import {
  DEFAULT_INTERVAL_MS, loadCameraName, normalizeCameraName, saveCameraName, startShopCamera,
  type CameraStatus, type RunningShopCamera,
} from "./stream";
import { createRoomDetect } from "./boxes";
import "../ui/tokens.css";
import "./camera.css";

const DEFAULT_WS = "ws://localhost:8080/ws";

function wsUrl(): string {
  return (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_AIRA_LIVE_WS ?? DEFAULT_WS;
}

export interface StartArgs {
  name: string;
  onStatus: (s: CameraStatus) => void;
}

/** Production start: a fresh socket with the camera role, the real rear camera and the on-device detector
 * for live boxes (one pass a second). */
export function defaultStart({ name, onStatus }: StartArgs): RunningShopCamera {
  const room = createRoomDetect();
  const run = startShopCamera({
    socket: new AiraSocket(wsUrl()),
    capture: captureFrame,
    name,
    settings: loadSettings(),
    intervalMs: DEFAULT_INTERVAL_MS,
    onStatus,
    boxes: { detect: room.detect },
  });
  return {
    stop() {
      run.stop();
      room.close();
      stopCamera();
    },
  };
}

const STATUS_TEXT: Record<CameraStatus, string> = {
  connecting: "Connecting to AIRA.",
  streaming: "Streaming. One picture every 5 seconds.",
  camera_error: "Camera not available. Allow the camera and try again.",
  stopped: "Stopped.",
};

export function CameraPage({ start = defaultStart }: { start?: (a: StartArgs) => RunningShopCamera }) {
  const [name, setName] = useState(() => loadCameraName());
  const [running, setRunning] = useState<RunningShopCamera | null>(null);
  const [status, setStatus] = useState<string>("Not streaming.");
  const runningRef = useRef<RunningShopCamera | null>(null);

  useEffect(() => () => { runningRef.current?.stop(); }, []);

  const toggle = () => {
    if (runningRef.current) {
      runningRef.current.stop();
      runningRef.current = null;
      setRunning(null);
      setStatus(STATUS_TEXT.stopped);
      return;
    }
    const safe = normalizeCameraName(name);
    setName(safe);
    saveCameraName(safe);
    const run = start({ name: safe, onStatus: (s) => setStatus(STATUS_TEXT[s]) });
    runningRef.current = run;
    setRunning(run);
  };

  return (
    <main class="camera">
      <h1 class="camera__title">AIRA shop camera</h1>
      <p class="camera__lead">
        Put this phone on a stand facing the shelf, the counter or the door. It sends one picture every few
        seconds to AIRA. No microphone is used on this phone. Stop it any time with the big button.
      </p>
      <label class="camera__label" for="camera-name">Camera name</label>
      <input
        id="camera-name"
        class="camera__input"
        type="text"
        autocomplete="off"
        value={name}
        disabled={running !== null}
        onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)}
        aria-describedby="camera-name-help"
      />
      <p id="camera-name-help" class="camera__help">Must match a camera listed in AIRA's settings, e.g. "front" or "shelf".</p>
      <button
        type="button"
        class={`camera__start${running ? " camera__start--on" : ""}`}
        onClick={toggle}
        aria-pressed={running !== null}
      >
        {running ? "Stop camera" : "Start camera"}
      </button>
      <p class="camera__status" role="status" aria-live="polite">{status}</p>
    </main>
  );
}
