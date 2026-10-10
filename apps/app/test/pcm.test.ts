import { describe, it, expect } from "vitest";
import {
  resampleLinear,
  StreamResampler,
  floatToPcm16,
  int16ToBase64,
  base64ToInt16,
  int16ToFloat32,
  rms,
  Chunker,
} from "../src/voice/pcm";

describe("pcm helpers", () => {
  it("resamples 48 kHz to 16 kHz with the right length", () => {
    const one = new Float32Array(48000).fill(0.5);
    const out = resampleLinear(one, 48000, 16000);
    expect(out.length).toBe(16000);
    expect(out[100]).toBeCloseTo(0.5, 5);
  });

  it("is identity when rates match", () => {
    const a = new Float32Array([0.1, -0.2, 0.3]);
    expect(Array.from(resampleLinear(a, 16000, 16000))).toEqual(Array.from(a));
  });

  it("clips floats into PCM16 range", () => {
    const pcm = floatToPcm16(new Float32Array([1.5, -1.5, 0, 0.5]));
    expect(Array.from(pcm)).toEqual([32767, -32768, 0, 16384]);
  });

  it("base64 round-trips little-endian int16", () => {
    const i = new Int16Array([1, -1, 32767, -32768, 1234]);
    expect(Array.from(base64ToInt16(int16ToBase64(i)))).toEqual(Array.from(i));
  });

  it("encodes PCM16 as little-endian bytes", () => {
    // 0x0102 little-endian = bytes [0x02, 0x01]
    expect(int16ToBase64(new Int16Array([0x0102]))).toBe(btoa("\x02\x01"));
  });

  it("int16ToFloat32 maps back to [-1, 1)", () => {
    const f = int16ToFloat32(new Int16Array([32767, -32768, 0]));
    expect(f[0]).toBeCloseTo(0.99997, 4);
    expect(f[0]).toBeLessThan(1);
    expect(f[1]).toBe(-1);
    expect(f[2]).toBe(0);
  });

  it("rms of silence is 0 and of a constant is its magnitude", () => {
    expect(rms(new Float32Array(64))).toBe(0);
    expect(rms(new Float32Array(64).fill(-0.5))).toBeCloseTo(0.5, 6);
    expect(rms(new Float32Array(0))).toBe(0);
  });

  it("Chunker emits exact 512-sample chunks and keeps the remainder", () => {
    const c = new Chunker(512);
    expect(c.push(new Float32Array(300))).toHaveLength(0);
    const out = c.push(new Float32Array(800)); // 1100 total → 2 chunks, 76 left
    expect(out.map((x) => x.length)).toEqual([512, 512]);
    expect(c.flush()!.length).toBe(76);
    expect(c.flush()).toBeNull();
  });

  it("Chunker keeps sample order across pushes and returns independent chunks", () => {
    const c = new Chunker(4);
    const a = c.push(Float32Array.from([1, 2, 3]));
    const b = c.push(Float32Array.from([4, 5, 6, 7, 8, 9]));
    expect(a).toHaveLength(0);
    expect(b.map((x) => Array.from(x))).toEqual([[1, 2, 3, 4], [5, 6, 7, 8]]);
    b[0][0] = 99; // mutating an emitted chunk must not leak into the buffer
    expect(Array.from(c.flush()!)).toEqual([9]);
  });
});

describe("StreamResampler (stateful, for 128-sample worklet quanta)", () => {
  const sine = (n: number, rate: number, hz = 440) =>
    Float32Array.from({ length: n }, (_, i) => Math.sin((2 * Math.PI * hz * i) / rate));

  function streamInQuanta(r: StreamResampler, input: Float32Array, q = 128): Float32Array {
    const parts: number[] = [];
    for (let i = 0; i < input.length; i += q) parts.push(...r.process(input.subarray(i, i + q)));
    return Float32Array.from(parts);
  }

  it("does not drift: 1 s of 48 kHz in 128-sample quanta gives ~16000 samples", () => {
    const out = streamInQuanta(new StreamResampler(48000, 16000), sine(48000, 48000));
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(1);
  });

  it("does not drift at 44.1 kHz either", () => {
    const out = streamInQuanta(new StreamResampler(44100, 16000), sine(44100, 44100));
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(1);
  });

  it("matches one-shot resampling across quantum boundaries", () => {
    const input = sine(44100, 44100);
    const whole = resampleLinear(input, 44100, 16000);
    const streamed = streamInQuanta(new StreamResampler(44100, 16000), input);
    const n = Math.min(whole.length, streamed.length);
    let maxErr = 0;
    for (let i = 0; i < n; i++) maxErr = Math.max(maxErr, Math.abs(whole[i] - streamed[i]));
    expect(maxErr).toBeLessThan(1e-4);
  });

  it("passes samples through unchanged when rates match", () => {
    const r = new StreamResampler(16000, 16000);
    expect(Array.from(r.process(Float32Array.from([0.25, -0.5])))).toEqual([0.25, -0.5]);
  });
});
