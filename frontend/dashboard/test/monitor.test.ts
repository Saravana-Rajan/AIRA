import { describe, expect, it, vi } from "vitest";
import type { MonitorMsg } from "@aira/contracts";
import {
  MAX_LINES, backoffMs, detailText, eventLabel, follow, frameAge, initialState, listSessions, monitorUrl, openMonitor, overlayShapes,
  reduce, sessionClock, sessionLabel, sinceText, toRect, toXY, type LiveState, type SocketLike,
} from "../src/lib/monitor";
import { jwt } from "./helpers/jwt";

/** 2026-10-10 06:30 UTC = 12:00 pm IST. */
const T_IST = Date.UTC(2026, 9, 10, 6, 30);

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
    // Its list entry still follows what it says (status, last heard), nothing else does.
    expect(s.sessions.s2).toEqual({ id: "s2", uid: "u2", lang: "ta", startedAt: 3, lastSeenAt: 6, status: "thinking" });
    expect(s.transcript.map((l) => l.text)).toEqual(["hi from s1"]);
    expect(s.frame).toBeNull();
    expect(s.status).toBe("listening");
    expect(s.stats).toBeNull();
    expect(s.lastMsgAt).toBe(3); // the dropped lines did not touch the page
    const picked = follow(s, "s2");
    expect(picked).toMatchObject({ session: "s2", sessionStartedAt: 3, lang: "ta", status: "thinking", transcript: [], frame: null, activity: [] });
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

describe("reduce: everyone using AIRA (the session list)", () => {
  const start = (session: string, ts: number, extra: Record<string, unknown> = {}) =>
    mon({ kind: "session", state: "start", lang: "ta", uid: `u-${session}`, session, ts, ...extra } as MonInput);

  it("a session start carries the server's startedAt into the list and the page clock, falling back to the message time", () => {
    const s = apply([start("s1", 50, { startedAt: 10 }), start("s2", 60), start("s3", 70, { startedAt: "soon" })]);
    expect(s.sessions.s1).toEqual({ id: "s1", uid: "u-s1", lang: "ta", startedAt: 10, lastSeenAt: 50, status: "idle" });
    expect(s.sessions.s2.startedAt).toBe(60);
    expect(s.sessions.s3.startedAt).toBe(70); // a startedAt that is not a number is ignored
    expect(s).toMatchObject({ session: "s1", sessionStartedAt: 10 });
    expect(follow(s, "s2").sessionStartedAt).toBe(60);
  });

  it("a status from a session it is not following updates that entry only; the page and lastMsgAt are untouched", () => {
    const s0 = apply([start("s1", 1), start("s2", 2), mon({ kind: "status", state: "guiding", ts: 3 })]);
    expect(s0.sessions.s1.status).toBe("guiding"); // the followed one's entry says what the page says
    const s = apply([
      mon({ kind: "status", state: "thinking", session: "s2", ts: 9 }),
      mon({ kind: "frame", jpeg_b64: "S2", w: 1, h: 1, purpose: "ask", session: "s2", ts: 10 }),
      mon({ kind: "event", name: "order_sent", detail: {}, session: "s2", ts: 11 }),
    ], s0);
    expect(s.sessions.s2).toMatchObject({ status: "thinking", lastSeenAt: 9 });
    expect(s.sessions.s1).toBe(s0.sessions.s1);
    expect(s).toMatchObject({ session: "s1", status: "guiding", frame: null, activity: s0.activity, lastMsgAt: 3 });
    expect(reduce(s, mon({ kind: "status", state: "idle", session: "nobody", ts: 12 }))).toBe(s); // not listed: nothing to update
  });

  it("when the followed session ends, the newest of the rest is followed from a clean slate; sessions leave the list as they end", () => {
    const s0 = apply([
      start("old", 1, { startedAt: 100 }),
      start("mid", 2, { startedAt: 300 }),
      start("new", 3, { startedAt: 200 }), // announced last, but began before "mid"
      mon({ kind: "transcript", role: "owner", text: "hello", lang: "ta", final: true, session: "old", ts: 4 }),
      mon({ kind: "frame", jpeg_b64: "OLD", w: 1, h: 1, purpose: "ask", session: "old", ts: 5 }),
      mon({ kind: "status", state: "thinking", session: "mid", ts: 6 }),
    ]);
    expect(s0.session).toBe("old");
    const s1 = reduce(s0, mon({ kind: "session", state: "end", lang: "ta", session: "old", ts: 7 }));
    expect(Object.keys(s1.sessions).sort()).toEqual(["mid", "new"]);
    expect(s1).toMatchObject({ session: "mid", sessionStartedAt: 300, lang: "ta", status: "thinking", frame: null, transcript: [], activity: [], lastMsgAt: 7 });
    expect(reduce(s1, mon({ kind: "frame", jpeg_b64: "MID", w: 1, h: 1, purpose: "ask", session: "mid", ts: 8 })).frame?.src).toContain("MID");
    const s2 = reduce(s1, mon({ kind: "session", state: "end", lang: "ta", session: "mid", ts: 9 }));
    expect(s2).toMatchObject({ session: "new", sessionStartedAt: 200 });
    const s3 = reduce(s2, mon({ kind: "session", state: "end", lang: "ta", session: "new", ts: 10 }));
    expect(s3).toMatchObject({ session: null, sessions: {}, status: "idle" });
  });

  it("lists people newest first and names them: the uid, else a short id; room cameras are never people", () => {
    const s = apply([start("a1", 1, { startedAt: 100 }), start("b2b2b2b2", 2, { startedAt: 300, uid: undefined }), start("c3", 3, { startedAt: 200 })]);
    expect(listSessions(s.sessions).map((p) => p.id)).toEqual(["b2b2b2b2", "c3", "a1"]);
    expect(sessionLabel(s.sessions.a1)).toBe("u-a1");
    expect(sessionLabel(s.sessions.b2b2b2b2)).toBe("b2b2b2");
    expect(sessionLabel({ id: "cam123456", uid: "camera:front" })).toBe("cam123");
    const withCam = reduce(s, mon({ kind: "session", state: "start", lang: "en", uid: "camera:front", session: "cam1", ts: 4 }));
    expect(Object.keys(withCam.sessions)).not.toContain("cam1");
    expect(sinceText(T_IST, T_IST + 59_000)).toBe("just now");
    expect(sinceText(T_IST, T_IST + 60_000)).toBe("since 12:00 pm");
  });
});

