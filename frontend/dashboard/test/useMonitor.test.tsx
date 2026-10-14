// useMonitor, by default (no VITE_MONITOR_AUTH), connects as soon as the Live page is open and sends nothing
// first: the server streams everyone using AIRA. In the VITE_MONITOR_AUTH=1 build it connects only while a
// viewer is signed in, sends the ID token as the first message of every socket and refreshes it before expiry.
// In both it keeps what it hears in memory only, scoped to one followed session, and drops it whenever the
// socket is not open: on close, when the Live page is left, when the page is unloaded (pagehide), on sign-out
// and when the server refuses the account. A merely hidden tab stays connected. Fake sockets and a fake
// sign-in stand in for WebSocket and Firebase.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import { useState } from "preact/hooks";
import { cleanup, flush, mount } from "./helpers/render";
import { jwt } from "./helpers/jwt";
import { useMonitor, type Monitor, type MonitorDeps } from "../src/lib/useMonitor";
import type { SocketLike } from "../src/lib/monitor";
import type { PeerCtor, PeerLike } from "../src/lib/rtcViewer";
import type { ViewerAuth, ViewerUser } from "../src/lib/viewerAuth";

type Fake = SocketLike & { closed: boolean; sent: string[] };
/** The sign-in build: every test below but the last describe runs with sign-in required. */
const ENV = { VITE_AIRA_LIVE_WS: "wss://live.example/ws", VITE_MONITOR_AUTH: "1" };
/** The default build: no sign-in. */
const OPEN_ENV = { VITE_AIRA_LIVE_WS: "wss://live.example/ws" };
const T0 = 1_760_000_000_000;
const SAM: ViewerUser = { uid: "u-sam", email: "sam@example.com", name: "Sam" };
const token = (exp = T0 + 3_600_000, tag = "t") => jwt({ sub: "u-sam", exp: Math.floor(exp / 1000), tag });
const authLine = (idToken: string) => JSON.stringify({ t: "auth", idToken });

function fakeSocket(): Fake {
  return {
    onopen: null, onmessage: null, onclose: null, onerror: null, closed: false, sent: [],
    send(d: string) { this.sent.push(d); },
    close() { this.closed = true; },
  };
}

