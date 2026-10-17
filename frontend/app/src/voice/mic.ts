import { Chunker, StreamResampler, floatToPcm16, int16ToBase64, rms } from "./pcm";

export const MIC_RATE = 16000;
export const MIC_CHUNK_SAMPLES = 512; // 32 ms @ 16 kHz
export const MIC_WORKLET_NAME = "aira-mic-tap";

/** Message posted by mic-worklet.ts. */
export interface MicFrame {
  samples: Float32Array;
  rate: number;
}

// The mic is always the OS default input, never a device picked by label. Bluetooth or wired earphones
// then carry both AIRA's voice and Sam's mic: on Chrome Android the input list is the communication route
// list ("Speakerphone", "Bluetooth headset", ...) and forcing "Speakerphone" sends ALL playback to the
// loudspeaker; on iOS Safari forcing the built-in mic breaks the HFP pair and output falls back to the
// speaker too. The default also follows the OS order (wired > USB > Bluetooth > phone), so a headset mic
// sits closer to the mouth than the phone's: a help on a noisy street. No output-device API exists on these
// phones (setSinkId is unsupported), so the OS decides where audio plays and the mic simply follows it.

export interface MicDeps {
  mediaDevices: Pick<MediaDevices, "getUserMedia">;
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
  /** Always "default": the OS picks the input, so an earphone mic and its speaker stay paired. */
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

/** How long startMic waits for a suspended context to resume before going on (it keeps trying in the background). */
export const MIC_RESUME_WAIT_MS = 1000;

/**
 * resume(), but never waits more than MIC_RESUME_WAIT_MS: outside a user gesture iOS Safari can leave the promise
 * pending, and startMic must not hang on it (the hands-free start and the mic watchdog call it without a tap).
 */
async function resumeBriefly(ctx: AudioContext): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  await Promise.race([
    ctx.resume().catch(() => {}),
    new Promise<void>((r) => { timer = setTimeout(r, MIC_RESUME_WAIT_MS); }),
  ]);
  clearTimeout(timer);
}

function release(stream: MediaStream | null): void {
  stream?.getTracks().forEach((t) => t.stop());
}

export async function startMic(opts: StartMicOpts): Promise<MicHandle> {
  const d = resolveDeps(opts.deps);
  // One getUserMedia, no deviceId: the OS default input (see the note at the top).
  const stream = await d.mediaDevices.getUserMedia({ audio: { ...VOICE } });

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
    if (ctx.state === "suspended") await resumeBriefly(ctx);
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
    // iOS Safari: a phone call or Siri leaves the context "interrupted" (or suspended); resume when it lets us.
    const onState = () => {
      const st = String(live.state);
      if (!stopped && (st === "interrupted" || st === "suspended")) void live.resume().catch(() => {});
    };
    live.addEventListener?.("statechange", onState);
    return {
      ctx: live,
      deviceId: "default",
      stop() {
        if (stopped) return;
        stopped = true;
        node.port.onmessage = null;
        try { live.removeEventListener?.("statechange", onState); } catch { /* fake or closed context */ }
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
