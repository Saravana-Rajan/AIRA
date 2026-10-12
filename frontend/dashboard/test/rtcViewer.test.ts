// The Live page's WebRTC viewer: it asks the followed phone session for video (`want`), answers the phone's
// offer recvonly, plays the track, and falls back to the JPEG preview when nothing connects within 6 s or the
// connection drops. Fake RTCPeerConnection and timers; no browser APIs.
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { RtcClientMsg, RtcMsg } from "@aira/contracts";
import { createRtcViewer, CONNECT_TIMEOUT_MS, ICE_SERVERS, RETRY_MS, type PeerCtor, type PeerLike, type RtcViewer, type VideoState } from "../src/lib/rtcViewer";

class FakePC implements PeerLike {
  static instances: FakePC[] = [];
  config: unknown;
  remote: unknown = null;
  local: unknown = null;
  candidates: unknown[] = [];
  closed = false;
  connectionState = "new";
  ontrack: ((ev: { track: MediaStreamTrack; streams: readonly MediaStream[] }) => void) | null = null;
  onicecandidate: ((ev: { candidate: RTCIceCandidate | null }) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  constructor(config: unknown) { this.config = config; FakePC.instances.push(this); }
  async setRemoteDescription(d: unknown) { this.remote = d; }
  async createAnswer() { return { type: "answer" as const, sdp: `ANSWER-${FakePC.instances.indexOf(this)}` }; }
  async setLocalDescription(d: unknown) { this.local = d; }
  async addIceCandidate(c: unknown) { this.candidates.push(c); }
  close() { this.closed = true; this.connectionState = "closed"; }
  go(state: string) { this.connectionState = state; this.onconnectionstatechange?.(); }
}

interface Timer { fn: () => void; ms: number; cleared: boolean }
let timers: Timer[];
let sent: RtcClientMsg[];
let states: VideoState[];
let viewer: RtcViewer;
const stream = { id: "remote" } as unknown as MediaStream;
const msg = (m: Record<string, unknown>): RtcMsg => ({ t: "rtc", from: "s1", to: "m1", ...m }) as RtcMsg;
const flush = () => new Promise((r) => setTimeout(r, 0));
const fire = (t: Timer) => { if (!t.cleared) { t.cleared = true; t.fn(); } };
const pending = () => timers.filter((t) => !t.cleared);

function make(opts: { pc?: boolean } = {}) {
  viewer = createRtcViewer({
    PeerConnection: opts.pc === false ? undefined : (FakePC as unknown as PeerCtor),
    setTimer: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; },
    clearTimer: (t) => { (t as Timer).cleared = true; },
  });
  viewer.subscribe((s) => states.push(s));
  viewer.attach((m) => { sent.push(m); return true; });
  return viewer;
}

beforeEach(() => {
  timers = [];
  sent = [];
  states = [];
  FakePC.instances = [];
});

