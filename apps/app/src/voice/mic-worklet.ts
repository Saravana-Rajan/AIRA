// Runs in AudioWorkletGlobalScope (loaded via ctx.audioWorklet.addModule).
// Collects the 128-sample render quanta into 512-sample frames (Float32, at the
// context's own rate) and transfers each frame to the main thread. Batching cuts
// main-thread messages 4x; at a 16 kHz context one frame is exactly one 32 ms chunk.

declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
}

const FRAME = 512;

class MicTap extends AudioWorkletProcessor {
  private buf = new Float32Array(FRAME);
  private n = 0;

  process(inputs: Float32Array[][]): boolean {
    const ch = inputs[0]?.[0];
    if (!ch || !ch.length) return true;
    let i = 0;
    while (i < ch.length) {
      const take = Math.min(FRAME - this.n, ch.length - i);
      this.buf.set(ch.subarray(i, i + take), this.n);
      this.n += take;
      i += take;
      if (this.n === FRAME) {
        const samples = this.buf;
        this.port.postMessage({ samples, rate: sampleRate }, [samples.buffer]);
        this.buf = new Float32Array(FRAME);
        this.n = 0;
      }
    }
    return true;
  }
}

// Must match MIC_WORKLET_NAME in ./mic.ts (kept literal: this file is bundled on its own).
registerProcessor("aira-mic-tap", MicTap);

export {};
