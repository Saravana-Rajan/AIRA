// The Live page's player (lib/listen): the Listen button sends {t:"listen", session} and null to stop, follows
// the followed person, and schedules the feed's audio chunks on a fake AudioContext's clock in order, with a
// small jitter buffer, dropping late ones instead of letting the delay grow. No real audio: a fake context
// records what was created and when each source was asked to start.
import { describe, expect, it, vi } from "vitest";
import type { MonitorVoice } from "@aira/contracts";
import {
  JITTER_S, MAX_LEAD_S, createListener, decodePcm16, rms,
  type AudioBufferLike, type AudioCtxLike, type AudioMsg, type GainLike, type SourceLike,
} from "../src/lib/listen";

/** PCM16 little-endian samples -> base64, as the feed carries them. */
function pcm(samples: number[]): string {
  const bytes = new Uint8Array(samples.length * 2);
  samples.forEach((v, i) => { bytes[2 * i] = v & 0xff; bytes[2 * i + 1] = (v >> 8) & 0xff; });
  return btoa(String.fromCharCode(...bytes));
}
const chunk = (who: MonitorVoice, n: number, session = "s1"): AudioMsg =>
  ({ t: "mon", kind: "audio", session, ts: 1, who, rate: who === "owner" ? 16000 : 24000, pcm: pcm(Array.from({ length: n }, (_, i) => (i % 2 ? 8192 : -8192))) });

interface Started { who: MonitorVoice | "?"; at: number | undefined; rate: number; frames: number }

function fakeContext(startState = "suspended") {
  const started: Started[] = [];
  const gains: GainLike[] = [];
  const ctx = {
    currentTime: 0, state: startState, destination: { name: "speakers" },
    resume: vi.fn(async () => { ctx.state = "running"; }),
    suspend: vi.fn(async () => { ctx.state = "suspended"; }),
    close: vi.fn(async () => { ctx.state = "closed"; }),
    createBuffer(_ch: number, length: number, sampleRate: number): AudioBufferLike & { rate: number; data: Float32Array } {
      const data = new Float32Array(length);
      return { duration: length / sampleRate, rate: sampleRate, data, getChannelData: () => data };
    },
    createBufferSource(): SourceLike {
      let dest: unknown = null;
      const src: SourceLike = {
        buffer: null, onended: null,
        connect: (d: unknown) => { dest = d; return d; },
        start: (when?: number) => {
          const b = src.buffer as (AudioBufferLike & { rate: number }) | null;
          const who = (dest as { who?: MonitorVoice } | null)?.who ?? "?";
          started.push({ who, at: when, rate: b?.rate ?? 0, frames: b ? Math.round(b.duration * b.rate) : 0 });
        },
      };
      return src;
    },
    createGain(): GainLike & { who?: MonitorVoice } {
      const g = { gain: { value: 1 }, who: undefined as MonitorVoice | undefined, connect: (d: unknown) => d };
      gains.push(g);
      g.who = gains.length === 1 ? "owner" : "aira"; // lanes are made owner first, aira second
      return g;
    },
  };
  return { ctx: ctx as unknown as AudioCtxLike, started, gains, raw: ctx };
}

function rig() {
  const f = fakeContext();
  const make = vi.fn(() => f.ctx);
  const l = createListener({ audioContext: make });
  const sent: unknown[] = [];
  const send = vi.fn((m: unknown) => { sent.push(m); return true; });
  const states: Array<ReturnType<typeof l.state>> = [];
  l.subscribe((s) => states.push(s));
  return { ...f, make, l, sent, send, states };
}

