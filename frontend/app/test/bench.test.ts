// frontend/app/test/bench.test.ts
import { describe, it, expect } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { BenchCollector } from "../src/bench/collector";
import { drainPerHour, startBatteryLogger } from "../src/bench/battery";
import { percentile, toNdjson } from "../src/bench/export";
import { GuidanceTrialLog, toTrialsJson } from "../src/bench/trials";
import { attachBench, memoryStore, loadSaved, loadTrials } from "../src/bench/attach";

describe("BenchCollector", () => {
  it("measures first audio after the user's final transcript, once per turn", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "transcript", role: "user", text: "stock?", final: true }, 1000);
    c.onIncoming({ t: "audio", pcm24: "A" }, 1650);
    c.onIncoming({ t: "audio", pcm24: "B" }, 1700);
    expect(c.samples).toEqual([{ metric: "first_audio_ms", value: 650, ts: 1650 }]);
  });
  it("measures count update from the most recent count frame", () => {
    const c = new BenchCollector();
    c.onOutgoing({ t: "frame", id: "f1", jpeg: "x", w: 1, h: 1, ts: 0, purpose: "count" }, 100);
    c.onOutgoing({ t: "frame", id: "f2", jpeg: "x", w: 1, h: 1, ts: 0, purpose: "count" }, 1100);
    c.onIncoming({ t: "count", sku: "s", counted: 1, expected: 10, final: false, confidence: 0.9 }, 1900);
    expect(c.samples).toEqual([{ metric: "count_update_ms", value: 800, ts: 1900 }]);
  });
  it("measures guidance time-to-touch and resets on stop", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "target", frameId: "f", label: "curd", point: [500, 500], confidence: 0.8 }, 0);
    c.onIncoming({ t: "target", frameId: "g", label: "curd", point: [510, 490], confidence: 0.8 }, 900);
    c.onOutgoing({ t: "event", name: "fingertip_in_target", data: {} }, 4200);
    expect(c.samples).toEqual([{ metric: "guidance_time_to_touch_ms", value: 4200, ts: 4200 }]);
    c.onIncoming({ t: "target", frameId: "h", label: "milk", point: [1, 1], confidence: 0.8 }, 5000);
    c.onOutgoing({ t: "stop" }, 5100);
    c.onOutgoing({ t: "event", name: "fingertip_in_target", data: {} }, 9000);
    expect(c.samples.length).toBe(1);
  });
  it("pairs sales by id; void and unmatched sales produce nothing (Review Focus #4)", () => {
    const c = new BenchCollector();
    c.onIncoming({ t: "state", entity: "sale", id: "a", state: "SALE_PENDING", summary: "" }, 0);
    c.onIncoming({ t: "state", entity: "sale", id: "b", state: "SALE_PENDING", summary: "" }, 1000);
    c.onIncoming({ t: "state", entity: "sale", id: "b", state: "SALE_COMPLETED", summary: "" }, 21000);
    c.onIncoming({ t: "state", entity: "sale", id: "a", state: "SALE_VOID", summary: "" }, 22000);
    c.onIncoming({ t: "state", entity: "sale", id: "z", state: "SALE_COMPLETED", summary: "" }, 23000);
    expect(c.samples).toEqual([{ metric: "sale_e2e_ms", value: 20000, ts: 21000 }]);
  });
});

describe("battery", () => {
  it("computes %/hour over the trailing non-charging run of ≥15 minutes", () => {
    const h = 3_600_000;
    expect(drainPerHour([
      { level: 1.0, charging: true, ts: 0 },
      { level: 0.9, charging: false, ts: 0.1 * h },
      { level: 0.84, charging: false, ts: 1.1 * h },
    ])).toBe(6);
  });
  it("returns null for under 15 minutes of data", () => {
    expect(drainPerHour([{ level: 1, charging: false, ts: 0 }, { level: 0.99, charging: false, ts: 60_000 }])).toBeNull();
  });
  it("is a no-op where the Battery API is missing (Review Focus #5)", async () => {
    const stop = await startBatteryLogger({} as Navigator, 10, () => { throw new Error("should not sample"); });
    expect(typeof stop).toBe("function");
    stop();
  });
  it("is a no-op when getBattery rejects (permissions policy)", async () => {
    const nav = { getBattery: () => Promise.reject(new Error("blocked")) } as unknown as Navigator;
    const stop = await startBatteryLogger(nav, 10, () => { throw new Error("should not sample"); });
    expect(typeof stop).toBe("function");
    stop();
  });
});

