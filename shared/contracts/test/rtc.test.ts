// WebRTC signalling: one message shape, {t:"rtc", kind, from, to, sdp?, candidate?}, relayed by the server
// between the phone (/ws) and the dashboard monitors (/monitor) of the same shop. The server fills `from`
// with the sender's own id (the session id, or the monitor id it assigned), so a client may leave it out.
import { describe, it, expect } from "vitest";
import clientJson from "../fixtures/client_msgs.json";
import serverJson from "../fixtures/server_msgs.json";
import {
  RTC_KINDS,
  isClientMsg,
  isMonitorClientMsg,
  isRtcMsg,
  isServerMsg,
  parseMonitorFeedMsg,
  parseRtcMsg,
} from "../src/index";

type Raw = Record<string, any>;
const client = clientJson as unknown as Raw[];
const server = serverJson as unknown as Raw[];

const offer = { t: "rtc", kind: "offer", from: "s1", to: "m1", sdp: "v=0\r\n..." };
const ice = { t: "rtc", kind: "ice", from: "m1", to: "s1", candidate: { candidate: "candidate:1 1 udp 2 1.2.3.4 5 typ host", sdpMid: "0", sdpMLineIndex: 0 } };

describe("rtc signalling contract", () => {
  it("kinds are fixed: want, offer, answer, ice, connected, bye", () => {
    expect(RTC_KINDS).toEqual(["want", "offer", "answer", "ice", "connected", "bye"]);
  });

  it("the fixtures carry an rtc message in each direction, and they cover every kind between them", () => {
    const kinds = new Set([...client, ...server].filter((m) => m.t === "rtc").map((m) => m.kind));
    expect(kinds).toEqual(new Set(RTC_KINDS));
    expect(client.some((m) => m.t === "rtc")).toBe(true);
    expect(server.some((m) => m.t === "rtc")).toBe(true);
  });

  it("accepts the shapes of every kind", () => {
    expect(isRtcMsg({ t: "rtc", kind: "want", from: "m1", to: "s1" })).toBe(true);
    expect(isRtcMsg(offer)).toBe(true);
    expect(isRtcMsg({ ...offer, kind: "answer", from: "m1", to: "s1" })).toBe(true);
    expect(isRtcMsg(ice)).toBe(true);
    expect(isRtcMsg({ t: "rtc", kind: "ice", from: "m1", to: "s1", candidate: { candidate: "" } })).toBe(true);
    expect(isRtcMsg({ t: "rtc", kind: "ice", from: "m1", to: "s1", candidate: { candidate: "x", sdpMid: null, sdpMLineIndex: null } })).toBe(true);
    expect(isRtcMsg({ t: "rtc", kind: "connected", from: "s1", to: "m1" })).toBe(true);
    expect(isRtcMsg({ t: "rtc", kind: "bye", from: "s1", to: "m1" })).toBe(true);
  });

  it("a client (phone or monitor) may leave `from` out: the server fills it in; a server message always has it", () => {
    const fromClient = { t: "rtc", kind: "offer", to: "m1", sdp: "v=0" };
    expect(isClientMsg(fromClient)).toBe(true);
    expect(isMonitorClientMsg({ t: "rtc", kind: "want", to: "s1" })).toBe(true);
    expect(isMonitorClientMsg({ t: "rtc", kind: "answer", to: "s1", sdp: "v=0" })).toBe(true);
    expect(isServerMsg(fromClient)).toBe(false);
    expect(isRtcMsg(fromClient)).toBe(false);
    expect(isServerMsg(offer)).toBe(true);
    expect(isClientMsg(offer)).toBe(true);
  });

  it("rejects an unknown kind, a missing peer, a non-string sdp and a malformed candidate", () => {
    expect(isRtcMsg({ t: "rtc", kind: "hello", from: "s1", to: "m1" })).toBe(false);
    expect(isRtcMsg({ t: "rtc", kind: "want", from: "m1" })).toBe(false);
    expect(isRtcMsg({ t: "rtc", kind: "want", from: "m1", to: 5 })).toBe(false);
    expect(isRtcMsg({ ...offer, sdp: 5 })).toBe(false);
    expect(isRtcMsg({ ...ice, candidate: "candidate:1" })).toBe(false);
    expect(isRtcMsg({ ...ice, candidate: { sdpMid: "0" } })).toBe(false);
    expect(isRtcMsg({ ...ice, candidate: { candidate: "x", sdpMLineIndex: "0" } })).toBe(false);
    expect(isClientMsg({ t: "rtc", kind: "offer", sdp: "v=0" })).toBe(false);
    expect(isMonitorClientMsg({ t: "rtc", kind: "want" })).toBe(false);
    expect(isMonitorClientMsg({ t: "rtc", kind: "nope", to: "s1" })).toBe(false);
  });

  it("parses raw socket text", () => {
    expect(parseRtcMsg(JSON.stringify(offer))).toEqual(offer);
    expect(parseRtcMsg("{nope")).toBeNull();
    expect(parseRtcMsg('{"t":"mon","kind":"status"}')).toBeNull();
  });

  it("the monitor feed carries both monitor messages and rtc messages", () => {
    expect(parseMonitorFeedMsg(JSON.stringify(offer))).toEqual(offer);
    const status = { t: "mon", kind: "status", session: "s1", ts: 1, state: "idle" };
    expect(parseMonitorFeedMsg(JSON.stringify(status))).toEqual(status);
    expect(parseMonitorFeedMsg('{"t":"auth","key":"k"}')).toBeNull();
    expect(parseMonitorFeedMsg("{nope")).toBeNull();
  });
});
