// WebSocket protocol. Mirrored in backend/aira_live/contracts.py and tested
// against the shared fixtures in ../fixtures. Audio: client 16 kHz PCM16 base64,
// server 24 kHz PCM16 base64. Coordinates: point [y, x], box [ymin, xmin, ymax, xmax],
// integers normalised 0..1000.
import {
  CLIENT_EVENT_NAMES, CONFIRM_ANSWERS, CONFIRM_KINDS, CUE_NAMES, ENTITIES, ERROR_CODES, FRAME_PURPOSES, LANGS,
  NORM_MAX, TRANSCRIPT_ROLES, VERBOSITIES,
  type ClientEventName, type ConfirmAnswer, type ConfirmKind, type CueName, type Entity, type ErrorCode,
  type FramePurpose, type Lang, type TranscriptRole, type Verbosity,
} from "./enums";

export type Point = [number, number];
export type Box = [number, number, number, number];

// ---------- client -> server ----------
export type ClientMsg =
  | { t: "hello"; uid: string; idToken?: string; shopId: string; lang: Lang; verbosity: Verbosity; appVersion: string; resume?: string }
  | { t: "audio"; pcm16: string }
  | { t: "frame"; id: string; jpeg: string; w: number; h: number; ts: number; purpose: FramePurpose }
  | { t: "text"; text: string }
  | { t: "event"; name: ClientEventName; data: Record<string, unknown> }
  | { t: "confirm"; id: string; answer: ConfirmAnswer; value?: string }
  | { t: "stop" }
  // A diagnostic line from the phone, written to Cloud Logging by the server (aira_live.diag) as
  // jsonPayload.aira="client". tag names what happened ("build", "camera", "tilt", "frame", "hint", "speak");
  // data is small metadata, never audio or image bytes. The server keeps at most 2 KB of it and 20 lines a
  // second per socket, and never answers it.
  | { t: "log"; tag: string; data: Record<string, unknown> };

// ---------- server -> client ----------
export type ServerMsg =
  | { t: "ready"; sessionId: string; resume: string; mock: boolean }
  | { t: "audio"; pcm24: string }
  // lang: the language AIRA detected from the speaker's last finished utterance (any code: "ta", "ja", "en",
  // ...), so the app's labels can follow. Absent on partial transcripts and before anyone has spoken.
  // unclear: a finished user transcript that looks like a language the shop does not usually speak (shop doc
  // "languages"); probably misheard, so AIRA asks to repeat instead of answering it. The caption marks it.
  | { t: "transcript"; role: TranscriptRole; text: string; final: boolean; lang?: string; unclear?: boolean }
  | { t: "request_frame"; purpose: FramePurpose; burst?: { count: number; intervalMs: number }; continuous?: boolean; stop?: boolean }
  | { t: "target"; frameId: string; label: string; point: Point; box?: Box; confidence: number }
  | { t: "count"; sku: string; counted: number; expected: number | null; final: boolean; confidence: number }
  | { t: "state"; entity: Entity; id: string; state: string; summary: string }
  | { t: "confirm"; id: string; kind: ConfirmKind; prompt: string }
  | { t: "cue"; cue: CueName }
  | { t: "error"; code: ErrorCode; say: string };

export type ClientMsgType = ClientMsg["t"];
export type ServerMsgType = ServerMsg["t"];
export type ClientMsgOf<T extends ClientMsgType> = Extract<ClientMsg, { t: T }>;
export type ServerMsgOf<T extends ServerMsgType> = Extract<ServerMsg, { t: T }>;

export const CLIENT_MSG_TYPES = Object.freeze(["hello", "audio", "frame", "text", "event", "confirm", "stop", "log"] as const);
export const SERVER_MSG_TYPES = Object.freeze([
  "ready", "audio", "transcript", "request_frame", "target", "count", "state", "confirm", "cue", "error",
] as const);

