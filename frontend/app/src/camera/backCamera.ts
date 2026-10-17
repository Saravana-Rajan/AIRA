// frontend/app/src/camera/backCamera.ts
// Guarantees the BACK main camera on Android phones. The phone is chest-mounted, so the
// back camera faces forward; a front camera would only ever see the shopkeeper's chin.
// Pure helpers over a MediaDevices-like object so tests can run with fakes.

export type CameraFacing = "user" | "environment" | "unknown";
export const BACK_CAMERA_KEY = "aira.camera.backDeviceId";

export interface DeviceLike {
  deviceId: string;
  kind: string;
  label: string;
}
export interface StorageLike {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
}
export interface BackCameraResult {
  stream: MediaStream;
  facing: CameraFacing;
  deviceId: string | null;
}

const BACK_RE = /back|rear|environment|0, facing back/i;
const FRONT_RE = /front|user|selfie|facing front/i;
const SECONDARY_RE = /ultra[\s-]?wide|wide[\s-]?angle|macro|tele(photo)?|depth|camera2 [2-9]\d*|camera2 \d{2,}/i;
const MAIN_RE = /camera2 0\b|camera 0\b/i;

/** Rank a back-camera label: higher is better. Main lens first, extra lenses last. */
function rank(label: string): number {
  let r = 0;
  if (MAIN_RE.test(label)) r += 10;
  if (SECONDARY_RE.test(label)) r -= 5;
  return r;
}

/** Picks the deviceId of the main back lens, or null when no back camera is listed. */
export function pickBackDevice(devices: readonly DeviceLike[]): string | null {
  let best: { id: string; r: number } | null = null;
  for (const d of devices) {
    if (d.kind !== "videoinput") continue;
    const label = d.label ?? "";
    if (!BACK_RE.test(label)) continue;
    if (FRONT_RE.test(label) && !/back|rear/i.test(label)) continue;
    const r = rank(label);
    if (!best || r > best.r) best = { id: d.deviceId, r };
  }
  return best?.id ?? null;
}

function trackSettings(stream: MediaStream): { facingMode?: string; deviceId?: string } {
  try {
    const t = stream.getVideoTracks?.()[0];
    return t?.getSettings?.() ?? {};
  } catch {
    return {};
  }
}

function facingOf(settings: { facingMode?: string }): CameraFacing {
  const f = settings.facingMode;
  return f === "environment" || f === "user" ? f : "unknown";
}

function stopAll(stream: MediaStream): void {
  try {
    stream.getTracks().forEach((t) => t.stop());
  } catch {
    /* ignore */
  }
}

function readSaved(storage: StorageLike | null): string | null {
  try {
    return storage?.getItem(BACK_CAMERA_KEY) || null;
  } catch {
    return null;
  }
}
function save(storage: StorageLike | null, id: string | null): void {
  try {
    if (id) storage?.setItem(BACK_CAMERA_KEY, id);
  } catch {
    /* storage may be blocked (private mode); the camera still works */
  }
}

const baseVideo = (): MediaTrackConstraints => ({ facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } });

/**
 * Opens the camera asking for the environment side, then verifies the result and
 * switches to the main back lens when the browser handed us something else.
 * Never throws after the first stream is open: any later failure keeps that stream.
 */
export async function ensureBackCamera(md: MediaDevices, storage: StorageLike | null): Promise<BackCameraResult> {
  const saved = readSaved(storage);
  const video = baseVideo();
  if (saved) video.deviceId = { ideal: saved };
  let first: MediaStream;
  try {
    first = await md.getUserMedia({ video, audio: false });
  } catch (e) {
    if (!saved) throw e;
    // A stale saved id can make some browsers fail; retry without it.
    first = await md.getUserMedia({ video: baseVideo(), audio: false });
  }

  const settings = trackSettings(first);
  const facing = facingOf(settings);
  const firstId = settings.deviceId ?? null;
  if (facing === "environment") {
    save(storage, firstId);
    return { stream: first, facing, deviceId: firstId };
  }

  // Not clearly the back camera: look at the labels (available once permission is granted).
  let devices: DeviceLike[] = [];
  try {
    devices = (await md.enumerateDevices()) as DeviceLike[];
  } catch {
    return { stream: first, facing, deviceId: firstId };
  }
  const backId = pickBackDevice(devices);
  if (!backId) return { stream: first, facing, deviceId: firstId };
  if (backId === firstId) {
    save(storage, backId);
    return { stream: first, facing: "environment", deviceId: backId };
  }

  try {
    const second = await md.getUserMedia({ video: { deviceId: { exact: backId }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
    stopAll(first);
    const s2 = trackSettings(second);
    const id2 = s2.deviceId ?? backId;
    save(storage, id2);
    const f2 = facingOf(s2);
    return { stream: second, facing: f2 === "user" ? "user" : "environment", deviceId: id2 };
  } catch {
    return { stream: first, facing, deviceId: firstId };
  }
}