/** A sign-in the test drives: who is signed in, and which token the next idToken() hands out. */
function fakeAuth(user: ViewerUser | null = SAM) {
  const subs = new Set<(u: ViewerUser | null) => void>();
  const a = {
    user, minted: 0,
    signIn: vi.fn(async () => { a.set(SAM); }),
    signOut: vi.fn(async () => { a.set(null); }),
    idToken: vi.fn(async (force?: boolean) => {
      if (!a.user) throw new Error("signed out");
      return token(force ? T0 + 7_200_000 : T0 + 3_600_000, force ? `fresh${a.minted++}` : "t");
    }),
    subscribe(cb: (u: ViewerUser | null) => void) {
      subs.add(cb);
      cb(a.user);
      return () => { subs.delete(cb); };
    },
    set(u: ViewerUser | null) {
      a.user = u;
      act(() => { for (const cb of subs) cb(u); });
    },
  };
  return a as typeof a & ViewerAuth;
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

function harness(enabled = true, peer?: PeerCtor, auth = fakeAuth(), env: Record<string, unknown> = ENV) {
  const sockets: Fake[] = [];
  const timers: Array<{ fn: () => void; ms: number }> = [];
  const deps: MonitorDeps = {
    connect: () => { const s = fakeSocket(); sockets.push(s); return s; },
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {}, now: () => T0, peer, auth,
  };
  let latest: Monitor | null = null;
  let setEnabled: (v: boolean) => void = () => {};
  function Probe() {
    const [on, set] = useState(enabled);
    setEnabled = set;
    latest = useMonitor(on, "sunrise_dairy", env, deps);
    return null;
  }
  mount(<Probe />);
  const last = () => sockets[sockets.length - 1];
  /** Opens the newest socket and lets the token go out. */
  const open = async () => { act(() => last().onopen!({})); await flush(); };
  const send = (m: Record<string, unknown>) => act(() => last().onmessage!({ data: JSON.stringify({ t: "mon", session: "s1", ts: 1, ...m }) }));
  return { sockets, timers, last, open, send, auth, mon: () => latest!, setEnabled: (v: boolean) => act(() => setEnabled(v)) };
}

beforeEach(() => {
  sessionStorage.clear();
  location.hash = "#/live";
});
afterEach(cleanup);

describe("useMonitor: sign-in", () => {
  it("opens no socket while signed out, and reports the sign-in state for the page", () => {
    const h = harness(true, undefined, fakeAuth(null));
    expect(h.sockets).toHaveLength(0);
    expect(h.mon().conn).toBe("waiting");
    expect(h.mon().auth).toMatchObject({ status: "signed_out", user: null, error: null });
  });

  it("connects once signed in and sends the ID token as the very first message; nothing goes to storage or the URL", async () => {
    const h = harness(true, undefined, fakeAuth(null));
    act(() => h.mon().auth.signIn());
    await flush();
    expect(h.auth.signIn).toHaveBeenCalledTimes(1);
    expect(h.mon().auth).toMatchObject({ status: "signed_in", user: SAM });
    expect(h.sockets).toHaveLength(1);
    expect(h.mon().conn).toBe("connecting");
    await h.open();
    expect(h.last().sent).toEqual([authLine(token())]);
    expect(h.mon().conn).toBe("live");
    expect(Object.keys(localStorage)).toEqual([]);
    expect(sessionStorage.length).toBe(0);
    expect(location.href).not.toContain(token());
  });

  it("reports a failed sign-in in words, without the error's internals", async () => {
    const a = fakeAuth(null);
    a.signIn.mockImplementationOnce(async () => { throw Object.assign(new Error("Firebase: Error (auth/configuration-not-found)."), { code: "auth/configuration-not-found" }); });
    const h = harness(true, undefined, a);
    act(() => h.mon().auth.signIn());
    await flush();
    expect(h.mon().auth.error).toBe("Google sign-in isn't enabled for this project yet. Ask the shop owner to enable it.");
    expect(h.mon().auth.error).not.toContain("Firebase");
    expect(h.sockets).toHaveLength(0);
  });

  it("says the sign-in is not configured when the build has no Firebase config and no fake", () => {
    const deps: MonitorDeps = { connect: () => fakeSocket(), setTimer: () => 0, clearTimer: () => {} };
    let m: Monitor | null = null;
    function Probe() {
      m = useMonitor(true, "sunrise_dairy", ENV, deps);
      return null;
    }
    mount(<Probe />);
    expect(m!.auth.status).toBe("unconfigured");
    expect(m!.conn).toBe("waiting");
  });

  it("signing out closes the socket and clears everything; signing in again opens a new one that authenticates again", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "AAAA", w: 1, h: 1, purpose: "ask" });
    expect(h.mon().state.frame).not.toBeNull();
    act(() => h.mon().auth.signOut());
    await flush();
    expect(h.last().closed).toBe(true);
    expect(h.mon().conn).toBe("waiting");
    expect(h.mon().state.frame).toBeNull();
    expect(h.mon().auth.status).toBe("signed_out");
    act(() => h.mon().auth.signIn());
    await flush();
    expect(h.sockets).toHaveLength(2);
    await h.open();
    expect(h.last().sent).toEqual([authLine(token())]);
    expect(h.mon().conn).toBe("live");
  });

  it("refreshes the token five minutes before it expires, on the same socket, with a freshly minted one", async () => {
    const h = harness();
    await h.open();
    const refresh = h.timers.find((t) => t.ms === 55 * 60_000)!;
    expect(refresh).toBeDefined();
    await act(async () => { refresh.fn(); await flush(); });
    expect(h.auth.idToken).toHaveBeenLastCalledWith(true);
    expect(h.last().sent).toHaveLength(2);
    expect(h.last().sent[1]).toBe(authLine(token(T0 + 7_200_000, "fresh0")));
    expect(h.sockets).toHaveLength(1);
    expect(h.mon().conn).toBe("live");
  });

  it("on 4401 it silently reconnects once with a freshly minted token; a second 4401 in a row is shown as sign-in expired", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "AAAA", w: 1, h: 1, purpose: "ask" });
    act(() => h.last().onclose!({ code: 4401 }));
    expect(h.mon().conn).toBe("connecting");
    expect(h.mon().state.frame).toBeNull();
    expect(h.sockets).toHaveLength(2);
    await h.open();
    expect(h.auth.idToken).toHaveBeenLastCalledWith(true);
    expect(h.last().sent).toEqual([authLine(token(T0 + 7_200_000, "fresh0"))]);
    expect(h.mon().conn).toBe("live");
    act(() => h.last().onclose!({ code: 4401 }));
    expect(h.mon().conn).toBe("unauthenticated");
    expect(h.sockets).toHaveLength(2); // no third try on its own
    expect(h.mon().auth.status).toBe("signed_in"); // the page offers "Sign in again"
  });

  it("a live socket resets the retry: a 4401 long after a successful reconnect gets its own silent retry", async () => {
    const h = harness();
    await h.open();
    act(() => h.last().onclose!({ code: 4401 }));
    await h.open();
    expect(h.mon().conn).toBe("live");
    act(() => h.last().onclose!({ code: 1006 })); // an ordinary drop
    expect(h.mon().conn).toBe("offline");
    h.timers.filter((t) => t.ms === 1000).at(-1)!.fn();
    await h.open();
    expect(h.mon().conn).toBe("live");
    act(() => h.last().onclose!({ code: 4401 }));
    expect(h.mon().conn).toBe("connecting"); // retried again, since a socket was live in between
  });

  it("4403 after the token went out means this account is not allowed: no retry, state cleared, still signed in", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "DDDD", w: 1, h: 1, purpose: "ask" });
    act(() => h.last().onclose!({ code: 4403 }));
    expect(h.mon().conn).toBe("unauthorized");
    expect(h.mon().state.frame).toBeNull();
    expect(h.sockets).toHaveLength(1);
    expect(h.mon().auth.status).toBe("signed_in");
    expect(h.mon().auth.user).toEqual(SAM);
  });

  it("4403 before the socket opened is the server's refusal of the page (Origin or shop), not of the account", () => {
    const h = harness();
    act(() => h.last().onclose!({ code: 4403 }));
    expect(h.mon().conn).toBe("rejected");
    expect(h.sockets).toHaveLength(1);
  });
});

