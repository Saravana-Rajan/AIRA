// apps/app/src/bench/export.ts
import type { Sample } from "./collector";

/** Nearest-rank percentile; null for no data. */
export function percentile(values: number[], p: number): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.min(sorted.length, Math.max(1, rank)) - 1];
}

/**
 * One JSON row per sample, in the latency_metrics row shape
 * {metric, value, unit, device, build, ts (ISO-8601)}. bench/publish.py reads these files
 * and writes the p50/p95 CSV.
 */
export function toNdjson(samples: Sample[], device: string, build: string): string {
  return samples.map((s) => JSON.stringify({
    metric: s.metric,
    value: s.value,
    unit: s.metric === "battery_drain_pct_per_hour" ? "pct_per_hour" : "ms",
    device,
    build,
    ts: new Date(s.ts).toISOString(),
  })).join("\n") + "\n";
}

/** Starts a browser download of `text` as `filename`. */
export function downloadText(text: string, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
