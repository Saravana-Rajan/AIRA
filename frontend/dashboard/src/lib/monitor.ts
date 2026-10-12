// The Live page's model: a pure reducer over /monitor messages, coordinate mapping for the camera
// overlay, the monitor key, the WebSocket URL and a reconnecting client. Nothing here touches the DOM
// except the client's WebSocket/timer hooks, which are injected so tests drive them.
import {
  NORM_MAX, parseMonitorMsg,
  type Box, type MonitorMsg, type MonitorRole, type MonitorState, type Point, type VisionObject,
} from "@aira/contracts";

export type { MonitorMsg, MonitorRole, MonitorState, VisionObject };

// ---------- state ----------
export interface TranscriptLine { id: number; role: MonitorRole; text: string; lang: string; final: boolean; ts: number }
export interface ActivityItem { id: number; name: string; detail: string; ts: number }

/** One readable line from an event's detail object: the prompt, text, target or summary when present, else key: value pairs. */
export function detailText(name: string, d: Record<string, unknown>): string {
  const pick = (k: string) => (typeof d[k] === "string" && (d[k] as string).trim() ? (d[k] as string) : null);
  const counted = typeof d.counted === "number" ? d.counted : null;
  if (counted !== null) return typeof d.expected === "number" ? `Counted ${counted}/${d.expected}` : `Counted ${counted}`;
  if (name.startsWith("confirm") && typeof d.answer === "string") return `Answer: ${d.answer}`;
  const direct = pick("prompt") ?? pick("text") ?? pick("summary") ?? pick("say") ?? pick("target") ?? pick("tool") ?? pick("label");
  if (direct) return direct;
  const parts = Object.entries(d)
    .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
    .map(([k, v]) => `${k} ${String(v)}`);
  return parts.slice(0, 3).join(", ");
}
export interface Frame { src: string; w: number; h: number; purpose: string; ts: number }
export interface LiveState {
  session: string | null;
  lang: string | null;
  status: MonitorState;
  frame: Frame | null;
  objects: VisionObject[];
  target: string | null;
  found: boolean;
  hand: { point: Point; near: boolean } | null;
  transcript: TranscriptLine[];
  activity: ActivityItem[];
  lastMsgAt: number | null;
}

export const MAX_LINES = 200;
export const MAX_ACTIVITY = 120;

export function initialState(): LiveState {
  return {
    session: null, lang: null, status: "idle", frame: null, objects: [], target: null, found: false, hand: null,
    transcript: [], activity: [], lastMsgAt: null,
  };
}

let nextId = 1;
const id = () => nextId++;

