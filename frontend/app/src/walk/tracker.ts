// frontend/app/src/walk/tracker.ts
// Gives each obstacle a stable key across frames (greedy IoU matching) and keeps its recent boxes,
// so timeToContact() can see it grow and the 1.5 s cooldown applies per object. Pure (no DOM).
import type { Box } from "../guidance/geometry";
import { iou, type RawDet } from "./detections";

export type Track = { key: string; label: string; box: Box; boxes: Box[]; ts: number[] };

export interface TrackerOptions {
  iouMin?: number;     // minimum overlap to call it the same object
  historyMs?: number;  // box history kept for time-to-contact
  maxAgeMs?: number;   // a track unseen for longer is forgotten (and gets a new key)
}

export function createTracker(opts: TrackerOptions = {}) {
  const iouMin = opts.iouMin ?? 0.3;
  const historyMs = opts.historyMs ?? 1000;
  const maxAgeMs = opts.maxAgeMs ?? 1500;
  let tracks: (Track & { lastSeen: number })[] = [];
  let seq = 0;

  function update(dets: RawDet[], ts: number): Track[] {
    tracks = tracks.filter((t) => ts - t.lastSeen <= maxAgeMs);
    const free = new Set(tracks);
    const out: Track[] = [];
    const byArea = [...dets].sort((a, b) => (b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) - (a.box[2] - a.box[0]) * (a.box[3] - a.box[1]));
    for (const d of byArea) {
      let best: (typeof tracks)[number] | null = null;
      let bestIou = iouMin;
      for (const t of free) {
        const o = iou(t.box, d.box);
        if (o >= bestIou) { best = t; bestIou = o; }
      }
      if (best) {
        free.delete(best);
        best.box = d.box;
        best.label = d.label;
        best.boxes.push(d.box);
        best.ts.push(ts);
        best.lastSeen = ts;
        while (best.ts.length > 1 && ts - best.ts[0] > historyMs) { best.ts.shift(); best.boxes.shift(); }
      } else {
        best = { key: `${d.label}#${++seq}`, label: d.label, box: d.box, boxes: [d.box], ts: [ts], lastSeen: ts };
        tracks.push(best);
      }
      out.push({ key: best.key, label: best.label, box: best.box, boxes: [...best.boxes], ts: [...best.ts] });
    }
    return out;
  }

  return {
    update,
    reset() { tracks = []; },
  };
}
