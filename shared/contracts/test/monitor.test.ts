import { describe, it, expect } from "vitest";
import monitorJson from "../fixtures/monitor_msgs.json";
import {
  isMonitorClientMsg,
  isMonitorMsg,
  parseMonitorMsg,
  MONITOR_KINDS,
  MONITOR_ROLES,
  MONITOR_STATES,
  MONITOR_VISION_SOURCES,
} from "../src/index";

type Raw = Record<string, any>;
const msgs = monitorJson as unknown as Raw[];

describe("monitor contract (/monitor, read-only dashboard feed)", () => {
  it("every fixture is a valid MonitorMsg", () => {
    for (const m of msgs) expect(isMonitorMsg(m), JSON.stringify(m)).toBe(true);
  });

  it("fixtures cover every kind, role, state and vision source", () => {
    expect(new Set(msgs.map((m) => m.kind))).toEqual(new Set(MONITOR_KINDS));
    expect(new Set(msgs.filter((m) => m.kind === "transcript").map((m) => m.role))).toEqual(new Set(MONITOR_ROLES));
    expect(new Set(msgs.filter((m) => m.kind === "status").map((m) => m.state))).toEqual(new Set(MONITOR_STATES));
    expect(new Set(msgs.filter((m) => m.kind === "vision").map((m) => m.source))).toEqual(
      new Set(MONITOR_VISION_SOURCES),
    );
  });

  it("lists are fixed", () => {
    expect(MONITOR_KINDS).toEqual(["session", "transcript", "frame", "vision", "hand", "event", "status", "stats"]);
    expect(MONITOR_ROLES).toEqual(["owner", "customer", "aira"]);
    expect(MONITOR_VISION_SOURCES).toEqual(["mediapipe", "er2", "bump"]);
    expect(MONITOR_STATES).toEqual(["listening", "thinking", "guiding", "found", "idle"]);
  });

  it("a vision object carries a box or point, or null for each, and optional score, severity and distance", () => {
    const base = { t: "mon", kind: "vision", session: "s", ts: 1, source: "er2", target: "milk", found: false };
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: [1, 2, 3, 4], point: [1, 2] }] })).toBe(true);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: null, point: null }] })).toBe(true);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: [1, 2, 3, 4], point: null, score: 0.9, severity: "danger", distance: "near" }], severity: "danger", context: "guidance", frameId: "f1" })).toBe(true);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: [1, 2, 3], point: null }] })).toBe(false);
    expect(isMonitorMsg({ ...base, objects: [{ label: 5, box: null, point: null }] })).toBe(false);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: null, point: [1001, 0] }] })).toBe(false);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: null, point: null, severity: "high" }] })).toBe(false);
    expect(isMonitorMsg({ ...base, objects: [{ label: "milk", box: null, point: null, distance: "close" }] })).toBe(false);
    expect(isMonitorMsg({ ...base, source: "guidance", objects: [] })).toBe(false);
  });

  it("a monitor sends only rtc signalling: there is no auth message any more (the monitor key was removed)", () => {
    expect(isMonitorClientMsg({ t: "rtc", kind: "want", to: "s1" })).toBe(true);
    expect(isMonitorClientMsg({ t: "auth", key: "k" })).toBe(false);
    expect(isMonitorClientMsg({ t: "auth" })).toBe(false);
    expect(isMonitorClientMsg({ t: "hello", key: "k" })).toBe(false);
    expect(isMonitorClientMsg(null)).toBe(false);
  });

  it("stats carry integer counters and a nullable latency", () => {
    const base = { t: "mon", kind: "stats", session: "s", ts: 1 };
    expect(isMonitorMsg({ ...base, turns: 1, er2Calls: 2, flashCalls: 0, visionMs: null, fps: 0 })).toBe(true);
    expect(isMonitorMsg({ ...base, turns: 1, er2Calls: 2, flashCalls: 0, visionMs: 820, fps: 2.5 })).toBe(true);
    expect(isMonitorMsg({ ...base, turns: 1, er2Calls: 2, flashCalls: 0, fps: 2.5 })).toBe(false);
    expect(isMonitorMsg({ ...base, turns: 1.5, er2Calls: 2, flashCalls: 0, visionMs: null, fps: 0 })).toBe(false);
  });

  it("rejects malformed messages", () => {
    expect(isMonitorMsg(null)).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "nope", session: "s", ts: 1 })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "status", session: "s", ts: 1, state: "sleeping" })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "transcript", session: "s", ts: 1, role: "user", text: "x", final: true })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "hand", session: "s", ts: 1, point: [1200, 5], near: true })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "frame", session: "s", ts: 1, jpeg_b64: "AAAA", w: "480", h: 1, purpose: "ask" })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "event", session: "s", ts: 1, name: "x", detail: [] })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "session", session: "s", ts: 1, state: "paused", lang: "en" })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "session", session: "s", ts: 1, state: "start", lang: "en", uid: 5 })).toBe(false);
    expect(isMonitorMsg({ t: "transcript", role: "aira", text: "x", final: true })).toBe(false);
  });

  it("parses raw socket text", () => {
    for (const m of msgs) expect(parseMonitorMsg(JSON.stringify(m))).toEqual(m);
    expect(parseMonitorMsg("{nope")).toBeNull();
    expect(parseMonitorMsg('{"t":"mon","kind":"future"}')).toBeNull();
  });
});
