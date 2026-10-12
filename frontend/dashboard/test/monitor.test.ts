import { describe, expect, it, vi } from "vitest";
import type { MonitorMsg } from "@aira/contracts";
import {
  MAX_LINES, backoffMs, detailText, eventLabel, follow, frameAge, initialState, keyFromHash, monitorUrl, openMonitor, overlayShapes,
  readKey, reduce, sessionClock, storeKey, stripKey, targetInfo, toRect, toXY, type LiveState, type SocketLike,
} from "../src/lib/monitor";

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number; session?: string } : never) : never;
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: 1, ...m }) as MonitorMsg;
const apply = (msgs: MonitorMsg[], s: LiveState = initialState()) => msgs.reduce(reduce, s);
const box = (b: number[]) => b as [number, number, number, number];
const layer = (objects: any[], ts = 0) => ({ objects, ts });

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
  const er2 = layer([
    { label: "Aavin milk 500 ml", box: box([400, 500, 440, 560]), point: [420, 530], score: 0.92 },
    { label: "Sakthi curd 1 L", box: null, point: [600, 200] },
  ]);
  const none = layer([]);
  it("draws ER 2 objects in the object tone with their score, the target in the target tone, and a point-only object as a dot", () => {
    const shapes = overlayShapes({ er2, mediapipe: none, target: "Aavin milk 500 ml", found: false, hand: null, status: "guiding" }, 1000, 1000);
    expect(shapes).toEqual([
      { kind: "box", label: "Aavin milk 500 ml", rect: { x: 500, y: 400, w: 60, h: 40 }, tone: "target", score: 0.92 },
      { kind: "dot", label: "Sakthi curd 1 L", at: { x: 200, y: 600 }, tone: "object" },
    ]);
  });
  it("turns the target green (found tone) when found, matching the label case-insensitively", () => {
    const shapes = overlayShapes({ er2, mediapipe: none, target: "aavin MILK 500 ml", found: true, hand: null, status: "found" }, 100, 100);
    expect(shapes[0]).toMatchObject({ kind: "box", tone: "found" });
    expect(shapes[1]).toMatchObject({ tone: "object" });
  });
  it("draws MediaPipe boxes first, thin (fast tone), red when the bump guard flags danger, and only while fresh", () => {
    const mediapipe = layer([
      { label: "chair", box: box([420, 310, 900, 690]), point: null, score: 0.82, severity: "danger" },
      { label: "bottle", box: box([100, 50, 300, 120]), point: null, score: 0.61 },
      { label: "no box", box: null, point: [1, 1] },
    ], 10_000);
    const shapes = overlayShapes({ er2, mediapipe, target: null, found: false, hand: null, status: "idle" }, 1000, 1000, 10_500);
    expect(shapes.slice(0, 2)).toEqual([
      { kind: "box", label: "chair", rect: { x: 310, y: 420, w: 380, h: 480 }, tone: "danger", score: 0.82 },
      { kind: "box", label: "bottle", rect: { x: 50, y: 100, w: 70, h: 200 }, tone: "fast", score: 0.61 },
    ]);
    expect(shapes).toHaveLength(4);
    const stale = overlayShapes({ er2, mediapipe, target: null, found: false, hand: null, status: "idle" }, 1000, 1000, 10_000 + 5000);
    expect(stale.every((s) => s.kind !== "box" || s.tone !== "fast")).toBe(true);
    expect(stale).toHaveLength(2);
  });
  it("adds the fingertip dot and a line from the hand to the target centre while guiding, but no line once found", () => {
    const hand = { point: [800, 100] as [number, number], near: false };
    const guiding = overlayShapes({ er2, mediapipe: none, target: "Aavin milk 500 ml", found: false, hand, status: "guiding" }, 1000, 1000);
    expect(guiding.at(-2)).toEqual({ kind: "line", from: { x: 100, y: 800 }, to: { x: 530, y: 420 } });
    expect(guiding.at(-1)).toEqual({ kind: "dot", label: "hand", at: { x: 100, y: 800 }, tone: "hand" });
    const found = overlayShapes({ er2, mediapipe: none, target: "Aavin milk 500 ml", found: true, hand, status: "found" }, 1000, 1000);
    expect(found.some((s) => s.kind === "line")).toBe(false);
    expect(found.at(-1)).toMatchObject({ kind: "dot", tone: "hand" });
  });
});

