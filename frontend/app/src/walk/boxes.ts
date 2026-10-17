// frontend/app/src/walk/boxes.ts
// The phone's own boxes, for the live monitor and the "Show boxes" overlay. Pure (no DOM, no network):
// - toBoxesMsg: MediaPipe detections (already [ymin, xmin, ymax, xmax] 0-1000, see detections.ts) → the
//   additive client message {t:"boxes", source:"mediapipe", objects:[{label, box, score, severity?}]}
// - createBoxSender: sends at most BOXES_MAX_PER_S a second (the server drops anything faster)
// - boxStore: the latest MediaPipe and ER 2 boxes, for the overlay (ui/BoxOverlay)
import type { Box, BoxObject, ClientMsg, ServerMsg } from "@aira/contracts";

/** At most this many `boxes` messages a second leave the phone (monitor v2 §5a). */
export const BOXES_MAX_PER_S = 5;
/** Boxes older than this are not drawn (a stale ER 2 box must not float over a new view). */
export const ER2_TTL_MS = 6000;
export const MEDIAPIPE_TTL_MS = 1000;

export type BoxReport = { label: string; box: Box; score?: number; danger?: boolean };

/** The client message for one detector frame. Labels are trimmed; the bump guard's "danger" flag becomes
 * severity "danger"; everything else is left as the detector gave it (already clamped to 0-1000). */
export function toBoxesMsg(reports: readonly BoxReport[], source: "mediapipe" | "bump" = "mediapipe"): Extract<ClientMsg, { t: "boxes" }> {
  const objects: BoxObject[] = reports.map((r) => ({
    label: r.label.trim() || "object",
    box: r.box,
    ...(typeof r.score === "number" && Number.isFinite(r.score) ? { score: Math.round(r.score * 100) / 100 } : {}),
    ...(r.danger ? { severity: "danger" as const } : {}),
  }));
  return { t: "boxes", source, objects };
}

export interface BoxSenderDeps {
  send: (m: ClientMsg) => boolean;
  /** Monotonic ms clock (performance.now()). */
  now: () => number;
  maxPerS?: number;
}

/**
 * Throttled sender: a frame that comes sooner than 1000/maxPerS ms after the last one SENT is dropped (so
 * 8 Hz detections become 5 messages a second). A frame with danger in it is never dropped: the red box
 * must reach the monitor at once.
 */
export function createBoxSender(deps: BoxSenderDeps) {
  const minGap = 1000 / (deps.maxPerS ?? BOXES_MAX_PER_S);
  let lastAt = -Infinity;
  let sent = 0;
  let dropped = 0;
  return {
    push(reports: readonly BoxReport[], source: "mediapipe" | "bump" = "mediapipe"): boolean {
      const now = deps.now();
      const urgent = reports.some((r) => r.danger);
      if (!urgent && now - lastAt < minGap) { dropped++; return false; }
      lastAt = now;
      sent++;
      return deps.send(toBoxesMsg(reports, source));
    },
    stats: () => ({ sent, dropped }),
  };
}

export type Er2Boxes = { objects: BoxObject[]; target?: string; found?: boolean; at: number };
export type BoxSnapshot = {
  mediapipe: { reports: BoxReport[]; at: number };
  er2: Er2Boxes | null;
};

/** The latest boxes from both sources, for the overlay. One store per page (the overlay reads `boxStore`). */
export function createBoxStore(now: () => number = () => (typeof performance !== "undefined" ? performance.now() : Date.now())) {
  let snap: BoxSnapshot = { mediapipe: { reports: [], at: -Infinity }, er2: null };
  const subs = new Set<(s: BoxSnapshot) => void>();
  const emit = () => subs.forEach((fn) => { try { fn(snap); } catch { /* a bad subscriber never blocks the others */ } });
  return {
    get: () => snap,
    setMediapipe(reports: BoxReport[]) {
      snap = { ...snap, mediapipe: { reports: [...reports], at: now() } };
      emit();
    },
    /** Server `boxes` messages (ER 2). Any other message is ignored, so this can sit on the socket directly. */
    onServerMsg(m: ServerMsg) {
      if (m.t !== "boxes" || m.source !== "er2") return;
      snap = { ...snap, er2: { objects: [...m.objects], target: m.target, found: m.found, at: now() } };
      emit();
    },
    clear() {
      snap = { mediapipe: { reports: [], at: -Infinity }, er2: null };
      emit();
    },
    subscribe(fn: (s: BoxSnapshot) => void) {
      subs.add(fn);
      return () => { subs.delete(fn); };
    },
  };
}

export type BoxStore = ReturnType<typeof createBoxStore>;

/** The app-wide store: the bump guard writes MediaPipe boxes, the camera wiring writes ER 2 boxes. */
export const boxStore: BoxStore = createBoxStore();
