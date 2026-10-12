import { describe, expect, it, vi } from "vitest";
import type { MonitorMsg } from "@aira/contracts";
import {
  backoffMs, detailText, eventLabel, initialState, monitorUrl, openMonitor, overlayShapes, readKey, reduce, storeKey, stripKey,
  toRect, toXY, type LiveState, type SocketLike,
} from "../src/lib/monitor";

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number } : never) : never;
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: 1, ...m }) as MonitorMsg;
const apply = (msgs: MonitorMsg[], s: LiveState = initialState()) => msgs.reduce(reduce, s);

describe("coordinate mapping ([y, x] in 0..1000 -> pixels)", () => {
  it("maps a point with y first", () => {
    expect(toXY([250, 500], 800, 400)).toEqual({ x: 400, y: 100 });
    expect(toXY([0, 1000], 640, 480)).toEqual({ x: 640, y: 0 });
  });
  it("maps a box [ymin, xmin, ymax, xmax] to x/y/w/h", () => {
    expect(toRect([100, 200, 300, 600], 1000, 500)).toEqual({ x: 200, y: 50, w: 400, h: 100 });
  });
});

describe("overlayShapes", () => {
  const objects = [
    { label: "Aavin milk 500 ml", box: [400, 500, 440, 560] as [number, number, number, number], point: [420, 530] as [number, number] },
    { label: "Sakthi curd 1 L", box: null, point: [600, 200] as [number, number] },
  ];
  it("draws every object in the object tone, the target in the target tone, and a point-only object as a dot", () => {
    const shapes = overlayShapes({ objects, target: "Aavin milk 500 ml", found: false, hand: null, status: "guiding" }, 1000, 1000);
    expect(shapes).toEqual([
      { kind: "box", label: "Aavin milk 500 ml", rect: { x: 500, y: 400, w: 60, h: 40 }, tone: "target" },
      { kind: "dot", label: "Sakthi curd 1 L", at: { x: 200, y: 600 }, tone: "object" },
    ]);
  });
  it("turns the target green (found tone) when found, matching the label case-insensitively", () => {
    const shapes = overlayShapes({ objects, target: "aavin MILK 500 ml", found: true, hand: null, status: "found" }, 100, 100);
    expect(shapes[0]).toMatchObject({ kind: "box", tone: "found" });
    expect(shapes[1]).toMatchObject({ tone: "object" });
  });
  it("adds the fingertip dot and a line from the hand to the target centre while guiding, but no line once found", () => {
    const hand = { point: [800, 100] as [number, number], near: false };
    const guiding = overlayShapes({ objects, target: "Aavin milk 500 ml", found: false, hand, status: "guiding" }, 1000, 1000);
    expect(guiding.at(-2)).toEqual({ kind: "line", from: { x: 100, y: 800 }, to: { x: 530, y: 420 } });
    expect(guiding.at(-1)).toEqual({ kind: "dot", label: "hand", at: { x: 100, y: 800 }, tone: "hand" });
    const found = overlayShapes({ objects, target: "Aavin milk 500 ml", found: true, hand, status: "found" }, 1000, 1000);
    expect(found.some((s) => s.kind === "line")).toBe(false);
    expect(found.at(-1)).toMatchObject({ kind: "dot", tone: "hand" });
  });
});

