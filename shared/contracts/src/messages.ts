// WebSocket protocol. Mirrored in backend/aira_live/contracts.py and tested
// against the shared fixtures in ../fixtures. Audio: client 16 kHz PCM16 base64,
// server 24 kHz PCM16 base64. Coordinates: point [y, x], box [ymin, xmin, ymax, xmax],
// integers normalised 0..1000.
import {
  BOX_SEVERITIES, BOX_SOURCES, CLIENT_EVENT_NAMES, CONFIRM_ANSWERS, CONFIRM_KINDS, CUE_NAMES, ENTITIES, ERROR_CODES,
  FRAME_PURPOSES, LANGS, MAX_BOXES, NORM_MAX, TRANSCRIPT_ROLES, VERBOSITIES,
  type BoxSeverity, type BoxSource, type ClientEventName, type ConfirmAnswer, type ConfirmKind, type CueName,
  type Entity, type ErrorCode, type FramePurpose, type Lang, type TranscriptRole, type Verbosity,
} from "./enums";

export type Point = [number, number];
export type Box = [number, number, number, number];
/** One drawn object: a label, its box, an optional detector score (0-1) and "danger" when the bump guard
 * flags it in the walking path. */
export type BoxObject = { label: string; box: Box; score?: number; severity?: BoxSeverity };

// ---------- client -> server ----------
export type ClientMsg =
  | { t: "hello"; uid: string; idToken?: string; shopId: string; lang: Lang; verbosity: Verbosity; appVersion: string; resume?: string }
  | { t: "audio"; pcm16: string }
  // `changed`/`labels` only on purpose "live": the view settled on something new (labels: the phone's own
  // detector labels, at most MAX_FRAME_LABELS), so the server may nudge Live to speak up.
  // `requestId`: echoed from the request_frame this frame answers (every frame of a burst), absent on continuous
  // and background frames. `captureTs`: phone clock (epoch ms) when the camera picture was grabbed, before encoding;
  // `ts` is stamped after encoding. The server only uses `ts - captureTs` (same clock), never compares either with
  // its own clock.
  | { t: "frame"; id: string; jpeg: string; w: number; h: number; ts: number; purpose: FramePurpose; changed?: boolean; labels?: string[]; requestId?: string; captureTs?: number }
  | { t: "text"; text: string }
  | { t: "event"; name: ClientEventName; data: Record<string, unknown> }
  | { t: "confirm"; id: string; answer: ConfirmAnswer; value?: string }
  | { t: "stop" }
  // A diagnostic line from the phone, written to Cloud Logging by the server (aira_live.diag) as
  // jsonPayload.aira="client". tag names what happened ("build", "camera", "tilt", "frame", "hint", "speak");
  // data is small metadata, never audio or image bytes. The server keeps at most 2 KB of it and 20 lines a
  // second per socket, and never answers it.
  | { t: "log"; tag: string; data: Record<string, unknown> }
  // Boxes the phone drew itself: MediaPipe on-device detections (at most 5 a second) or the bump guard's
  // obstacles. Never answered: the server relays them to the live monitor as kind "vision" and drops the rest
  // above its per-socket rate limit. Nothing here changes state.
  | { t: "boxes"; source: BoxSource; objects: BoxObject[]; frameId?: string }
  // WebRTC signalling from the phone to a dashboard monitor (`to` is the monitor id a `want` carried in `from`).
  // The server fills `from` with the session id and relays it; it never reads the SDP.
  | RtcClientMsg;

// ---------- WebRTC signalling (phone <-> server <-> monitor; the server only relays) ----------
// The dashboard asks a phone session for live video with `want`; the phone answers with an `offer` (video only,
// sendonly), the dashboard with an `answer` (recvonly), both trickle `ice`; the phone says `connected` once the
// peer connection is up (the server then pauses that session's JPEG preview for that monitor) and either side
// says `bye` (the preview resumes). `from` is always the sender's id as the server knows it: a session id for
// the phone, a monitor id for the dashboard; `to` names the peer. The server relays only inside one shop.
export const RTC_KINDS = Object.freeze(["want", "offer", "answer", "ice", "connected", "bye"] as const);
export type RtcKind = (typeof RTC_KINDS)[number];
export type RtcCandidate = { candidate: string; sdpMid?: string | null; sdpMLineIndex?: number | null };
export type RtcMsg = { t: "rtc"; kind: RtcKind; from: string; to: string; sdp?: string; candidate?: RtcCandidate };
/** What a client sends: `from` is optional (the server overwrites it with the sender's own id anyway). */
export type RtcClientMsg = Omit<RtcMsg, "from"> & { from?: string };