describe("useMonitor: the feed", () => {
  it("reduces messages into memory and drops everything when the socket closes; the reconnect authenticates again", async () => {
    const h = harness();
    expect(h.sockets).toHaveLength(1);
    await h.open();
    expect(h.last().sent).toEqual([authLine(token())]);
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
    h.timers.filter((t) => t.ms === 1000).at(-1)!.fn();
    expect(h.sockets).toHaveLength(2);
    await h.open();
    expect(h.last().sent).toEqual([authLine(token())]);
    expect(h.mon().conn).toBe("live");
    // Nothing reached storage or the address bar.
    expect(Object.keys(localStorage)).toEqual([]);
    expect(sessionStorage.length).toBe(0);
    expect(location.href).not.toContain("AAAA");
  });

  it("only follows one session; `follow` switches and starts clean", async () => {
    const h = harness();
    await h.open();
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

  it("closes the socket and clears the state when the Live page is left; reconnects (and signs in again) when it is opened again", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "transcript", role: "owner", text: "hello", lang: "en", final: true });
    expect(h.mon().state.transcript).toHaveLength(1);
    h.setEnabled(false);
    expect(h.last().closed).toBe(true);
    expect(h.mon().state.transcript).toEqual([]);
    expect(h.mon().conn).toBe("waiting");
    h.setEnabled(true);
    expect(h.sockets).toHaveLength(2);
    await h.open();
    expect(h.last().sent).toEqual([authLine(token())]);
    expect(h.mon().conn).toBe("live");
  });

  it("stays connected while the tab is merely hidden", async () => {
    const h = harness();
    await h.open();
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

  it("closes and clears on pagehide, and reconnects on pageshow", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "CCCC", w: 1, h: 1, purpose: "ask" });
    act(() => { window.dispatchEvent(new Event("pagehide")); });
    expect(h.last().closed).toBe(true);
    expect(h.mon().state.frame).toBeNull();
    expect(h.mon().conn).toBe("waiting");
    act(() => { window.dispatchEvent(new Event("pageshow")); });
    expect(h.sockets).toHaveLength(2);
    expect(h.last().closed).toBe(false);
    await h.open();
    expect(h.mon().conn).toBe("live");
  });

  it("reports whether a live URL is configured", () => {
    expect(harness().mon().configured).toBe(true);
    cleanup();
    let m: Monitor | null = null;
    // Hoisted: `deps` is in the hook's effect dependencies, so an inline object (new on every render) would
    // re-run the effect, set state, and render again without end.
    const deps: MonitorDeps = { connect: () => fakeSocket(), setTimer: () => 0, clearTimer: () => {}, auth: fakeAuth() };
    function Probe() {
      m = useMonitor(true, "sunrise_dairy", {}, deps);
      return null;
    }
    mount(<Probe />);
    expect(m!.configured).toBe(false);
    expect(m!.conn).toBe("waiting");
  });

  it("asks the followed session for WebRTC video over the same socket, only after the token, plays its offer, and says bye when the page is left", async () => {
    FakePC.instances = [];
    const h = harness(true, FakePC as unknown as PeerCtor);
    await h.open();
    expect(h.mon().video).toEqual({ mode: "preview", stream: null, session: null });
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    expect(h.last().sent[0]).toBe(authLine(token()));
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

  it("without RTCPeerConnection the socket carries nothing but the token and the preview is all there is", async () => {
    const h = harness();
    await h.open();
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    expect(h.last().sent).toEqual([authLine(token())]);
    expect(h.mon().video.mode).toBe("preview");
  });
});