describe("targetInfo", () => {
  it("is null without a target, and reads visibility, confidence, distance and direction off the ER 2 objects", () => {
    expect(targetInfo({ target: null, found: false, er2: layer([]) })).toBeNull();
    const er2 = layer([{ label: "Aavin milk", box: box([100, 700, 200, 900]), point: null, score: 0.92, distance: "arm" }]);
    expect(targetInfo({ target: "aavin milk", found: false, er2 })).toEqual({
      label: "aavin milk", visible: true, found: false, confidence: 0.92, distance: "arm", side: "right", shelf: "top",
    });
    expect(targetInfo({ target: "curd", found: false, er2 })).toEqual({
      label: "curd", visible: false, found: false, confidence: null, distance: null, side: null, shelf: null,
    });
    const centre = layer([{ label: "curd", box: null, point: [500, 500] }]);
    expect(targetInfo({ target: "curd", found: true, er2: centre })).toMatchObject({ visible: true, found: true, side: "centre", shelf: "middle", confidence: null });
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

describe("reduce: camera, vision, hand, status, stats, events and sessions", () => {
  it("stores the latest frame as a data URL", () => {
    const s = apply([mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "guide" })]);
    expect(s.frame).toMatchObject({ src: "data:image/jpeg;base64,AAAA", w: 480, h: 640, purpose: "guide" });
  });
  it("keeps ER 2 and MediaPipe boxes apart; an obstacle is logged once", () => {
    const s = apply([
      mon({ kind: "vision", source: "er2", objects: [{ label: "milk", box: box([1, 2, 3, 4]), point: null }], context: "camera_ask" }),
      mon({ kind: "vision", source: "mediapipe", objects: [{ label: "chair", box: box([1, 2, 3, 4]), point: null, severity: "danger" }], severity: "danger", ts: 5 }),
      mon({ kind: "vision", source: "bump", objects: [{ label: "chair", box: box([1, 2, 3, 4]), point: null, severity: "danger" }], severity: "danger", ts: 6 }),
    ]);
    expect(s.er2.objects.map((o) => o.label)).toEqual(["milk"]);
    expect(s.context).toBe("camera_ask");
    expect(s.mediapipe).toEqual({ objects: [{ label: "chair", box: [1, 2, 3, 4], point: null, severity: "danger" }], ts: 6 });
    expect(s.activity.filter((a) => a.name === "obstacle")).toHaveLength(1);
    expect(s.activity[0]).toMatchObject({ detail: "chair", tag: "vision" });
  });
  it("vision found=true sets found, status found and logs a Found activity item once", () => {
    const vision = (found: boolean) =>
      mon({ kind: "vision", source: "er2", objects: [{ label: "Aavin milk 500 ml", box: box([1, 2, 3, 4]), point: null }], target: "Aavin milk 500 ml", found, context: "guidance" });
    const s = apply([mon({ kind: "status", state: "guiding" }), vision(false), vision(true), vision(true)]);
    expect(s.found).toBe(true);
    expect(s.status).toBe("found");
    expect(s.activity.filter((a) => a.name === "found")).toHaveLength(1);
    expect(s.activity[0].detail).toBe("Aavin milk 500 ml");
  });
  it("a guidance message without a target keeps the one being looked for", () => {
    const s = apply([
      mon({ kind: "vision", source: "er2", objects: [], target: "curd", found: false, context: "guidance" }),
      mon({ kind: "vision", source: "er2", objects: [], context: "guidance" }),
    ]);
    expect(s.target).toBe("curd");
  });
  it("a new guiding status clears found; hand and events are recorded with a tag", () => {
    const s = apply([
      mon({ kind: "vision", source: "er2", objects: [], target: "x", found: true }),
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "hand", point: [10, 20], near: true }),
      mon({ kind: "event", name: "order_sent", detail: { summary: "30 L curd" } }),
      mon({ kind: "event", name: "tool_call", detail: { tool: "camera_ask", ok: true } }),
      mon({ kind: "event", name: "client_event", detail: { name: "network" } }),
    ]);
    expect(s.found).toBe(false);
    expect(s.hand).toEqual({ point: [10, 20], near: true });
    expect(s.activity.slice(-3).map((a) => [a.name, a.detail, a.tag])).toEqual([
      ["order_sent", "30 L curd", "order"], ["tool_call", "camera_ask", "vision"], ["client_event", "network", "sam"],
    ]);
  });
  it("keeps the latest stats", () => {
    const s = apply([mon({ kind: "stats", turns: 3, er2Calls: 2, flashCalls: 1, visionMs: 820, fps: 2.5, ts: 9 })]);
    expect(s.stats).toEqual({ turns: 3, er2Calls: 2, flashCalls: 1, visionMs: 820, fps: 2.5, ts: 9 });
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
    const s1 = reduce(s0, mon({ kind: "session", state: "start", lang: "ta", ts: 5, uid: "u1" }));
    expect(s1.frame).toBeNull();
    expect(s1.transcript).toHaveLength(1);
    expect(s1).toMatchObject({ session: "s1", sessionStartedAt: 5, lang: "ta", status: "listening", lastMsgAt: 5 });
    expect(s1.activity.at(-1)?.name).toBe("session_start");
    const s2 = reduce(s1, mon({ kind: "session", state: "end", lang: "ta" }));
    expect(s2).toMatchObject({ session: null, status: "idle", sessions: {} });
    expect(s2.transcript).toEqual([]); // nothing of a session outlives it
    expect(s2.activity).toEqual([]);
  });
  it("a room camera session lives in rooms: its frames and MediaPipe boxes go there, not to Sam's view", () => {
    const s = apply([
      mon({ kind: "session", state: "start", lang: "en", uid: "u1", ts: 1 }),
      mon({ kind: "session", state: "start", lang: "en", uid: "camera:front", session: "cam1", ts: 2 }),
      mon({ kind: "frame", jpeg_b64: "ROOM", w: 480, h: 270, purpose: "ask", session: "cam1", ts: 3 }),
      mon({ kind: "vision", source: "mediapipe", objects: [{ label: "door", box: box([1, 2, 3, 4]), point: null }], session: "cam1", ts: 4 }),
      mon({ kind: "frame", jpeg_b64: "SAM", w: 480, h: 640, purpose: "guide", ts: 5 }),
    ]);
    expect(s.session).toBe("s1");
    expect(s.frame?.src).toBe("data:image/jpeg;base64,SAM");
    expect(s.mediapipe.objects).toEqual([]);
    expect(s.rooms.cam1).toMatchObject({ name: "front", frame: { src: "data:image/jpeg;base64,ROOM", w: 480, h: 270 } });
    expect(s.rooms.cam1.mediapipe.objects.map((o) => o.label)).toEqual(["door"]);
    expect(s.activity.map((a) => a.name)).toEqual(["session_start", "room_start"]);
    const gone = reduce(s, mon({ kind: "session", state: "end", lang: "en", uid: "camera:front", session: "cam1", ts: 6 }));
    expect(gone.rooms).toEqual({});
    expect(gone.session).toBe("s1");
  });
  it("follows one session: a second phone is listed but its lines are dropped until picked, and picking starts clean", () => {
    const s = apply([
      mon({ kind: "session", state: "start", lang: "en", uid: "u1", ts: 1 }),
      mon({ kind: "transcript", role: "owner", text: "hi from s1", lang: "en", final: true, ts: 2 }),
      mon({ kind: "session", state: "start", lang: "ta", uid: "u2", session: "s2", ts: 3 }),
      mon({ kind: "transcript", role: "owner", text: "hi from s2", lang: "ta", final: true, session: "s2", ts: 4 }),
      mon({ kind: "frame", jpeg_b64: "S2", w: 1, h: 1, purpose: "ask", session: "s2", ts: 5 }),
      mon({ kind: "status", state: "thinking", session: "s2", ts: 6 }),
      mon({ kind: "stats", turns: 9, er2Calls: 0, flashCalls: 0, visionMs: null, fps: 0, session: "s2", ts: 7 }),
    ]);
    expect(s.session).toBe("s1");
    expect(Object.keys(s.sessions)).toEqual(["s1", "s2"]);
    expect(s.sessions.s2).toEqual({ id: "s2", uid: "u2", lang: "ta", startedAt: 3 });
    expect(s.transcript.map((l) => l.text)).toEqual(["hi from s1"]);
    expect(s.frame).toBeNull();
    expect(s.status).toBe("listening");
    expect(s.stats).toBeNull();
    expect(s.lastMsgAt).toBe(3); // the dropped lines did not touch the page
    const picked = follow(s, "s2");
    expect(picked).toMatchObject({ session: "s2", sessionStartedAt: 3, lang: "ta", status: "listening", transcript: [], frame: null, activity: [] });
    expect(picked.sessions).toEqual(s.sessions);
    expect(follow(s, "s1")).toBe(s);
    expect(follow(s, "nope")).toBe(s);
    const after = reduce(picked, mon({ kind: "transcript", role: "owner", text: "now s2", lang: "ta", final: true, session: "s2", ts: 8 }));
    expect(after.transcript.map((l) => l.text)).toEqual(["now s2"]);
    expect(reduce(after, mon({ kind: "transcript", role: "owner", text: "s1 again", lang: "en", final: true, ts: 9 })).transcript).toHaveLength(1);
  });
  it("adopts the first session it hears from, and after that session ends the next one is followed from scratch", () => {
    const s = apply([mon({ kind: "frame", jpeg_b64: "A", w: 1, h: 1, purpose: "ask", session: "s1", ts: 1 })]);
    expect(s.session).toBe("s1");
    const other = reduce(s, mon({ kind: "frame", jpeg_b64: "B", w: 1, h: 1, purpose: "ask", session: "s2", ts: 2 }));
    expect(other).toBe(s);
    const ended = reduce(s, mon({ kind: "session", state: "end", lang: "en", session: "s1", ts: 3 }));
    expect(ended).toMatchObject({ session: null, frame: null, sessions: {} });
    const next = reduce(ended, mon({ kind: "session", state: "start", lang: "hi", uid: "u2", session: "s2", ts: 4 }));
    expect(next).toMatchObject({ session: "s2", sessionStartedAt: 4, lang: "hi", transcript: [] });
    expect(next.activity.map((a) => a.name)).toEqual(["session_start"]);
  });
  it("the end of a session it is not following only drops it from the list; a repeated start of the followed one keeps its clock and talk", () => {
    const s = apply([
      mon({ kind: "session", state: "start", lang: "en", uid: "u1", ts: 1 }),
      mon({ kind: "transcript", role: "owner", text: "hi", lang: "en", final: true, ts: 2 }),
      mon({ kind: "session", state: "start", lang: "en", uid: "u2", session: "s2", ts: 3 }),
      mon({ kind: "session", state: "end", lang: "en", uid: "u2", session: "s2", ts: 4 }),
      mon({ kind: "session", state: "start", lang: "en", uid: "u1", ts: 5 }), // the monitor reconnected: s1 announced again
    ]);
    expect(Object.keys(s.sessions)).toEqual(["s1"]);
    expect(s).toMatchObject({ session: "s1", sessionStartedAt: 1 });
    expect(s.transcript.map((l) => l.text)).toEqual(["hi"]);
  });
  it("keeps at most MAX_LINES transcript lines and only the last frame", () => {
    const lines = Array.from({ length: MAX_LINES + 25 }, (_, i) =>
      mon({ kind: "transcript", role: "owner", text: `line ${i}`, lang: "en", final: true, ts: i + 1 }));
    const s = apply([...lines, mon({ kind: "frame", jpeg_b64: "ONE", w: 1, h: 1, purpose: "ask" }), mon({ kind: "frame", jpeg_b64: "TWO", w: 1, h: 1, purpose: "ask" })]);
    expect(s.transcript).toHaveLength(MAX_LINES);
    expect(s.transcript[0].text).toBe("line 25");
    expect(s.frame?.src).toBe("data:image/jpeg;base64,TWO");
    expect(JSON.stringify(s)).not.toContain("ONE");
  });
  it("labels known events for the timeline", () => {
    expect(eventLabel("order_sent")).toBe("Order sent");
    expect(eventLabel("hand_near")).toBe("Hand near");
    expect(eventLabel("some_new_thing")).toBe("Some new thing");
  });
});

