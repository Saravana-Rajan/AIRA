import type { CueName } from "@aira/contracts";

export type Earcon = { tones: { hz: number; ms: number }[]; gain: number };

/**
 * One short, word-free sound per cue. Every earcon is at most 600 ms.
 * `match` rises and `mismatch` falls so the two are told apart by ear alone.
 */
export const EARCONS: Record<CueName, Earcon> = {
  ack:         { tones: [{ hz: 880, ms: 60 }], gain: 0.25 },
  thinking:    { tones: [{ hz: 440, ms: 120 }, { hz: 494, ms: 120 }], gain: 0.08 },
  done:        { tones: [{ hz: 660, ms: 80 }, { hz: 990, ms: 120 }], gain: 0.25 },
  warn:        { tones: [{ hz: 330, ms: 150 }, { hz: 330, ms: 150 }], gain: 0.35 },
  match:       { tones: [{ hz: 523, ms: 90 }, { hz: 659, ms: 90 }, { hz: 784, ms: 140 }], gain: 0.3 },
  mismatch:    { tones: [{ hz: 784, ms: 140 }, { hz: 392, ms: 260 }], gain: 0.35 },
  unsure:      { tones: [{ hz: 587, ms: 120 }, { hz: 587, ms: 120 }], gain: 0.25 },
  interrupted: { tones: [{ hz: 700, ms: 40 }], gain: 0.15 },
  reconnect:   { tones: [{ hz: 523, ms: 80 }, { hz: 784, ms: 80 }], gain: 0.2 },
  offline:     { tones: [{ hz: 392, ms: 200 }, { hz: 262, ms: 300 }], gain: 0.3 },
};

/**
 * Earcons the app plays on its own, never sent by the server (the contract's CueName list is frozen).
 * `listening`: the hands-free start opened the mic; a short rise, softer than `done`.
 * `working`: AIRA asked the camera for a photo (count, ask, identify, invoice, cash) and is working on it; a soft
 * low double tick, repeated every 2 s until AIRA speaks. No words, so it means the same in every language.
 */
export const LOCAL_EARCONS: Record<"listening" | "working", Earcon> = {
  listening: { tones: [{ hz: 587, ms: 70 }, { hz: 880, ms: 110 }], gain: 0.2 },
  working: { tones: [{ hz: 392, ms: 50 }, { hz: 0, ms: 70 }, { hz: 392, ms: 50 }], gain: 0.07 },
};

export type EarconName = CueName | keyof typeof LOCAL_EARCONS;

/** Synthesized with OscillatorNode, so there are no asset files to download. */
export function playEarcon(ctx: AudioContext, n: EarconName): void {
  const e: Earcon | undefined = (EARCONS as Record<string, Earcon>)[n] ?? (LOCAL_EARCONS as Record<string, Earcon>)[n];
  if (!e) return;
  let t = ctx.currentTime;
  for (const tone of e.tones) {
    const dur = tone.ms / 1000;
    if (tone.hz <= 0) { t += dur; continue; } // a rest between two ticks
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.frequency.value = tone.hz;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(e.gain, t + 0.01);
    g.gain.linearRampToValueAtTime(0, t + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.02);
    t += dur;
  }
}
