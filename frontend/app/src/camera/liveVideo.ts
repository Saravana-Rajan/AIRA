// frontend/app/src/camera/liveVideo.ts
// The camera as Gemini Live video: while the Live session is up and the camera is on, one `live` frame a second
// (768 px, quality 0.7). Tool frames (ask, count, identify...) always win: a tick is skipped while one is being
// captured, and a live frame is dropped when a tool capture started while it was being made. A tick is also
// skipped while the socket is still busy (stale video is worse than none) and while the page is hidden.
//
// "Show and it reacts": a cheap probe (a 32x24 gray grid of the camera, plus the phone's MediaPipe object boxes and
// the hand box when the hand landmarker runs) is sampled 5 times a second. The change detector fires when
//   - a new object label settles in the centre of the view (ignoring people),
//   - a hand holding an object (or a close hand alone) settles near the centre, or
//   - the gray view moved a lot from the settled one and is steady again (small chest-mount sway tolerated),
// each after ~400 ms. A fire captures and sends ONE live frame at once (the fast path, not waiting for the 1 fps
// tick), marked `changed` with the labels, so the server can ask Live to say what is shown.
//
// Production 2026-10-13 (Galaxy A22): 11 live frames in 160 s and never one `changed`. The old detector only
// ran on the 1 fps frames that actually went out (most ticks were skipped while big count photos filled the
// socket), and required the view to be steady against the PREVIOUS live frame, which was then many seconds old:
// a swaying chest camera was never "steady", so nothing ever fired. Now the detector has its own sampling,
// and the socket cap for live frames sits above the background (preview / describe) cap so they never starve it.
//
// Pure: every browser touch comes through deps.
import type { ClientMsg } from "@aira/contracts";
import type { FrameKeep, FrameMsg } from "./capture";
import { coarseGray, meanAbsDiff } from "./sceneGate";
import type { Box } from "../guidance/geometry";
import { candidateOf, isCentral, HAND_LABEL, MIN_HAND_AREA } from "../ambient/holdUp";

export const LIVE_FPS = 1;
/** Bytes waiting on the socket above which a live tick is skipped (and a finished live frame dropped). Above the
 *  background cap (preview / describe stop at 32 KB), so they can never starve the live video, and about one live
 *  frame (~60 KB) on top of that: an owner's tool frame never waits behind a backlog of video. */
export const LIVE_MAX_BUFFERED = 96_000;
/** Mean absolute luma difference (0-255, 32x24 grid) from the settled view that counts as a new scene. */
export const CHANGE_THRESHOLD = 14;
/** ...and the most two probe samples (~200 ms apart) may differ for the view to count as steady. Generous enough
 *  for a chest-mounted phone's sway. */
export const STEADY_THRESHOLD = 8;
/** A change must hold this long before it fires. */
export const SETTLE_MS = 400;
/** An object (or a held item) unseen this long has left: showing it again counts as new. */
export const LEAVE_MS = 1500;
/** At most one fire per this many ms (the server gates the spoken nudge further). */
export const FIRE_GAP_MS = 1500;
/** The probe (gray grid + boxes) samples this often. */
export const PROBE_MS = 200;
/** A regular tick this soon after a fast-path frame is skipped (the server keeps ~1 fps). */
export const FAST_QUIET_MS = 800;
/** Detector labels that never count as "something shown" (customers walk past all day). */
export const IGNORED_LABELS: ReadonlySet<string> = new Set(["person"]);
/** The central region for "shown": the middle 60 % of the frame in x and y. */
export const SHOW_CENTRE = 0.6;
export const MAX_LABELS = 8;
export const STATS_EVERY_MS = 60_000;

export type ChangeReason = "label" | "held" | "scene";

export interface SceneSample {
  /** The 32x24 gray grid, or null when no picture this time (labels / hand still count). */
  grid: Float32Array | null;
  /** Detector labels in the centre of the view now. */
  labels: readonly string[];
  /** A hand holds an object (or a close hand alone) near the centre. */
  held: boolean;
  ts: number;
}

