// useMonitor keeps what it hears in memory only, scoped to one session, and drops it whenever the socket is
// not open: on close, when the Live page is left, when the page is unloaded (pagehide), and when the key is
// refused (which also forgets the key for the tab). A merely hidden tab stays connected. Fake sockets stand in
// for WebSocket.
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "preact/test-utils";
import { useState } from "preact/hooks";
import { cleanup, mount } from "./helpers/render";
import { useMonitor, type Monitor } from "../src/lib/useMonitor";
import { KEY_STORAGE, type ClientDeps, type SocketLike } from "../src/lib/monitor";

type Fake = SocketLike & { closed: boolean; sent: string[] };
const ENV = { VITE_AIRA_LIVE_WS: "wss://live.example/ws" };

function fakeSocket(): Fake {
  return {
    onopen: null, onmessage: null, onclose: null, onerror: null, closed: false, sent: [],
    send(d: string) { this.sent.push(d); },
    close() { this.closed = true; },
  };
}

function harness(enabled = true) {
  const sockets: Fake[] = [];
  const deps: ClientDeps = { connect: () => { const s = fakeSocket(); sockets.push(s); return s; }, setTimer: () => 0, clearTimer: () => {} };
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
  sessionStorage.setItem(KEY_STORAGE, "k-1");
  location.hash = "#/live";
});
afterEach(cleanup);

describe("useMonitor", () => {
  it("connects with the tab's key, reduces messages into memory, and drops everything when the socket closes", () => {
    const h = harness();
    expect(h.sockets).toHaveLength(1);
    expect(h.mon().keyNeeded).toBe(false);
    h.open();
    expect(h.last().sent).toEqual([JSON.stringify({ t: "auth", key: "k-1" })]);
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
    expect([...Array(sessionStorage.length).keys()].map((i) => sessionStorage.key(i))).toEqual([KEY_STORAGE]);
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
    const deps: ClientDeps = { connect: () => fakeSocket(), setTimer: () => 0, clearTimer: () => {} };
    function Probe() {
      m = useMonitor(true, "sunrise_dairy", {}, deps);
      return null;
    }
    mount(<Probe />);
    expect(m!.configured).toBe(false);
    expect(m!.conn).toBe("waiting");
  });

  it("forgets a refused key and asks for a new one, without retrying", () => {
    const h = harness();
    h.open();
    act(() => h.last().onclose!({ code: 4403 }));
    expect(h.mon().conn).toBe("rejected");
    expect(h.mon().keyNeeded).toBe(true);
    expect(sessionStorage.getItem(KEY_STORAGE)).toBeNull();
    expect(h.sockets).toHaveLength(1);
    act(() => h.mon().setKey("k-2"));
    expect(sessionStorage.getItem(KEY_STORAGE)).toBe("k-2");
    expect(h.sockets).toHaveLength(2);
    h.open();
    expect(h.last().sent).toEqual([JSON.stringify({ t: "auth", key: "k-2" })]);
  });
});
