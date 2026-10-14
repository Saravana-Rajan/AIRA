// The Live page's model: a pure reducer over /monitor messages, coordinate mapping for the camera
// overlay, the WebSocket URL and a reconnecting client. By default (no token source) the socket is live as
// soon as it opens and the server sends every live session of the shop at once: no sign-in, no key. Only the
// VITE_MONITOR_AUTH=1 build gives the client a token source; then the first thing it sends on every socket is
// {t:"auth", idToken} (the viewer's Firebase ID token, from lib/viewerAuth) and the server verifies it and
// its viewer allowlist before it sends a single line, closing with 4401 / 4403 otherwise.
// Nothing here touches the DOM except the client's WebSocket/timer hooks, which are injected so tests drive them.
import {
  MONITOR_CLOSE_UNAUTHENTICATED, MONITOR_CLOSE_UNAUTHORIZED, NORM_MAX, parseMonitorFeedMsg,
  type Box, type MonitorAuthMsg, type MonitorMsg, type MonitorRole, type MonitorState, type Point, type VisionObject, type VisionSource,
} from "@aira/contracts";
import { formatTimeIST } from "./format";
import { advanceTask, type Task } from "./liveTask";
import type { SignalPort } from "./rtcViewer";
import { refreshDelayMs, tokenExp } from "./viewerAuth";

export type { MonitorMsg, MonitorRole, MonitorState, VisionObject, VisionSource };

// ---------- state ----------
export interface TranscriptLine { id: number; role: MonitorRole; text: string; lang: string; final: boolean; ts: number }
export interface ActivityItem { id: number; name: string; detail: string; ts: number; tag: ActivityTag }
export type ActivityTag = "sam" | "aira" | "customer" | "tool" | "vision" | "order";

/** One readable line from an event's detail object: the prompt, text, target or summary when present, else key: value pairs. */
export function detailText(name: string, d: Record<string, unknown>): string {
  const pick = (k: string) => (typeof d[k] === "string" && (d[k] as string).trim() ? (d[k] as string) : null);
  const counted = typeof d.counted === "number" ? d.counted : null;
  if (counted !== null) return typeof d.expected === "number" ? `Counted ${counted}/${d.expected}` : `Counted ${counted}`;
  if (name.startsWith("confirm") && typeof d.answer === "string") return `Answer: ${d.answer}`;
  const direct = pick("prompt") ?? pick("text") ?? pick("summary") ?? pick("say") ?? pick("target") ?? pick("tool") ?? pick("label") ?? pick("name");
  if (direct) return direct;
  const parts = Object.entries(d)
    .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    .map(([k, v]) => `${k} ${String(v)}`);
  return parts.slice(0, 3).join(", ");
}

/** Who an activity row belongs to, for its tag: the tool runtime, vision, orders, AIRA or the person. */
export function activityTag(name: string, d: Record<string, unknown>): ActivityTag {
  if (name === "tool_call") {
    const tool = typeof d.tool === "string" ? d.tool : "";
    if (/order|supplier|vendor/.test(tool)) return "order";
    if (/camera|describe|product_find|identify|count|point|memory_where/.test(tool)) return "vision";
    return "tool";
  }
  if (/^(guidance|target|found|lost|vision|count|point)/.test(name)) return "vision";
  if (/order|notify|vendor|sent/.test(name)) return "order";
  if (/^confirm|^state|^notify|^error|^session/.test(name)) return "aira";
  if (name === "client_event") return "sam";
  return "aira";
}

