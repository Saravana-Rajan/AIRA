/**
 * Demo-mode audio injection: plays a recorded clip (e.g. the UPI soundbox) into the SAME
 * `audio` stream the microphone uses, 16 kHz mono PCM16 little-endian, base64. The server
 * cannot tell it apart from live mic input, so the real pipeline runs unchanged.
 */
import type { ClientMsg } from "@aira/contracts";
import { floatToPcm16, int16ToBase64, resampleLinear } from "../voice/pcm";

// One implementation for mic and demo audio, so both streams share one wire format.
export { floatToPcm16, int16ToBase64, resampleLinear };

const TARGET_RATE = 16000;

/** Averages all channels into one (stereo soundbox clip → mono). Always returns a new array. */
export function downmixToMono(channels: Float32Array[]): Float32Array {
  const n = channels[0]?.length ?? 0;
  const out = new Float32Array(n);
  for (const ch of channels) for (let i = 0; i < n; i++) out[i] += ch[i] / channels.length;
  return out;
}

/** Splits PCM into fixed-size views (no copy); the last chunk keeps the remainder. */
export function chunkPcm(pcm: Int16Array, samplesPerChunk: number): Int16Array[] {
  const chunks: Int16Array[] = [];
  for (let i = 0; i < pcm.length; i += samplesPerChunk) chunks.push(pcm.subarray(i, i + samplesPerChunk));
  return chunks;
}

/**
 * Decoding into a 16 kHz offline context lets the browser's band-limited resampler do the
 * rate change; the linear resample below then only runs if a context reports another rate.
 */
function defaultContext(): BaseAudioContext {
  try {
    return new OfflineAudioContext(1, 1, TARGET_RATE);
  } catch {
    return new OfflineAudioContext(1, 1, 48000);
  }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export interface InjectOpts {
  /** Chunk length in ms (default 40 ms = 640 samples @ 16 kHz). */
  chunkMs?: number;
  /** Pace chunks like live speech so server-side VAD behaves normally (default true). */
  realtime?: boolean;
  ctx?: BaseAudioContext;
  fetchImpl?: typeof fetch;
  /** Abort to stop sending mid-clip (e.g. the judge leaves /try). */
  signal?: AbortSignal;
}

/**
 * Plays a demo audio file into the `audio` stream. Returns the number of chunks the socket
 * accepted. Stops early when `send` returns false (socket not open) or `signal` aborts.
 */
export async function injectAudioFile(
  url: string,
  send: (m: ClientMsg) => boolean,
  opts: InjectOpts = {},
): Promise<number> {
  const chunkMs = opts.chunkMs ?? 40;
  const realtime = opts.realtime ?? true;
  const res = await (opts.fetchImpl ?? fetch)(url);
  if (!res.ok) throw new Error(`demo audio ${res.status}: ${url}`);
  const ctx = opts.ctx ?? defaultContext();
  const buf = await ctx.decodeAudioData(await res.arrayBuffer());
  const channels = Array.from({ length: buf.numberOfChannels }, (_, c) => buf.getChannelData(c));
  const pcm = floatToPcm16(resampleLinear(downmixToMono(channels), buf.sampleRate, TARGET_RATE));
  const chunks = chunkPcm(pcm, Math.round((TARGET_RATE * chunkMs) / 1000));
  let sent = 0;
  for (const c of chunks) {
    if (opts.signal?.aborted) break;
    if (!send({ t: "audio", pcm16: int16ToBase64(c) })) break;
    sent++;
    if (realtime) await sleep(chunkMs);
  }
  return sent;
}