const EVENT_LABELS: Record<string, string> = {
  guidance_start: "Searching", guide_start: "Searching", searching: "Searching",
  pointing: "Pointing", point: "Pointing",
  hand_near: "Hand near",
  guidance_found: "Found", found: "Found",
  guidance_lost: "Lost sight", lost: "Lost sight",
  order_sent: "Order sent",
  count: "Counted", count_update: "Counted",
  confirm_proposed: "Waiting for yes", confirm_yes: "Confirm yes", confirm_no: "Confirm no",
  tool_call: "Tool call",
  session_start: "Sam's phone connected", session_end: "Session ended",
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

/**
 * Applies one monitor message. Partial transcript lines are kept one per speaker and replaced in place
 * by the next partial or final line from that speaker; final lines stay in arrival order.
 */
export function reduce(s: LiveState, m: MonitorMsg): LiveState {
  const base = { ...s, lastMsgAt: m.ts };
  switch (m.kind) {
    case "session": {
      const started = m.state === "start";
      const fresh = started ? { ...initialState(), transcript: s.transcript, activity: s.activity } : base;
      return {
        ...fresh,
        lastMsgAt: m.ts,
        session: started ? m.session : null,
        lang: m.lang,
        status: started ? "listening" : "idle",
        activity: push(s.activity, { id: id(), name: started ? "session_start" : "session_end", detail: started ? `Language ${m.lang}` : "", ts: m.ts }, MAX_ACTIVITY),
      };
    }
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
      return { ...base, frame: { src: `data:image/jpeg;base64,${m.jpeg_b64}`, w: m.w, h: m.h, purpose: m.purpose, ts: m.ts } };
    case "vision": {
      const found = m.found === true;
      const activity = found && !s.found && m.target
        ? push(s.activity, { id: id(), name: "found", detail: m.target, ts: m.ts }, MAX_ACTIVITY)
        : s.activity;
      return { ...base, objects: m.objects, target: m.target ?? null, found, activity, status: found ? "found" : s.status };
    }
    case "hand":
      return { ...base, hand: { point: m.point, near: m.near } };
    case "event":
      return { ...base, activity: push(s.activity, { id: id(), name: m.name, detail: detailText(m.name, m.detail), ts: m.ts }, MAX_ACTIVITY) };
    case "status":
      return { ...base, status: m.state, found: m.state === "found" ? s.found || s.target !== null : m.state === "guiding" ? false : s.found };
  }
  return base;
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

export type Tone = "object" | "target" | "found";
export interface BoxShape { kind: "box"; label: string; rect: Rect; tone: Tone }
export interface DotShape { kind: "dot"; label: string; at: XY; tone: Tone | "hand" }
export interface LineShape { kind: "line"; from: XY; to: XY }
export type Shape = BoxShape | DotShape | LineShape;

const isTarget = (o: VisionObject, target: string | null) => target !== null && o.label.toLowerCase() === target.toLowerCase();

/**
 * What to draw over a w x h frame: every object as a box (or a dot when it only has a point), the
 * guidance target in its own tone (green once found), the fingertip, and a dashed line from the hand
 * to the target while guiding.
 */
export function overlayShapes(s: Pick<LiveState, "objects" | "target" | "found" | "hand" | "status">, w: number, h: number): Shape[] {
  const out: Shape[] = [];
  let targetAt: XY | null = null;
  for (const o of s.objects) {
    const tone: Tone = isTarget(o, s.target) ? (s.found ? "found" : "target") : "object";
    if (o.box) {
      out.push({ kind: "box", label: o.label, rect: toRect(o.box, w, h), tone });
      if (tone !== "object") targetAt = toXY(centre(o.box), w, h);
    } else if (o.point) {
      out.push({ kind: "dot", label: o.label, at: toXY(o.point, w, h), tone });
      if (tone !== "object") targetAt = toXY(o.point, w, h);
    }
  }
  if (s.hand) {
    const at = toXY(s.hand.point, w, h);
    if (targetAt && !s.found && s.status === "guiding") out.push({ kind: "line", from: at, to: targetAt });
    out.push({ kind: "dot", label: "hand", at, tone: "hand" });
  }
  return out;
}

// ---------- key and URL ----------
export const KEY_STORAGE = "aira.monitorKey";
export const KEY_PARAM = "key";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** The monitor key from ?key=... (saved for the tab), else the one saved earlier in this tab, else null. */
export function readKey(search: string, storage: StorageLike | null | undefined): string | null {
  const fromUrl = new URLSearchParams(search).get(KEY_PARAM)?.trim() || null;
  if (fromUrl) {
    storeKey(fromUrl, storage);
    return fromUrl;
  }
  try {
    return storage?.getItem(KEY_STORAGE)?.trim() || null;
  } catch {
    return null;
  }
}

/** Remembers the key for this tab only; a blocked storage is not an error. */
export function storeKey(key: string | null, storage: StorageLike | null | undefined): void {
  try {
    if (key) storage?.setItem(KEY_STORAGE, key);
    else storage?.removeItem(KEY_STORAGE);
  } catch {
    /* private window or blocked storage: the key lives in memory only */
  }
}

/** ?key= stripped from a search string, so the key is not left in the address bar or history. */
export function stripKey(search: string): string {
  const p = new URLSearchParams(search);
  p.delete(KEY_PARAM);
  const q = p.toString();
  return q ? `?${q}` : "";
}

/**
 * The /monitor WebSocket URL from the app's VITE_AIRA_LIVE_WS (wss://host/ws -> wss://host/monitor?shop=&key=).
 * Null when nothing usable is configured.
 */
export function monitorUrl(env: Record<string, unknown>, shopId: string, key: string): string | null {
  const raw = typeof env.VITE_AIRA_LIVE_WS === "string" ? env.VITE_AIRA_LIVE_WS.trim() : "";
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (u.protocol !== "wss:" && u.protocol !== "ws:") return null;
    u.pathname = u.pathname.replace(/\/ws\/?$/, "") + "/monitor";
    u.pathname = u.pathname.replace(/\/{2,}/g, "/");
    u.search = "";
    u.searchParams.set("shop", shopId);
    u.searchParams.set("key", key);
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

/** waiting: no key or URL yet · connecting: socket opening · live: open · offline: closed, retrying · rejected: the key was refused. */
export type Connection = "waiting" | "connecting" | "live" | "offline" | "rejected";

/** The slice of WebSocket the client uses; a real WebSocket satisfies it, and tests pass a fake. */
export interface SocketLike {
  onopen: ((this: any, ev: any) => void) | null;
  onmessage: ((this: any, ev: { data: unknown }) => void) | null;
  onclose: ((this: any, ev: { code?: number }) => void) | null;
  onerror: ((this: any, ev: any) => void) | null;
  close(): void;
}

export interface ClientDeps {
  connect: (url: string) => SocketLike;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

/** WebSocket close codes the server uses to refuse the key: policy violation or auth-style. */
const REJECT_CODES = new Set([1008, 4401, 4403]);

/**
 * Keeps one /monitor socket open: on every close it waits backoffMs(attempt) and connects again,
 * resetting the attempt count once a socket opens. stop() closes it for good.
 */
export function openMonitor(
  url: string,
  onMsg: (m: MonitorMsg) => void,
  onConn: (c: Connection, attempt: number) => void,
  deps: ClientDeps,
): () => void {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  let attempt = 0;
  let stopped = false;
  let sock: SocketLike | null = null;
  let timer: unknown = null;

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
    s.onopen = () => {
      if (stopped) return;
      attempt = 0;
      onConn("live", 0);
    };
    s.onmessage = (ev) => {
      if (stopped || typeof ev.data !== "string") return;
      const m = parseMonitorMsg(ev.data);
      if (m) onMsg(m);
    };
    s.onerror = () => {};
    s.onclose = (ev) => {
      if (stopped || sock !== s) return;
      sock = null;
      if (REJECT_CODES.has(ev?.code ?? 0)) {
        onConn("rejected", attempt);
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
    const s = sock;
    sock = null;
    try {
      s?.close();
    } catch {
      /* already closed */
    }
  };
}