export interface ChangeDetector {
  /** One probe sample; the reason when something new was just shown and settled, else null. */
  feed(s: SceneSample): ChangeReason | null;
  reset(): void;
}

const norm = (l: string) => l.trim().toLowerCase();

export interface ChangeOptions {
  threshold?: number;
  steady?: number;
  settleMs?: number;
  leaveMs?: number;
  gapMs?: number;
}

export function createChangeDetector(opts: ChangeOptions = {}): ChangeDetector {
  const threshold = opts.threshold ?? CHANGE_THRESHOLD;
  const steady = opts.steady ?? STEADY_THRESHOLD;
  const settleMs = opts.settleMs ?? SETTLE_MS;
  const leaveMs = opts.leaveMs ?? LEAVE_MS;
  const gapMs = opts.gapMs ?? FIRE_GAP_MS;
  let ref: Float32Array | null = null;
  let prev: { grid: Float32Array; ts: number } | null = null;
  let steadySince: number | null = null;
  const known = new Map<string, number>();                         // label -> last seen (part of the settled view)
  const pending = new Map<string, { since: number; last: number }>(); // new labels waiting to settle
  let heldSince: number | null = null;
  let heldLast = -Infinity;
  let heldArmed = true;
  let lastFire = -Infinity;
  let started = false;

  return {
    feed(s) {
      const ts = s.ts;
      const labels = [...new Set(s.labels.map(norm).filter((l) => l && !IGNORED_LABELS.has(l)))];
      if (!started) {
        // The first sample is the reference: what is already in view was not "shown".
        started = true;
        ref = s.grid;
        if (s.grid) prev = { grid: s.grid, ts };
        for (const l of labels) known.set(l, ts);
        if (s.held) { heldArmed = false; heldLast = ts; }
        return null;
      }
      // Labels: a label unseen for LEAVE_MS is new; it fires once it stayed SETTLE_MS (short flickers tolerated).
      let newLabel = false;
      for (const l of labels) {
        const seen = known.get(l);
        if (seen !== undefined && ts - seen <= leaveMs) { known.set(l, ts); continue; }
        known.delete(l);
        const p = pending.get(l) ?? { since: ts, last: ts };
        p.last = ts;
        pending.set(l, p);
        if (ts - p.since >= settleMs) newLabel = true;
      }
      for (const [l, p] of pending) if (ts - p.last > settleMs) pending.delete(l);
      // Held item: rising edge, settled SETTLE_MS; re-armed once nothing was held for LEAVE_MS.
      let held = false;
      if (s.held) {
        if (heldSince === null || ts - heldLast > settleMs) heldSince = ts;
        heldLast = ts;
        if (heldArmed && ts - heldSince >= settleMs) held = true;
      } else if (ts - heldLast > leaveMs) {
        heldArmed = true;
        heldSince = null;
      }
      // Scene: far from the settled view, and steady (sample to sample) for SETTLE_MS.
      let scene = false;
      if (s.grid) {
        if (ref === null) ref = s.grid;
        const far = meanAbsDiff(s.grid, ref) >= threshold;
        const still = prev !== null && meanAbsDiff(s.grid, prev.grid) < steady;
        if (far && still) {
          steadySince ??= prev!.ts;
          scene = ts - steadySince >= settleMs;
        } else {
          steadySince = null;
        }
        prev = { grid: s.grid, ts };
      }
      const reason: ChangeReason | null = held ? "held" : newLabel ? "label" : scene ? "scene" : null;
      if (reason === null || ts - lastFire < gapMs) return null;
      lastFire = ts;
      // The new view is the settled one now.
      if (s.grid) ref = s.grid;
      steadySince = null;
      for (const l of labels) { known.set(l, ts); pending.delete(l); }
      if (s.held) heldArmed = false;
      return reason;
    },
    reset() {
      ref = null;
      prev = null;
      steadySince = null;
      known.clear();
      pending.clear();
      heldSince = null;
      heldLast = -Infinity;
      heldArmed = true;
      lastFire = -Infinity;
      started = false;
    },
  };
}

export type SeenObject = { label: string; box: Box };

