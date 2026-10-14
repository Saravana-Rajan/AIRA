// useMonitor keeps what it hears in memory only, scoped to one session, and drops it whenever the socket is
// not open: on close, when the Live page is left, when the page is unloaded (pagehide), and when the server
// refuses the page. There is no monitor key: it connects at once. A merely hidden tab stays connected. Fake
// sockets stand in for WebSocket.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "preact/test-utils";
import { useState } from "preact/hooks";
import { cleanup, mount } from "./helpers/render";
import { useMonitor, type Monitor, type MonitorDeps } from "../src/lib/useMonitor";
import type { SocketLike } from "../src/lib/monitor";
import type { PeerCtor, PeerLike } from "../src/lib/rtcViewer";

type Fake = SocketLike & { closed: boolean; sent: string[] };
const ENV = { VITE_AIRA_LIVE_WS: "wss://live.example/ws" };

function fakeSocket(): Fake {
  return {
    onopen: null, onmessage: null, onclose: null, onerror: null, closed: false, sent: [],
    send(d: string) { this.sent.push(d); },
    close() { this.closed = true; },
  };
}

class FakePC implements PeerLike {
  static instances: FakePC[] = [];
  remote: unknown = null;
  closed = false;
  connectionState = "new";
  ontrack: PeerLike["ontrack"] = null;
  onicecandidate: PeerLike["onicecandidate"] = null;
  onconnectionstatechange: PeerLike["onconnectionstatechange"] = null;
  constructor() { FakePC.instances.push(this); }
  async setRemoteDescription(d: unknown) { this.remote = d; }
  async createAnswer() { return { type: "answer" as const, sdp: "ANSWER" }; }
  async setLocalDescription() {}
  async addIceCandidate() {}
  close() { this.closed = true; }
}

function harness(enabled = true, peer?: PeerCtor) {
  const sockets: Fake[] = [];
  const deps: MonitorDeps = { connect: () => { const s = fakeSocket(); sockets.push(s); return s; }, setTimer: () => 0, clearTimer: () => {}, peer };
  let latest: Monitor | null = null;
  let setEnabled: (v: boolean) => void = () => {};
  function Probe() {
    const [on, set] = useState(enabled);
    setEnabled = set;
    latest = useMonitor(on, "sunrise_dairy", ENV, deps);
    return null;
  }
  mount(<Probe />);
  const last = () => sockets[sockets.length - 1];
  const open = () => act(() => last().onopen!({}));
  const send = (m: Record<string, unknown>) => act(() => last().onmessage!({ data: JSON.stringify({ t: "mon", session: "s1", ts: 1, ...m }) }));
  return { sockets, last, open, send, mon: () => latest!, setEnabled: (v: boolean) => act(() => setEnabled(v)) };
}

beforeEach(() => {
  sessionStorage.clear();
  location.hash = "#/live";
});
afterEach(cleanup);