describe("reduce: transcript", () => {
  it("keeps final lines in arrival order across speakers", () => {
    const s = apply([
      mon({ kind: "transcript", role: "owner", text: "Where is the milk?", lang: "en", final: true }),
      mon({ kind: "transcript", role: "aira", text: "Fridge two.", lang: "en", final: true }),
      mon({ kind: "transcript", role: "customer", text: "One curd please", lang: "ta", final: true }),
    ]);
    expect(s.transcript.map((l) => [l.role, l.text])).toEqual([
      ["owner", "Where is the milk?"], ["aira", "Fridge two."], ["customer", "One curd please"],
    ]);
  });
  it("replaces a speaker's partial line in place, then with the final line, keeping its id", () => {
    const s1 = apply([
      mon({ kind: "transcript", role: "owner", text: "Where", lang: "en", final: false }),
      mon({ kind: "transcript", role: "aira", text: "Thinking", lang: "en", final: false }),
      mon({ kind: "transcript", role: "owner", text: "Where is the", lang: "en", final: false }),
    ]);
    expect(s1.transcript.map((l) => l.text)).toEqual(["Where is the", "Thinking"]);
    expect(s1.transcript[0].final).toBe(false);
    const s2 = reduce(s1, mon({ kind: "transcript", role: "owner", text: "Where is the milk?", lang: "en", final: true }));
    expect(s2.transcript.map((l) => [l.text, l.final])).toEqual([["Where is the milk?", true], ["Thinking", false]]);
    expect(s2.transcript[0].id).toBe(s1.transcript[0].id);
    const s3 = reduce(s2, mon({ kind: "transcript", role: "owner", text: "Milk", lang: "en", final: false }));
    expect(s3.transcript).toHaveLength(3);
    expect(s3.transcript[2]).toMatchObject({ role: "owner", text: "Milk", final: false });
  });
  it("ignores empty partials", () => {
    expect(apply([mon({ kind: "transcript", role: "owner", text: "  ", lang: "en", final: false })]).transcript).toHaveLength(0);
  });
});

describe("reduce: camera, vision, hand, status, events and sessions", () => {
  it("stores the latest frame as a data URL", () => {
    const s = apply([mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "guide" })]);
    expect(s.frame).toMatchObject({ src: "data:image/jpeg;base64,AAAA", w: 480, h: 640, purpose: "guide" });
  });
  it("vision found=true sets found, status found and logs a Found activity item once", () => {
    const vision = (found: boolean) =>
      mon({ kind: "vision", source: "guidance", objects: [{ label: "Aavin milk 500 ml", box: [1, 2, 3, 4], point: null }], target: "Aavin milk 500 ml", found });
    const s = apply([mon({ kind: "status", state: "guiding" }), vision(false), vision(true), vision(true)]);
    expect(s.found).toBe(true);
    expect(s.status).toBe("found");
    expect(s.activity.filter((a) => a.name === "found")).toHaveLength(1);
    expect(s.activity[0].detail).toBe("Aavin milk 500 ml");
  });
  it("a new guiding status clears found; hand and events are recorded", () => {
    const s = apply([
      mon({ kind: "vision", source: "guidance", objects: [], target: "x", found: true }),
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "hand", point: [10, 20], near: true }),
      mon({ kind: "event", name: "order_sent", detail: { summary: "30 L curd" } }),
    ]);
    expect(s.found).toBe(false);
    expect(s.hand).toEqual({ point: [10, 20], near: true });
    expect(s.activity.at(-1)).toMatchObject({ name: "order_sent", detail: "30 L curd" });
  });
  it("turns an event's detail object into one readable line", () => {
    expect(detailText("count", { sku: "sakthi_curd_1l", counted: 28, expected: 30 })).toBe("Counted 28/30");
    expect(detailText("confirm_resolved", { id: "c1", answer: "yes", status: "committed" })).toBe("Answer: yes");
    expect(detailText("confirm_proposed", { id: "c1", kind: "order", prompt: "Order 30 litres?" })).toBe("Order 30 litres?");
    expect(detailText("tool_call", { tool: "product_find", ok: true, ms: 820, args: { product: "milk" } })).toBe("product_find");
    expect(detailText("tick", { i: 5, ok: false })).toBe("i 5, ok false");
  });
  it("session start resets the camera state but keeps the conversation; end sets idle", () => {
    const s0 = apply([
      mon({ kind: "frame", jpeg_b64: "A", w: 1, h: 1, purpose: "ask" }),
      mon({ kind: "transcript", role: "owner", text: "hi", lang: "en", final: true }),
    ]);
    const s1 = reduce(s0, mon({ kind: "session", state: "start", lang: "ta", ts: 5 }));
    expect(s1.frame).toBeNull();
    expect(s1.transcript).toHaveLength(1);
    expect(s1).toMatchObject({ session: "s1", lang: "ta", status: "listening", lastMsgAt: 5 });
    expect(s1.activity.at(-1)?.name).toBe("session_start");
    const s2 = reduce(s1, mon({ kind: "session", state: "end", lang: "ta" }));
    expect(s2).toMatchObject({ session: null, status: "idle" });
  });
  it("labels known events for the timeline", () => {
    expect(eventLabel("order_sent")).toBe("Order sent");
    expect(eventLabel("hand_near")).toBe("Hand near");
    expect(eventLabel("some_new_thing")).toBe("Some new thing");
  });
});