export interface Frame { src: string; w: number; h: number; purpose: string; ts: number }
/** One person using AIRA now: a phone session the shop announced (room cameras live in `rooms`, not here). */
export interface SessionInfo {
  id: string;
  uid: string | null;
  lang: string;
  /** When the phone session began (the server's `startedAt`, else the announcement's time). */
  startedAt: number;
  /** The last status or transcript heard from it (its start before any). */
  lastSeenAt: number;
  /** What AIRA is doing for this person, from its own `status` lines; "idle" until the first one. */
  status: MonitorState;
}
export interface Stats { turns: number; er2Calls: number; flashCalls: number; visionMs: number | null; fps: number; ts: number }
export interface VisionLayer { objects: VisionObject[]; ts: number }
export interface LiveState {
  /**
   * The session the page follows (Sam's phone); room cameras are kept apart in `rooms`. Every other field
   * below `session` belongs to this one session: messages from any other session are ignored until the user
   * picks it (`follow`), and everything is cleared when it ends.
   */
  session: string | null;
  /** The shop's live phone sessions, by id, as announced by `session` start/end; the user may pick one. */
  sessions: Record<string, SessionInfo>;
  sessionStartedAt: number | null;
  lang: string | null;
  status: MonitorState;
  frame: Frame | null;
  /** Gemini Robotics-ER 2 boxes (thick, labelled) and the on-device MediaPipe boxes (thin, fast). */
  er2: VisionLayer;
  mediapipe: VisionLayer;
  /** The last ER 2 message's context (camera_ask, describe, guidance, ...). */
  context: string | null;
  target: string | null;
  found: boolean;
  hand: { point: Point; near: boolean } | null;
  transcript: TranscriptLine[];
  activity: ActivityItem[];
  stats: Stats | null;
  task: Task | null;
  /** Room cameras (uid "camera:<name>"), by session id. */
  rooms: Record<string, { name: string; frame: Frame | null; mediapipe: VisionLayer; startedAt: number }>;
  lastMsgAt: number | null;
}

export const MAX_LINES = 200;
export const MAX_ACTIVITY = 120;
/** MediaPipe boxes older than this are not drawn: the phone sends them live, a stale box must not float. */
export const MEDIAPIPE_TTL_MS = 1500;
/** ER 2 boxes stay until the next result or this long, whichever comes first. */
export const ER2_TTL_MS = 8000;

const noLayer = (): VisionLayer => ({ objects: [], ts: 0 });

export function initialState(): LiveState {
  return {
    session: null, sessions: {}, sessionStartedAt: null, lang: null, status: "idle", frame: null, er2: noLayer(), mediapipe: noLayer(),
    context: null, target: null, found: false, hand: null, transcript: [], activity: [], stats: null, task: null, rooms: {},
    lastMsgAt: null,
  };
}

/** Everything the followed session owned, gone (frame, boxes, transcript, activity, stats, task); the shop's session list and room cameras stay. */
function clearedSession(s: LiveState, sessions: Record<string, SessionInfo> = s.sessions): LiveState {
  return { ...initialState(), sessions, rooms: s.rooms, lastMsgAt: s.lastMsgAt };
}

/** Follows session `id` (one the shop announced) with a clean slate; the same id, or an unknown one, changes nothing. */
export function follow(s: LiveState, id: string): LiveState {
  const info = s.sessions[id];
  if (!info || s.session === id) return s;
  return { ...clearedSession(s), session: id, sessionStartedAt: info.startedAt, lang: info.lang, status: info.status };
}

/** The listed sessions newest first (the latest to start on top), as the people panel shows them. */
export function listSessions(sessions: Record<string, SessionInfo>): SessionInfo[] {
  return Object.values(sessions).sort((a, b) => b.startedAt - a.startedAt || a.id.localeCompare(b.id));
}

/** A short name for a person: their uid when the phone sent one, else the first 6 characters of the session id. */
export function sessionLabel(info: Pick<SessionInfo, "id" | "uid">): string {
  return info.uid && !isCamera(info.uid) ? info.uid : info.id.slice(0, 6);
}

/** "since 14:02" (IST) for a session that began at `startedAt`, or "just now" within its first minute. */
export function sinceText(startedAt: number, now: number): string {
  return now - startedAt < 60_000 ? "just now" : `since ${formatTimeIST(startedAt)}`;
}

/** The server's `startedAt` (epoch ms when the phone session began) when the message carries a usable one, else `fallback`. */
function startedAtOf(m: { startedAt?: unknown }, fallback: number): number {
  return typeof m.startedAt === "number" && Number.isFinite(m.startedAt) ? m.startedAt : fallback;
}

/** `sessions` with the entry for `id` patched, or unchanged when `id` is not listed. */
function patchSession(sessions: Record<string, SessionInfo>, id: string, patch: Partial<SessionInfo>): Record<string, SessionInfo> {
  const info = sessions[id];
  return info ? { ...sessions, [id]: { ...info, ...patch } } : sessions;
}

let nextId = 1;
const id = () => nextId++;

