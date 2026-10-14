import { describe, it, expect, vi, afterEach } from "vitest";
import { startMic, MIC_WORKLET_NAME, MIC_CHUNK_SAMPLES, MIC_RATE, type MicDeps } from "../src/voice/mic";
import { base64ToInt16 } from "../src/voice/pcm";

const dev = (deviceId: string, label: string, kind: MediaDeviceKind = "audioinput"): MediaDeviceInfo =>
  ({ deviceId, label, kind, groupId: "g", toJSON() { return {}; } }) as MediaDeviceInfo;

describe("mic chunk sizing (latency)", () => {
  it("one chunk is 20-40 ms of 16 kHz PCM16: 640-1280 bytes, never a large batch", () => {
    expect(MIC_RATE).toBe(16000);
    const ms = (MIC_CHUNK_SAMPLES / MIC_RATE) * 1000;
    const bytes = MIC_CHUNK_SAMPLES * 2;
    expect(ms).toBeGreaterThanOrEqual(20);
    expect(ms).toBeLessThanOrEqual(40);
    expect(bytes).toBeGreaterThanOrEqual(640);
    expect(bytes).toBeLessThanOrEqual(1280);
  });
});

/* ---------- startMic with fake browser APIs ---------- */

type Frame = { samples: Float32Array; rate: number };

function fakeEnv(opts: { devices?: MediaDeviceInfo[]; allow16k?: boolean } = {}) {
  // Chrome Android lists communication routes, not microphones; a headset sits beside "Speakerphone".
  const devices = opts.devices ?? [dev("spk", "Speakerphone"), dev("bt1", "Bluetooth headset"), dev("int1", "Built-in microphone")];
  const tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
  const gumCalls: MediaStreamConstraints[] = [];

  const mediaDevices = {
    enumerateDevices: vi.fn(async () => devices),
    getUserMedia: vi.fn(async (c: MediaStreamConstraints) => {
      gumCalls.push(c);
      const t = { stop: vi.fn() };
      tracks.push(t);
      return { getTracks: () => [t] } as unknown as MediaStream;
    }),
  };

  const node = {
    port: { onmessage: null as null | ((e: { data: Frame }) => void) },
    connect: vi.fn(),
    disconnect: vi.fn(),
  };
  const src = { connect: vi.fn(), disconnect: vi.fn() };
  const contexts: any[] = [];
  const createContext = vi.fn((o?: AudioContextOptions) => {
    if (o?.sampleRate && !opts.allow16k) throw new Error("NotSupportedError: sample rate");
    const ctx = {
      sampleRate: o?.sampleRate ?? 48000,
      state: "suspended",
      destination: {},
      audioWorklet: { addModule: vi.fn(async () => {}) },
      createMediaStreamSource: vi.fn(() => src),
      resume: vi.fn(async () => { ctx.state = "running"; }),
      close: vi.fn(async () => { ctx.state = "closed"; }),
    };
    contexts.push(ctx);
    return ctx as unknown as AudioContext;
  });
  const createWorkletNode = vi.fn(() => node as unknown as AudioWorkletNode);
  const deps: MicDeps = {
    mediaDevices: mediaDevices as unknown as MicDeps["mediaDevices"],
    createContext,
    createWorkletNode,
    loadWorkletUrl: async () => "blob:mic-worklet",
  };
  const post = (samples: Float32Array, rate: number) => node.port.onmessage?.({ data: { samples, rate } });
  return { deps, mediaDevices, gumCalls, tracks, node, src, contexts, createContext, createWorkletNode, post };
}

