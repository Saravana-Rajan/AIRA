// apps/app/src/bench/BenchPage.tsx
// /bench: live p50/p95 per metric, battery drain, guidance trials, and file export.
// Recording happens in the app at "/" (installBenchIfEnabled at startup); this page reads
// what was saved on this device.
import { useEffect, useState } from "preact/hooks";
import { APP_VERSION } from "../core/wire";
import type { Sample } from "./collector";
import { drainPerHour, type BatterySample } from "./battery";
import { downloadText, percentile, toNdjson } from "./export";
import { toTrialsJson, type GuidanceTrial } from "./trials";
import {
  benchEnabled, clearSaved, getSetting, loadBattery, loadSaved, loadTrials, setBenchEnabled, setSetting,
} from "./attach";
import "../ui/tokens.css";
import "./bench.css";

/** The `build` column of every exported row: the app version the hello message reports. */
const BUILD = APP_VERSION;

const METRICS = [
  { id: "first_audio_ms", label: "First audio after you stop talking" },
  { id: "count_update_ms", label: "Count update after a count frame" },
  { id: "guidance_time_to_touch_ms", label: "Hand guidance: time to touch" },
  { id: "sale_e2e_ms", label: "Sale: pending to completed" },
] as const;

function ms(v: number | null): string {
  if (v === null) return "–";
  return v >= 10_000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`;
}

function slug(s: string): string {
  return (s || "unknown").replace(/\W+/g, "-").replace(/^-|-$/g, "") || "unknown";
}

export function BenchPage() {
  const [device, setDevice] = useState(() => getSetting("device"));
  const [product, setProduct] = useState(() => getSetting("product"));
  const [on, setOn] = useState(() => benchEnabled());
  const [samples, setSamples] = useState<Sample[]>(() => loadSaved());
  const [battery, setBattery] = useState<BatterySample[]>(() => loadBattery());
  const [trials, setTrials] = useState<GuidanceTrial[]>(() => loadTrials());

  useEffect(() => {
    const refresh = () => { setSamples(loadSaved()); setBattery(loadBattery()); setTrials(loadTrials()); };
    const tick = setInterval(refresh, 2000);
    window.addEventListener("storage", refresh);
    return () => { clearInterval(tick); window.removeEventListener("storage", refresh); };
  }, []);

  const drain = drainPerHour(battery);
  const lastBattery = battery.length ? battery[battery.length - 1] : null;
  const touched = trials.filter((t) => t.touchedAtMs !== null && t.correctItem).length;

  function toggle() {
    setBenchEnabled(!on);
    setOn(!on);
  }

  function exportLatency() {
    const all: Sample[] = [
      ...samples,
      ...(drain === null ? [] : [{ metric: "battery_drain_pct_per_hour" as const, value: drain, ts: Date.now() }]),
    ];
    downloadText(toNdjson(all, device.trim() || "unknown", BUILD),
      `aira-bench-${slug(device)}-${Date.now()}.ndjson`, "application/x-ndjson");
  }

  function exportTrials() {
    downloadText(toTrialsJson(trials), "trials.json", "application/json");
  }

  function clearAll() {
    if (!window.confirm("Delete all saved bench samples, battery readings and trials on this phone?")) return;
    clearSaved();
    setSamples([]); setBattery([]); setTrials([]);
  }

  return (
    <main class="bench">
      <header class="bench-head">
        <h1>AIRA bench</h1>
        <span class={`bench-chip ${on ? "is-on" : ""}`} role="status">
          {on ? "Recording on this phone" : "Recording off"}
        </span>
      </header>

      <section class="bench-card" aria-labelledby="bench-setup">
        <h2 id="bench-setup">Setup</h2>
        <label class="bench-field">
          <span>Device name</span>
          <input value={device} placeholder="Redmi Note 13" autocomplete="off"
            onInput={(e) => { const v = e.currentTarget.value; setDevice(v); setSetting("device", v); }} />
        </label>
        <label class="bench-field">
          <span>Product under test (SKU, for guidance trials)</span>
          <input value={product} placeholder="sakthi_curd_1l" autocomplete="off" spellcheck={false}
            onInput={(e) => { const v = e.currentTarget.value; setProduct(v); setSetting("product", v); }} />
        </label>
        <div class="bench-actions">
          <button type="button" class={on ? "bench-btn" : "bench-btn primary"} aria-pressed={on} onClick={toggle}>
            {on ? "Stop recording" : "Start recording"}
          </button>
          <a class="bench-btn" href="/">Open AIRA</a>
        </div>
        <p class="bench-help">
          Start recording, then open AIRA and run each scenario at least 20 times. Recording begins
          when the app loads. For battery drain, keep the phone unplugged for at least 15 minutes.
        </p>
      </section>

      <section class="bench-card" aria-labelledby="bench-latency">
        <h2 id="bench-latency">Latency</h2>
        <div class="bench-table-wrap">
          <table class="bench-table">
            <thead><tr><th scope="col">Metric</th><th scope="col">n</th><th scope="col">p50</th><th scope="col">p95</th></tr></thead>
            <tbody>
              {METRICS.map((m) => {
                const v = samples.filter((s) => s.metric === m.id).map((s) => s.value);
                return (
                  <tr key={m.id}>
                    <th scope="row"><span class="bench-metric">{m.label}</span><code>{m.id}</code></th>
                    <td class={v.length < 20 ? "low-n" : ""}>{v.length}</td>
                    <td>{ms(percentile(v, 50))}</td>
                    <td>{ms(percentile(v, 95))}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section class="bench-card" aria-labelledby="bench-battery">
        <h2 id="bench-battery">Battery</h2>
        <p class="bench-big">{drain === null ? "Needs 15+ minutes unplugged" : `${drain} % per hour`}</p>
        <p class="bench-help">
          {battery.length} readings
          {lastBattery ? ` · last ${Math.round(lastBattery.level * 100)} %${lastBattery.charging ? ", charging" : ""}` : " · battery level not available in this browser"}
        </p>
      </section>

      <section class="bench-card" aria-labelledby="bench-trials">
        <h2 id="bench-trials">Guidance trials</h2>
        <p class="bench-big">{trials.length ? `${touched} of ${trials.length} touched the right item` : "No trials yet"}</p>
        <p class="bench-help">Save the file as <code>bench/data/guidance/trials.json</code>.</p>
      </section>

      <div class="bench-actions">
        <button type="button" class="bench-btn primary" onClick={exportLatency} disabled={samples.length === 0 && drain === null}>
          Export latency (NDJSON)
        </button>
        <button type="button" class="bench-btn" onClick={exportTrials} disabled={trials.length === 0}>
          Export trials.json
        </button>
        <button type="button" class="bench-btn danger" onClick={clearAll}>Clear</button>
      </div>
    </main>
  );
}