const EVENT_LABELS: Record<string, string> = {
  guidance_start: "Searching", guide_start: "Searching", searching: "Searching",
  pointing: "Pointing", point: "Pointing", target: "Pointing",
  hand_near: "Hand near",
  guidance_found: "Found", found: "Found",
  guidance_lost: "Lost sight", lost: "Lost sight",
  guidance_stop: "Guidance stopped",
  order_sent: "Order sent",
  count: "Counted", count_update: "Counted",
  confirm_proposed: "Waiting for yes", confirm_resolved: "Answered", confirm_yes: "Confirm yes", confirm_no: "Confirm no",
  tool_call: "Tool call",
  notify: "Spoken to Sam",
  state: "State changed",
  client_event: "Phone event",
  session_start: "Sam's phone connected", session_end: "Session ended",
  room_start: "Room camera connected", room_end: "Room camera left",
};

/** A short, readable name for an event (the raw name when the list does not know it). */
export function eventLabel(name: string): string {
  return EVENT_LABELS[name] ?? name.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function push<T>(list: T[], item: T, max: number): T[] {
  const next = list.length >= max ? list.slice(list.length - max + 1) : list.slice();
  next.push(item);
  return next;
}

const isCamera = (uid: string | null | undefined) => typeof uid === "string" && uid.startsWith("camera:");
const roomName = (uid: string) => uid.slice("camera:".length) || "room";

function logActivity(s: LiveState, name: string, detail: string, ts: number, tag: ActivityTag): ActivityItem[] {
  return push(s.activity, { id: id(), name, detail, ts, tag }, MAX_ACTIVITY);
}

/**
 * Applies one monitor message. Partial transcript lines are kept one per speaker and replaced in place
 * by the next partial or final line from that speaker; final lines stay in arrival order. Messages from a
 * room camera's session only touch `rooms`.
 *
 * Session scope: the page follows one phone session. The first session heard is followed; a later one is
 * only listed in `sessions` until the user picks it (`follow`); its `status` lines keep its list entry's
 * status current and everything else from it is dropped (nothing of it touches the page or `lastMsgAt`).
 * When the followed session ends everything it produced is cleared (nothing of a session outlives it in
 * memory) and the newest of the remaining sessions is followed from a clean slate.
 */
export function reduce(s: LiveState, m: MonitorMsg): LiveState {
  const base = { ...s, lastMsgAt: m.ts };
  const room = s.rooms[m.session];
  if (room && m.kind !== "session") {
    if (m.kind === "frame") {
      return { ...base, rooms: { ...s.rooms, [m.session]: { ...room, frame: toFrame(m) } } };
    }
    if (m.kind === "vision" && m.source !== "er2") {
      return { ...base, rooms: { ...s.rooms, [m.session]: { ...room, mediapipe: { objects: m.objects, ts: m.ts } } } };
    }
    return base;
  }
  if (m.kind === "session") {
    const started = m.state === "start";
    if (isCamera(m.uid)) {
      const rooms = { ...s.rooms };
      if (started) rooms[m.session] = { name: roomName(m.uid!), frame: null, mediapipe: noLayer(), startedAt: m.ts };
      else delete rooms[m.session];
      return { ...base, rooms, activity: logActivity(s, started ? "room_start" : "room_end", roomName(m.uid!), m.ts, "vision") };
    }
    if (started) {
      const startedAt = startedAtOf(m, m.ts);
      const known = s.sessions[m.session];
      const info: SessionInfo = { id: m.session, uid: m.uid ?? null, lang: m.lang, startedAt, lastSeenAt: m.ts, status: known?.status ?? "idle" };
      const sessions = { ...s.sessions, [m.session]: info };
      if (s.session !== null && s.session !== m.session) return { ...base, sessions }; // another phone: listed, not followed
      // The first session, or the followed one announced again (the monitor reconnected): a fresh camera, the same conversation.
      const same = s.session === m.session;
      const kept = same ? s : clearedSession(s);
      return {
        ...clearedSession(s, sessions),
        session: m.session,
        sessionStartedAt: same && s.sessionStartedAt !== null ? s.sessionStartedAt : startedAt,
        lang: m.lang,
        status: "listening",
        transcript: kept.transcript,
        activity: logActivity(kept, "session_start", `Language ${m.lang}`, m.ts, "sam"),
        lastMsgAt: m.ts,
      };
    }
    const sessions = { ...s.sessions };
    delete sessions[m.session];
    if (s.session !== m.session) return { ...base, sessions }; // a session the page was not following ended
    const next = listSessions(sessions)[0]; // the newest of the rest takes the page, from scratch
    if (!next) return { ...clearedSession(s, sessions), lastMsgAt: m.ts };
    return { ...clearedSession(s, sessions), session: next.id, sessionStartedAt: next.startedAt, lang: next.lang, status: next.status, lastMsgAt: m.ts };
  }
  if (s.session === null) s = { ...s, session: m.session }; // nothing followed yet: this session is the one
  else if (s.session !== m.session) return reduceOther(s, m); // another session's line: only its list entry may change
  return reduceOwn(s, m);
}

/** A line from a listed session the page is not following: its status and last-seen time, nothing else. */
function reduceOther(s: LiveState, m: Exclude<MonitorMsg, { kind: "session" }>): LiveState {
  if (!s.sessions[m.session]) return s;
  if (m.kind === "status") return { ...s, sessions: patchSession(s.sessions, m.session, { status: m.state, lastSeenAt: m.ts }) };
  if (m.kind === "transcript") return { ...s, sessions: patchSession(s.sessions, m.session, { lastSeenAt: m.ts }) };
  return s;
}

function reduceOwn(s: LiveState, m: Exclude<MonitorMsg, { kind: "session" }>): LiveState {
  const sessions = m.kind === "transcript" ? patchSession(s.sessions, m.session, { lastSeenAt: m.ts }) : s.sessions;
  const base = { ...s, sessions, lastMsgAt: m.ts };
  switch (m.kind) {
    case "transcript": {
      const i = s.transcript.findIndex((l) => !l.final && l.role === m.role);
      const line: TranscriptLine = { id: i >= 0 ? s.transcript[i].id : id(), role: m.role, text: m.text, lang: m.lang, final: m.final, ts: m.ts };
      if (m.text.trim() === "" && !m.final) return base;
      if (i >= 0) {
        const t = s.transcript.slice();
        t[i] = line;
        return { ...base, transcript: t };
      }
      return { ...base, transcript: push(s.transcript, line, MAX_LINES) };
    }
    case "frame":
      return { ...base, frame: toFrame(m) };
    case "vision": {
      if (m.source !== "er2") {
        const next = { ...base, mediapipe: { objects: m.objects, ts: m.ts } };
        const danger = m.objects.filter((o) => o.severity === "danger");
        const hadDanger = s.mediapipe.objects.some((o) => o.severity === "danger");
        if (danger.length && !hadDanger) next.activity = logActivity(s, "obstacle", danger.map((o) => o.label).join(", "), m.ts, "vision");
        return next;
      }
      const found = m.found === true;
      const target = m.target ?? (m.context === "guidance" ? s.target : null);
      let activity = s.activity;
      if (found && !s.found && target) activity = logActivity(s, "found", target, m.ts, "vision");
      const next: LiveState = {
        ...base, er2: { objects: m.objects, ts: m.ts }, context: m.context ?? null, target, found, activity,
        status: found ? "found" : s.status,
      };
      next.task = advanceTask(s.task, { kind: "vision", target, found, context: m.context ?? null, objects: m.objects.length }, m.ts);
      return next;
    }
    case "hand":
      return { ...base, hand: { point: m.point, near: m.near } };
    case "event": {
      const next: LiveState = { ...base, activity: logActivity(s, m.name, detailText(m.name, m.detail), m.ts, activityTag(m.name, m.detail)) };
      next.task = advanceTask(s.task, { kind: "event", name: m.name, detail: m.detail }, m.ts);
      return next;
    }
    case "status": {
      const next: LiveState = {
        ...base, status: m.state, found: m.state === "found" ? s.found || s.target !== null : m.state === "guiding" ? false : s.found,
        sessions: patchSession(s.sessions, m.session, { status: m.state, lastSeenAt: m.ts }), // the list entry says the same
      };
      next.task = advanceTask(s.task, { kind: "status", state: m.state }, m.ts);
      return next;
    }
    case "stats":
      return { ...base, stats: { turns: m.turns, er2Calls: m.er2Calls, flashCalls: m.flashCalls, visionMs: m.visionMs, fps: m.fps, ts: m.ts } };
  }
  return base;
}

function toFrame(m: Extract<MonitorMsg, { kind: "frame" }>): Frame {
  return { src: `data:image/jpeg;base64,${m.jpeg_b64}`, w: m.w, h: m.h, purpose: m.purpose, ts: m.ts };
}

// ---------- overlay geometry ----------
export interface Rect { x: number; y: number; w: number; h: number }
export interface XY { x: number; y: number }

const px = (n: number) => Math.round(n * 100) / 100;

/** [y, x] in 0..1000 -> pixel position in a w x h box (to 0.01 px). */
export function toXY(p: Point, w: number, h: number): XY {
  return { x: px((p[1] / NORM_MAX) * w), y: px((p[0] / NORM_MAX) * h) };
}

/** [ymin, xmin, ymax, xmax] in 0..1000 -> pixel rect in a w x h box (to 0.01 px). */
export function toRect(b: Box, w: number, h: number): Rect {
  const [ymin, xmin, ymax, xmax] = b;
  return { x: px((xmin / NORM_MAX) * w), y: px((ymin / NORM_MAX) * h), w: px(((xmax - xmin) / NORM_MAX) * w), h: px(((ymax - ymin) / NORM_MAX) * h) };
}

export const centre = (b: Box): Point => [Math.round((b[0] + b[2]) / 2), Math.round((b[1] + b[3]) / 2)];

/** object: ER 2 (blue, thick) · fast: MediaPipe (grey, thin) · danger: red · target: blue, looked for · found: green with a check. */
export type Tone = "object" | "fast" | "danger" | "target" | "found";
export interface BoxShape { kind: "box"; label: string; rect: Rect; tone: Tone; score: number | null }
export interface DotShape { kind: "dot"; label: string; at: XY; tone: Tone | "hand" }
export interface LineShape { kind: "line"; from: XY; to: XY }
export type Shape = BoxShape | DotShape | LineShape;

const isTarget = (o: VisionObject, target: string | null) => target !== null && o.label.toLowerCase() === target.toLowerCase();

/**
 * What to draw over a w x h frame, back to front: the MediaPipe boxes (thin; red when the bump guard flags
 * them, and only while fresh), then every ER 2 object as a box (or a dot when it only has a point), the
 * guidance target in its own tone (green once found), the fingertip, and a dashed line from the hand to
 * the target while guiding.
 */
export function overlayShapes(
  s: Pick<LiveState, "er2" | "mediapipe" | "target" | "found" | "hand" | "status">, w: number, h: number, now?: number,
): Shape[] {
  const out: Shape[] = [];
  const fresh = (layer: VisionLayer, ttl: number) => now === undefined || layer.ts === 0 || now - layer.ts <= ttl;
  if (fresh(s.mediapipe, MEDIAPIPE_TTL_MS)) {
    for (const o of s.mediapipe.objects) {
      if (!o.box) continue;
      out.push({ kind: "box", label: o.label, rect: toRect(o.box, w, h), tone: o.severity === "danger" ? "danger" : "fast", score: o.score ?? null });
    }
  }
  let targetAt: XY | null = null;
  if (fresh(s.er2, ER2_TTL_MS)) {
    for (const o of s.er2.objects) {
      const tone: Tone = isTarget(o, s.target) ? (s.found ? "found" : "target") : o.severity === "danger" ? "danger" : "object";
      if (o.box) {
        out.push({ kind: "box", label: o.label, rect: toRect(o.box, w, h), tone, score: o.score ?? null });
        if (tone === "target" || tone === "found") targetAt = toXY(centre(o.box), w, h);
      } else if (o.point) {
        out.push({ kind: "dot", label: o.label, at: toXY(o.point, w, h), tone });
        if (tone === "target" || tone === "found") targetAt = toXY(o.point, w, h);
      }
    }
  }
  if (s.hand) {
    const at = toXY(s.hand.point, w, h);
    if (targetAt && !s.found && s.status === "guiding") out.push({ kind: "line", from: at, to: targetAt });
    out.push({ kind: "dot", label: "hand", at, tone: "hand" });
  }
  return out;
}

// ---------- URL ----------
/**
 * The /monitor WebSocket URL from the app's VITE_AIRA_LIVE_WS (wss://host/ws -> wss://host/monitor?shop=).
 * Nothing else goes in it: the credential travels in the first message, never the URL. Null when nothing
 * usable is configured.
 */
export function monitorUrl(env: Record<string, unknown>, shopId: string): string | null {
  const raw = typeof env.VITE_AIRA_LIVE_WS === "string" ? env.VITE_AIRA_LIVE_WS.trim() : "";
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (u.protocol !== "wss:" && u.protocol !== "ws:") return null;
    u.pathname = u.pathname.replace(/\/ws\/?$/, "") + "/monitor";
    u.pathname = u.pathname.replace(/\/{2,}/g, "/");
    u.search = "";
    u.searchParams.set("shop", shopId);
    return u.toString();
  } catch {
    return null;
  }
}