describe("export", () => {
  it("nearest-rank percentiles", () => {
    expect(percentile([5, 1, 3, 2, 4], 50)).toBe(3);
    expect(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95)).toBe(10);
    expect(percentile([], 50)).toBeNull();
  });
  it("NDJSON rows match the aira.latency_metrics schema", () => {
    const out = toNdjson([{ metric: "first_audio_ms", value: 650, ts: Date.UTC(2026, 9, 15, 10, 0, 0) }], "Redmi Note 13", "0.1.0");
    expect(JSON.parse(out.trim())).toEqual({
      metric: "first_audio_ms", value: 650, unit: "ms", device: "Redmi Note 13", build: "0.1.0", ts: "2026-10-15T10:00:00.000Z",
    });
  });
});

const target = (label: string): ServerMsg => ({ t: "target", frameId: "f", label, point: [500, 500], confidence: 0.8 });
const touch: ClientMsg = { t: "event", name: "fingertip_in_target", data: {} };
const say = (text: string): ServerMsg => ({ t: "transcript", role: "aira", text, final: true });

describe("guidance trial log (shared/bench/data/guidance/trials.json)", () => {
  it("records start at the first target, touch time, and the ✓ verdict", () => {
    const log = new GuidanceTrialLog();
    log.setProduct("sakthi_curd_1l");
    log.onIncoming(target("curd"), 1000);
    log.onIncoming(target("curd"), 1500);
    log.onOutgoing(touch, 5200);
    log.onIncoming(say("Sakthi curd 1 L ✓. Take 1 more."), 6000);
    expect(log.trials).toEqual([
      { trial: 1, product: "sakthi_curd_1l", startedAtMs: 1000, touchedAtMs: 5200, correctItem: true },
    ]);
  });
  it("a mismatch cue marks the touched item wrong; product falls back to the target label", () => {
    const log = new GuidanceTrialLog();
    log.onIncoming(target("aavin_milk_500ml"), 0);
    log.onOutgoing(touch, 4000);
    log.onIncoming({ t: "cue", cue: "mismatch" }, 4500);
    expect(log.trials).toEqual([
      { trial: 1, product: "aavin_milk_500ml", startedAtMs: 0, touchedAtMs: 4000, correctItem: false },
    ]);
  });
  it("no touch within 30 s is a timeout (touchedAtMs null), even if a touch comes later", () => {
    const log = new GuidanceTrialLog();
    log.setProduct("arun_icecream_box");
    log.onIncoming(target("ice cream"), 0);
    log.onOutgoing(touch, 31_000);
    expect(log.trials).toEqual([
      { trial: 1, product: "arun_icecream_box", startedAtMs: 0, touchedAtMs: null, correctItem: false },
    ]);
  });
  it("tick() closes a timed-out trial without any further message", () => {
    const log = new GuidanceTrialLog();
    log.onIncoming(target("curd"), 0);
    log.tick(29_999);
    expect(log.trials).toEqual([]);
    log.tick(30_001);
    expect(log.trials).toEqual([{ trial: 1, product: "curd", startedAtMs: 0, touchedAtMs: null, correctItem: false }]);
  });
  it("stop before 30 s discards the run; numbering continues from recorded trials", () => {
    const log = new GuidanceTrialLog();
    log.onIncoming(target("curd"), 0);
    log.onOutgoing({ t: "stop" }, 2000);
    log.onIncoming(target("curd"), 3000);
    log.onOutgoing(touch, 7000);
    log.onIncoming({ t: "cue", cue: "match" }, 7500);
    expect(log.trials.map((t) => [t.trial, t.startedAtMs, t.touchedAtMs, t.correctItem])).toEqual([[1, 3000, 7000, true]]);
  });
  it("continues numbering after previously saved trials", () => {
    const log = new GuidanceTrialLog(3);
    log.onIncoming(target("curd"), 0);
    log.onOutgoing(touch, 1000);
    log.onIncoming({ t: "cue", cue: "match" }, 1100);
    expect(log.trials[0].trial).toBe(4);
  });
  it("serializes to exactly the scorer's {trials:[...]} shape", () => {
    const json = toTrialsJson([{ trial: 1, product: "sakthi_curd_1l", startedAtMs: 0, touchedAtMs: 4200, correctItem: true }]);
    expect(JSON.parse(json)).toEqual({
      trials: [{ trial: 1, product: "sakthi_curd_1l", startedAtMs: 0, touchedAtMs: 4200, correctItem: true }],
    });
    expect(Object.keys(JSON.parse(json).trials[0])).toEqual(["trial", "product", "startedAtMs", "touchedAtMs", "correctItem"]);
    expect(JSON.parse(toTrialsJson([]))).toEqual({ trials: [] });
  });
});

