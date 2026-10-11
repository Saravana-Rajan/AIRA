// frontend/app/src/camera/countUx.ts — speaks live `count` messages: each new number with a tick, final read-back once.
import type { CueName, Lang, ServerMsg } from "@aira/contracts";
import { finalReadback } from "../i18n/camera";

export interface CountProgress {
  sku: string;
  counted: number;
  expected: number | null;
  final: boolean;
}

export interface CountUxDeps {
  speak: (s: string) => void;
  earcon: (n: CueName) => void;
  lang: () => Lang;
  /** Optional: drives the status chip ("Counting 12 / 30") and the task card. Called once per spoken update. */
  onProgress?: (p: CountProgress) => void;
}

export function createCountUx(deps: CountUxDeps) {
  // last number spoken (running or final) — a resent identical message is never spoken twice
  let last: { sku: string; counted: number } | null = null;
  // per SKU, the final read-back already spoken in the current counting run
  const finalSpoken = new Map<string, string>();

  return {
    onServerMsg(m: ServerMsg): void {
      if (m.t !== "count") return;
      const progress: CountProgress = { sku: m.sku, counted: m.counted, expected: m.expected, final: m.final };

      if (m.final) {
        const key = `${m.counted}:${m.expected ?? "-"}`;
        if (finalSpoken.get(m.sku) === key) return;
        finalSpoken.set(m.sku, key);
        last = { sku: m.sku, counted: m.counted };
        deps.onProgress?.(progress);
        deps.earcon("done");
        deps.speak(finalReadback(deps.lang(), m.counted, m.expected));
        return;
      }

      if (last && last.sku === m.sku && last.counted === m.counted) return;
      // a new running number for this SKU starts a new counting run: its final may be read back again
      finalSpoken.delete(m.sku);
      last = { sku: m.sku, counted: m.counted };
      deps.onProgress?.(progress);
      deps.earcon("ack");
      deps.speak(String(m.counted));
    },
  };
}