describe("key handling (the fragment, never the query string)", () => {
  const mem = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
  };
  it("reads #key= in its three spellings", () => {
    expect(keyFromHash("#key=abc123")).toBe("abc123");
    expect(keyFromHash("#/live?key=abc123")).toBe("abc123");
    expect(keyFromHash("#/live?shop=x&key=a%20b")).toBe("a b");
    expect(keyFromHash("#/live")).toBeNull();
    expect(keyFromHash("")).toBeNull();
    expect(keyFromHash("#/live?monkey=1")).toBeNull();
  });
  it("takes the key from the fragment and remembers it for the tab", () => {
    const st = mem();
    expect(readKey("#/live?key=abc123", st)).toBe("abc123");
    expect(st.m.get("aira.monitorKey")).toBe("abc123");
    expect(readKey("#/live", st)).toBe("abc123");
  });
  it("returns null with no key anywhere, and survives a throwing storage", () => {
    expect(readKey("#/live", mem())).toBeNull();
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
    expect(readKey("#key=k", broken)).toBe("k");
    expect(readKey("", broken)).toBeNull();
    expect(() => storeKey("k", broken)).not.toThrow();
    expect(readKey("#key=k", null)).toBe("k");
  });
  it("strips key= from the fragment and nothing else", () => {
    expect(stripKey("#/live?key=abc")).toBe("#/live");
    expect(stripKey("#/live?shop=x&key=abc")).toBe("#/live?shop=x");
    expect(stripKey("#/live?key=abc&shop=x")).toBe("#/live?shop=x");
    expect(stripKey("#key=abc")).toBe("");
    expect(stripKey("#/live")).toBe("#/live");
  });
});

