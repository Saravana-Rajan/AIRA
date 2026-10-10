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
  CLIENT_EVENT_NAMES,
  CONFIRM_KINDS,
  ENTITIES,
  ERROR_CODES,
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

  it("hello is valid without an idToken (no Firebase Auth)", () => {
    const hellos = client.filter((m) => m.t === "hello");
    expect(hellos.some((m) => m.idToken === undefined)).toBe(true);
  });

  it("rejects unknown types, bad purposes and out-of-range points", () => {
    expect(isClientMsg({ t: "nope" })).toBe(false);
    expect(isClientMsg({ t: "frame", id: "f", jpeg: "x", w: 1, h: 1, ts: 1, purpose: "selfie" })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [1200, 5], confidence: 1 })).toBe(false);
    expect(isServerMsg({ t: "target", frameId: "f", label: "x", point: [5], confidence: 1 })).toBe(false);
  });

  it("rejects malformed messages", () => {
    expect(isClientMsg(null)).toBe(false);
    expect(isClientMsg([])).toBe(false);
    expect(isClientMsg("hello")).toBe(false);
    expect(isClientMsg({ t: "hello", uid: "u", lang: "ta", verbosity: "short", appVersion: "0" })).toBe(false);
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
    expect(isServerMsg({ t: "state", entity: "customer", id: "x", state: "S", summary: "" })).toBe(false);
    expect(isServerMsg({ t: "error", code: "oops", say: "x" })).toBe(false);
    expect(isServerMsg({ t: "ready", sessionId: "s", resume: "s", mock: "true" })).toBe(false);
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
