import { describe, it, expect } from "vitest";
import { PlaybackQueue, PLAYBACK_RATE, PLAYBACK_START_LEAD_MS, type PlayStartInfo } from "../src/voice/playback";
import { int16ToBase64 } from "../src/voice/pcm";

class FakeSource {
  buffer: any = null;
  started = false;
  stopped = false;
  disconnected = false;
  startAt = -1;
  connectedTo: unknown = null;
  onended: (() => void) | null = null;
  connect(n: unknown) { this.connectedTo = n; }
  disconnect() { this.disconnected = true; }
  start(t: number) { this.started = true; this.startAt = t; }
  stop() { this.stopped = true; }
}

class FakeCtx {
  currentTime = 0;
  destination = { kind: "destination" };
  sources: FakeSource[] = [];
  buffers: { rate: number; data: Float32Array; duration: number }[] = [];
  createBuffer(_ch: number, len: number, rate: number) {
    const data = new Float32Array(len);
    const b = { rate, data, duration: len / rate, getChannelData: () => data };
    this.buffers.push(b);
    return b;
  }
  createBufferSource() { const s = new FakeSource(); this.sources.push(s); return s; }
}

const chunk = (ms: number) => int16ToBase64(new Int16Array(Math.round((24000 * ms) / 1000)));
const make = (ctx = new FakeCtx(), opts?: ConstructorParameters<typeof PlaybackQueue>[2]) => ({
  ctx,
  q: new PlaybackQueue(ctx as unknown as AudioContext, undefined, opts),
});