describe("decoding", () => {
  it("turns base64 PCM16 little-endian into -1..1 samples and measures their level", () => {
    const s = decodePcm16(pcm([0, 16384, -16384, 32767, -32768]));
    expect([...s].map((v) => Math.round(v * 1000) / 1000)).toEqual([0, 0.5, -0.5, 1, -1]);
    expect(rms(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5, 6);
    expect(rms(new Float32Array(0))).toBe(0);
    expect(() => decodePcm16("%%%")).toThrow();
  });
});

describe("the button and the request", () => {
  it("starts on a click: makes and resumes the AudioContext, then asks for the followed session; stop asks for null and suspends", async () => {
    const r = rig();
    r.l.attach(r.send);
    r.l.want("s1");
    expect(r.sent).toEqual([]); // following alone sends nothing: the viewer did not ask to hear
    expect(r.make).not.toHaveBeenCalled(); // and no AudioContext before a gesture
    r.l.toggle();
    expect(r.make).toHaveBeenCalledTimes(1);
    expect(r.raw.resume).toHaveBeenCalledTimes(1);
    expect(r.sent).toEqual([{ t: "listen", session: "s1" }]);
    expect(r.l.state()).toMatchObject({ on: true, session: "s1", dropped: 0 });
    r.l.toggle();
    await Promise.resolve();
    expect(r.sent).toEqual([{ t: "listen", session: "s1" }, { t: "listen", session: null }]);
    expect(r.raw.suspend).toHaveBeenCalledTimes(1);
    expect(r.l.state().on).toBe(false);
    expect(r.states.map((s) => [s.on, s.session])).toEqual([[false, "s1"], [true, "s1"], [false, "s1"]]); // want, start, stop
  });
  it("re-targets the request when the followed person changes, asks again on a new socket, and never repeats itself", () => {
    const r = rig();
    r.l.attach(r.send);
    r.l.start();
    expect(r.sent).toEqual([]); // nothing followed yet: nothing to ask for
    r.l.want("s1");
    r.l.want("s1");
    r.l.want("s2");
    r.l.want(null); // the session ended
    expect(r.sent).toEqual([{ t: "listen", session: "s1" }, { t: "listen", session: "s2" }, { t: "listen", session: null }]);
    r.l.want("s3");
    r.l.detach(); // the socket dropped
    r.l.want("s4"); // nothing to send to
    expect(r.sent).toHaveLength(4);
    const sent2: unknown[] = [];
    r.l.attach((m) => { sent2.push(m); return true; }); // the reconnect: still on, so it asks for the current person
    expect(sent2).toEqual([{ t: "listen", session: "s4" }]);
  });
  it("starting with no socket sends nothing until one is attached; a failed send is retried on the next change", () => {
    const r = rig();
    r.l.want("s1");
    r.l.start();
    expect(r.sent).toEqual([]);
    const failing = vi.fn(() => false);
    r.l.attach(failing);
    expect(failing).toHaveBeenCalledWith({ t: "listen", session: "s1" });
    r.l.want("s2");
    expect(failing).toHaveBeenLastCalledWith({ t: "listen", session: "s2" });
  });
});

describe("playback: in order, a small jitter buffer, late chunks dropped", () => {
  it("ignores chunks before Listen is on, for another session, or when malformed", () => {
    const r = rig();
    r.l.want("s1");
    r.l.receive(chunk("owner", 160));
    r.l.start();
    r.l.receive(chunk("owner", 160, "s9"));
    r.l.receive({ ...chunk("owner", 160), pcm: "%%%" });
    r.l.receive({ ...chunk("owner", 160), pcm: "" });
    expect(r.started).toEqual([]);
  });
  it("schedules each voice back to back on its own lane at the stream's own sample rate, the first chunk JITTER_S ahead", () => {
    const r = rig();
    r.l.want("s1");
    r.l.start();
    r.raw.currentTime = 10;
    r.l.receive(chunk("owner", 512)); // 32 ms at 16 kHz
    r.l.receive(chunk("owner", 512));
    r.l.receive(chunk("aira", 2400)); // 100 ms at 24 kHz
    r.l.receive(chunk("aira", 2400));
    expect(JITTER_S).toBe(0.2);
    expect(r.started.map((s) => [s.who, s.rate, s.frames, Math.round((s.at ?? 0) * 1000)])).toEqual([
      ["owner", 16000, 512, 10200], ["owner", 16000, 512, 10232], ["aira", 24000, 2400, 10200], ["aira", 24000, 2400, 10300],
    ]);
    expect(r.gains.map((g) => g.gain.value)).toEqual([1, 1]);
    r.l.setGain("aira", 0.25);
    expect(r.gains[1].gain.value).toBe(0.25);
  });
  it("after an underrun the next chunk gets a fresh jitter buffer; a chunk that would start more than MAX_LEAD_S ahead is dropped and counted", () => {
    const r = rig();
    r.l.want("s1");
    r.l.start();
    r.raw.currentTime = 1;
    r.l.receive(chunk("owner", 1600)); // 100 ms: plays 1.2-1.3
    r.raw.currentTime = 2; // silence passed: the lane's clock is in the past
    r.l.receive(chunk("owner", 1600)); // re-buffered: 2.2-2.3
    expect(r.started.map((s) => s.at)).toEqual([1.2, 2.2]);
    // a stall then a burst: chunks pile up ahead of the clock until MAX_LEAD_S, the rest are late and dropped
    expect(MAX_LEAD_S).toBe(0.6);
    for (let i = 0; i < 10; i++) r.l.receive(chunk("owner", 1600));
    const ahead = r.started.slice(2).map((s) => Math.round(((s.at ?? 0) - 2) * 100) / 100);
    expect(ahead).toEqual([0.3, 0.4, 0.5, 0.6]); // 0.7 would be late
    expect(r.l.state().dropped).toBe(6);
    expect(r.states[r.states.length - 1].dropped).toBe(6);
    r.raw.currentTime = 2.65; // time moved on: the lane is within reach again
    r.l.receive(chunk("owner", 1600));
    expect(r.started[r.started.length - 1].at).toBeCloseTo(2.7, 6);
    expect(r.l.state().dropped).toBe(6);
  });
  it("switching person starts the new one's sound afresh; stop and start reset the lanes and the drop count", () => {
    const r = rig();
    r.l.want("s1");
    r.l.start();
    r.raw.currentTime = 5;
    r.l.receive(chunk("aira", 2400));
    for (let i = 0; i < 8; i++) r.l.receive(chunk("aira", 2400));
    expect(r.l.state().dropped).toBeGreaterThan(0);
    r.l.want("s2");
    r.raw.currentTime = 5.05;
    r.l.receive(chunk("aira", 2400, "s2"));
    expect(r.started[r.started.length - 1].at).toBeCloseTo(5.25, 6); // not queued behind s1's sound
    r.l.stop();
    r.l.start();
    expect(r.l.state().dropped).toBe(0);
    expect(r.make).toHaveBeenCalledTimes(1); // the same AudioContext, resumed
    expect(r.raw.resume).toHaveBeenCalledTimes(2);
  });
  it("reports each scheduled chunk's level per voice to the meter, and dispose closes the context", async () => {
    const r = rig();
    const levels: Array<[MonitorVoice, number]> = [];
    const off = r.l.subscribeLevels((who, level) => levels.push([who, Math.round(level * 100) / 100]));
    r.l.want("s1");
    r.l.start();
    r.l.receive(chunk("owner", 160));
    r.l.receive(chunk("aira", 240));
    expect(levels).toEqual([["owner", 0.25], ["aira", 0.25]]);
    off();
    r.l.receive(chunk("owner", 160));
    expect(levels).toHaveLength(2);
    r.l.attach(r.send); // a socket arrives while on: it is asked for the session
    r.l.dispose();
    await Promise.resolve();
    expect(r.sent).toEqual([{ t: "listen", session: "s1" }, { t: "listen", session: null }]);
    expect(r.raw.close).toHaveBeenCalledTimes(1);
    expect(r.l.state().on).toBe(false);
    r.l.receive(chunk("owner", 160)); // nothing to play on
    expect(r.started).toHaveLength(3); // the two metered chunks and the one after off(); nothing since dispose
  });
});