// ---------- server -> client ----------
export type ServerMsg =
  | { t: "ready"; sessionId: string; resume: string; mock: boolean }
  | { t: "audio"; pcm24: string }
  // lang: the language AIRA detected from the speaker's last finished utterance (any code: "ta", "ja", "en",
  // ...), so the app's labels can follow. Absent on partial transcripts and before anyone has spoken.
  // unclear: a finished user transcript that looks like a language the shop does not usually speak (shop doc
  // "languages"); probably misheard, so AIRA asks to repeat instead of answering it. The caption marks it.
  | { t: "transcript"; role: TranscriptRole; text: string; final: boolean; lang?: string; unclear?: boolean }
  // requestId: a one-off (single/burst) request's id; the phone echoes it on every frame it captures for it.
  // ttlMs: how long after the phone RECEIVES the request it may still start a capture (relative, so no clock sync);
  // past it the server has stopped waiting, so the phone skips the capture.
  | { t: "request_frame"; purpose: FramePurpose; burst?: { count: number; intervalMs: number }; continuous?: boolean; stop?: boolean; requestId?: string; ttlMs?: number }
  | { t: "target"; frameId: string; label: string; point: Point; box?: Box; confidence: number }
  | { t: "count"; sku: string; counted: number; expected: number | null; final: boolean; confidence: number }
  | { t: "state"; entity: Entity; id: string; state: string; summary: string }
  | { t: "confirm"; id: string; kind: ConfirmKind; prompt: string }
  | { t: "cue"; cue: CueName }
  | { t: "error"; code: ErrorCode; say: string }
  // Boxes Gemini Robotics-ER 2 drew (source "er2") for the phone's optional "Show boxes" overlay: the same shape
  // as the client message, plus the label being looked for (target, hand guidance) and whether it was found
  // (drawn green). Never spoken.
  | { t: "boxes"; source: BoxSource; objects: BoxObject[]; frameId?: string; target?: string; found?: boolean }
  // WebRTC signalling relayed from a dashboard monitor (`from` is its monitor id) to this phone session.
  | RtcMsg;

export type ClientMsgType = ClientMsg["t"];
export type ServerMsgType = ServerMsg["t"];
export type ClientMsgOf<T extends ClientMsgType> = Extract<ClientMsg, { t: T }>;
export type ServerMsgOf<T extends ServerMsgType> = Extract<ServerMsg, { t: T }>;

export const CLIENT_MSG_TYPES = Object.freeze([
  "hello", "audio", "frame", "text", "event", "confirm", "stop", "log", "boxes", "rtc",
] as const);
export const SERVER_MSG_TYPES = Object.freeze([
  "ready", "audio", "transcript", "request_frame", "target", "count", "state", "confirm", "cue", "error", "boxes", "rtc",
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
const isBoxObject = (o: unknown): o is BoxObject =>
  isObj(o) && isStr(o.label) && isBox(o.box) && optional(o.score, isNum) && optional(o.severity, (v) => inList(BOX_SEVERITIES, v));
/** At most this many detector labels ride on a live frame. */
export const MAX_FRAME_LABELS = 8;
const isLabelList = (l: unknown): boolean =>
  Array.isArray(l) && l.length <= MAX_FRAME_LABELS && l.every((v) => isStr(v) && v.length <= 60);
const isBoxList = (l: unknown): l is BoxObject[] => Array.isArray(l) && l.length <= MAX_BOXES && l.every(isBoxObject);
const isCandidate = (c: unknown): c is RtcCandidate =>
  isObj(c) && isStr(c.candidate) && (c.sdpMid === undefined || c.sdpMid === null || isStr(c.sdpMid)) &&
  (c.sdpMLineIndex === undefined || c.sdpMLineIndex === null || isInt(c.sdpMLineIndex));
/** The rtc shape without `from` (a client may leave it out; the server always sets it). */
const isRtcBody = (x: Obj): boolean =>
  inList(RTC_KINDS, x.kind) && isStr(x.to) && optional(x.sdp, isStr) && optional(x.candidate, isCandidate);

export function isRtcClientMsg(x: unknown): x is RtcClientMsg {
  return isObj(x) && x.t === "rtc" && isRtcBody(x) && optional(x.from, isStr);
}

export function isRtcMsg(x: unknown): x is RtcMsg {
  return isObj(x) && x.t === "rtc" && isRtcBody(x) && isStr(x.from);
}

export function parseRtcMsg(raw: string): RtcMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isRtcMsg(v) ? v : null;
  } catch {
    return null;
  }
}

