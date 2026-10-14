// frontend/app/src/walk/detector.worker.ts
// Module worker: runs the bump-guard object detector off the UI thread.
// Protocol: see ToWorker / FromWorker in ./detector.
/// <reference lib="webworker" />
import type { FromWorker, ToWorker } from "./detector";
import * as mp from "@mediapipe/tasks-vision";
import { createEngine, type Engine } from "./engine";

const scope = self as unknown as DedicatedWorkerGlobalScope;
let engine: Engine | null = null;

const post = (m: FromWorker) => scope.postMessage(m);

scope.onmessage = async (e: MessageEvent<ToWorker>) => {
  const m = e.data;
  if (m.type === "init") {
    try {
      engine ??= await createEngine(mp, { module: true });
      post({ type: "ready", delegate: engine.delegate });
    } catch (err) {
      post({ type: "error", message: err instanceof Error ? err.message : String(err) });
    }
    return;
  }
  if (m.type === "frame") {
    try {
      const r = engine ? engine.run(m.frame, m.ts) : { ts: m.ts, dets: [], floor: null };
      post({ type: "result", seq: m.seq, ...r });
    } catch (err) {
      // engine.run already catches a detect throw; this is for a failure in run() itself. Report it, never hide it.
      post({ type: "result", seq: m.seq, ts: m.ts, dets: [], floor: null,
        err: err instanceof Error ? err.message : String(err) });
    } finally {
      if ("close" in m.frame && typeof m.frame.close === "function") m.frame.close();
    }
  }
};