// ---------- reconnecting client ----------
export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_MAX_MS = 15_000;

/** Delay before reconnect attempt n (0-based): 1 s, 2 s, 4 s, ... capped at 15 s. */
export function backoffMs(attempt: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempt));
}

/**
 * waiting: no URL (or, in the sign-in build, no sign-in yet) · connecting: socket opening or signing in · live:
 * the feed runs · offline: closed, retrying · rejected: the server refused this page (Origin, shop or capacity;
 * every 4403 in the default build), no retry · unauthenticated: the server did not accept the token (4401) ·
 * unauthorized: the signed-in account is not on the server's viewer list (4403 after auth), no retry.
 */
export type Connection = "waiting" | "connecting" | "live" | "offline" | "rejected" | "unauthenticated" | "unauthorized";

/** The slice of WebSocket the client uses; a real WebSocket satisfies it, and tests pass a fake. */
export interface SocketLike {
  onopen: ((this: any, ev: any) => void) | null;
  onmessage: ((this: any, ev: { data: unknown }) => void) | null;
  onclose: ((this: any, ev: { code?: number }) => void) | null;
  onerror: ((this: any, ev: any) => void) | null;
  send(data: string): void;
  close(): void;
}

export interface ClientDeps {
  connect: (url: string) => SocketLike;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
  /** WebRTC signalling (rtcViewer): attached once a socket is authenticated, told every {t:"rtc"} line, detached on close. */
  signal?: SignalPort;
  /**
   * The viewer's Firebase ID token (lib/viewerAuth); `force` asks for a freshly minted one. Rejects when signed
   * out. Given only by the VITE_MONITOR_AUTH=1 build: without it no auth line is ever sent and a socket is live
   * as soon as it opens.
   */
  getToken?: (force?: boolean) => Promise<string>;
  /** The clock the refresh schedule reads (tests). */
  now?: () => number;
}