export function isClientMsg(x: unknown): x is ClientMsg {
  if (!isObj(x) || !inList(CLIENT_MSG_TYPES, x.t)) return false;
  switch (x.t as ClientMsgType) {
    case "hello":
      return isStr(x.uid) && optional(x.idToken, isStr) && isStr(x.shopId) && inList(LANGS, x.lang) &&
        inList(VERBOSITIES, x.verbosity) && isStr(x.appVersion) && optional(x.resume, isStr);
    case "audio":
      return isStr(x.pcm16);
    case "frame":
      return isStr(x.id) && isStr(x.jpeg) && isInt(x.w) && isInt(x.h) && isNum(x.ts) && inList(FRAME_PURPOSES, x.purpose) &&
        optional(x.changed, isBool) && optional(x.labels, isLabelList) && optional(x.requestId, isStr) &&
        optional(x.captureTs, isNum);
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
    case "boxes":
      return inList(BOX_SOURCES, x.source) && isBoxList(x.objects) && optional(x.frameId, isStr);
    case "rtc":
      return isRtcClientMsg(x);
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
        optional(x.continuous, isBool) && optional(x.stop, isBool) && optional(x.requestId, isStr) &&
        optional(x.ttlMs, isInt);
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
    case "boxes":
      return inList(BOX_SOURCES, x.source) && isBoxList(x.objects) && optional(x.frameId, isStr) &&
        optional(x.target, isStr) && optional(x.found, isBool);
    case "rtc":
      return isRtcMsg(x);
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

// ---------- server -> monitor (GET /monitor?shop=<id>, then {t:"auth", key}; read-only) ----------
// A projector view of one shop's live sessions. Every message is {t:"mon", kind, session, ts, ...}.
// The monitor's first and only message is {t:"auth", key}: the server closes with 4403 when the key is wrong,
// missing or late (5 s), and publishes nothing before it. The key never travels in the URL.
// Frames are sent only while a monitor is connected (<= 480 px wide, quality 0.6, <= 4 fps a session).
// Mirrored in backend/aira_live/contracts.py; fixtures in ../fixtures/monitor_msgs.json.
// After auth a monitor may also send rtc signalling for one of the shop's sessions (`to` = session id).
export type MonitorClientMsg = { t: "auth"; key: string } | RtcClientMsg;
export const MONITOR_KINDS = Object.freeze(["session", "transcript", "frame", "vision", "hand", "event", "status", "stats"] as const);
export type MonitorKind = (typeof MONITOR_KINDS)[number];
export const MONITOR_ROLES = Object.freeze(["owner", "customer", "aira"] as const);
export type MonitorRole = (typeof MONITOR_ROLES)[number];
export const MONITOR_SESSION_STATES = Object.freeze(["start", "end"] as const);
export type MonitorSessionState = (typeof MONITOR_SESSION_STATES)[number];
// Who drew the boxes: the phone's on-device MediaPipe detector, Gemini Robotics-ER 2, or the phone's bump
// guard (always severity "danger"). `context` names the ER 2 prompt (camera_ask, describe, guidance, ...).
export const MONITOR_VISION_SOURCES = BOX_SOURCES;
export type VisionSource = BoxSource;
export const VISION_DISTANCES = Object.freeze(["near", "arm", "far"] as const);
export type VisionDistance = (typeof VISION_DISTANCES)[number];
export const MONITOR_STATES = Object.freeze(["listening", "thinking", "guiding", "found", "idle"] as const);
export type MonitorState = (typeof MONITOR_STATES)[number];

export interface VisionObject {
  label: string;
  box: Box | null;
  point: Point | null;
  /** Detector score or ER 2 confidence, 0-1. */
  score?: number;
  /** "danger": the bump guard flagged it in the walking path. */
  severity?: BoxSeverity;
  distance?: VisionDistance;
}

type MonBase = { t: "mon"; session: string; ts: number };
export type MonitorMsg = MonBase & (
  // uid "camera:<name>" marks a room camera's session.
  | { kind: "session"; state: MonitorSessionState; lang: string; uid?: string }
  | { kind: "transcript"; role: MonitorRole; text: string; lang: string; final: boolean }
  | { kind: "frame"; jpeg_b64: string; w: number; h: number; purpose: FramePurpose }
  | { kind: "vision"; source: VisionSource; objects: VisionObject[]; target?: string; found?: boolean; severity?: BoxSeverity; context?: string; frameId?: string }
  | { kind: "hand"; point: Point; near: boolean }
  | { kind: "event"; name: string; detail: Record<string, unknown> }
  | { kind: "status"; state: MonitorState }
  // Every 2 s per live session: Live API turns, ER 2 / Flash calls, average vision latency (ms; null before
  // the first call) and the phone's frame rate over the last window.
  | { kind: "stats"; turns: number; er2Calls: number; flashCalls: number; visionMs: number | null; fps: number }
);
export type MonitorMsgOf<K extends MonitorKind> = Extract<MonitorMsg, { kind: K }>;

const isVisionObject = (o: unknown): o is VisionObject =>
  isObj(o) && isStr(o.label) && (o.box === null || isBox(o.box)) && (o.point === null || isPoint(o.point)) &&
  optional(o.score, isNum) && optional(o.severity, (v) => inList(BOX_SEVERITIES, v)) &&
  optional(o.distance, (v) => inList(VISION_DISTANCES, v));

export function isMonitorClientMsg(x: unknown): x is MonitorClientMsg {
  return (isObj(x) && x.t === "auth" && isStr(x.key)) || isRtcClientMsg(x);
}

/** One text frame of the /monitor feed: a monitor message, or rtc signalling from a phone session. */
export function parseMonitorFeedMsg(raw: string): MonitorMsg | RtcMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isMonitorMsg(v) ? v : isRtcMsg(v) ? v : null;
  } catch {
    return null;
  }
}

