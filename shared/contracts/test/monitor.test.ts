import { describe, it, expect } from "vitest";
import monitorJson from "../fixtures/monitor_msgs.json";
import monitorClientJson from "../fixtures/monitor_client_msgs.json";
import {
  isMonitorAuthMsg,
  isMonitorClientMsg,
  isMonitorListenMsg,
  isMonitorMsg,
  parseMonitorClientMsg,
  parseMonitorMsg,
  MONITOR_AUDIO_RATES,
  MONITOR_AUTH_TIMEOUT_S,
  MONITOR_CLOSE_UNAUTHENTICATED,
  MONITOR_CLOSE_UNAUTHORIZED,
  MONITOR_KINDS,
  MONITOR_ROLES,
  MONITOR_STATES,
  MONITOR_VISION_SOURCES,
  MONITOR_VOICES,
} from "../src/index";

type Raw = Record<string, any>;
const msgs = monitorJson as unknown as Raw[];
const clientMsgs = monitorClientJson as unknown as Raw[];

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
    expect(MONITOR_KINDS).toEqual(["session", "transcript", "frame", "vision", "hand", "event", "status", "orientation", "stats", "ready", "audio"]);
    expect(MONITOR_VOICES).toEqual(["owner", "aira"]);
    expect(MONITOR_AUDIO_RATES).toEqual([16000, 24000]);
    expect(MONITOR_ROLES).toEqual(["owner", "customer", "aira"]);
    expect(MONITOR_VISION_SOURCES).toEqual(["mediapipe", "er2", "bump"]);
    expect(MONITOR_STATES).toEqual(["listening", "thinking", "guiding", "found", "idle"]);
  });

  it("orientation: the phone's screen angle (0/90/180/270) and nothing else", () => {
    const base = { t: "mon", kind: "orientation", session: "s1", ts: 1 };
    expect(isMonitorMsg({ ...base, angle: 90 })).toBe(true);
    expect(isMonitorMsg({ ...base, angle: 45 })).toBe(false);
    expect(isMonitorMsg(base)).toBe(false);
    // a session message may carry the last-known orientation, for a monitor that joins late
    const sess = { t: "mon", kind: "session", session: "s1", ts: 1, state: "start", lang: "ta" };
    expect(isMonitorMsg({ ...sess, orientation: 180 })).toBe(true);
    expect(isMonitorMsg({ ...sess, orientation: 45 })).toBe(false);
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

  it("a monitor sends its Firebase ID token as {t:\"auth\", idToken}, listen requests and rtc signalling, nothing else", () => {
    for (const m of clientMsgs) expect(isMonitorClientMsg(m), JSON.stringify(m)).toBe(true);
    expect(new Set(clientMsgs.map((m) => m.t))).toEqual(new Set(["auth", "listen", "rtc"]));
    expect(isMonitorAuthMsg({ t: "auth", idToken: "eyJ.x.y" })).toBe(true);
    expect(isMonitorClientMsg({ t: "auth", idToken: "eyJ.x.y" })).toBe(true);
    expect(isMonitorClientMsg({ t: "rtc", kind: "want", to: "s1" })).toBe(true);
    // the old shared-key message is not an auth message
    expect(isMonitorClientMsg({ t: "auth", key: "k" })).toBe(false);
    expect(isMonitorAuthMsg({ t: "auth", key: "k" })).toBe(false);
    expect(isMonitorClientMsg({ t: "auth" })).toBe(false);
    expect(isMonitorClientMsg({ t: "auth", idToken: "" })).toBe(false);
    expect(isMonitorClientMsg({ t: "auth", idToken: 5 })).toBe(false);
    expect(isMonitorClientMsg({ t: "hello", idToken: "x" })).toBe(false);
    expect(isMonitorClientMsg(null)).toBe(false);
    for (const m of clientMsgs) expect(parseMonitorClientMsg(JSON.stringify(m))).toEqual(m);
    expect(parseMonitorClientMsg("{nope")).toBeNull();
    expect(parseMonitorClientMsg('{"t":"auth"}')).toBeNull();
  });

  it("listen: a session id to hear, or null to stop; nothing else", () => {
    expect(isMonitorListenMsg({ t: "listen", session: "s1" })).toBe(true);
    expect(isMonitorListenMsg({ t: "listen", session: null })).toBe(true);
    expect(isMonitorClientMsg({ t: "listen", session: "s1" })).toBe(true);
    expect(isMonitorListenMsg({ t: "listen" })).toBe(false);
    expect(isMonitorListenMsg({ t: "listen", session: "" })).toBe(false);
    expect(isMonitorListenMsg({ t: "listen", session: 5 })).toBe(false);
    expect(isMonitorListenMsg({ t: "listen", session: ["s1"] })).toBe(false);
    expect(isMonitorListenMsg({ t: "auth", session: "s1" })).toBe(false);
    expect(parseMonitorClientMsg('{"t":"listen","session":null}')).toEqual({ t: "listen", session: null });
    expect(parseMonitorClientMsg('{"t":"listen"}')).toBeNull();
  });

  it("ready says whether the server relays audio; its session is the monitor's own id, not a phone session", () => {
    expect(isMonitorMsg({ t: "mon", kind: "ready", session: "m1a2b3c4", ts: 1, audio: true })).toBe(true);
    expect(isMonitorMsg({ t: "mon", kind: "ready", session: "m1a2b3c4", ts: 1, audio: false })).toBe(true);
    expect(isMonitorMsg({ t: "mon", kind: "ready", session: "m1a2b3c4", ts: 1 })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "ready", session: "m1a2b3c4", ts: 1, audio: "yes" })).toBe(false);
    expect(isMonitorMsg({ t: "mon", kind: "ready", ts: 1, audio: true })).toBe(false);
    expect(msgs.filter((m) => m.kind === "ready").map((m) => m.audio).sort()).toEqual([false, true]);
  });

  it("audio: the owner's microphone at 16 kHz or AIRA's voice at 24 kHz, PCM16 base64; nothing else", () => {
    const base = { t: "mon", kind: "audio", session: "s1", ts: 1 };
    expect(isMonitorMsg({ ...base, who: "owner", rate: 16000, pcm: "AAAA" })).toBe(true);
    expect(isMonitorMsg({ ...base, who: "aira", rate: 24000, pcm: "AAAA" })).toBe(true);
    expect(isMonitorMsg({ ...base, who: "customer", rate: 16000, pcm: "AAAA" })).toBe(false);
    expect(isMonitorMsg({ ...base, who: "owner", rate: 44100, pcm: "AAAA" })).toBe(false);
    expect(isMonitorMsg({ ...base, who: "owner", rate: "16000", pcm: "AAAA" })).toBe(false);
    expect(isMonitorMsg({ ...base, who: "owner", rate: 16000 })).toBe(false);
    expect(isMonitorMsg({ ...base, who: "owner", rate: 16000, pcm: 5 })).toBe(false);
    expect(new Set(msgs.filter((m) => m.kind === "audio").map((m) => `${m.who}@${m.rate}`))).toEqual(new Set(["owner@16000", "aira@24000"]));
  });

  it("names the close codes and the auth deadline the server uses", () => {
    expect(MONITOR_CLOSE_UNAUTHENTICATED).toBe(4401);
    expect(MONITOR_CLOSE_UNAUTHORIZED).toBe(4403);
    expect(MONITOR_AUTH_TIMEOUT_S).toBe(5);
  });

  it("a session start may say when the session began (startedAt, epoch ms)", () => {
    const base = { t: "mon", kind: "session", session: "s", ts: 1, state: "start", lang: "en" };
    expect(isMonitorMsg({ ...base, startedAt: 1759999990000 })).toBe(true);
    expect(isMonitorMsg({ ...base })).toBe(true);
    expect(isMonitorMsg({ ...base, startedAt: "x" })).toBe(false);
    expect(isMonitorMsg({ ...base, startedAt: Number.NaN })).toBe(false);
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