/** Labels in the central region (people ignored), the held state, and the labels for the frame (centre first). */
export function readScene(objects: readonly SeenObject[], hand: Box | null): { centred: string[]; held: boolean; labels: string[] } {
  const centred: string[] = [];
  const rest: string[] = [];
  for (const o of objects) {
    const l = norm(o.label);
    if (!l) continue;
    if (!IGNORED_LABELS.has(l) && isCentral(o.box, SHOW_CENTRE)) { if (!centred.includes(l)) centred.push(l); }
    else if (!rest.includes(l)) rest.push(l);
  }
  const cand = hand ? candidateOf({ hand, objects }, { centre: SHOW_CENTRE, minHandArea: MIN_HAND_AREA }) : null;
  const held = cand !== null && (cand.label === HAND_LABEL || isCentral(hand!, SHOW_CENTRE) || isCentral(cand.box, SHOW_CENTRE));
  const labels = [...centred, ...rest.filter((l) => !centred.includes(l))];
  if (held && cand && cand.label !== HAND_LABEL) {
    const l = norm(cand.label);
    if (!labels.includes(l)) labels.unshift(l);
  }
  if (held && !labels.includes("held item")) labels.unshift("held item");
  return { centred, held, labels: labels.slice(0, MAX_LABELS) };
}

export type SkipReason = "hidden" | "inactive" | "camera" | "tool" | "buffered" | "busy" | "late" | "quiet" | "error";

export interface LiveVideoDeps {
  /** captureFrameIf("live", keep): the gate sees the gray frame and always keeps it. */
  capture: (keep: FrameKeep) => Promise<FrameMsg | null>;
  send: (m: ClientMsg) => boolean;
  /** The Live session is up (ready received, socket open). */
  active: () => boolean;
  cameraOn: () => boolean;
  hidden: () => boolean;
  /** Bytes waiting on the socket. */
  buffered: () => number;
  toolBusy: () => boolean;
  toolCount: () => number;
  /** The phone's current MediaPipe object boxes (fresh only). */
  objects: () => readonly SeenObject[];
  /** The hand box when the hand landmarker is running (fresh only); absent / null otherwise. */
  hand?: () => Box | null;
  /** A cheap 32x24 gray grid of the camera now (no encode), or null. Absent: the detector runs on live frames. */
  probe?: () => Float32Array | null;
  /** Diagnostics sink (the per-minute "live_stats" line). */
  log?: (tag: string, data: Record<string, unknown>) => void;
  now?: () => number;
  detector?: ChangeDetector;
  fps?: number;
}

