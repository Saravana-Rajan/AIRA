import { describe, it, expect, vi, afterEach } from "vitest";
import { AiraSocket, backoffMs } from "../src/net/socket";

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen?: () => void;
  onmessage?: (e: { data: string }) => void;
  onclose?: () => void;
  constructor(public url: string) { FakeWS.instances.push(this); }
  send(s: string) { this.sent.push(s); }
  close() { this.readyState = 3; this.onclose?.(); }
  open() { this.readyState = 1; this.onopen?.(); }
}
const HELLO = { t: "hello", uid: "u", shopId: "sunrise_dairy", lang: "en", verbosity: "short", appVersion: "0.1.0" } as const;

afterEach(() => { vi.useRealTimers(); });

describe("backoffMs", () => {
  it("doubles from 500ms and caps at 15s", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 8000, 15000, 15000]);
  });
});

describe("AiraSocket", () => {
  it("sends hello on open and forwards only valid server messages", () => {
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    const got: any[] = [];
    s.on((m) => got.push(m));
    s.connect(HELLO);
    const ws = FakeWS.instances.at(-1)!;
    ws.open();
    expect(JSON.parse(ws.sent[0]).t).toBe("hello");
    ws.onmessage!({ data: JSON.stringify({ t: "cue", cue: "ack" }) });
    ws.onmessage!({ data: JSON.stringify({ t: "unknown_future_type" }) });
    ws.onmessage!({ data: "not json" });
    expect(got).toEqual([{ t: "cue", cue: "ack" }]);
  });

  it("reconnects with backoff and re-sends hello", () => {
    vi.useFakeTimers();
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    s.connect(HELLO);
    const first = FakeWS.instances.at(-1)!;
    first.open(); first.close();
    const before = FakeWS.instances.length;
    vi.advanceTimersByTime(499);
    expect(FakeWS.instances.length).toBe(before);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances.length).toBe(before + 1);
    const second = FakeWS.instances.at(-1)!;
    second.open();
    expect(JSON.parse(second.sent[0]).t).toBe("hello");
  });

  it("carries the server's resume handle in every reconnect hello", () => {
    vi.useFakeTimers();
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    s.connect(HELLO);
    const first = FakeWS.instances.at(-1)!;
    first.open();
    expect("resume" in JSON.parse(first.sent[0])).toBe(false);
    first.onmessage!({ data: JSON.stringify({ t: "ready", sessionId: "s1", resume: "s1", mock: false }) });
    first.close();
    vi.advanceTimersByTime(500);
    const second = FakeWS.instances.at(-1)!;
    second.open();
    expect(JSON.parse(second.sent[0])).toMatchObject({ t: "hello", resume: "s1" });
    // a later ready (a new session after the window closed) replaces it
    second.onmessage!({ data: JSON.stringify({ t: "ready", sessionId: "s2", resume: "s2", mock: false }) });
    expect(s.hello).toMatchObject({ resume: "s2" });
  });

  it("reports the bytes still queued on the open socket, and 0 while disconnected", () => {
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    expect(s.bufferedAmount()).toBe(0);
    s.connect(HELLO);
    const ws = FakeWS.instances.at(-1)! as FakeWS & { bufferedAmount?: number };
    ws.open();
    expect(s.bufferedAmount()).toBe(0);
    ws.bufferedAmount = 48_000;
    expect(s.bufferedAmount()).toBe(48_000);
    ws.close();
    expect(s.bufferedAmount()).toBe(0);
  });

  it("send returns false while disconnected", () => {
    expect(new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any }).send({ t: "stop" })).toBe(false);
  });

  it("send returns true and writes JSON once open", () => {
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    s.connect(HELLO);
    const ws = FakeWS.instances.at(-1)!;
    ws.open();
    expect(s.send({ t: "stop" })).toBe(true);
    expect(JSON.parse(ws.sent.at(-1)!)).toEqual({ t: "stop" });
  });

  it("close() during a pending reconnect stops reconnecting", () => {
    vi.useFakeTimers();
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    s.connect(HELLO);
    const ws = FakeWS.instances.at(-1)!;
    ws.open();
    ws.close(); // dropped by the network: a reconnect is now scheduled
    const before = FakeWS.instances.length;
    s.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.instances.length).toBe(before);
  });

  it("on() returns an unsubscribe function", () => {
    const s = new AiraSocket("ws://x", { WebSocketImpl: FakeWS as any });
    const got: unknown[] = [];
    const off = s.on((m) => got.push(m));
    s.connect(HELLO);
    const ws = FakeWS.instances.at(-1)!;
    ws.open();
    off();
    ws.onmessage!({ data: JSON.stringify({ t: "cue", cue: "ack" }) });
    expect(got).toEqual([]);
  });
});
