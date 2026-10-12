// frontend/app/test/camera.rtcSender.test.ts — the phone's WebRTC sender: one peer connection per dashboard
// monitor that asks (`want`), the EXISTING camera track (no second prompt), video only, sendonly, 600 kbps /
// 24 fps / half resolution, offer -> answer -> ice, `connected` and `bye` to the server, clean close.
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import {
  createRtcSender, ICE_SERVERS, MAX_BITRATE, MAX_FPS, SCALE_DOWN, type PeerCtor, type PeerLike, type RtcSender,
} from "../src/camera/rtcSender";

class FakeSender {
  params: Record<string, unknown> = {};
  constructor(init: Record<string, unknown>) { this.params = { encodings: init.sendEncodings ?? [{}] }; }
  getParameters() { return { ...this.params }; }
  setParameters = vi.fn(async (p: Record<string, unknown>) => { this.params = p; });
}

class FakePC implements PeerLike {
  static instances: FakePC[] = [];
  config: unknown;
  transceivers: Array<{ track: MediaStreamTrack; init: Record<string, unknown>; sender: FakeSender }> = [];
  local: unknown = null;
  remote: unknown = null;
  candidates: unknown[] = [];
  closed = false;
  connectionState = "new";
  localDescription: RTCSessionDescriptionInit | null = null;
  onicecandidate: ((ev: { candidate: RTCIceCandidate | null }) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  constructor(config: unknown) { this.config = config; FakePC.instances.push(this); }
  addTransceiver(track: MediaStreamTrack, init: Record<string, unknown>) {
    const sender = new FakeSender(init);
    this.transceivers.push({ track, init, sender });
    return { sender } as unknown as RTCRtpTransceiver;
  }
  async createOffer() { return { type: "offer" as const, sdp: `OFFER-${FakePC.instances.indexOf(this)}` }; }
  async setLocalDescription(d: RTCSessionDescriptionInit) { this.local = d; this.localDescription = d; }
  async setRemoteDescription(d: unknown) { this.remote = d; }
  async addIceCandidate(c: unknown) { this.candidates.push(c); }
  close() { this.closed = true; this.connectionState = "closed"; }
  go(state: string) { this.connectionState = state; this.onconnectionstatechange?.(); }
  ice(c: { candidate: string; sdpMid: string | null; sdpMLineIndex: number | null } | null) {
    this.onicecandidate?.({ candidate: c as unknown as RTCIceCandidate | null });
  }
}

const videoTrack = { kind: "video", id: "v1" } as unknown as MediaStreamTrack;
const audioTrack = { kind: "audio", id: "a1" } as unknown as MediaStreamTrack;
const stream = {
  getVideoTracks: () => [videoTrack],
  getAudioTracks: () => [audioTrack],
  getTracks: () => [videoTrack, audioTrack],
} as unknown as MediaStream;

const rtc = (m: Record<string, unknown>): ServerMsg => ({ t: "rtc", from: "m1", to: "s1", ...m }) as ServerMsg;
const flush = () => new Promise((r) => setTimeout(r, 0));

let sent: ClientMsg[];
let hidden: Array<(h: boolean) => void>;
let getStream: ReturnType<typeof vi.fn>;
let sender: RtcSender;

function make(opts: { stream?: MediaStream | null } = {}) {
  getStream = vi.fn(async () => (opts.stream === undefined ? stream : opts.stream));
  sender = createRtcSender({
    send: (m) => { sent.push(m); return true; },
    getStream,
    PeerConnection: FakePC as unknown as PeerCtor,
    visibility: { subscribe: (fn) => { hidden.push(fn); return () => {}; } },
  });
  return sender;
}

beforeEach(() => {
  sent = [];
  hidden = [];
  FakePC.instances = [];
});

describe("rtcSender", () => {
  it("answers a monitor's want with a sendonly video-only offer on the existing camera stream, STUN only", async () => {
    make();
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(getStream).toHaveBeenCalledTimes(1);
    expect(FakePC.instances).toHaveLength(1);
    const pc = FakePC.instances[0];
    expect(pc.config).toEqual({ iceServers: ICE_SERVERS });
    expect(ICE_SERVERS).toEqual([{ urls: "stun:stun.l.google.com:19302" }]);
    expect(pc.transceivers).toHaveLength(1);
    const [tx] = pc.transceivers;
    expect(tx.track).toBe(videoTrack); // never the audio track
    expect(tx.init.direction).toBe("sendonly");
    expect(tx.init.streams).toEqual([stream]);
    expect(tx.init.sendEncodings).toEqual([{ maxBitrate: MAX_BITRATE, maxFramerate: MAX_FPS, scaleResolutionDownBy: SCALE_DOWN }]);
    expect(MAX_BITRATE).toBe(600_000);
    expect(MAX_FPS).toBe(24);
    expect(tx.sender.setParameters).toHaveBeenCalled();
    expect(tx.sender.params.degradationPreference).toBe("maintain-framerate");
    expect(pc.local).toEqual({ type: "offer", sdp: "OFFER-0" });
    expect(sent).toEqual([{ t: "rtc", kind: "offer", to: "m1", sdp: "OFFER-0" }]);
    expect(sender.peers()).toBe(1);
  });

  it("takes the answer and remote ice (buffered until the answer), trickles local ice, and reports connected", async () => {
    make();
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    const pc = FakePC.instances[0];
    const cand = { candidate: "candidate:1 1 udp 1 1.2.3.4 5 typ host", sdpMid: "0", sdpMLineIndex: 0 };
    sender.onServerMsg(rtc({ kind: "ice", candidate: cand }));
    await flush();
    expect(pc.candidates).toEqual([]); // no remote description yet
    sender.onServerMsg(rtc({ kind: "answer", sdp: "ANSWER" }));
    await flush();
    expect(pc.remote).toEqual({ type: "answer", sdp: "ANSWER" });
    expect(pc.candidates).toEqual([cand]);
    sender.onServerMsg(rtc({ kind: "ice", candidate: { candidate: "candidate:2", sdpMid: null, sdpMLineIndex: null } }));
    await flush();
    expect(pc.candidates).toHaveLength(2);
    pc.ice({ candidate: "candidate:9 1 udp 1 5.6.7.8 9 typ host", sdpMid: "0", sdpMLineIndex: 0 });
    pc.ice(null); // end of candidates: nothing to send
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "ice", to: "m1", candidate: { candidate: "candidate:9 1 udp 1 5.6.7.8 9 typ host", sdpMid: "0", sdpMLineIndex: 0 } });
    pc.go("connecting");
    pc.go("connected");
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "connected", to: "m1" });
    expect(sent.filter((m) => m.t === "rtc" && m.kind === "connected")).toHaveLength(1);
  });

  it("says bye and closes when the connection fails or disconnects, and closes quietly on the monitor's bye", async () => {
    make();
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    const a = FakePC.instances[0];
    a.go("connected");
    a.go("failed");
    expect(a.closed).toBe(true);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "m1" });
    expect(sender.peers()).toBe(0);
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    const b = FakePC.instances[1];
    const before = sent.length;
    sender.onServerMsg(rtc({ kind: "bye" }));
    expect(b.closed).toBe(true);
    expect(sent).toHaveLength(before); // no bye back
    expect(sender.peers()).toBe(0);
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    FakePC.instances[2].go("disconnected");
    expect(FakePC.instances[2].closed).toBe(true);
    expect(sent.at(-1)).toEqual({ t: "rtc", kind: "bye", to: "m1" });
  });

  it("keeps one connection per monitor: a repeated want replaces it, another monitor gets its own", async () => {
    make();
    sender.onServerMsg(rtc({ kind: "want" }));
    sender.onServerMsg(rtc({ kind: "want", from: "m2" }));
    await flush();
    expect(FakePC.instances).toHaveLength(2);
    expect(sender.peers()).toBe(2);
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(FakePC.instances[0].closed).toBe(true);
    expect(FakePC.instances[1].closed).toBe(false);
    expect(sender.peers()).toBe(2);
    expect(sent.map((m) => (m as { to?: string }).to)).toEqual(["m1", "m2", "m1"]);
    expect(getStream).toHaveBeenCalledTimes(3); // the same open stream each time; the camera is never re-prompted
  });

  it("refuses with bye when there is no camera track, and ignores signalling for an unknown peer", async () => {
    make({ stream: null });
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(FakePC.instances).toHaveLength(0);
    expect(sent).toEqual([{ t: "rtc", kind: "bye", to: "m1" }]);
    sender.onServerMsg(rtc({ kind: "answer", sdp: "x" }));
    sender.onServerMsg(rtc({ kind: "ice", candidate: { candidate: "c" } }));
    await flush();
    expect(sent).toHaveLength(1);
  });

  it("closes everything with a bye when the page hides or the wiring is disposed, and ignores messages after", async () => {
    make();
    sender.onServerMsg(rtc({ kind: "want" }));
    sender.onServerMsg(rtc({ kind: "want", from: "m2" }));
    await flush();
    hidden[0](true);
    expect(FakePC.instances.every((p) => p.closed)).toBe(true);
    expect(sent.slice(-2)).toEqual([{ t: "rtc", kind: "bye", to: "m1" }, { t: "rtc", kind: "bye", to: "m2" }]);
    expect(sender.peers()).toBe(0);
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(FakePC.instances).toHaveLength(3); // visible again later: a new want works
    sender.dispose();
    expect(FakePC.instances[2].closed).toBe(true);
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(FakePC.instances).toHaveLength(3);
  });

  it("does nothing without RTCPeerConnection", async () => {
    sender = createRtcSender({ send: (m) => { sent.push(m); return true; }, getStream: async () => stream, PeerConnection: undefined });
    sender.onServerMsg(rtc({ kind: "want" }));
    await flush();
    expect(sent).toEqual([{ t: "rtc", kind: "bye", to: "m1" }]);
  });
});