/** Starts the live sender and its show detector; returns the stop function. Never throws. */
export function startLiveVideo(deps: LiveVideoDeps): () => void {
  const det = deps.detector ?? createChangeDetector();
  const period = Math.round(1000 / (deps.fps ?? LIVE_FPS));
  const now = deps.now ?? (() => Date.now());
  let stopped = false;
  let inFlight = false;
  let wasHidden = false;
  let lastSentAt = -Infinity;
  /** A change that fired while a capture was in flight: sent right after it. */
  let queued: { labels: string[]; reason: ChangeReason } | null = null;

  // ---------- per-minute stats ("live_stats") ----------
  const fresh = () => ({ sent: 0, changed: 0, fast: 0, skipped: {} as Partial<Record<SkipReason, number>>,
    fired: {} as Partial<Record<ChangeReason, number>>, probes: 0 });
  let stats = fresh();
  let windowAt = now();
  const skipped = (r: SkipReason) => { stats.skipped[r] = (stats.skipped[r] ?? 0) + 1; };
  const flushStats = (force = false) => {
    const t = now();
    if (!force && t - windowAt < STATS_EVERY_MS) return;
    const any = stats.sent || stats.probes || Object.keys(stats.skipped).length;
    if (any) {
      try { deps.log?.("live_stats", { ...stats, windowS: Math.round((t - windowAt) / 1000) }); } catch { /* diagnostics only */ }
    }
    stats = fresh();
    windowAt = t;
  };

  const blocked = (): SkipReason | null => {
    const hidden = deps.hidden();
    if (hidden) { wasHidden = true; return "hidden"; }
    if (!deps.active()) return "inactive";
    if (!deps.cameraOn()) return "camera";
    if (deps.toolBusy()) return "tool";
    if (deps.buffered() > LIVE_MAX_BUFFERED) return "buffered";
    return null;
  };

  const scene = () => {
    let objects: readonly SeenObject[] = [];
    let hand: Box | null = null;
    try { objects = deps.objects(); } catch { objects = []; }
    try { hand = deps.hand?.() ?? null; } catch { hand = null; }
    return readScene(objects, hand);
  };

  /** Captures and sends one live frame; `change` marks it changed. */
  const shoot = async (change: { labels: string[]; reason: ChangeReason } | null, fast: boolean, feedTick: boolean) => {
    inFlight = true;
    const seq = deps.toolCount();
    let grid: Float32Array | null = null;
    try {
      const f = await deps.capture((gray, w, h) => { if (feedTick) grid = coarseGray(gray, w, h); return true; });
      if (!f || stopped) return;
      // A tool frame started meanwhile, or the socket filled up: the tool frame goes first, this one never.
      if (deps.toolCount() !== seq || deps.toolBusy() || deps.hidden() || deps.buffered() > LIVE_MAX_BUFFERED) {
        skipped("late");
        if (change) queued = null; // the change is stale by the time the tool is done
        return;
      }
      let mark = change;
      if (!mark && feedTick && grid !== null) {
        const sc = scene();
        const reason = det.feed({ grid, labels: sc.centred, held: sc.held, ts: now() });
        if (reason) { stats.fired[reason] = (stats.fired[reason] ?? 0) + 1; mark = { labels: sc.labels, reason }; }
      }
      const ok = deps.send(mark ? { ...f, changed: true, labels: [...mark.labels] } : f);
      if (ok) {
        lastSentAt = now();
        stats.sent++;
        if (mark) stats.changed++;
        if (fast) stats.fast++;
      }
    } catch {
      skipped("error"); // camera hiccup: the next tick tries again
    } finally {
      inFlight = false;
    }
  };

  /** Fast path: one changed frame right now (or right after the capture in flight). */
  const fire = async (change: { labels: string[]; reason: ChangeReason }) => {
    if (inFlight) { queued = change; return; }
    const why = blocked();
    if (why) { skipped(why); return; }
    await shoot(change, true, false);
    await drain();
  };
  const drain = async () => {
    while (queued && !inFlight && !stopped) {
      const q = queued;
      queued = null;
      const why = blocked();
      if (why) { skipped(why); return; }
      await shoot(q, true, false);
    }
  };

  const tick = async () => {
    flushStats();
    if (stopped) return;
    if (inFlight) { skipped("busy"); return; }
    let why: SkipReason | null;
    try { why = blocked(); } catch { return; }
    if (why) { skipped(why); return; }
    if (wasHidden) { det.reset(); wasHidden = false; } // back from hidden: the first view is a reference only
    if (now() - lastSentAt < FAST_QUIET_MS) { skipped("quiet"); return; }
    await shoot(null, false, !deps.probe);
    await drain();
  };

  const probeTick = () => {
    if (stopped || !deps.probe) return;
    try {
      if (deps.hidden() || !deps.active() || !deps.cameraOn()) { if (deps.hidden()) wasHidden = true; return; }
      if (wasHidden) { det.reset(); wasHidden = false; }
      const grid = deps.probe();
      const sc = scene();
      stats.probes++;
      const reason = det.feed({ grid, labels: sc.centred, held: sc.held, ts: now() });
      if (!reason) return;
      stats.fired[reason] = (stats.fired[reason] ?? 0) + 1;
      void fire({ labels: sc.labels, reason });
    } catch {
      // a probe hiccup is just a missed sample
    }
  };

  const id = setInterval(() => { void tick(); }, period);
  const probeId = deps.probe ? setInterval(probeTick, PROBE_MS) : null;
  void tick();
  return () => {
    stopped = true;
    clearInterval(id);
    if (probeId !== null) clearInterval(probeId);
    flushStats(true);
  };
}
