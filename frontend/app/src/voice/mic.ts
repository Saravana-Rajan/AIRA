import { Chunker, StreamResampler, floatToPcm16, int16ToBase64, rms } from "./pcm";

export const MIC_RATE = 16000;
export const MIC_CHUNK_SAMPLES = 512; // 32 ms @ 16 kHz
export const MIC_WORKLET_NAME = "aira-mic-tap";

/** Message posted by mic-worklet.ts. */
export interface MicFrame {
  samples: Float32Array;
  rate: number;
}

// Anything that would pull Bluetooth earphones out of stereo A2DP into HFP/SCO.
const BAD =
  /(bluetooth|headset|head-?phones?|ear-?phones?|ear-?buds?|\bbuds?\d*\b|airpods|\bsco\b|\bhfp\b|hands-?free|rockerz|wireless)/i;
// Labels that clearly mean the phone's own mic.
const STRONG = /(built-?in|internal|integrated|\bphone\b|speakerphone|bottom)/i;
// A generic mic label: acceptable when nothing stronger exists.
const GENERIC = /(microphone|\bmic\b)/i;
// Ids that follow the OS default and can jump to a Bluetooth mic later.
const ALIASES = new Set(["default", "communications", ""]);

/**
 * Pure: choose the phone's own mic so Bluetooth earphones stay in stereo (A2DP).
 * Never returns a Bluetooth/headset device. Order-independent. Returns "default"
 * when no input is clearly built-in.
 */
export function pickBuiltInMic(devices: MediaDeviceInfo[]): string {
  let best: { id: string; score: number } | null = null;
  for (const d of devices) {
    if (d.kind !== "audioinput" || BAD.test(d.label)) continue;
    let score = STRONG.test(d.label) ? 20 : GENERIC.test(d.label) ? 10 : 0;
    if (!score) continue;
    if (!ALIASES.has(d.deviceId)) score += 1;
    if (!best || score > best.score) best = { id: d.deviceId, score };
  }
  return best && best.id ? best.id : "default";
}

export interface MicDeps {
  mediaDevices: Pick<MediaDevices, "getUserMedia" | "enumerateDevices">;
  createContext(opts?: AudioContextOptions): AudioContext;
  createWorkletNode(ctx: AudioContext, name: string): AudioWorkletNode;
  loadWorkletUrl(): Promise<string>;
}

export interface StartMicOpts {
  /** Called with each 32 ms chunk: 16 kHz mono PCM16 little-endian, base64. */
  onChunk(b64: string): void;
  /** Optional RMS level (0–1) per chunk, for the voice orb. */
  onLevel?(level: number): void;
  /** Browser adapters; tests inject fakes. */
  deps?: Partial<MicDeps>;
}

export interface MicHandle {
  /** Stops capture, sends any partial chunk, releases the mic and closes the context. Idempotent. */
  stop(): void;
  ctx: AudioContext;
  /** The device actually opened ("default" if no built-in mic was identified). */
  deviceId: string;
}

const VOICE: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

function resolveDeps(d: Partial<MicDeps> = {}): MicDeps {
  return {
    mediaDevices: d.mediaDevices ?? navigator.mediaDevices,
    createContext: d.createContext ?? ((o) => new AudioContext(o)),
    createWorkletNode:
      d.createWorkletNode ??
      ((ctx, name) =>
        new AudioWorkletNode(ctx, name, {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: 1,
          channelCountMode: "explicit", // downmix stereo mics to mono
        })),
    // Vite bundles the worklet as its own file and gives back its URL.
    loadWorkletUrl:
      d.loadWorkletUrl ?? (async () => (await import("./mic-worklet.ts?worker&url")).default),
  };
}

function release(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

const hasLabels = (ds: MediaDeviceInfo[]) => ds.some((d) => d.kind === "audioinput" && d.label);

async function openStream(md: MicDeps["mediaDevices"], deviceId: string): Promise<{ stream: MediaStream; deviceId: string }> {
  if (deviceId !== "default") {
    try {
      const stream = await md.getUserMedia({ audio: { ...VOICE, deviceId: { exact: deviceId } } });
      return { stream, deviceId };
    } catch (e) {
      if ((e as { name?: string })?.name === "NotAllowedError") throw e;
      // Device vanished or is busy: fall through to the default mic.
    }
  }
  return { stream: await md.getUserMedia({ audio: { ...VOICE } }), deviceId: "default" };
}

export async function startMic(opts: StartMicOpts): Promise<MicHandle> {
  const d = resolveDeps(opts.deps);
  const md = d.mediaDevices;

  // Labels are empty until permission is granted: probe once, then enumerate again.
  let devices = await md.enumerateDevices();
  if (!hasLabels(devices)) {
    release(await md.getUserMedia({ audio: true }));
    devices = await md.enumerateDevices();
  }
  const opened = await openStream(md, pickBuiltInMic(devices));
  const stream = opened.stream;

  let ctx: AudioContext | null = null;
  try {
    // Prefer a 16 kHz context so the browser resamples with proper filtering.
    // Some browsers refuse a custom rate or a cross-rate mic source: use the native rate.
    let src: MediaStreamAudioSourceNode;
    try {
      ctx = d.createContext({ sampleRate: MIC_RATE });
      src = ctx.createMediaStreamSource(stream);
    } catch {
      if (ctx) void ctx.close().catch(() => {});
      ctx = d.createContext();
      src = ctx.createMediaStreamSource(stream);
    }
    if (ctx.state === "suspended") await ctx.resume().catch(() => {});
    await ctx.audioWorklet.addModule(await d.loadWorkletUrl());
    const node = d.createWorkletNode(ctx, MIC_WORKLET_NAME);

    const chunker = new Chunker(MIC_CHUNK_SAMPLES);
    let resampler: StreamResampler | null = null;
    const emit = (c: Float32Array) => {
      opts.onLevel?.(rms(c));
      opts.onChunk(int16ToBase64(floatToPcm16(c)));
    };
    node.port.onmessage = (e: MessageEvent<MicFrame>) => {
      const { samples, rate } = e.data;
      if (!resampler || resampler.fromRate !== rate) resampler = new StreamResampler(rate, MIC_RATE);
      for (const c of chunker.push(resampler.process(samples))) emit(c);
    };
    src.connect(node);
    node.connect(ctx.destination); // keeps the node pulled; it outputs silence

    const live = ctx;
    let stopped = false;
    return {
      ctx: live,
      deviceId: opened.deviceId,
      stop() {
        if (stopped) return;
        stopped = true;
        node.port.onmessage = null;
        try { src.disconnect(); } catch { /* already disconnected */ }
        try { node.disconnect(); } catch { /* already disconnected */ }
        release(stream);
        const rest = chunker.flush();
        if (rest) emit(rest);
        void live.close().catch(() => {});
      },
    };
  } catch (e) {
    release(stream);
    if (ctx) void ctx.close().catch(() => {});
    throw e;
  }
}