/** A close before the handshake completed: the server's Origin / shop / capacity guard (a policy close, or 4403 before any auth). */
const POLICY_CLOSE = 1008;

const authLine = (idToken: string): string => JSON.stringify({ t: "auth", idToken } satisfies MonitorAuthMsg);

/**
 * Keeps one /monitor socket open. Without a token source (deps.getToken, the default build) a socket is
 * "live" the moment it opens, the WebRTC signal port is attached and the feed is read at once: nothing is
 * sent first, nothing is scheduled. With a token source (the VITE_MONITOR_AUTH=1 build) the first message on
 * every open is {t:"auth", idToken}; only then is the connection "live" and the port attached. The token's
 * `exp` schedules a refresh (refreshDelayMs): a fresh token (getToken(true)) goes out as another auth message
 * on the same socket, and the next refresh is scheduled from it. Closes: 4403 (or a 1008 policy close)
 * before any auth line went out is "rejected" (Origin, shop or capacity; no retry), 4403 after it is
 * "unauthorized" (no retry), 4401 is "unauthenticated" (no retry here; the hook decides whether to try a
 * fresh token), a failing getToken closes the socket as "unauthenticated", and every other close waits
 * backoffMs(attempt) and connects again (the attempt count resets once a socket is live). stop() closes it
 * for good. The token never reaches the URL, storage or the console.
 */