describe("attachBench", () => {
  function fakeApp() {
    const handlers = new Set<(m: ServerMsg) => void>();
    const sent: ClientMsg[] = [];
    let open = true;
    const socket = {
      on(h: (m: ServerMsg) => void) { handlers.add(h); return () => { handlers.delete(h); }; },
      send(m: ClientMsg) { if (!open) return false; sent.push(m); return true; },
    };
    return {
      app: { socket },
      emit: (m: ServerMsg) => handlers.forEach((h) => h(m)),
      sent, handlers,
      setOpen: (v: boolean) => { open = v; },
    };
  }

  it("observes traffic without changing it and persists samples and trials", () => {
    const f = fakeApp();
    const store = memoryStore();
    let now = 0;
    const off = attachBench(f.app, { store, now: () => now, battery: false });
    f.emit({ t: "transcript", role: "user", text: "hi", final: true });
    now = 700;
    f.emit({ t: "audio", pcm24: "A" });
    now = 1000;
    f.emit(target("curd"));
    now = 3000;
    expect(f.app.socket.send(touch)).toBe(true);
    expect(f.sent).toEqual([touch]);
    expect(loadSaved(store)).toEqual([
      { metric: "first_audio_ms", value: 700, ts: 700 },
      { metric: "guidance_time_to_touch_ms", value: 2000, ts: 3000 },
    ]);
    now = 3200;
    f.emit({ t: "cue", cue: "match" });
    expect(loadTrials(store)).toEqual([{ trial: 1, product: "curd", startedAtMs: 1000, touchedAtMs: 3000, correctItem: true }]);
    off();
    expect(f.handlers.size).toBe(0);
  });

  it("ignores messages the socket failed to send", () => {
    const f = fakeApp();
    const store = memoryStore();
    let now = 0;
    const off = attachBench(f.app, { store, now: () => now, battery: false });
    f.setOpen(false);
    expect(f.app.socket.send({ t: "frame", id: "f1", jpeg: "x", w: 1, h: 1, ts: 0, purpose: "count" })).toBe(false);
    now = 900;
    f.emit({ t: "count", sku: "s", counted: 1, expected: null, final: false, confidence: 0.9 });
    expect(loadSaved(store)).toEqual([]);
    off();
  });

  it("restores the original send on detach and is idempotent per socket", () => {
    const f = fakeApp();
    const original = f.app.socket.send;
    const store = memoryStore();
    const off1 = attachBench(f.app, { store, battery: false });
    const off2 = attachBench(f.app, { store, battery: false });
    expect(f.handlers.size).toBe(1);
    off2();
    expect(f.handlers.size).toBe(1);
    off1();
    expect(f.app.socket.send).toBe(original);
    expect(f.handlers.size).toBe(0);
  });
});
