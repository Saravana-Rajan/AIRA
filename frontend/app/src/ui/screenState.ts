// What the phone screen shows, derived from server messages and local gestures.
// Pure: no DOM, no timers. Audio, earcons and haptics are handled elsewhere (core/dispatch).
import type { ConfirmKind, Entity, ServerMsg } from "@aira/contracts";

export type StatusKind = "connecting" | "ready" | "listening" | "thinking" | "counting" | "warning" | "stop";
export interface Status { kind: StatusKind; label: string }

export type TaskTone = "neutral" | "ok" | "warn" | "danger";
export interface TaskCard { title: string; detail: string; tone: TaskTone }

export interface ScreenState {
  status: Status;
  /** Last thing the user said (live caption). */
  userText: string;
  /** Last thing AIRA said (live caption). */
  airaText: string;
  /** Current task shown at the bottom, or null when idle. */
  task: TaskCard | null;
}

/** Events raised on the phone itself: gestures, the bump guard and the network monitor. */
export type LocalEvent = { t: "local"; event: "talk" | "stop" | "bump" | "bump_clear" | "offline" };
export type ScreenInput = ServerMsg | LocalEvent;

const READY: Status = { kind: "ready", label: "Ready" };
const LISTENING: Status = { kind: "listening", label: "Listening" };
const THINKING: Status = { kind: "thinking", label: "Thinking" };
const OFFLINE: Status = { kind: "warning", label: "Offline" };

export const initialScreen: ScreenState = Object.freeze({
  status: { kind: "connecting", label: "Connecting" },
  userText: "",
  airaText: "",
  task: null,
}) as ScreenState;

const ENTITY_TITLE: Record<Entity, string> = {
  order: "Order",
  delivery: "Delivery",
  sale: "Sale",
  payment: "Payment",
  inventory: "Stock",
};

const CONFIRM_TITLE: Record<ConfirmKind, string> = {
  order: "Confirm order",
  payment: "Confirm payment",
  sale: "Confirm sale",
  discrepancy: "Confirm discrepancy",
  generic: "Confirm",
};

const OK_STATES = /PLACED|RECONCILED|COMPLETED|CONFIRMED|CLOSED/;
const DANGER_STATES = /DISCREPANCY|FAILED|VOID/;

function toneFor(state: string): TaskTone {
  if (DANGER_STATES.test(state)) return "danger";
  if (OK_STATES.test(state)) return "ok";
  return "neutral";
}

/** The bump guard and a live count own the chip until they end. */
const holdsChip = (s: Status) => s.kind === "stop" || s.kind === "counting";

function withStatus(s: ScreenState, status: Status): ScreenState {
  return s.status.kind === "stop" ? s : { ...s, status };
}

export function reduceScreen(s: ScreenState, m: ScreenInput): ScreenState {
  switch (m.t) {
    case "local":
      switch (m.event) {
        case "talk": return withStatus(s, LISTENING);
        case "stop": return { ...s, status: READY };
        case "bump": return { ...s, status: { kind: "stop", label: "Stop!" } };
        case "bump_clear": return { ...s, status: READY };
        case "offline": return withStatus(s, OFFLINE);
      }
      return s;

    case "ready":
      return withStatus(s, READY);

    case "transcript": {
      // Captions are paired per turn: the owner speaking again starts a new turn, so the previous AIRA
      // reply leaves the screen and the next AIRA line shown is the answer to THIS "You" line.
      // An unclear transcript (a language the shop does not usually speak, probably misheard) is marked so a
      // sighted helper can see why AIRA asked to repeat.
      const userText = m.unclear ? `${m.text} (not clear)` : m.text;
      const next = m.role === "user" ? { ...s, userText, airaText: "" } : { ...s, airaText: m.text };
      if (holdsChip(s.status)) return next;
      if (m.role === "user") return { ...next, status: m.final ? THINKING : LISTENING };
      return { ...next, status: READY };
    }

    case "cue":
      switch (m.cue) {
        case "thinking": return withStatus(s, THINKING);
        case "warn": return withStatus(s, { kind: "warning", label: "Warning" });
        case "mismatch": return withStatus(s, { kind: "warning", label: "Mismatch" });
        case "unsure": return withStatus(s, { kind: "warning", label: "Not sure" });
        case "offline": return withStatus(s, OFFLINE);
        case "reconnect": return withStatus(s, { kind: "connecting", label: "Reconnecting" });
        case "done":
        case "match":
        case "interrupted": return withStatus(s, READY);
        case "ack": return s;
      }
      return s;

    case "count": {
      const total = m.expected === null ? `${m.counted}` : `${m.counted} / ${m.expected}`;
      if (!m.final) {
        return {
          ...withStatus(s, { kind: "counting", label: `Counting ${total}` }),
          task: { title: "Delivery", detail: `${total} counted`, tone: "neutral" },
        };
      }
      const diff = m.expected === null ? 0 : m.counted - m.expected;
      const status: Status = diff === 0 ? READY : { kind: "warning", label: diff < 0 ? `${-diff} short` : `${diff} extra` };
      return {
        ...withStatus(s, status),
        task: { title: "Delivery", detail: `${total} counted`, tone: diff === 0 ? "ok" : "warn" },
      };
    }

    case "state":
      // OPEN_URL hand-offs (click-to-chat, tel:) are handled by the link opener, not shown.
      if (m.summary.startsWith("OPEN_URL ")) return s;
      return { ...s, task: { title: ENTITY_TITLE[m.entity], detail: m.summary, tone: toneFor(m.state) } };

    case "confirm":
      return {
        ...withStatus(s, { kind: "listening", label: "Yes or no?" }),
        airaText: m.prompt,
        task: { title: CONFIRM_TITLE[m.kind], detail: m.prompt, tone: "neutral" },
      };

    case "error":
      return { ...withStatus(s, m.code === "network" ? OFFLINE : { kind: "warning", label: "Warning" }), airaText: m.say };

    default:
      // audio, request_frame, target: no visible change here.
      return s;
  }
}
