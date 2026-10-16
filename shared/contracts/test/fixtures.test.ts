import { describe, it, expect } from "vitest";
import clientJson from "../fixtures/client_msgs.json";
import serverJson from "../fixtures/server_msgs.json";
import {
  isClientMsg,
  isServerMsg,
  parseClientMsg,
  parseServerMsg,
  CLIENT_MSG_TYPES,
  SERVER_MSG_TYPES,
  CUE_NAMES,
  BOX_SOURCES,
  MAX_BOXES,
  CLIENT_EVENT_NAMES,
  CONFIRM_KINDS,
  ENTITIES,
  ERROR_CODES,
  LIGHT_LEVELS,
  TORCH_STATES,
} from "../src/index";

type Raw = Record<string, any>;
const client = clientJson as unknown as Raw[];
const server = serverJson as unknown as Raw[];
const valuesOf = (msgs: Raw[], t: string, field: string) =>
  new Set(msgs.filter((m) => m.t === t).map((m) => m[field]));

describe("contract fixtures", () => {
  it("every client fixture is a valid ClientMsg", () => {
    for (const m of client) expect(isClientMsg(m), JSON.stringify(m)).toBe(true);
  });

  it("every server fixture is a valid ServerMsg", () => {
    for (const m of server) expect(isServerMsg(m), JSON.stringify(m)).toBe(true);
  });

  it("fixtures cover every message type", () => {
    expect(new Set(client.map((m) => m.t))).toEqual(new Set(CLIENT_MSG_TYPES));
    expect(new Set(server.map((m) => m.t))).toEqual(new Set(SERVER_MSG_TYPES));
  });

  it("fixtures cover every cue (incl. interrupted), event, confirm kind, entity and error code", () => {
    expect(valuesOf(server, "cue", "cue")).toEqual(new Set(CUE_NAMES));
    expect(valuesOf(server, "cue", "cue").has("interrupted")).toBe(true);
    expect(valuesOf(client, "event", "name")).toEqual(new Set(CLIENT_EVENT_NAMES));
    expect(valuesOf(server, "confirm", "kind")).toEqual(new Set(CONFIRM_KINDS));
    expect(valuesOf(server, "state", "entity")).toEqual(new Set(ENTITIES));
    expect(valuesOf(server, "error", "code")).toEqual(new Set(ERROR_CODES));
  });

  it("low light: the phone reports a sustained level, raw luma and the real torch state; the server only commands", () => {
    expect(LIGHT_LEVELS).toEqual(["dark", "dim", "bright"]);
    expect(TORCH_STATES).toEqual(["off", "on", "unsupported"]);
    expect(valuesOf(client, "light", "level")).toEqual(new Set(LIGHT_LEVELS));
    expect(valuesOf(client, "light", "torch")).toEqual(new Set(TORCH_STATES));
    expect(valuesOf(server, "torch", "on")).toEqual(new Set([true, false]));
    expect(isClientMsg({ t: "light", level: "dark", luma: 0, torch: "off" })).toBe(true);
    expect(isClientMsg({ t: "light", level: "bright", luma: 255, torch: "unsupported" })).toBe(true);
    // rejected: an unknown level or torch state, a luma outside 0-255 or not a number, a missing field
    expect(isClientMsg({ t: "light", level: "pitch", luma: 10, torch: "off" })).toBe(false);
    expect(isClientMsg({ t: "light", level: "dark", luma: 10, torch: "maybe" })).toBe(false);
    expect(isClientMsg({ t: "light", level: "dark", luma: -1, torch: "off" })).toBe(false);
    expect(isClientMsg({ t: "light", level: "dark", luma: 256, torch: "off" })).toBe(false);
    expect(isClientMsg({ t: "light", level: "dark", luma: "10", torch: "off" })).toBe(false);
    expect(isClientMsg({ t: "light", level: "dark", torch: "off" })).toBe(false);
    // the torch command is server -> phone only, with a boolean
    expect(isServerMsg({ t: "torch", on: true })).toBe(true);
    expect(isServerMsg({ t: "torch", on: "yes" })).toBe(false);
    expect(isServerMsg({ t: "torch" })).toBe(false);
    expect(isClientMsg({ t: "torch", on: true })).toBe(false);
    expect(isServerMsg({ t: "light", level: "dark", luma: 10, torch: "off" })).toBe(false);
  });

  it("orientation: the phone reports its screen angle (0/90/180/270), nothing else", () => {
    expect(isClientMsg({ t: "orientation", angle: 0 })).toBe(true);
    expect(isClientMsg({ t: "orientation", angle: 270 })).toBe(true);
    expect(isClientMsg({ t: "orientation", angle: 45 })).toBe(false);
    expect(isClientMsg({ t: "orientation", angle: "90" })).toBe(false);
    expect(isClientMsg({ t: "orientation" })).toBe(false);
  });

  it("a finished user transcript carries the language AIRA detected (any code, not only the label languages)", () => {
    const withLang = server.filter((m) => m.t === "transcript" && typeof m.lang === "string");
    expect(withLang.length).toBeGreaterThan(0);
    expect(withLang.every((m) => m.final === true)).toBe(true);
  });

  it("a finished user transcript may be flagged unclear (a language the shop does not usually speak)", () => {
    const unclear = server.filter((m) => m.t === "transcript" && m.unclear === true);
    expect(unclear.length).toBe(1);
    expect(unclear[0].role).toBe("user");
    expect(unclear[0].final).toBe(true);
    expect(isServerMsg({ t: "transcript", role: "user", text: "x", final: true, unclear: "yes" })).toBe(false);
  });

  it("a client log line carries a tag and a data object, never anything else", () => {
    expect(isClientMsg({ t: "log", tag: "tilt", data: { beta: 131.2, reason: "speak" } })).toBe(true);
    expect(isClientMsg({ t: "log", tag: "build", data: {} })).toBe(true);
    expect(isClientMsg({ t: "log", tag: 5, data: {} })).toBe(false);
    expect(isClientMsg({ t: "log", tag: "tilt", data: [] })).toBe(false);
    expect(isClientMsg({ t: "log", tag: "tilt", data: null })).toBe(false);
    expect(isClientMsg({ t: "log", tag: "tilt" })).toBe(false);
    expect(isServerMsg({ t: "log", tag: "tilt", data: {} })).toBe(false);
  });

  it("boxes: the phone sends MediaPipe/bump boxes, the server sends ER 2 boxes with a target; both 0-1000", () => {
    expect(valuesOf(client, "boxes", "source")).toEqual(new Set(["mediapipe", "bump"]));
    expect(valuesOf(server, "boxes", "source")).toEqual(new Set(["er2"]));
    expect(BOX_SOURCES).toEqual(["mediapipe", "er2", "bump"]);
    const obj = { label: "chair", box: [420, 310, 900, 690], score: 0.82, severity: "danger" };
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [obj] })).toBe(true);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [], frameId: "f1" })).toBe(true);
    expect(isServerMsg({ t: "boxes", source: "er2", objects: [obj], target: "chair", found: true })).toBe(true);
    // rejected: unknown source, a missing/invalid box, a non-finite or string score, an unknown severity, too many
    expect(isClientMsg({ t: "boxes", source: "yolo", objects: [] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe" })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: {} })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "chair" }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "chair", box: [0, 0, 1001, 10] }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "chair", box: [0, 0, 10] }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "chair", box: [0, 0, 10.5, 10] }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: 5, box: [0, 0, 10, 10] }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "c", box: [0, 0, 10, 10], score: "0.9" }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "c", box: [0, 0, 10, 10], score: Infinity }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: [{ label: "c", box: [0, 0, 10, 10], severity: "warn" }] })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: Array(MAX_BOXES + 1).fill(obj) })).toBe(false);
    expect(isClientMsg({ t: "boxes", source: "mediapipe", objects: Array(MAX_BOXES).fill(obj) })).toBe(true);
    // found must be a boolean and target a string
    expect(isServerMsg({ t: "boxes", source: "er2", objects: [obj], found: "yes" })).toBe(false);
    expect(isServerMsg({ t: "boxes", source: "er2", objects: [obj], target: 7 })).toBe(false);
    expect(isServerMsg({ t: "boxes", source: "lidar", objects: [] })).toBe(false);
  });

  it("hello is valid without an idToken (no Firebase Auth)", () => {
    const hellos = client.filter((m) => m.t === "hello");
    expect(hellos.some((m) => m.idToken === undefined)).toBe(true);
  });

  it("hello is valid without a shopId (one shop: the server picks it; an old client may still send one)", () => {
    const hellos = client.filter((m) => m.t === "hello");
    expect(hellos.some((m) => m.shopId === undefined)).toBe(true);
    expect(hellos.some((m) => m.shopId === "sunrise_dairy")).toBe(true);
    expect(isClientMsg({ t: "hello", uid: "u", lang: "ta", verbosity: "short", appVersion: "0" })).toBe(true);
  });

  it("rejects unknown types, bad purposes and out-of-range points", () => {
    expect(isClientMsg({ t: "nope" })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "selfie" })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "live", changed: "yes" })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "live", labels: [1] })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "live", labels: Array(9).fill("a") })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [1200, 5], confidence: 1 })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [5], confidence: 1 })).toBe(false);
  });

  it("rejects malformed messages", () => {
    expect(isClientMsg(null)).toBe(false);
    expect(isClientMsg([])).toBe(false);
    expect(isClientMsg("hello")).toBe(false);
    expect(isClientMsg({ t: "hello", uid: "u", lang: "ta", verbosity: "short", appVersion: "0", shopId: 5 })).toBe(false);
    expect(isClientMsg({ t: "hello", uid: "u", shopId: "s", lang: "fr", verbosity: "short", appVersion: "0" })).toBe(false);
    expect(isClientMsg({ t: "hello", uid: "u", shopId: "s", lang: "ta", verbosity: "short", appVersion: "0", idToken: 5 })).toBe(false);
    expect(isClientMsg({ t: "confirm", id: "c", answer: "maybe" })).toBe(false);
    expect(isClientMsg({ t: "event", name: "shake", data: {} })).toBe(false);
    expect(isClientMsg({ t: "event", name: "network", data: null })).toBe(false);
    expect(isClientMsg({ t: "audio" })).toBe(false);
    expect(isServerMsg({ t: "cue", cue: "boom" })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [5, 5], box: [0, 0, 1001, 10], confidence: 1 })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [5.5, 5], confidence: 1 })).toBe(false);
    expect(isServerMsg({ t: "count", sku: "s", counted: 3, final: false, confidence: 0.5 })).toBe(false);
    expect(isServerMsg({ t: "request_frame", purpose: "count", burst: { count: 3 } })).toBe(false);
    expect(isServerMsg({ t: "request_frame", purpose: "count", continuous: "yes" })).toBe(false);
    expect(isServerMsg({ t: "request_frame", purpose: "count", requestId: 5 })).toBe(false);
    expect(isServerMsg({ t: "request_frame", purpose: "count", ttlMs: 1.5 })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "count", requestId: 7 })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "count", captureTs: "1" })).toBe(false);
    expect(isServerMsg({ t: "state", entity: "customer", id: "x", state: "S", summary: "" })).toBe(false);
    expect(isServerMsg({ t: "error", code: "oops", say: "x" })).toBe(false);
    expect(isServerMsg({ t: "ready", sessionId: "s", resume: "s", mock: "true" })).toBe(false);
    expect(isServerMsg({ t: "transcript", role: "user", text: "x", final: true, lang: 5 })).toBe(false);
    expect(isServerMsg({ t: "transcript", role: "user", text: "x", final: true, lang: "ja" })).toBe(true);
  });

  it("parses raw socket text, returning null for bad JSON or invalid messages", () => {
    expect(parseServerMsg('{"t":"cue","cue":"interrupted"}')).toEqual({ t: "cue", cue: "interrupted" });
    expect(parseServerMsg("{not json")).toBeNull();
    expect(parseServerMsg('{"t":"future_thing"}')).toBeNull();
    expect(parseClientMsg('{"t":"stop"}')).toEqual({ t: "stop" });
    expect(parseClientMsg('{"t":"text"}')).toBeNull();
    for (const m of server) expect(parseServerMsg(JSON.stringify(m))).toEqual(m);
    for (const m of client) expect(parseClientMsg(JSON.stringify(m))).toEqual(m);
  });

  it("client and server confirm/audio share a tag but are validated by direction", () => {
    expect(isClientMsg({ t: "audio", pcm16: "AAAA" })).toBe(true);
    expect(isServerMsg({ t: "audio", pcm16: "AAAA" })).toBe(false);
    expect(isServerMsg({ t: "confirm", id: "c1", answer: "yes" })).toBe(false);
    expect(isClientMsg({ t: "confirm", id: "c1", kind: "order", prompt: "?" })).toBe(false);
  });
});
