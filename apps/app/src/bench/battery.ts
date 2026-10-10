// apps/app/src/bench/battery.ts
export interface BatterySample { level: number; charging: boolean; ts: number }

interface BatteryLike { level: number; charging: boolean }

/** % drained per hour over the trailing run of non-charging samples; needs >= 15 minutes. */
export function drainPerHour(samples: BatterySample[]): number | null {
  let i = samples.length - 1;
  while (i >= 0 && !samples[i].charging) i--;
  const run = samples.slice(i + 1);
  if (run.length < 2) return null;
  const first = run[0];
  const last = run[run.length - 1];
  const hours = (last.ts - first.ts) / 3_600_000;
  if (hours < 0.25) return null;
  return Math.round(((first.level - last.level) * 100 / hours) * 100) / 100;
}

/**
 * Samples the Battery Status API every `everyMs`. Where the API is missing (iOS, Firefox)
 * or blocked by a permissions policy, it resolves to a no-op stop function and never throws.
 */
export async function startBatteryLogger(
  nav: Navigator = navigator,
  everyMs = 60_000,
  onSample: (s: BatterySample) => void = () => {},
): Promise<() => void> {
  const get = (nav as unknown as { getBattery?: () => Promise<BatteryLike> }).getBattery;
  if (typeof get !== "function") return () => {};
  let battery: BatteryLike;
  try {
    battery = await get.call(nav);
  } catch {
    return () => {};
  }
  const sample = () => {
    try { onSample({ level: battery.level, charging: battery.charging, ts: Date.now() }); } catch { /* a bad listener must not stop logging */ }
  };
  sample();
  const id = setInterval(sample, everyMs);
  return () => clearInterval(id);
}