export function isMonitorMsg(x: unknown): x is MonitorMsg {
  if (!isObj(x) || x.t !== "mon" || !inList(MONITOR_KINDS, x.kind) || !isStr(x.session) || !isNum(x.ts)) return false;
  switch (x.kind as MonitorKind) {
    case "session":
      return inList(MONITOR_SESSION_STATES, x.state) && isStr(x.lang) && optional(x.uid, isStr);
    case "transcript":
      return inList(MONITOR_ROLES, x.role) && isStr(x.text) && isStr(x.lang) && isBool(x.final);
    case "frame":
      return isStr(x.jpeg_b64) && isInt(x.w) && isInt(x.h) && inList(FRAME_PURPOSES, x.purpose);
    case "vision":
      return inList(MONITOR_VISION_SOURCES, x.source) && Array.isArray(x.objects) && x.objects.every(isVisionObject) &&
        optional(x.target, isStr) && optional(x.found, isBool) && optional(x.severity, (v) => inList(BOX_SEVERITIES, v)) &&
        optional(x.context, isStr) && optional(x.frameId, isStr);
    case "hand":
      return isPoint(x.point) && isBool(x.near);
    case "event":
      return isStr(x.name) && isObj(x.detail);
    case "status":
      return inList(MONITOR_STATES, x.state);
    case "stats":
      return isInt(x.turns) && isInt(x.er2Calls) && isInt(x.flashCalls) && (x.visionMs === null || isInt(x.visionMs)) && isNum(x.fps);
  }
  return false;
}

export function parseMonitorMsg(raw: string): MonitorMsg | null {
  try {
    const v: unknown = JSON.parse(raw);
    return isMonitorMsg(v) ? v : null;
  } catch {
    return null;
  }
}
