// frontend/app/src/camera/encode.worker.ts
// Module worker: scales a camera frame on an OffscreenCanvas and encodes it as JPEG, off the UI thread.
// Protocol: EncodeJob / EncodeReply in ./encode. Jobs run one at a time, in arrival order.
/// <reference lib="webworker" />
import type { EncodeJob, EncodeReply } from "./encode";
import { runEncodeJob, type JobCanvas } from "./encodeJob";

const scope = self as unknown as DedicatedWorkerGlobalScope;
const makeCanvas = (w: number, h: number) => new OffscreenCanvas(w, h) as unknown as JobCanvas;

let chain: Promise<void> = Promise.resolve();

scope.onmessage = (e: MessageEvent<EncodeJob>) => {
  const job = e.data;
  if (!job || job.type !== "encode") return;
  chain = chain.then(async () => {
    let reply: EncodeReply;
    try {
      reply = await runEncodeJob(job, makeCanvas);
    } catch {
      reply = { type: "fail", id: job.id, code: "no_canvas" };
    }
    scope.postMessage(reply, reply.type === "done" && reply.gray ? [reply.gray.buffer] : []);
  });
};