describe("key handling", () => {
  const mem = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
  };
  it("takes ?key= from the URL and remembers it for the tab", () => {
    const st = mem();
    expect(readKey("?shop=x&key=abc123", st)).toBe("abc123");
    expect(st.m.get("aira.monitorKey")).toBe("abc123");
    expect(readKey("", st)).toBe("abc123");
  });
  it("returns null with no key anywhere, and survives a throwing storage", () => {
    expect(readKey("?shop=x", mem())).toBeNull();
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
    expect(readKey("?key=k", broken)).toBe("k");
    expect(readKey("", broken)).toBeNull();
    expect(() => storeKey("k", broken)).not.toThrow();
    expect(readKey("?key=k", null)).toBe("k");
  });
  it("strips ?key= from the address", () => {
    expect(stripKey("?shop=x&key=abc")).toBe("?shop=x");
    expect(stripKey("?key=abc")).toBe("");
  });
});

describe("monitorUrl", () => {
  it("replaces /ws with /monitor and adds shop and key", () => {
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "wss://aira-live-abc.a.run.app/ws" }, "sunrise_dairy", "k1")).toBe(
      "wss://aira-live-abc.a.run.app/monitor?shop=sunrise_dairy&key=k1",
    );
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "ws://localhost:8080/ws" }, "s", "k")).toBe("ws://localhost:8080/monitor?shop=s&key=k");
  });
  it("is null when unset or not a WebSocket URL", () => {
    expect(monitorUrl({}, "s", "k")).toBeNull();
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "https://x/ws" }, "s", "k")).toBeNull();
  });
});

describe("reconnect backoff", () => {
  it("doubles from 1 s and caps at 15 s", () => {
    expect([0, 1, 2, 3, 4, 9].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
  });

  function fakeSocket(): SocketLike & { closed: boolean } {
    return { onopen: null, onmessage: null, onclose: null, onerror: null, closed: false, close() { this.closed = true; } };
  }

  it("reconnects after each close with growing delays, resets after an open, and stops cleanly", () => {
    const sockets: ReturnType<typeof fakeSocket>[] = [];
    const timers: Array<{ fn: () => void; ms: number }> = [];
    const onMsg = vi.fn();
    const onConn = vi.fn();
    const stop = openMonitor(
      "ws://x/monitor?shop=s&key=k",
      onMsg,
      onConn,
      { connect: () => { const s = fakeSocket(); sockets.push(s); return s; }, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: vi.fn() },
    );
    expect(onConn).toHaveBeenLastCalledWith("connecting", 0);
    sockets[0].onclose!({ code: 1006 });
    expect(onConn).toHaveBeenLastCalledWith("offline", 0);
    expect(timers.map((t) => t.ms)).toEqual([1000]);
    timers[0].fn();
    sockets[1].onclose!({ code: 1006 });
    expect(timers.map((t) => t.ms)).toEqual([1000, 2000]);
    timers[1].fn();
    sockets[2].onopen!({});
    expect(onConn).toHaveBeenLastCalledWith("live", 0);
    sockets[2].onmessage!({ data: JSON.stringify({ t: "mon", kind: "status", session: "s", ts: 1, state: "thinking" }) });
    sockets[2].onmessage!({ data: "not json" });
    expect(onMsg).toHaveBeenCalledTimes(1);
    expect(onMsg.mock.calls[0][0]).toMatchObject({ kind: "status", state: "thinking" });
    sockets[2].onclose!({ code: 1006 });
    expect(timers.map((t) => t.ms)).toEqual([1000, 2000, 1000]); // attempt count reset by the open
    stop();
    timers[2].fn();
    expect(sockets).toHaveLength(3); // no dial after stop
  });

  it("reports a refused key instead of retrying", () => {
    const onConn = vi.fn();
    const s = fakeSocket();
    openMonitor("ws://x", vi.fn(), onConn, { connect: () => s, setTimer: vi.fn(), clearTimer: vi.fn() });
    s.onclose!({ code: 1008 });
    expect(onConn).toHaveBeenLastCalledWith("rejected", 0);
  });
});