export function openMonitor(
  url: string,
  onMsg: (m: MonitorMsg) => void,
  onConn: (c: Connection, attempt: number) => void,
  deps: ClientDeps,
): () => void {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  const now = deps.now ?? Date.now;
  const getToken = deps.getToken ?? null;
  let attempt = 0;
  let stopped = false;
  let sock: SocketLike | null = null;
  let timer: unknown = null;
  let refresh: unknown = null;

  const clearRefresh = () => {
    if (refresh !== null) clearTimer(refresh);
    refresh = null;
  };

  /** Sends `idToken` on `s` and books the next refresh from its expiry. */
  const sendAuth = (s: SocketLike, idToken: string): boolean => {
    if (stopped || sock !== s) return false;
    try {
      s.send(authLine(idToken));
    } catch {
      return false;
    }
    clearRefresh();
    const exp = tokenExp(idToken);
    if (exp !== null && getToken) {
      refresh = setTimer(() => {
        refresh = null;
        if (stopped || sock !== s) return;
        getToken(true).then(
          (fresh) => { sendAuth(s, fresh); },
          () => { closeAs(s, "unauthenticated"); },
        );
      }, refreshDelayMs(exp, now()));
    }
    return true;
  };

  const closeAs = (s: SocketLike, c: Connection) => {
    if (stopped || sock !== s) return;
    sock = null;
    clearRefresh();
    deps.signal?.detach();
    try {
      s.close();
    } catch {
      /* already closed */
    }
    onConn(c, attempt);
  };

  const dial = () => {
    if (stopped) return;
    onConn("connecting", attempt);
    let s: SocketLike;
    try {
      s = deps.connect(url);
    } catch {
      retry();
      return;
    }
    sock = s;
    let authSent = false;
    let live = false;
    const goLive = () => {
      live = true;
      attempt = 0;
      onConn("live", 0);
      deps.signal?.attach((m) => {
        if (sock !== s) return false;
        try {
          s.send(JSON.stringify(m));
          return true;
        } catch {
          return false;
        }
      });
    };
    s.onopen = () => {
      if (stopped || sock !== s) return;
      if (!getToken) {
        goLive(); // no sign-in in this build: the server streams from the open
        return;
      }
      getToken().then(
        (idToken) => {
          if (!sendAuth(s, idToken)) return;
          authSent = true;
          goLive();
        },
        () => { closeAs(s, "unauthenticated"); },
      );
    };
    s.onmessage = (ev) => {
      if (stopped || sock !== s || !live || typeof ev.data !== "string") return;
      const m = parseMonitorFeedMsg(ev.data);
      if (!m) return;
      if (m.t === "rtc") deps.signal?.receive(m);
      else onMsg(m);
    };
    s.onerror = () => {};
    s.onclose = (ev) => {
      if (stopped || sock !== s) return;
      sock = null;
      clearRefresh();
      deps.signal?.detach();
      const code = ev?.code ?? 0;
      if (code === MONITOR_CLOSE_UNAUTHENTICATED) {
        onConn("unauthenticated", attempt);
        return;
      }
      if (code === MONITOR_CLOSE_UNAUTHORIZED || code === POLICY_CLOSE) {
        onConn(authSent ? "unauthorized" : "rejected", attempt);
        return;
      }
      retry();
    };
  };

  const retry = () => {
    if (stopped) return;
    onConn("offline", attempt);
    const wait = backoffMs(attempt);
    attempt += 1;
    timer = setTimer(() => {
      timer = null;
      dial();
    }, wait);
  };

  dial();
  return () => {
    stopped = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
    clearRefresh();
    const s = sock;
    sock = null;
    deps.signal?.detach();
    try {
      s?.close();
    } catch {
      /* already closed */
    }
  };
}

// ---------- time ----------
/** "T+01:24" for a session that started `startedAt` ms ago (epoch ms both). */
export function sessionClock(startedAt: number | null, now: number): string {
  if (startedAt === null) return "T+00:00";
  const s = Math.max(0, Math.floor((now - startedAt) / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `T+${mm}:${ss}`;
}

/** "0.4s ago" / "12s ago" for the last frame; "—" before any frame. */
export function frameAge(frameTs: number | null, now: number): string {
  if (frameTs === null) return "—";
  const s = Math.max(0, (now - frameTs) / 1000);
  return s < 10 ? `${s.toFixed(1)}s ago` : `${Math.round(s)}s ago`;
}