describe("startMic", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens the OS default input with voice processing: one getUserMedia, no deviceId, even with a headset listed", async () => {
    const env = fakeEnv();
    const mic = await startMic({ onChunk: () => {}, deps: env.deps });
    expect(env.gumCalls).toHaveLength(1);
    const a = env.gumCalls[0].audio as MediaTrackConstraints;
    expect(a.deviceId).toBeUndefined(); // never "Speakerphone" by exact id: that would send AIRA's voice to the loudspeaker
    expect(a).toMatchObject({ echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 });
    expect(mic.deviceId).toBe("default");
    mic.stop();
  });

  it("never picks a device by label, whatever the list holds (the OS pairs the earphone mic with its speaker)", async () => {
    for (const devices of [
      [dev("bt", "Bluetooth headset"), dev("p", "Phone microphone")],
      [dev("int", "Built-in microphone")],
      [dev("spk", "Speakerphone"), dev("ear", "Headset earpiece"), dev("sco", "Bluetooth SCO")],
      [],
    ]) {
      const env = fakeEnv({ devices });
      const mic = await startMic({ onChunk: () => {}, deps: env.deps });
      expect(env.gumCalls).toHaveLength(1);
      expect(JSON.stringify(env.gumCalls[0])).not.toContain("deviceId");
      expect(env.mediaDevices.enumerateDevices).not.toHaveBeenCalled(); // no probe: nothing to choose
      mic.stop();
    }
  });

  it("uses a 16 kHz context when allowed and emits one 512-sample chunk per 512 samples", async () => {
    const env = fakeEnv({ allow16k: true });
    const chunks: string[] = [];
    const mic = await startMic({ onChunk: (b) => chunks.push(b), deps: env.deps });
    expect(env.createContext).toHaveBeenCalledWith({ sampleRate: 16000 });
    expect(mic.ctx.sampleRate).toBe(16000);
    expect(env.createWorkletNode).toHaveBeenCalledWith(mic.ctx, MIC_WORKLET_NAME);
    expect(env.contexts[0].audioWorklet.addModule).toHaveBeenCalledWith("blob:mic-worklet");
    expect(env.contexts[0].resume).toHaveBeenCalled();
    expect(env.src.connect).toHaveBeenCalledWith(env.node);
    env.post(new Float32Array(512).fill(0.5), 16000);
    expect(chunks).toHaveLength(1); // synchronously: nothing awaits or batches between the worklet and onChunk
    const pcm = base64ToInt16(chunks[0]);
    expect(pcm.length).toBe(512);
    expect(pcm.length * 2).toBe(1024); // bytes on the wire per chunk
    expect(pcm[10]).toBe(16384);
    mic.stop();
  });

  it("falls back to a native-rate context and streams 16 kHz PCM16 chunks, flushing the tail on stop", async () => {
    const env = fakeEnv({ allow16k: false });
    const chunks: string[] = [];
    const levels: number[] = [];
    const mic = await startMic({ onChunk: (b) => chunks.push(b), onLevel: (l) => levels.push(l), deps: env.deps });
    expect(mic.ctx.sampleRate).toBe(48000);
    for (let i = 0; i < 12; i++) env.post(new Float32Array(512).fill(0.5), 48000); // 6144 @48k ≈ 2048 @16k
    expect(chunks.map((c) => base64ToInt16(c).length)).toEqual([512, 512, 512, 512]);
    expect(levels).toHaveLength(4);
    expect(levels[0]).toBeCloseTo(0.5, 3);
    env.post(new Float32Array(128).fill(0.5), 48000); // ~43 more samples sit in the chunker
    expect(chunks).toHaveLength(4);
    mic.stop();
    expect(chunks).toHaveLength(5);
    const tail = base64ToInt16(chunks[4]).length;
    expect(tail).toBeGreaterThan(0);
    expect(tail).toBeLessThan(512);
    expect(env.tracks.every((t) => t.stop.mock.calls.length > 0)).toBe(true);
    expect(env.contexts[env.contexts.length - 1].close).toHaveBeenCalled();
    expect(env.node.port.onmessage).toBeNull();
    mic.stop(); // idempotent
    expect(chunks).toHaveLength(5);
  });

  it("releases the mic stream if the worklet fails to load", async () => {
    const env = fakeEnv({ allow16k: true });
    env.deps.loadWorkletUrl = async () => { throw new Error("worklet 404"); };
    await expect(startMic({ onChunk: () => {}, deps: env.deps })).rejects.toThrow("worklet 404");
    expect(env.tracks[0].stop).toHaveBeenCalled();
    expect(env.contexts[0].close).toHaveBeenCalled();
  });
});

/* ---------- the AudioWorkletProcessor itself ---------- */

describe("mic worklet", () => {
  const g = globalThis as Record<string, unknown>;
  afterEach(() => {
    delete g.sampleRate;
    delete g.AudioWorkletProcessor;
    delete g.registerProcessor;
  });

  it("batches 128-sample quanta into 512-sample frames tagged with the context rate", async () => {
    const registered: Record<string, new () => any> = {};
    class FakeProcessor { port = { postMessage: vi.fn() }; }
    g.sampleRate = 48000;
    g.AudioWorkletProcessor = FakeProcessor;
    g.registerProcessor = (name: string, ctor: new () => any) => { registered[name] = ctor; };
    await import("../src/voice/mic-worklet");
    const P = registered[MIC_WORKLET_NAME];
    expect(P).toBeTypeOf("function");
    const p = new P();
    for (let i = 0; i < 4; i++) {
      expect(p.process([[Float32Array.from({ length: 128 }, (_, k) => i * 128 + k)]])).toBe(true);
    }
    expect(p.process([[]])).toBe(true); // no input: stay alive, post nothing
    expect(p.process([])).toBe(true);
    expect(p.port.postMessage).toHaveBeenCalledTimes(1);
    const [msg, transfer] = p.port.postMessage.mock.calls[0];
    expect(msg.rate).toBe(48000);
    expect(msg.samples).toHaveLength(512);
    expect(msg.samples[0]).toBe(0);
    expect(msg.samples[511]).toBe(511);
    expect(transfer).toEqual([msg.samples.buffer]);
  });
});
