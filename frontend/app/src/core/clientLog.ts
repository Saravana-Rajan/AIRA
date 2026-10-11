// frontend/app/src/core/clientLog.ts
// Diagnostic lines from the phone to aira-live (the "log" client message), which the server writes to Cloud
// Logging as jsonPayload.aira="client". Only metadata: tags such as build, camera, tilt, frame, hint and
// speak with small numbers and short strings. Never audio or image bytes. Throttled here (the server also
// caps at 20/s per session) and dropped while the socket is closed: a diagnostic line is never queued or
// retried, and nothing here changes what the app does.
import type { ClientMsg } from "@aira/contracts";
import { app } from "./app";

export type LogData = Record<string, unknown>;

export interface ClientLogOpts {
  /** Drop a line of this tag within this many ms of the previous one that was sent. */
  minGapMs?: number;
}

/** Lines per wall-clock second, all tags together (below the server's 20/s cap). */
export const CLIENT_LOG_MAX_PER_S = 15;
/** A data object whose JSON is longer than this is replaced by a size marker. */
export const CLIENT_LOG_MAX_BYTES = 1500;
/** Free text (a spoken line, a label) is cut here. */
export const CLIENT_LOG_MAX_TEXT = 60;

export function shortText(s: string, n = CLIENT_LOG_MAX_TEXT): string {
  return s.length <= n ? s : `${s.slice(0, n)}…`;
}

function capData(data: LogData): LogData {
  let s: string;
  try {
    s = JSON.stringify(data) ?? "{}";
  } catch {
    return { _unserializable: true };
  }
  if (s.length <= CLIENT_LOG_MAX_BYTES) return data;
  return { _truncated: true, size: s.length, head: s.slice(0, 200) };
}

export interface ClientLogger {
  /** True when the line went out now. False: throttled, socket closed or app not wired. */
  log(tag: string, data?: LogData, opts?: ClientLogOpts): boolean;
  reset(): void;
}

export function createClientLogger(
  send: (m: ClientMsg) => boolean,
  now: () => number = () => Date.now(),
  maxPerS = CLIENT_LOG_MAX_PER_S,
): ClientLogger {
  let bucket = Number.NaN;
  let n = 0;
  const lastByTag = new Map<string, number>();
  return {
    log(tag, data = {}, opts) {
      const t = now();
      const gap = opts?.minGapMs ?? 0;
      if (gap > 0) {
        const prev = lastByTag.get(tag);
        if (prev !== undefined && t - prev < gap) return false;
      }
      const b = Math.floor(t / 1000);
      if (b !== bucket) { bucket = b; n = 0; }
      if (++n > maxPerS) return false;
      let sent = false;
      try {
        sent = send({ t: "log", tag, data: capData(data) });
      } catch {
        sent = false;
      }
      if (sent && gap > 0) lastByTag.set(tag, t);
      return sent;
    },
    reset() {
      bucket = Number.NaN;
      n = 0;
      lastByTag.clear();
    },
  };
}

/** Sends through the app singleton; false (never a throw) before initApp or while the socket is closed. */
function appSend(m: ClientMsg): boolean {
  const send = (app as Partial<typeof app>).send;
  return typeof send === "function" ? send(m) : false;
}

const defaultLogger = createClientLogger(appSend);

/** One diagnostic line to the server (see the header). Safe to call from anywhere, at any rate. */
export function clientLog(tag: string, data?: LogData, opts?: ClientLogOpts): boolean {
  return defaultLogger.log(tag, data, opts);
}

/** Tests only: forget the throttle state. */
export function __resetClientLog(): void {
  defaultLogger.reset();
}

/** What shared/scripts/build_hosting.sh writes to /version.json on every deploy. */
export interface BuildInfo {
  build: string;
  updated?: string;
}

type FetchLike = (input: string, init?: { cache?: RequestCache }) => Promise<{ ok: boolean; json(): Promise<unknown> }>;

const defaultFetch: FetchLike | undefined =
  typeof fetch === "function" ? (input, init) => fetch(input, init) : undefined;

/** The deployed build from /version.json (never cached), or null when it cannot be read (dev server, offline). */
export async function loadBuildInfo(fetchImpl: FetchLike | undefined = defaultFetch): Promise<BuildInfo | null> {
  if (!fetchImpl) return null;
  try {
    const r = await fetchImpl("/version.json", { cache: "no-store" });
    if (!r.ok) return null;
    const j = (await r.json()) as Partial<BuildInfo> | null;
    if (!j || typeof j.build !== "string") return null;
    return { build: j.build, ...(typeof j.updated === "string" ? { updated: j.updated } : {}) };
  } catch {
    return null;
  }
}
