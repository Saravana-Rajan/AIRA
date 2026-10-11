import { describe, it, expect, vi, afterEach } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import {
  downmixToMono,
  resampleLinear,
  floatToPcm16,
  chunkPcm,
  int16ToBase64,
  injectAudioFile,
} from "../src/demo/audioInject";
import * as pcm from "../src/voice/pcm";

function fakeCtx(channels: number, rate: number, length: number, value = 0.25) {
  return {
    decodeAudioData: vi.fn(async () => ({
      numberOfChannels: channels,
      sampleRate: rate,
      length,
      getChannelData: (_: number) => new Float32Array(length).fill(value),
    })),
  } as unknown as BaseAudioContext;
}
const okFetch = () =>
  vi.fn(async () => ({ ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(8) })) as unknown as typeof fetch;

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("audio conversion", () => {
  it("downmixes stereo by averaging", () => {
    const out = downmixToMono([new Float32Array([1, 0, -1]), new Float32Array([0, 0, 1])]);
    expect(Array.from(out)).toEqual([0.5, 0, 0]);
  });

  it("downmix of mono is a copy, and of no channels is empty", () => {
    const mono = new Float32Array([0.1, -0.2]);
    const out = downmixToMono([mono]);
    expect(Array.from(out)).toEqual(Array.from(mono));
    expect(out).not.toBe(mono);
    expect(downmixToMono([]).length).toBe(0);
  });

  it("resamples 48 kHz to 16 kHz with the right length (Review Focus #2)", () => {
    const src = new Float32Array(48000).map((_, i) => Math.sin(i / 10));
    expect(resampleLinear(src, 48000, 16000).length).toBe(16000);
  });

  it("clamps instead of wrapping around when converting to PCM16 (Review Focus #2)", () => {
    expect(Array.from(floatToPcm16(new Float32Array([1.5, -1.5, 0, 0.5])))).toEqual([32767, -32768, 0, 16384]);
  });

  it("chunks into 640-sample (40 ms) pieces, keeping the remainder", () => {
    const chunks = chunkPcm(new Int16Array(1500), 640);
    expect(chunks.map((c) => c.length)).toEqual([640, 640, 220]);
  });

  it("base64-encodes little-endian bytes", () => {
    expect(int16ToBase64(new Int16Array([1, -1]))).toBe(btoa(String.fromCharCode(1, 0, 255, 255)));
  });

  it("encodes a chunk taken from the middle of a larger buffer correctly", () => {
    const [, mid] = chunkPcm(new Int16Array([7, 9, 1, -1, 5]), 2);
    expect(Array.from(mid)).toEqual([1, -1]);
    expect(int16ToBase64(mid)).toBe(btoa(String.fromCharCode(1, 0, 255, 255)));
  });

  it("reuses the microphone's PCM helpers so both streams share one wire format", () => {
    expect(resampleLinear).toBe(pcm.resampleLinear);
    expect(floatToPcm16).toBe(pcm.floatToPcm16);
    expect(int16ToBase64).toBe(pcm.int16ToBase64);
  });
});

describe("injectAudioFile", () => {
  it("decodes, converts and sends one `audio` message per 40 ms chunk", async () => {
    const ctx = fakeCtx(2, 48000, 4800);
    const sent: ClientMsg[] = [];
    const n = await injectAudioFile(
      "/demo/soundbox_upi_60.wav",
      (m) => {
        sent.push(m);
        return true;
      },
      { ctx, fetchImpl: okFetch(), realtime: false },
    );
    // 4800 samples @48k = 0.1 s = 1600 samples @16k = 3 chunks (640, 640, 320)
    expect(n).toBe(3);
    expect(sent).toHaveLength(3);
    expect(sent.every((m) => m.t === "audio" && typeof m.pcm16 === "string")).toBe(true);
    // 0.25 → round(0.25 * 32767) = 8192 = 0x2000 → little-endian bytes 00 20
    const first = sent[0] as Extract<ClientMsg, { t: "audio" }>;
    expect(atob(first.pcm16).length).toBe(640 * 2);
    expect(atob(first.pcm16).slice(0, 2)).toBe(String.fromCharCode(0x00, 0x20));
  });

  it("throws a readable error when the file is missing", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false, status: 404 })) as unknown as typeof fetch;
    await expect(
      injectAudioFile("/demo/nope.wav", () => true, { fetchImpl, realtime: false, ctx: {} as BaseAudioContext }),
    ).rejects.toThrow("demo audio 404: /demo/nope.wav");
  });

  it("stops at the first chunk the socket refuses and returns how many were sent", async () => {
    const sent: ClientMsg[] = [];
    const n = await injectAudioFile(
      "/demo/soundbox_upi_60.wav",
      (m) => {
        if (sent.length === 1) return false; // socket closed after the first chunk
        sent.push(m);
        return true;
      },
      { ctx: fakeCtx(1, 16000, 640 * 5), fetchImpl: okFetch(), realtime: false },
    );
    expect(n).toBe(1);
    expect(sent).toHaveLength(1);
  });

  it("paces chunks like live speech when realtime (default)", async () => {
    vi.useFakeTimers();
    const sent: ClientMsg[] = [];
    const done = injectAudioFile(
      "/demo/soundbox_upi_60.wav",
      (m) => {
        sent.push(m);
        return true;
      },
      { ctx: fakeCtx(1, 16000, 640 * 3), fetchImpl: okFetch() },
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(sent).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(40);
    expect(sent).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(80);
    expect(await done).toBe(3);
  });

  it("stops sending when aborted (leaving /try mid-clip)", async () => {
    vi.useFakeTimers();
    const ac = new AbortController();
    const sent: ClientMsg[] = [];
    const done = injectAudioFile(
      "/demo/soundbox_upi_60.wav",
      (m) => {
        sent.push(m);
        return true;
      },
      { ctx: fakeCtx(1, 16000, 640 * 10), fetchImpl: okFetch(), signal: ac.signal },
    );
    await vi.advanceTimersByTimeAsync(40);
    expect(sent).toHaveLength(2);
    ac.abort();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await done).toBe(2);
    expect(sent).toHaveLength(2);
  });

  it("sends nothing when already aborted", async () => {
    const ac = new AbortController();
    ac.abort();
    const send = vi.fn(() => true);
    const n = await injectAudioFile("/demo/soundbox_upi_60.wav", send, {
      ctx: fakeCtx(1, 16000, 640),
      fetchImpl: okFetch(),
      realtime: false,
      signal: ac.signal,
    });
    expect(n).toBe(0);
    expect(send).not.toHaveBeenCalled();
  });

  it("by default decodes with an OfflineAudioContext at 16 kHz (browser resampler, no aliasing)", async () => {
    const made: number[][] = [];
    class FakeOffline {
      constructor(ch: number, len: number, rate: number) {
        made.push([ch, len, rate]);
      }
      decodeAudioData = async () => ({
        numberOfChannels: 1,
        sampleRate: 16000,
        length: 640,
        getChannelData: () => new Float32Array(640),
      });
    }
    vi.stubGlobal("OfflineAudioContext", FakeOffline);
    const n = await injectAudioFile("/demo/soundbox_upi_60.wav", () => true, {
      fetchImpl: okFetch(),
      realtime: false,
    });
    expect(n).toBe(1);
    expect(made).toEqual([[1, 1, 16000]]);
  });
});