// Compile-time check that the type lists match the unions exactly.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const _clientTypesMatch: Same<(typeof CLIENT_MSG_TYPES)[number], ClientMsgType> = true;
const _serverTypesMatch: Same<(typeof SERVER_MSG_TYPES)[number], ServerMsgType> = true;
void _clientTypesMatch;
void _serverTypesMatch;

// ---------- guards ----------
type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
const isStr = (x: unknown): x is string => typeof x === "string";
const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const isInt = (x: unknown): x is number => isNum(x) && Number.isInteger(x);
const isBool = (x: unknown): x is boolean => typeof x === "boolean";
const inList = (list: readonly string[], x: unknown): boolean => isStr(x) && list.includes(x);
const optional = (x: unknown, check: (v: unknown) => boolean): boolean => x === undefined || check(x);
const isNorm = (n: unknown): boolean => isInt(n) && n >= 0 && n <= NORM_MAX;
const isPoint = (p: unknown): p is Point => Array.isArray(p) && p.length === 2 && p.every(isNorm);
const isBox = (b: unknown): b is Box => Array.isArray(b) && b.length === 4 && b.every(isNorm);
const isBurst = (b: unknown): boolean => isObj(b) && isInt(b.count) && isInt(b.intervalMs);

export function isClientMsg(x: unknown): x is ClientMsg {
  if (!isObj(x) || !inList(CLIENT_MSG_TYPES, x.t)) return false;
  switch (x.t as ClientMsgType) {
    case "hello":
      return isStr(x.uid) && optional(x.idToken, isStr) && isStr(x.shopId) && inList(LANGS, x.lang) &&
        inList(VERBOSITIES, x.verbosity) && isStr(x.appVersion) && optional(x.resume, isStr);
    case "audio":
      return isStr(x.pcm16);
    case "frame":
      return isStr(x.id) && isStr(x.jpeg) && isInt(x.w) && isInt(x.h) && isNum(x.ts) && inList(FRAME_PURPOSES, x.purpose);
    case "text":
      return isStr(x.text);
    case "event":
      return inList(CLIENT_EVENT_NAMES, x.name) && isObj(x.data);
    case "confirm":
      return isStr(x.id) && inList(CONFIRM_ANSWERS, x.answer) && optional(x.value, isStr);
    case "stop":
      return true;
    case "log":
      return isStr(x.tag) && isObj(x.data);
  }
  return false;
}

export function isServerMsg(x: unknown): x is ServerMsg {
  if (!isObj(x) || !inList(SERVER_MSG_TYPES, x.t)) return false;
  switch (x.t as ServerMsgType) {
    case "ready":
      return isStr(x.sessionId) && isStr(x.resume) && isBool(x.mock);
    case "audio":
      return isStr(x.pcm24);
    case "transcript":
      return inList(TRANSCRIPT_ROLES, x.role) && isStr(x.text) && isBool(x.final) && optional(x.lang, isStr) &&
        optional(x.unclear, isBool);
    case "request_frame":
      return inList(FRAME_PURPOSES, x.purpose) && optional(x.burst, isBurst) &&
        optional(x.continuous, isBool) && optional(x.stop, isBool);
    case "target":
      return isStr(x.frameId) && isStr(x.label) && isPoint(x.point) && optional(x.box, isBox) && isNum(x.confidence);
    case "count":
      return isStr(x.sku) && isInt(x.counted) && (x.expected === null || isNum(x.expected)) && isBool(x.final) &&
        isNum(x.confidence);
    case "state":
      return inList(ENTITIES, x.entity) && isStr(x.id) && isStr(x.state) && isStr(x.summary);
    case "confirm":
      return isStr(x.id) && inList(CONFIRM_KINDS, x.kind) && isStr(x.prompt);
    case "cue":
      return inList(CUE_NAMES, x.cue);
    case "error":
      return inList(ERROR_CODES, x.code) && isStr(x.say);
  }
  return false;
}

/** Parse one WebSocket text frame from the server. Returns null for bad JSON or unknown/invalid messages. */
export function parseServerMsg(raw: string): ServerMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isServerMsg(v) ? v : null;
  } catch {
    return null;
  }
}

