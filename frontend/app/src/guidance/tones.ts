// frontend/app/src/guidance/tones.ts
// Stereo pan = left/right, pitch = up/down, beep rate = distance. One oscillator, reused.
import { app } from "../core/app";
import { hintText } from "../i18n/camera";
import { toneParams, vectorTo } from "./geometry";

const BEEP_GAIN = 0.25;
/** "Show your hand" repeats at most once per 8 s while no fingertip is seen. */
export const SHOW_HAND_GAP_MS = 8000;

let ctx: AudioContext | null = null;
let osc: OscillatorNode | null = null;
let pan: StereoPannerNode | null = null;
let gain: GainNode | null = null;
let beepTimer: ReturnType<typeof setInterval> | null = null;
let beepPeriodMs = 0;
let lastShowHand = -Infinity;

/** Tests / wiring: inject an AudioContext (e.g. the app's shared one). Resets the tone graph. */
export function __setAudioContext(c: AudioContext | null) {
  stopTone();
  ctx = c;
  osc = pan = gain = null;
  lastShowHand = -Infinity;
}

/**
 * Wiring: play the tones on the app's shared AudioContext, the one unlocked inside the Start tap (iOS Safari never
 * starts a context created later, without a gesture). `shareToneContext(null, old)` lets go only while `old` is the
 * one in use, so a disposed app never leaves the tones on a closed context.
 */
export function shareToneContext(c: AudioContext | null, old?: AudioContext): void {
  if (c === null && old !== undefined && ctx !== old) return;
  __setAudioContext(c);
}

function ensure(): AudioContext {
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") void ctx.resume().catch(() => {});
  if (!osc) {
    osc = ctx.createOscillator();
    pan = ctx.createStereoPanner();
    gain = ctx.createGain();
    gain.gain.value = 0;
    osc.type = "sine";
    osc.connect(pan).connect(gain).connect(ctx.destination);
    osc.start();
  }
  return ctx;
}

function setBeepRate(hz: number) {
  const period = Math.round(1000 / hz);
  if (beepTimer && Math.abs(period - beepPeriodMs) < 20) return;
  if (beepTimer) clearInterval(beepTimer);
  beepPeriodMs = period;
  beepTimer = setInterval(() => {
    if (!ctx || !gain) return;
    const t = ctx.currentTime;
    gain.gain.cancelScheduledValues(t);
    gain.gain.setValueAtTime(BEEP_GAIN, t);
    gain.gain.setTargetAtTime(0, t + 0.06, 0.02);
  }, period);
}

/** Guide the hand to `target` ([y, x], 0–1000). A null tip ⇒ silence + "show your hand" (at most once per 8 s). */
export function guideTo(target: [number, number], tip: { x1000: number; y1000: number } | null): void {
  if (!tip) {
    stopTone();
    const now = Date.now();
    if (now - lastShowHand >= SHOW_HAND_GAP_MS) {
      lastShowHand = now;
      app.speak(hintText("show_hand", app.lang()));
    }
    return;
  }
  const c = ensure();
  const p = toneParams(vectorTo(target, tip));
  const t = c.currentTime;
  osc!.frequency.setTargetAtTime(p.freqHz, t, 0.03);
  pan!.pan.setTargetAtTime(p.pan, t, 0.03);
  setBeepRate(p.beepHz);
}

export function stopTone(): void {
  if (beepTimer) { clearInterval(beepTimer); beepTimer = null; beepPeriodMs = 0; }
  if (ctx && gain) {
    gain.gain.cancelScheduledValues(ctx.currentTime);
    gain.gain.setTargetAtTime(0, ctx.currentTime, 0.02);
  }
}
