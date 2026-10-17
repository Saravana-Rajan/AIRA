// frontend/app/src/camera-mode/boxes.ts
// The room camera (/camera) also runs the on-device MediaPipe detector at a low rate and sends its boxes as
// {t:"boxes", source:"mediapipe"} so the monitor can draw the room view with live boxes. Pure loop; the
// production detect() wraps the bump guard's worker detector and the shared rear camera.
import type { ClientMsg } from "@aira/contracts";
import { toBoxesMsg, type BoxReport } from "../walk/boxes";

/** One detection a second: the room view changes slowly and the phone on the stand must not heat up. */
export const ROOM_BOXES_INTERVAL_MS = 1000;

export interface RoomBoxesDeps {
  /** One detector pass on the current frame; null when the camera is not ready. */
  detect: () => Promise<BoxReport[] | null>;
  send: (m: ClientMsg) => boolean;
  intervalMs?: number;
}

export interface RoomBoxes {
  stop(): void;
}

/** Detects and sends on a fixed cadence; detections never overlap, an error only skips a tick. */
export function startRoomBoxes(deps: RoomBoxesDeps): RoomBoxes {
  const interval = Math.max(200, deps.intervalMs ?? ROOM_BOXES_INTERVAL_MS);
  let stopped = false;
  let busy = false;
  const tick = async () => {
    if (stopped || busy) return;
    busy = true;
    try {
      const reports = await deps.detect();
      if (!stopped && reports) deps.send(toBoxesMsg(reports, "mediapipe"));
    } catch {
      /* a detector hiccup: try again next tick */
    } finally {
      busy = false;
    }
  };
  const timer = setInterval(() => { void tick(); }, interval);
  void tick();
  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
  };
}

/**
 * Production detect(): the bump-guard worker detector on a small frame of the shared rear camera. The
 * detector (a ~4.5 MB model download) is created on the first call; a failure is remembered so the camera
 * page keeps streaming frames without boxes.
 */
export function createRoomDetect(): { detect: () => Promise<BoxReport[] | null>; close: () => void } {
  let detector: import("../walk/detector").Detector | null = null;
  let starting: Promise<void> | null = null;
  let failed = false;
  const start = () =>
    (starting ??= (async () => {
      try {
        const { createDetector } = await import("../walk/detector");
        detector = await createDetector();
      } catch {
        failed = true;
      }
    })());
  return {
    async detect() {
      if (failed) return null;
      if (!detector) { await start(); if (!detector) return null; }
      const { grabFrame } = await import("../walk/start");
      const g = await grabFrame();
      if (!g) return null;
      const r = await detector.detect(g.frame, g.ts);
      return r.dets.map((d) => ({ label: d.label, box: d.box, score: d.score }));
    },
    close() {
      detector?.close();
      detector = null;
    },
  };
}