describe("PlaybackQueue", () => {
  it("schedules chunks back-to-back without gaps", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(100)); q.enqueue(chunk(100));
    const lead = PLAYBACK_START_LEAD_MS / 1000;
    expect(ctx.sources[0].startAt).toBeCloseTo(lead, 5);
    expect(ctx.sources[1].startAt).toBeCloseTo(lead + 0.1, 5);
    expect(q.playing).toBe(true);
  });

  describe("start threshold (latency)", () => {
    it("the first chunk of an answer starts within 200 ms, never after seconds of buffering", () => {
      expect(PLAYBACK_START_LEAD_MS).toBeGreaterThanOrEqual(0);
      expect(PLAYBACK_START_LEAD_MS).toBeLessThanOrEqual(200);
      const { ctx, q } = make();
      ctx.currentTime = 1;
      q.enqueue(chunk(20)); // one tiny 20 ms chunk is enough to start: no waiting for more
      expect(ctx.sources).toHaveLength(1);
      expect(ctx.sources[0].started).toBe(true);
      expect(ctx.sources[0].startAt - 1).toBeLessThanOrEqual(0.2);
    });

    it("schedules each chunk as it arrives, back to back after the first one", () => {
      const { ctx, q } = make();
      for (let i = 0; i < 5; i++) q.enqueue(chunk(20));
      expect(ctx.sources).toHaveLength(5);
      for (let i = 1; i < 5; i++) {
        expect(ctx.sources[i].startAt).toBeCloseTo(ctx.sources[i - 1].startAt + 0.02, 5);
      }
    });

    it("reports when a new answer starts playing, once per answer, with the ms since its first chunk", () => {
      let now = 1000;
      const starts: PlayStartInfo[] = [];
      const { ctx, q } = make(new FakeCtx(), { now: () => now, onPlayStart: (i) => starts.push(i) });
      q.enqueue(chunk(100)); q.enqueue(chunk(100));
      expect(starts).toHaveLength(1);
      expect(starts[0].leadMs).toBe(PLAYBACK_START_LEAD_MS);
      expect(starts[0].msSinceFirstChunk).toBeGreaterThanOrEqual(PLAYBACK_START_LEAD_MS);
      expect(starts[0].msSinceFirstChunk).toBeLessThanOrEqual(200);
      ctx.sources.forEach((s) => s.onended!()); // the answer finished
      now += 5000;
      q.enqueue(chunk(100)); // next answer
      expect(starts).toHaveLength(2);
    });

    it("a chunk after a barge-in flush counts as a new answer", () => {
      const starts: PlayStartInfo[] = [];
      const { q } = make(new FakeCtx(), { onPlayStart: (i) => starts.push(i) });
      q.enqueue(chunk(100));
      q.flush({ holdUntilResume: true });
      q.resume();
      q.enqueue(chunk(100));
      expect(starts).toHaveLength(2);
    });
  });

  it("flush() stops every scheduled source and resets the clock (barge-in)", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(200)); q.enqueue(chunk(200)); q.enqueue(chunk(200));
    q.flush();
    expect(ctx.sources.every((s) => s.stopped)).toBe(true);
    expect(ctx.sources.every((s) => s.disconnected)).toBe(true);
    expect(q.playing).toBe(false);
    ctx.currentTime = 0.05;
    q.enqueue(chunk(100)); // new turn after the interruption starts "now", not after old audio
    expect(ctx.sources.at(-1)!.startAt).toBeCloseTo(0.05 + PLAYBACK_START_LEAD_MS / 1000, 5);
  });

  it("drops chunks that arrive in the same tick as an interruption until resume()", () => {
    const { ctx, q } = make();
    q.flush({ holdUntilResume: true });
    q.enqueue(chunk(100));
    expect(ctx.sources).toHaveLength(0);
    q.resume();
    q.enqueue(chunk(100));
    expect(ctx.sources).toHaveLength(1);
  });

  it("barge-in with audio still queued: nothing old plays, and same-tick chunks are dropped", () => {
    const { ctx, q } = make();
    for (let i = 0; i < 5; i++) q.enqueue(chunk(200)); // 1 s of AIRA speech queued
    const old = [...ctx.sources];
    q.flush({ holdUntilResume: true }); // cue "interrupted"
    q.enqueue(chunk(200)); // late chunks of the interrupted turn, same tick
    q.enqueue(chunk(200));
    expect(old.every((s) => s.stopped)).toBe(true);
    expect(ctx.sources).toHaveLength(old.length);
    expect(q.playing).toBe(false);
    expect(q.held).toBe(true);
  });

  it("decodes little-endian PCM16 into a 24 kHz mono buffer wired to the output", () => {
    const { ctx, q } = make();
    q.enqueue(int16ToBase64(Int16Array.from([0, 16384, -32768, 32767])));
    expect(PLAYBACK_RATE).toBe(24000);
    expect(ctx.buffers[0].rate).toBe(24000);
    expect(Array.from(ctx.buffers[0].data)).toEqual([0, 0.5, -1, 32767 / 32768]);
    expect(ctx.sources[0].buffer).toBe(ctx.buffers[0]);
    expect(ctx.sources[0].connectedTo).toBe(ctx.destination);
  });

  it("routes audio to a custom output node (e.g. an analyser for the voice orb)", () => {
    const ctx = new FakeCtx();
    const out = { kind: "analyser" };
    const q = new PlaybackQueue(ctx as unknown as AudioContext, out as unknown as AudioNode);
    q.enqueue(chunk(20));
    expect(ctx.sources[0].connectedTo).toBe(out);
  });

  it("a chunk that arrives after the queue drained starts now, not in the past", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(100));
    ctx.currentTime = 0.5; // queue finished at 0.1
    q.enqueue(chunk(100));
    expect(ctx.sources[1].startAt).toBeCloseTo(0.5, 5);
  });

  it("ignores empty and malformed chunks without throwing", () => {
    const { ctx, q } = make();
    expect(() => q.enqueue("")).not.toThrow();
    expect(() => q.enqueue("@@not base64@@")).not.toThrow();
    expect(() => q.enqueue(btoa("\u0001"))).not.toThrow(); // one byte: no whole sample
    expect(ctx.sources).toHaveLength(0);
    expect(q.playing).toBe(false);
  });

  it("onended removes the source from the playing set", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(100)); q.enqueue(chunk(100));
    ctx.sources[0].onended!();
    expect(q.playing).toBe(true);
    ctx.sources[1].onended!();
    expect(q.playing).toBe(false);
  });

  it("a late onended from a flushed source does not touch the next turn", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(100));
    const stale = ctx.sources[0];
    q.flush();
    q.enqueue(chunk(100));
    stale.onended?.();
    expect(q.playing).toBe(true);
  });

  it("flush() is safe when a source throws on stop() and when called twice", () => {
    const { ctx, q } = make();
    q.enqueue(chunk(100));
    ctx.sources[0].stop = () => { throw new Error("InvalidStateError"); };
    expect(() => { q.flush(); q.flush(); }).not.toThrow();
    expect(q.playing).toBe(false);
  });

  describe("lastAudioAt (no double speech)", () => {
    it("starts far in the past so the client may speak before any Live audio", () => {
      const { q } = make();
      expect(q.lastAudioAt).toBe(-Infinity);
      expect(q.msSinceAudio()).toBe(Infinity);
    });

    it("marks the end of the scheduled AIRA audio, so a long burst still counts as speaking", () => {
      const { ctx, q } = make();
      q.enqueue(chunk(1000)); q.enqueue(chunk(1000)); // Live sends 2 s of speech at once
      expect(q.lastAudioAt).toBeCloseTo(2 + PLAYBACK_START_LEAD_MS / 1000, 5);
      ctx.currentTime = 1.5; // still speaking
      expect(q.msSinceAudio()).toBe(0);
      ctx.currentTime = 3.2 + PLAYBACK_START_LEAD_MS / 1000;
      expect(q.msSinceAudio()).toBeCloseTo(1200, 3);
    });

    it("on flush, AIRA audio ends now, not when the dropped audio would have ended", () => {
      const { ctx, q } = make();
      q.enqueue(chunk(2000));
      ctx.currentTime = 0.3;
      q.flush({ holdUntilResume: true });
      expect(q.lastAudioAt).toBeCloseTo(0.3, 5);
      ctx.currentTime = 1.5;
      expect(q.msSinceAudio()).toBeCloseTo(1200, 3);
    });

    it("dropped chunks do not move lastAudioAt", () => {
      const { ctx, q } = make();
      q.flush({ holdUntilResume: true });
      ctx.currentTime = 0.4;
      q.enqueue(chunk(500));
      expect(q.lastAudioAt).toBe(-Infinity);
    });
  });

  describe("hold safety net", () => {
    it("releases the hold on its own after maxHoldMs, so AIRA is never stuck silent", () => {
      let now = 1000;
      const { ctx, q } = make(new FakeCtx(), { maxHoldMs: 1500, now: () => now });
      q.flush({ holdUntilResume: true });
      now += 1499;
      q.enqueue(chunk(100));
      expect(ctx.sources).toHaveLength(0);
      now += 1;
      q.enqueue(chunk(100));
      expect(ctx.sources).toHaveLength(1);
      expect(q.held).toBe(false);
    });

    it("a plain flush() clears an earlier hold", () => {
      const { ctx, q } = make();
      q.flush({ holdUntilResume: true });
      q.flush();
      q.enqueue(chunk(100));
      expect(ctx.sources).toHaveLength(1);
    });

    it("maxHoldMs of Infinity holds until resume()", () => {
      let now = 0;
      const { ctx, q } = make(new FakeCtx(), { maxHoldMs: Infinity, now: () => now });
      q.flush({ holdUntilResume: true });
      now += 60_000;
      q.enqueue(chunk(100));
      expect(ctx.sources).toHaveLength(0);
      q.resume();
      q.enqueue(chunk(100));
      expect(ctx.sources).toHaveLength(1);
    });
  });
});
