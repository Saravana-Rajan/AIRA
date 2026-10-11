// apps/app/src/guidance/session.ts
// Server `target` messages → fingertip tracking → stereo tones + spoken micro-cues → one dwell event.
import type { ClientMsg, Lang, ServerMsg } from "@aira/contracts";
import { cueText } from "../i18n/camera";
import { DirectionHold, DwellTimer, insideBox, microCue, vectorTo, type Box, type Point, type Tip } from "./geometry";

/** Fingertip within this distance (0–1000 units) of the target counts as "near" (mutes the bump guard). */
export const NEAR_RADIUS = 150;

export interface GuidanceDeps {
  track: (onTip: (tip: Tip | null) => void) => Promise<() => void>;
  guide: (target: Point, tip: Tip | null) => void;
  stopTone: () => void;
  speak: (s: string) => void;
  send: (m: ClientMsg) => boolean;
  lang: () => Lang;
  now: () => number;
  /** Optional: told when the hand gets near / leaves the target (the wiring mutes the bump guard). */
  onNear?: (near: boolean) => void;
}

type TargetMsg = Extract<ServerMsg, { t: "target" }>;
// Guidance ends on the server's own end signal: the guidance loop always sends `request_frame {guide, stop}`
// when it finishes (tools/guidance.py stop()), and `done` / `interrupted` come from the long-press stop and
// barge-in. `match` is NOT an ending: the server speaks it for an intermediate pick ("Take 1 more") while it
// keeps sending targets, and the payment flow uses it for a cash match.
const ENDING_CUES = new Set(["done", "interrupted"]);

export function createGuidanceSession(deps: GuidanceDeps) {
  let target: { frameId: string; label: string; point: Point; box?: Box } | null = null;
  let stopTrack: (() => void) | null = null;
  let starting = false;
  let near = false;
  const hold = new DirectionHold();
  const dwell = new DwellTimer();

  function setNear(n: boolean) {
    if (n === near) return;
    near = n;
    deps.onNear?.(n);
  }

  function onTip(tip: Tip | null) {
    if (!target) return;
    deps.guide(target.point, tip);
    const now = deps.now();
    if (!tip) { dwell.update(false, now); return; }   // missing tip: lets the dwell grace window expire
    const v = vectorTo(target.point, tip);
    const inBox = insideBox(tip, target.box, target.point);
    setNear(inBox || v.dist <= NEAR_RADIUS);
    const cue = hold.update(microCue(v, inBox), now);
    if (cue) deps.speak(cueText(cue, deps.lang()));
    if (dwell.update(inBox, now)) {
      deps.send({ t: "event", name: "fingertip_in_target", data: { frameId: target.frameId, label: target.label } });
    }
  }

  async function startTracking() {
    if (stopTrack || starting) return;
    starting = true;
    try {
      const stop = await deps.track(onTip);
      if (target) stopTrack = stop;
      else stop();                       // guidance ended while the camera / model was starting
    } catch {
      /* camera or model unavailable: the next target retries */
    } finally {
      starting = false;
    }
  }

  function onTarget(m: TargetMsg) {
    target = { frameId: m.frameId, label: m.label, point: m.point, box: m.box };
    hold.reset();
    dwell.reset();
    void startTracking();
  }

  function end() {
    target = null;
    stopTrack?.();
    stopTrack = null;
    deps.stopTone();
    hold.reset();
    dwell.reset();
    setNear(false);
  }

  return {
    onServerMsg(m: ServerMsg) {
      if (m.t === "target") onTarget(m);
      else if (m.t === "cue" && ENDING_CUES.has(m.cue)) end();
      else if (m.t === "request_frame" && m.purpose === "guide" && m.stop) end();
    },
    end,
    _onTip: onTip,
  };
}