/** Parse one WebSocket text frame from the client. Returns null for bad JSON or unknown/invalid messages. */
export function parseClientMsg(raw: string): ClientMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isClientMsg(v) ? v : null;
  } catch {
    return null;
  }
}

// ---------- server -> monitor (GET /monitor?shop=<id>&key=<AIRA_MONITOR_KEY>, read-only) ----------
// A projector view of one shop's live sessions. Every message is {t:"mon", kind, session, ts, ...}.
// Frames are sent only while a monitor is connected (<= 480 px wide, quality 0.6, <= 4 fps a session).
// Mirrored in backend/aira_live/contracts.py; fixtures in ../fixtures/monitor_msgs.json.
export const MONITOR_KINDS = Object.freeze(["session", "transcript", "frame", "vision", "hand", "event", "status"] as const);
export type MonitorKind = (typeof MONITOR_KINDS)[number];
export const MONITOR_ROLES = Object.freeze(["owner", "customer", "aira"] as const);
export type MonitorRole = (typeof MONITOR_ROLES)[number];
export const MONITOR_SESSION_STATES = Object.freeze(["start", "end"] as const);
export type MonitorSessionState = (typeof MONITOR_SESSION_STATES)[number];
export const MONITOR_VISION_SOURCES = Object.freeze(["camera_ask", "describe", "point", "guidance", "identify"] as const);
export type VisionSource = (typeof MONITOR_VISION_SOURCES)[number];
export const MONITOR_STATES = Object.freeze(["listening", "thinking", "guiding", "found", "idle"] as const);
export type MonitorState = (typeof MONITOR_STATES)[number];

export interface VisionObject { label: string; box: Box | null; point: Point | null }

type MonBase = { t: "mon"; session: string; ts: number };
export type MonitorMsg = MonBase & (
  | { kind: "session"; state: MonitorSessionState; lang: string }
  | { kind: "transcript"; role: MonitorRole; text: string; lang: string; final: boolean }
  | { kind: "frame"; jpeg_b64: string; w: number; h: number; purpose: FramePurpose }
  | { kind: "vision"; source: VisionSource; objects: VisionObject[]; target?: string; found?: boolean }
  | { kind: "hand"; point: Point; near: boolean }
  | { kind: "event"; name: string; detail: Record<string, unknown> }
  | { kind: "status"; state: MonitorState }
);
export type MonitorMsgOf<K extends MonitorKind> = Extract<MonitorMsg, { kind: K }>;

const isVisionObject = (o: unknown): o is VisionObject =>
  isObj(o) && isStr(o.label) && (o.box === null || isBox(o.box)) && (o.point === null || isPoint(o.point));

export function isMonitorMsg(x: unknown): x is MonitorMsg {
  if (!isObj(x) || x.t !== "mon" || !inList(MONITOR_KINDS, x.kind) || !isStr(x.session) || !isNum(x.ts)) return false;
  switch (x.kind as MonitorKind) {
    case "session":
      return inList(MONITOR_SESSION_STATES, x.state) && isStr(x.lang);
    case "transcript":
      return inList(MONITOR_ROLES, x.role) && isStr(x.text) && isStr(x.lang) && isBool(x.final);
    case "frame":
      return isStr(x.jpeg_b64) && isInt(x.w) && isInt(x.h) && inList(FRAME_PURPOSES, x.purpose);
    case "vision":
      return inList(MONITOR_VISION_SOURCES, x.source) && Array.isArray(x.objects) && x.objects.every(isVisionObject) &&
        optional(x.target, isStr) && optional(x.found, isBool);
    case "hand":
      return isPoint(x.point) && isBool(x.near);
    case "event":
      return isStr(x.name) && isObj(x.detail);
    case "status":
      return inList(MONITOR_STATES, x.state);
  }
  return false;
}

/** Parse one WebSocket text frame from the /monitor channel. Returns null for bad JSON or unknown/invalid messages. */
export function parseMonitorMsg(raw: string): MonitorMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isMonitorMsg(v) ? v : null;
  } catch {
    return null;
  }
}