describe("useMonitor: no sign-in (the default)", () => {
  it("connects the moment the page is open, sends nothing, and is live on open; sign-in is off even with a fake injected", async () => {
    const a = fakeAuth(null); // a fake sign-in, signed out: it does not count, the flag decides
    const h = harness(true, undefined, a, OPEN_ENV);
    expect(h.sockets).toHaveLength(1);
    expect(h.mon().conn).toBe("connecting");
    expect(h.mon().auth).toMatchObject({ status: "off", user: null, error: null });
    await h.open();
    expect(h.mon().conn).toBe("live");
    expect(h.last().sent).toEqual([]);
    expect(a.idToken).not.toHaveBeenCalled();
    expect(h.timers).toEqual([]); // no token refresh booked
    act(() => h.mon().auth.signIn()); // no-ops
    act(() => h.mon().auth.signOut());
    await flush();
    expect(a.signIn).not.toHaveBeenCalled();
    expect(a.signOut).not.toHaveBeenCalled();
    expect(h.mon().auth.status).toBe("off");
    expect(h.mon().conn).toBe("live");
    expect(h.last().sent).toEqual([]);
  });

  it("lists everyone using AIRA, follows the first and switches on follow", async () => {
    const h = harness(true, undefined, fakeAuth(null), OPEN_ENV);
    await h.open();
    h.send({ kind: "session", state: "start", lang: "ta", uid: "u1", startedAt: T0 - 60_000 });
    h.send({ kind: "session", state: "start", lang: "hi", uid: "u2", session: "s2", startedAt: T0 - 5_000, ts: 2 });
    h.send({ kind: "status", state: "guiding", session: "s2", ts: 3 });
    h.send({ kind: "transcript", role: "owner", text: "from s2", lang: "hi", final: true, session: "s2", ts: 4 });
    expect(h.mon().state.session).toBe("s1");
    expect(h.mon().state.sessions.s1).toMatchObject({ uid: "u1", lang: "ta", startedAt: T0 - 60_000 });
    expect(h.mon().state.sessions.s2).toMatchObject({ uid: "u2", lang: "hi", startedAt: T0 - 5_000, status: "guiding" });
    expect(h.mon().state.transcript).toEqual([]);
    act(() => h.mon().follow("s2"));
    expect(h.mon().state).toMatchObject({ session: "s2", sessionStartedAt: T0 - 5_000, status: "guiding" });
    h.send({ kind: "transcript", role: "owner", text: "from s2 again", lang: "hi", final: true, session: "s2", ts: 5 });
    h.send({ kind: "transcript", role: "owner", text: "from s1", lang: "ta", final: true, ts: 6 });
    expect(h.mon().state.transcript.map((l) => l.text)).toEqual(["from s2 again"]);
    h.send({ kind: "session", state: "end", lang: "hi", session: "s2", ts: 7 });
    expect(h.mon().state.session).toBe("s1"); // the one left is followed
    expect(Object.keys(h.mon().state.sessions)).toEqual(["s1"]);
  });

  it("closes the socket and clears the state when the Live page is left, and reconnects without a handshake when it is opened again", async () => {
    const h = harness(true, undefined, fakeAuth(null), OPEN_ENV);
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "AAAA", w: 1, h: 1, purpose: "ask" });
    expect(h.mon().state.frame).not.toBeNull();
    h.setEnabled(false);
    expect(h.last().closed).toBe(true);
    expect(h.mon().state.frame).toBeNull();
    expect(h.mon().conn).toBe("waiting");
    h.setEnabled(true);
    expect(h.sockets).toHaveLength(2);
    await h.open();
    expect(h.last().sent).toEqual([]);
    expect(h.mon().conn).toBe("live");
  });

  it("a 4403 is the server's refusal of the page (rejected), no retry; a 4401 is shown as it comes, no retry with a token", async () => {
    const h = harness(true, undefined, fakeAuth(null), OPEN_ENV);
    await h.open();
    h.send({ kind: "frame", jpeg_b64: "DDDD", w: 1, h: 1, purpose: "ask" });
    act(() => h.last().onclose!({ code: 4403 }));
    expect(h.mon().conn).toBe("rejected");
    expect(h.mon().state.frame).toBeNull();
    expect(h.sockets).toHaveLength(1);
    cleanup();
    const g = harness(true, undefined, fakeAuth(null), OPEN_ENV);
    await g.open();
    act(() => g.last().onclose!({ code: 4401 }));
    expect(g.mon().conn).toBe("unauthenticated");
    expect(g.sockets).toHaveLength(1);
    expect(g.auth.idToken).not.toHaveBeenCalled();
  });

  it("asks the followed session for WebRTC video as the first and only thing it sends", async () => {
    FakePC.instances = [];
    const h = harness(true, FakePC as unknown as PeerCtor, fakeAuth(null), OPEN_ENV);
    await h.open();
    h.send({ kind: "session", state: "start", lang: "en", uid: "u1" });
    expect(h.last().sent).toEqual([JSON.stringify({ t: "rtc", kind: "want", to: "s1" })]);
  });
});