describe("monitorUrl", () => {
  it("replaces /ws with /monitor and adds the shop, never the key", () => {
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "wss://aira-live-abc.a.run.app/ws" }, "sunrise_dairy")).toBe(
      "wss://aira-live-abc.a.run.app/monitor?shop=sunrise_dairy",
    );
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "ws://localhost:8080/ws" }, "s")).toBe("ws://localhost:8080/monitor?shop=s");
  });
  it("is null when unset or not a WebSocket URL", () => {
    expect(monitorUrl({}, "s")).toBeNull();
    expect(monitorUrl({ VITE_AIRA_LIVE_WS: "https://x/ws" }, "s")).toBeNull();
  });
});

describe("clocks", () => {
  it("formats the session timer and the frame age", () => {
    expect(sessionClock(null, 5000)).toBe("T+00:00");
    expect(sessionClock(1000, 85_500)).toBe("T+01:24");
    expect(frameAge(null, 1)).toBe("—");
    expect(frameAge(1000, 1400)).toBe("0.4s ago");
    expect(frameAge(1000, 13_400)).toBe("12s ago");
  });
});

describe("reconnect backoff and auth", () => {
  it("doubles from 1 s and caps at 15 s", () => {
    expect([0, 1, 2, 3, 4, 9].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
  });

  function fakeSocket(): SocketLike & { closed: boolean; sent: string[] } {
    return {
      onopen: null, onmessage: null, onclose: null, onerror: null, closed: false, sent: [],
      send(d: string) { this.sent.push(d); },
      close() { this.closed = true; },
    };
  }

  it("sends {t:auth, key} as its first message, reconnects after each close with growing delays, resets after an open, and stops cleanly", () => {
    const sockets: ReturnType<typeof fakeSocket>[] = [];
    const timers: Array<{ fn: () => void; ms: number }> = [];
    const onMsg = vi.fn();
    const onConn = vi.fn();
    const stop = openMonitor(
      "ws://x/monitor?shop=s",
      "k-secret",
      onMsg,
      onConn,
      { connect: () => { const s = fakeSocket(); sockets.push(s); return s; }, setTimer: (fn: () => void, ms: number) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: vi.fn() },
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
    expect(sockets[2].sent).toEqual([JSON.stringify({ t: "auth", key: "k-secret" })]);
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
    openMonitor("ws://x", "k", vi.fn(), onConn, { connect: () => s, setTimer: vi.fn(), clearTimer: vi.fn() });
    s.onopen!({});
    s.onclose!({ code: 4403 });
    expect(onConn).toHaveBeenLastCalledWith("rejected", 0);
  });
});
