// frontend/app/src/net/sendPriority.ts
// One WebSocket carries the owner's mic audio and every camera frame. When the uplink is slow, `bufferedAmount`
// grows and something has to give. The order, highest first:
//   1. mic audio      - sent until MIC_MAX_BUFFERED_BYTES (the owner's speech must not be lost)
//   2. tool frames    - a frame the server asked for waits (bounded) for the socket to drain, then goes anyway
//   3. live video     - skipped above LIVE_MAX_BUFFERED, and while the mic is backed up
//   4. preview/describe - skipped above BACKGROUND_MAX_BUFFERED, and while the mic is backed up
// Each lower stream stops well before the one above it, so a single frame put on the socket never pushes the
// buffer past the mic's cap. Nothing here queues: a skipped frame is gone, a dropped mic chunk is gone.

/** Mic chunks are dropped only above this many unsent bytes: room for a count photo (~110 KB) still leaving the
 *  phone plus a little speech. A 32 ms chunk is ~1.4 KB of JSON (~44 KB/s), so this is ~3 s on a dead link. */
export const MIC_MAX_BUFFERED_BYTES = 128_000;
/** A live video tick is skipped (and a finished live frame dropped) above this. Kept low so video never piles up
 *  in front of speech: on an iPhone, 768 px live frames were ~93 KB and the owner's speech queued behind them
 *  (production 2026-10-13: a mic chunk dropped at 129 KB buffered). */
export const LIVE_MAX_BUFFERED = 24_000;
/** Preview and describe frames are skipped above this: low enough that this cap plus one preview frame
 *  (~15 KB) stays under the live video's cap, so they never starve it. */
export const BACKGROUND_MAX_BUFFERED = 8_000;
/** While the owner is speaking, and this long after the mic last heard speech, video sends nothing at all. */
export const SPEECH_HOLD_MS = 1_200;
/** A tool frame (ask, count, identify, walk...) is handed to the socket once the buffer is below this... */
export const TOOL_SEND_BUFFERED = 32_000;
/** ...or after waiting this long for it, whichever comes first (the server's tool call is waiting). */
export const TOOL_DRAIN_MAX_MS = 1_500;
export const TOOL_DRAIN_POLL_MS = 50;
/** A mic chunk sent above this many buffered bytes (or dropped) means the mic is backed up. */
export const MIC_PRESSURE_BYTES = LIVE_MAX_BUFFERED;
/** Video yields to the mic for this long after the mic was last backed up. */
export const MIC_BACKLOG_MS = 1_500;

let micPressureAt = -Infinity;
let micDrops = 0;
let speechAt = -Infinity;

const clock = (): number => Date.now();

/** The mic path calls this for every chunk with the socket backlog it saw; true when the chunk may be sent. */
export function micMaySend(buffered: number, now: number = clock()): boolean {
  if (buffered > MIC_PRESSURE_BYTES) micPressureAt = now;
  if (buffered > MIC_MAX_BUFFERED_BYTES) {
    micDrops++;
    return false;
  }
  return true;
}

/** True while the mic was backed up recently: live, preview and describe frames yield to it. */
export function micBackedUp(now: number = clock()): boolean {
  return now - micPressureAt < MIC_BACKLOG_MS;
}

/** The mic heard the owner speaking (audible level): video holds off so his words go out first. */
export function noteSpeech(now: number = clock()): void {
  speechAt = now;
}

/** True while the owner is speaking (or just stopped), or his mic audio is backed up: camera video waits. */
export function videoYields(now: number = clock()): boolean {
  return now - speechAt < SPEECH_HOLD_MS || micBackedUp(now);
}

/** Total mic chunks dropped since load (the mic_drop diagnostics line). */
export function micDropCount(): number {
  return micDrops;
}

/** Tests only. */
export function __resetSendPriority(): void {
  micPressureAt = -Infinity;
  micDrops = 0;
  speechAt = -Infinity;
}

export interface DrainOpts {
  below?: number;
  maxMs?: number;
  pollMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

/**
 * Waits until `buffered()` is at or below `below` (default TOOL_SEND_BUFFERED), at most `maxMs`
 * (default TOOL_DRAIN_MAX_MS). Never throws: a failing `buffered` counts as drained.
 */
export async function waitForDrain(buffered: () => number, o: DrainOpts = {}): Promise<{ waitedMs: number; drained: boolean }> {
  const below = o.below ?? TOOL_SEND_BUFFERED;
  const maxMs = o.maxMs ?? TOOL_DRAIN_MAX_MS;
  const pollMs = o.pollMs ?? TOOL_DRAIN_POLL_MS;
  const sleep = o.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const now = o.now ?? clock;
  const read = () => { try { return buffered(); } catch { return 0; } };
  const start = now();
  while (read() > below) {
    const waited = now() - start;
    if (waited >= maxMs) return { waitedMs: waited, drained: false };
    await sleep(Math.min(pollMs, maxMs - waited));
  }
  return { waitedMs: now() - start, drained: true };
}