describe("monitorUrl", () => {
  it("replaces /ws with /monitor and adds the shop, nothing else (the token never goes in the URL)", () => {
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

describe("reconnect backoff, sign-in and refusal", () => {
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
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const T0 = 1_760_000_000_000;
  const token = (exp: number, tag = "t") => jwt({ sub: "u1", exp: Math.floor(exp / 1000), tag });
  const authLine = (idToken: string) => JSON.stringify({ t: "auth", idToken });

  function rig(getToken = vi.fn(async (_force?: boolean) => token(T0 + 3_600_000))) {
    const sockets: ReturnType<typeof fakeSocket>[] = [];
    const timers: Array<{ fn: () => void; ms: number; id: number }> = [];
    const cleared: number[] = [];
    const onMsg = vi.fn();
    const onConn = vi.fn();
    const port = { attach: vi.fn(), detach: vi.fn(), receive: vi.fn() };
    const stop = openMonitor("ws://x/monitor?shop=s", onMsg, onConn, {
      connect: () => { const s = fakeSocket(); sockets.push(s); return s; },
      setTimer: (fn: () => void, ms: number) => { const id = timers.length + 1; timers.push({ fn, ms, id }); return id; },
      clearTimer: (t: unknown) => { cleared.push(t as number); },
      signal: port, getToken, now: () => T0,
    });
    return { sockets, timers, cleared, onMsg, onConn, port, stop, getToken };
  }

  it("sends the ID token as its first message, reports live and attaches the signal port only after it, and schedules the refresh", async () => {
    const r = rig();
    expect(r.onConn).toHaveBeenLastCalledWith("connecting", 0);
    r.sockets[0].onopen!({});
    expect(r.sockets[0].sent).toEqual([]); // the token is fetched first
    expect(r.port.attach).not.toHaveBeenCalled();
    await tick();
    expect(r.getToken).toHaveBeenCalledWith();
    expect(r.sockets[0].sent).toEqual([authLine(token(T0 + 3_600_000))]);
    expect(r.onConn).toHaveBeenLastCalledWith("live", 0);
    expect(r.port.attach).toHaveBeenCalledTimes(1);
    expect(r.timers.map((t) => t.ms)).toEqual([55 * 60_000]); // exp - 5 min
    const send = r.port.attach.mock.calls[0][0] as (m: unknown) => boolean;
    expect(send({ t: "rtc", kind: "want", to: "s1" })).toBe(true);
    expect(r.sockets[0].sent[0]).toContain('"t":"auth"'); // auth stays first
    expect(r.sockets[0].sent[1]).toBe(JSON.stringify({ t: "rtc", kind: "want", to: "s1" }));
  });

  it("refreshes with a freshly minted token before expiry, on the same socket, and books the next refresh from it", async () => {
    let n = 0;
    const getToken = vi.fn(async (force?: boolean) => token(T0 + (force ? 7_200_000 : 3_600_000), `t${n++}`));
    const r = rig(getToken);
    r.sockets[0].onopen!({});
    await tick();
    expect(r.timers).toHaveLength(1);
    r.timers[0].fn();
    await tick();
    expect(getToken).toHaveBeenLastCalledWith(true);
    expect(r.sockets[0].sent).toHaveLength(2);
    expect(JSON.parse(r.sockets[0].sent[1])).toMatchObject({ t: "auth" });
    expect(r.sockets[0].sent[1]).toContain(token(T0 + 7_200_000, "t1"));
    expect(r.timers.map((t) => t.ms)).toEqual([55 * 60_000, 115 * 60_000]);
    expect(r.sockets).toHaveLength(1); // no reconnect: the refresh rides the open socket
    r.sockets[0].onclose!({ code: 1006 });
    expect(r.cleared).toContain(2); // the pending refresh is cancelled with the socket
  });

  it("drops messages that arrive before the token went out, and hands rtc lines to the port after", async () => {
    const r = rig();
    r.sockets[0].onopen!({});
    r.sockets[0].onmessage!({ data: JSON.stringify({ t: "mon", kind: "status", session: "s", ts: 1, state: "thinking" }) });
    expect(r.onMsg).not.toHaveBeenCalled();
    await tick();
    r.sockets[0].onmessage!({ data: JSON.stringify({ t: "mon", kind: "status", session: "s", ts: 1, state: "thinking" }) });
    r.sockets[0].onmessage!({ data: "not json" });
    const offer = { t: "rtc", kind: "offer", from: "s1", to: "m1", sdp: "v=0" };
    r.sockets[0].onmessage!({ data: JSON.stringify(offer) });
    expect(r.onMsg).toHaveBeenCalledTimes(1);
    expect(r.onMsg.mock.calls[0][0]).toMatchObject({ kind: "status", state: "thinking" });
    expect(r.port.receive).toHaveBeenCalledWith(offer);
    r.sockets[0].onclose!({ code: 1006 });
    expect(r.port.detach).toHaveBeenCalledTimes(1);
    const send = r.port.attach.mock.calls[0][0] as (m: unknown) => boolean;
    expect(send({ t: "rtc", kind: "bye", to: "s1" })).toBe(false); // a dead socket's send is refused
    r.stop();
    expect(r.port.detach).toHaveBeenCalledTimes(2);
  });

  it("reconnects after each ordinary close with growing delays, signs in again on every socket, resets after an open, and stops cleanly", async () => {
    const r = rig();
    r.sockets[0].onclose!({ code: 1006 });
    expect(r.onConn).toHaveBeenLastCalledWith("offline", 0);
    expect(r.timers.map((t) => t.ms)).toEqual([1000]);
    r.timers[0].fn();
    r.sockets[1].onclose!({ code: 1006 });
    expect(r.timers.map((t) => t.ms)).toEqual([1000, 2000]);
    r.timers[1].fn();
    r.sockets[2].onopen!({});
    await tick();
    expect(r.sockets[2].sent).toEqual([authLine(token(T0 + 3_600_000))]); // every socket authenticates from scratch
    expect(r.onConn).toHaveBeenLastCalledWith("live", 0);
    r.sockets[2].onclose!({ code: 1006 });
    expect(r.timers.map((t) => t.ms)).toEqual([1000, 2000, 55 * 60_000, 1000]); // attempt count reset by the open
    r.stop();
    r.timers[3].fn();
    expect(r.sockets).toHaveLength(3); // no dial after stop
  });

  it("closes as unauthenticated when no token can be had", async () => {
    const r = rig(vi.fn(async () => { throw new Error("signed out"); }));
    r.sockets[0].onopen!({});
    await tick();
    expect(r.sockets[0].closed).toBe(true);
    expect(r.sockets[0].sent).toEqual([]);
    expect(r.onConn).toHaveBeenLastCalledWith("unauthenticated", 0);
    expect(r.sockets).toHaveLength(1);
  });

  it("4403 before the token went out is a refusal of the page (Origin or shop); after it, the account is not allowed; neither retries", async () => {
    const a = rig();
    a.sockets[0].onclose!({ code: 4403 });
    expect(a.onConn).toHaveBeenLastCalledWith("rejected", 0);
    const b = rig();
    b.sockets[0].onclose!({ code: 1008 });
    expect(b.onConn).toHaveBeenLastCalledWith("rejected", 0);
    const c = rig();
    c.sockets[0].onopen!({});
    await tick();
    c.sockets[0].onclose!({ code: 4403 });
    expect(c.onConn).toHaveBeenLastCalledWith("unauthorized", 0);
    expect(c.sockets).toHaveLength(1);
    expect(c.timers.map((t) => t.ms)).toEqual([55 * 60_000]); // only the (now cancelled) refresh: no reconnect timer
    expect(c.cleared).toEqual([1]);
  });

  it("4401 (the server did not accept the token) is reported as unauthenticated without a retry of its own", async () => {
    const r = rig();
    r.sockets[0].onopen!({});
    await tick();
    r.sockets[0].onclose!({ code: 4401 });
    expect(r.onConn).toHaveBeenLastCalledWith("unauthenticated", 0);
    expect(r.sockets).toHaveLength(1);
  });

  it("never puts the token anywhere but the socket", async () => {
    const r = rig();
    r.sockets[0].onopen!({});
    await tick();
    const t = token(T0 + 3_600_000);
    expect(r.sockets[0].sent[0]).toContain(t);
    expect(location.href).not.toContain(t);
    expect(sessionStorage.length).toBe(0);
    expect(Object.keys(localStorage)).toEqual([]);
  });

  describe("without a token source (the default build: no sign-in)", () => {
    function openRig() {
      const sockets: ReturnType<typeof fakeSocket>[] = [];
      const timers: Array<{ fn: () => void; ms: number }> = [];
      const onMsg = vi.fn();
      const onConn = vi.fn();
      const port = { attach: vi.fn(), detach: vi.fn(), receive: vi.fn() };
      const stop = openMonitor("ws://x/monitor?shop=s", onMsg, onConn, {
        connect: () => { const s = fakeSocket(); sockets.push(s); return s; },
        setTimer: (fn: () => void, ms: number) => { timers.push({ fn, ms }); return timers.length; },
        clearTimer: () => {},
        signal: port, now: () => T0,
      });
      return { sockets, timers, onMsg, onConn, port, stop };
    }

    it("is live the moment the socket opens: nothing sent, the port attached, messages delivered, no timer booked", () => {
      const r = openRig();
      expect(r.onConn).toHaveBeenLastCalledWith("connecting", 0);
      r.sockets[0].onmessage!({ data: JSON.stringify({ t: "mon", kind: "status", session: "s", ts: 1, state: "thinking" }) });
      expect(r.onMsg).not.toHaveBeenCalled(); // not open yet
      r.sockets[0].onopen!({});
      expect(r.onConn).toHaveBeenLastCalledWith("live", 0);
      expect(r.sockets[0].sent).toEqual([]);
      expect(r.port.attach).toHaveBeenCalledTimes(1);
      expect(r.timers).toEqual([]);
      r.sockets[0].onmessage!({ data: JSON.stringify({ t: "mon", kind: "session", session: "s1", ts: 1, state: "start", lang: "ta", startedAt: 0 }) });
      const offer = { t: "rtc", kind: "offer", from: "s1", to: "m1", sdp: "v=0" };
      r.sockets[0].onmessage!({ data: JSON.stringify(offer) });
      expect(r.onMsg).toHaveBeenCalledTimes(1);
      expect(r.onMsg.mock.calls[0][0]).toMatchObject({ kind: "session", state: "start", startedAt: 0 });
      expect(r.port.receive).toHaveBeenCalledWith(offer);
      const send = r.port.attach.mock.calls[0][0] as (m: unknown) => boolean;
      expect(send({ t: "rtc", kind: "want", to: "s1" })).toBe(true);
      expect(r.sockets[0].sent).toEqual([JSON.stringify({ t: "rtc", kind: "want", to: "s1" })]); // the only thing ever sent
      expect(r.sockets[0].sent.join("")).not.toContain('"auth"');
      r.stop();
      expect(r.sockets[0].closed).toBe(true);
      expect(r.port.detach).toHaveBeenCalled();
    });

    it("4403 and 1008 are the server's refusal of the page (rejected), before and after the open; 4401 still reads unauthenticated; other closes retry", () => {
      const a = openRig();
      a.sockets[0].onclose!({ code: 4403 });
      expect(a.onConn).toHaveBeenLastCalledWith("rejected", 0);
      expect(a.sockets).toHaveLength(1);
      const b = openRig();
      b.sockets[0].onopen!({});
      b.sockets[0].onclose!({ code: 4403 });
      expect(b.onConn).toHaveBeenLastCalledWith("rejected", 0); // never "unauthorized": no account was offered
      expect(b.timers).toEqual([]);
      const c = openRig();
      c.sockets[0].onopen!({});
      c.sockets[0].onclose!({ code: 1008 });
      expect(c.onConn).toHaveBeenLastCalledWith("rejected", 0);
      const d = openRig();
      d.sockets[0].onclose!({ code: 4401 });
      expect(d.onConn).toHaveBeenLastCalledWith("unauthenticated", 0);
      const e = openRig();
      e.sockets[0].onopen!({});
      e.sockets[0].onclose!({ code: 1006 });
      expect(e.onConn).toHaveBeenLastCalledWith("offline", 0);
      expect(e.timers.map((t) => t.ms)).toEqual([1000]);
      e.timers[0].fn();
      e.sockets[1].onopen!({});
      expect(e.sockets[1].sent).toEqual([]);
      expect(e.onConn).toHaveBeenLastCalledWith("live", 0);
    });
  });
});