describe("useMonitor", () => {
  it("connects at once without any key, reduces messages into memory, and drops everything when the socket closes", () => {
    const h = harness();
    expect(h.sockets).toHaveLength(1);
    h.open();
    expect(h.last().sent).toEqual([]); // no auth message: there is no monitor key
    expect(h.mon().conn).toBe("live");
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    h.send({ kind: "transcript", role: "owner", text: "two litres", lang: "en", final: true });
    h.send({ kind: "frame", jpeg_b64: "AAAA", w: 1, h: 1, purpose: "ask" });
    expect(h.mon().state.transcript.map((l) => l.text)).toEqual(["two litres"]);
    expect(h.mon().state.frame?.src).toContain("AAAA");
    act(() => h.last().onclose!({ code: 1006 }));
    expect(h.mon().conn).toBe("offline");
    expect(h.mon().state.transcript).toEqual([]);
    expect(h.mon().state.frame).toBeNull();
    expect(h.mon().state.session).toBeNull();
    // Nothing reached storage or the address bar.
    expect(Object.keys(localStorage)).toEqual([]);
    expect(sessionStorage.length).toBe(0);
    expect(location.href).not.toContain("AAAA");
  });

  it("only follows one session; `follow` switches and starts clean", () => {
    const h = harness();
    h.open();
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    h.send({ kind: "session", state: "start", lang: "ta", uid: "u2", session: "s2" });
    h.send({ kind: "transcript", role: "owner", text: "from s2", lang: "ta", final: true, session: "s2" });
    expect(h.mon().state.session).toBe("s1");
    expect(h.mon().state.transcript).toEqual([]);
    act(() => h.mon().follow("s2"));
    expect(h.mon().state.session).toBe("s2");
    h.send({ kind: "transcript", role: "owner", text: "from s2 again", lang: "ta", final: true, session: "s2" });
    h.send({ kind: "transcript", role: "owner", text: "from s1", lang: "en", final: true });
    expect(h.mon().state.transcript.map((l) => l.text)).toEqual(["from s2 again"]);
  });

  it("closes the socket and clears the state when the Live page is left; reconnects when it is opened again", () => {
    const h = harness();
    h.open();
    h.send({ kind: "transcript", role: "owner", text: "hello", lang: "en", final: true });
    expect(h.mon().state.transcript).toHaveLength(1);
    h.setEnabled(false);
    expect(h.last().closed).toBe(true);
    expect(h.mon().state.transcript).toEqual([]);
    expect(h.mon().conn).toBe("waiting");
    h.setEnabled(true);
    expect(h.sockets).toHaveLength(2);
    h.open();
    expect(h.mon().conn).toBe("live");
  });

  it("stays connected while the tab is merely hidden", () => {
    const h = harness();
    h.open();
    h.send({ kind: "frame", jpeg_b64: "BBBB", w: 1, h: 1, purpose: "ask" });
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    try {
      act(() => { document.dispatchEvent(new Event("visibilitychange")); });
      expect(h.sockets).toHaveLength(1);
      expect(h.last().closed).toBe(false);
      expect(h.mon().conn).toBe("live");
      expect(h.mon().state.frame).not.toBeNull();
    } finally {
      delete (document as unknown as Record<string, unknown>).visibilityState;
    }
  });

  it("closes and clears on pagehide, and reconnects on pageshow", () => {
    const h = harness();
    h.open();
    h.send({ kind: "frame", jpeg_b64: "CCCC", w: 1, h: 1, purpose: "ask" });
    act(() => { window.dispatchEvent(new Event("pagehide")); });
    expect(h.last().closed).toBe(true);
    expect(h.mon().state.frame).toBeNull();
    expect(h.mon().conn).toBe("waiting");
    act(() => { window.dispatchEvent(new Event("pageshow")); });
    expect(h.sockets).toHaveLength(2);
    expect(h.last().closed).toBe(false);
    h.open();
    expect(h.mon().conn).toBe("live");
  });

  it("reports whether a live URL is configured", () => {
    expect(harness().mon().configured).toBe(true);
    cleanup();
    let m: Monitor | null = null;
    // Hoisted: `deps` is in the hook's effect dependencies, so an inline object (new on every render) would
    // re-run the effect, set state, and render again without end.
    const deps: MonitorDeps = { connect: () => fakeSocket(), setTimer: () => 0, clearTimer: () => {} };
    function Probe() {
      m = useMonitor(true, "sunrise_dairy", {}, deps);
      return null;
    }
    mount(<Probe />);
    expect(m!.configured).toBe(false);
    expect(m!.conn).toBe("waiting");
  });

  it("asks the followed session for WebRTC video over the same socket, plays its offer, and says bye when the page is left", async () => {
    FakePC.instances = [];
    const h = harness(true, FakePC as unknown as PeerCtor);
    h.open();
    expect(h.mon().video).toEqual({ mode: "preview", stream: null, session: null });
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    expect(h.last().sent.at(-1)).toBe(JSON.stringify({ t: "rtc", kind: "want", to: "s1" }));
    expect(h.mon().video.mode).toBe("connecting");
    act(() => h.last().onmessage!({ data: JSON.stringify({ t: "rtc", kind: "offer", from: "s1", to: "m1", sdp: "OFFER" }) }));
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(FakePC.instances).toHaveLength(1);
    expect(h.last().sent.at(-1)).toBe(JSON.stringify({ t: "rtc", kind: "answer", to: "s1", sdp: "ANSWER" }));
    const stream = { id: "remote" } as unknown as MediaStream;
    act(() => {
      FakePC.instances[0].ontrack!({ track: {} as MediaStreamTrack, streams: [stream] });
      FakePC.instances[0].connectionState = "connected";
      FakePC.instances[0].onconnectionstatechange!();
    });
    expect(h.mon().video).toEqual({ mode: "webrtc", stream, session: "s1" });
    h.setEnabled(false);
    expect(h.last().sent.at(-1)).toBe(JSON.stringify({ t: "rtc", kind: "bye", to: "s1" }));
    expect(h.last().closed).toBe(true);
    expect(FakePC.instances[0].closed).toBe(true);
    expect(h.mon().video).toEqual({ mode: "preview", stream: null, session: null });
  });

  it("without RTCPeerConnection the socket carries no signalling and the preview is all there is", () => {
    const h = harness();
    h.open();
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    expect(h.last().sent).toEqual([]);
    expect(h.mon().video.mode).toBe("preview");
  });

  it("reports a refusal (4403: the server's Origin allowlist or shop check) and does not retry", () => {
    const h = harness();
    h.open();
    h.send({ kind: "frame", jpeg_b64: "DDDD", w: 1, h: 1, purpose: "ask" });
    act(() => h.last().onclose!({ code: 4403 }));
    expect(h.mon().conn).toBe("rejected");
    expect(h.mon().state.frame).toBeNull();
    expect(h.sockets).toHaveLength(1);
    expect(sessionStorage.length).toBe(0);
  });
});