describe("rtcViewer", () => {
  it("wants the followed session, answers its offer recvonly over STUN, plays the track and reports webrtc", async () => {
    make();
    expect(viewer.state()).toEqual({ mode: "preview", stream: null, session: null });
    viewer.want("s1");
    expect(sent).toEqual([{ t: "rtc", kind: "want", to: "s1" }]);
    expect(viewer.state().mode).toBe("connecting");
    expect(pending().map((t) => t.ms)).toEqual([CONNECT_TIMEOUT_MS]);
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    const pc = FakePC.instances[0];
    expect(pc.config).toEqual({ iceServers: ICE_SERVERS });
    expect(pc.remote).toEqual({ type: "offer", sdp: "OFFER" });
    expect(pc.local).toEqual({ type: "answer", sdp: "ANSWER-0" });
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "answer", to: "s1", sdp: "ANSWER-0" });
    pc.ontrack!({ track: { kind: "video" } as MediaStreamTrack, streams: [stream] });
    pc.onicecandidate!({ candidate: { candidate: "candidate:1", sdpMid: "0", sdpMLineIndex: 0 } as RTCIceCandidate });
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "ice", to: "s1", candidate: { candidate: "candidate:1", sdpMid: "0", sdpMLineIndex: 0 } });
    viewer.receive(msg({ kind: "ice", candidate: { candidate: "candidate:7", sdpMid: null, sdpMLineIndex: null } }));
    await flush();
    expect(pc.candidates).toEqual([{ candidate: "candidate:7", sdpMid: null, sdpMLineIndex: null }]);
    pc.go("connected");
    expect(viewer.state()).toEqual({ mode: "webrtc", stream, session: "s1" });
    expect(pending()).toEqual([]); // the fallback timer is cleared
    expect(states.at(-1)!.mode).toBe("webrtc");
  });

  it("falls back to the preview when nothing connects within 6 s, and tries again later", async () => {
    make();
    viewer.want("s1");
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    fire(timers[0]);
    expect(viewer.state().mode).toBe("preview");
    expect(FakePC.instances[0].closed).toBe(true);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "s1" });
    const retry = pending();
    expect(retry.map((t) => t.ms)).toEqual([RETRY_MS]);
    fire(retry[0]);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "want", to: "s1" });
    expect(viewer.state().mode).toBe("connecting");
  });

  it("falls back when the connection fails or the phone says bye, and ignores offers from other sessions", async () => {
    make();
    viewer.want("s1");
    viewer.receive(msg({ kind: "offer", sdp: "X", from: "s2" }));
    await flush();
    expect(FakePC.instances).toHaveLength(0);
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    const pc = FakePC.instances[0];
    pc.go("connected");
    expect(viewer.state().mode).toBe("webrtc");
    pc.go("failed");
    expect(viewer.state()).toEqual({ mode: "preview", stream: null, session: "s1" });
    expect(pc.closed).toBe(true);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "s1" });
    fire(pending()[0]); // retry
    viewer.receive(msg({ kind: "offer", sdp: "OFFER2" }));
    await flush();
    FakePC.instances[1].go("connected");
    viewer.receive(msg({ kind: "bye" }));
    expect(viewer.state().mode).toBe("preview");
    expect(FakePC.instances[1].closed).toBe(true);
    expect(sent.filter((m) => m.kind === "bye")).toHaveLength(1); // no bye back for a bye
  });

  it("switches sessions cleanly and stops when the session is gone", async () => {
    make();
    viewer.want("s1");
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    FakePC.instances[0].go("connected");
    viewer.want("s1"); // the same session: nothing happens
    expect(sent.filter((m) => m.kind === "want")).toHaveLength(1);
    viewer.want("s2");
    expect(FakePC.instances[0].closed).toBe(true);
    expect(sent.slice(-2)).toEqual([{ t: "rtc", kind: "bye", to: "s1" }, { t: "rtc", kind: "want", to: "s2" }]);
    expect(viewer.state()).toEqual({ mode: "connecting", stream: null, session: "s2" });
    viewer.want(null);
    expect(viewer.state()).toEqual({ mode: "preview", stream: null, session: null });
    expect(pending()).toEqual([]);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "s2" });
  });

  it("drops the connection quietly when the socket closes, and asks again once it is back", async () => {
    make();
    viewer.want("s1");
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    FakePC.instances[0].go("connected");
    viewer.detach();
    expect(FakePC.instances[0].closed).toBe(true);
    expect(viewer.state()).toEqual({ mode: "preview", stream: null, session: "s1" });
    expect(sent.filter((m) => m.kind === "bye")).toHaveLength(0); // nowhere to send it
    expect(pending()).toEqual([]);
    viewer.attach((m) => { sent.push(m); return true; });
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "want", to: "s1" });
  });

  it("stop() says bye, closes and forgets; nothing happens without RTCPeerConnection", async () => {
    make();
    viewer.want("s1");
    viewer.receive(msg({ kind: "offer", sdp: "OFFER" }));
    await flush();
    viewer.stop();
    expect(FakePC.instances[0].closed).toBe(true);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "s1" });
    expect(viewer.state()).toEqual({ mode: "preview", stream: null, session: null });
    expect(pending()).toEqual([]);
    sent = [];
    make({ pc: false });
    viewer.want("s1");
    expect(sent).toEqual([]);
    expect(viewer.state().mode).toBe("preview");
  });
});
