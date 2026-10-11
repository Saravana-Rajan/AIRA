// Pure PCM helpers for the voice pipeline.
// Mic → server: 16 kHz mono PCM16 little-endian, base64. Server → playback: 24 kHz PCM16 base64.

/** One-shot linear resampler for a whole buffer. Returns a copy when the rates match. */
export function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input.slice();
  const ratio = fromRate / toRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

/**
 * Streaming linear resampler. It keeps the fractional read position and the last
 * input sample between calls, so feeding it small worklet frames gives the same
 * output as resampling the whole stream at once. Resampling each frame on its own
 * would drop up to one sample per frame, which speeds up and pitch-shifts the voice
 * by about 1.5 % at 48 kHz.
 */
export class StreamResampler {
  private readonly ratio: number;
  private pos = 0; // read position, relative to the first sample of the next buffer
  private carry: number | null = null; // last sample of the previous input

  constructor(readonly fromRate: number, readonly toRate: number) {
    if (!(fromRate > 0) || !(toRate > 0)) throw new RangeError("sample rates must be positive");
    this.ratio = fromRate / toRate;
  }

  process(input: Float32Array): Float32Array {
    if (this.fromRate === this.toRate) return input.slice();
    if (!input.length) return new Float32Array(0);
    const hasCarry = this.carry !== null;
    const len = input.length + (hasCarry ? 1 : 0);
    const at = (k: number) => (hasCarry ? (k === 0 ? (this.carry as number) : input[k - 1]) : input[k]);
    const out: number[] = [];
    let t = this.pos;
    // Interpolate only where both neighbours exist; the rest waits for the next call.
    while (Math.floor(t) + 1 < len) {
      const i0 = Math.floor(t);
      const frac = t - i0;
      out.push(at(i0) * (1 - frac) + at(i0 + 1) * frac);
      t += this.ratio;
    }
    this.carry = input[input.length - 1];
    this.pos = t - (len - 1);
    return Float32Array.from(out);
  }
}

export function floatToPcm16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) {
    const s = Math.max(-1, Math.min(1, f[i]));
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
  }
  return out;
}

/** PCM16 → float in [-1, 1). */
export function int16ToFloat32(i: Int16Array): Float32Array {
  const out = new Float32Array(i.length);
  for (let k = 0; k < i.length; k++) out[k] = i[k] / 32768;
  return out;
}

export function int16ToBase64(i: Int16Array): string {
  const bytes = new Uint8Array(i.length * 2);
  const view = new DataView(bytes.buffer);
  for (let k = 0; k < i.length; k++) view.setInt16(k * 2, i[k], true);
  let bin = "";
  const STEP = 0x8000;
  for (let k = 0; k < bytes.length; k += STEP) {
    bin += String.fromCharCode.apply(null, bytes.subarray(k, k + STEP) as unknown as number[]);
  }
  return btoa(bin);
}

export function base64ToInt16(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let k = 0; k < bin.length; k++) bytes[k] = bin.charCodeAt(k);
  const view = new DataView(bytes.buffer);
  const out = new Int16Array(Math.floor(bytes.length / 2));
  for (let k = 0; k < out.length; k++) out[k] = view.getInt16(k * 2, true);
  return out;
}

/** Root-mean-square level of a frame (0 for an empty frame). Drives the voice orb. */
export function rms(f: Float32Array): number {
  if (!f.length) return 0;
  let sum = 0;
  for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
  return Math.sqrt(sum / f.length);
}

/** Accumulates float samples and emits fixed-size chunks (512 samples @16 kHz = 32 ms). */
export class Chunker {
  private buf: Float32Array;
  private n = 0;

  constructor(private readonly samplesPerChunk: number) {
    if (!Number.isInteger(samplesPerChunk) || samplesPerChunk <= 0) {
      throw new RangeError("samplesPerChunk must be a positive integer");
    }
    this.buf = new Float32Array(samplesPerChunk);
  }

  push(f: Float32Array): Float32Array[] {
    const out: Float32Array[] = [];
    let i = 0;
    while (i < f.length) {
      const take = Math.min(this.samplesPerChunk - this.n, f.length - i);
      this.buf.set(f.subarray(i, i + take), this.n);
      this.n += take;
      i += take;
      if (this.n === this.samplesPerChunk) {
        out.push(this.buf.slice());
        this.n = 0;
      }
    }
    return out;
  }

  flush(): Float32Array | null {
    if (!this.n) return null;
    const out = this.buf.slice(0, this.n);
    this.n = 0;
    return out;
  }
}
