// The Live page: Sam's camera with the vision overlay, the conversation, and the activity timeline.
// Built for a projector: large type, three fixed regions, nothing scrolls sideways.
import { useEffect, useRef, useState } from "preact/hooks";
import { formatTimeIST } from "../lib/format";
import {
  eventLabel, overlayShapes, type ActivityItem, type Connection, type LiveState, type MonitorRole, type MonitorState,
  type Shape, type TranscriptLine,
} from "../lib/monitor";

export interface LivePageProps {
  state: LiveState;
  conn: Connection;
  /** True when no monitor key is known yet: the page asks for one instead of connecting. */
  keyNeeded: boolean;
  onKey: (key: string) => void;
}

const SPEAKER: Record<MonitorRole, string> = { owner: "Sam", customer: "Customer", aira: "AIRA" };

const STATUS_TEXT: Record<MonitorState, string> = {
  listening: "Listening", thinking: "Thinking", guiding: "Guiding", found: "Found", idle: "Idle",
};

const CONN_TEXT: Record<Connection, string> = {
  waiting: "Not connected", connecting: "Connecting…", live: "Live", offline: "Reconnecting…", rejected: "Key refused",
};

export function LivePage({ state, conn, keyNeeded, onKey }: LivePageProps) {
  return (
    <div class="livepage">
      <section class="stage" aria-labelledby="stage-title">
        <header class="stage__bar">
          <h2 id="stage-title" class="stage__title">Sam's camera</h2>
          <span class={`conn conn--${conn}`} role="status">{CONN_TEXT[conn]}</span>
          <span class={`status status--${state.status}`} role="status" aria-label={`AIRA is ${STATUS_TEXT[state.status].toLowerCase()}`}>
            <span class="status__dot" aria-hidden="true" />
            {STATUS_TEXT[state.status]}
          </span>
        </header>
        <div class="stage__view">
          {keyNeeded ? <KeyPrompt onKey={onKey} /> : state.frame ? <Camera state={state} /> : <Waiting conn={conn} />}
        </div>
      </section>

      <section class="talk" aria-labelledby="talk-title">
        <h2 id="talk-title" class="region__title">Conversation</h2>
        <Transcript lines={state.transcript} />
      </section>

      <section class="activity" aria-labelledby="activity-title">
        <h2 id="activity-title" class="region__title">Activity</h2>
        <Activity items={state.activity} />
      </section>
    </div>
  );
}

function Waiting({ conn }: { conn: Connection }) {
  return (
    <div class="stage__empty" role="status">
      <span class="stage__pulse" aria-hidden="true" />
      <p class="stage__empty-title">Waiting for Sam's phone…</p>
      <p class="stage__empty-sub">
        {conn === "rejected"
          ? "The monitor key was refused. Open this page with the right ?key= link."
          : conn === "live"
            ? "Connected. The picture appears when Sam starts AIRA."
            : conn === "waiting"
              ? "AIRA Live is not configured for this dashboard."
              : "Connecting to AIRA Live."}
      </p>
    </div>
  );
}

function KeyPrompt({ onKey }: { onKey: (key: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      class="keyprompt"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onKey(value.trim());
      }}
    >
      <label class="keyprompt__label" for="monitor-key">Monitor key</label>
      <p class="keyprompt__help">Paste the key from the AIRA Live settings, or open this page with ?key= in the address.</p>
      <div class="keyprompt__row">
        <input id="monitor-key" class="keyprompt__input" type="password" autocomplete="off" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
        <button class="keyprompt__btn" type="submit">Connect</button>
      </div>
    </form>
  );
}

const TONE_CLASS = { object: "ov--object", target: "ov--target", found: "ov--found", hand: "ov--hand" } as const;

/** The latest frame with the vision overlay drawn in the frame's own pixel space, so it scales with the picture. */
function Camera({ state }: { state: LiveState }) {
  const { frame } = state;
  if (!frame) return null;
  const shapes = overlayShapes(state, frame.w, frame.h);
  const labelSize = Math.max(14, Math.round(frame.w / 28));
  return (
    <div class="camera" style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
      <img class="camera__img" src={frame.src} width={frame.w} height={frame.h} alt="" />
      <svg class="camera__overlay" viewBox={`0 0 ${frame.w} ${frame.h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true" data-testid="overlay">
        {shapes.map((s, i) => <OverlayShape key={i} shape={s} labelSize={labelSize} />)}
      </svg>
      {state.found && state.target && (
        <p class="camera__found" role="status">
          <span class="camera__check" aria-hidden="true">✓</span> Found: {state.target}
        </p>
      )}
      {state.target && !state.found && (
        <p class="camera__seek" role="status">Looking for {state.target}</p>
      )}
    </div>
  );
}

function OverlayShape({ shape, labelSize }: { shape: Shape; labelSize: number }) {
  if (shape.kind === "line") {
    return <line class="ov ov--line" x1={shape.from.x} y1={shape.from.y} x2={shape.to.x} y2={shape.to.y} />;
  }
  if (shape.kind === "dot") {
    const r = shape.tone === "hand" ? labelSize * 0.55 : labelSize * 0.4;
    return (
      <g class={`ov ${TONE_CLASS[shape.tone]}`} data-tone={shape.tone} data-label={shape.label}>
        <circle cx={shape.at.x} cy={shape.at.y} r={r} />
        {shape.tone !== "hand" && (
          <text x={shape.at.x + r + 4} y={shape.at.y + labelSize * 0.35} font-size={labelSize}>{shape.label}</text>
        )}
      </g>
    );
  }
  const { rect } = shape;
  const labelY = rect.y - 6 >= labelSize ? rect.y - 6 : rect.y + rect.h + labelSize;
  return (
    <g class={`ov ${TONE_CLASS[shape.tone]}`} data-tone={shape.tone} data-label={shape.label}>
      <rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={Math.min(8, rect.w / 8)} />
      <text x={rect.x} y={labelY} font-size={labelSize}>
        {shape.tone === "found" ? `✓ ${shape.label}` : shape.label}
      </text>
    </g>
  );
}

function Transcript({ lines }: { lines: TranscriptLine[] }) {
  const ref = useRef<HTMLOListElement>(null);
  const last = lines.at(-1);
  useEffect(() => {
    try {
      const el = ref.current;
      if (el) el.scrollTop = el.scrollHeight;
    } catch {
      /* jsdom or a detached node */
    }
  }, [last?.id, last?.text]);
  if (lines.length === 0) {
    return <p class="talk__empty">Nothing said yet. Sam's words, the customer's words and AIRA's replies appear here.</p>;
  }
  return (
    <ol class="talk__list" ref={ref} aria-live="polite" aria-relevant="additions text">
      {lines.map((l) => (
        <li key={l.id} class={`line line--${l.role}${l.final ? "" : " line--partial"}`}>
          <span class="line__meta">
            <span class="chip">{SPEAKER[l.role]}</span>
            {l.lang && <span class="lang">{l.lang}</span>}
          </span>
          <p class="line__text">{l.text}</p>
        </li>
      ))}
    </ol>
  );
}

function Activity({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return <p class="activity__empty">What AIRA does shows up here: searching, pointing, counting, confirming.</p>;
  const shown = items.slice(-8).reverse();
  return (
    <ol class="activity__list">
      {shown.map((a) => (
        <li key={a.id} class={`act act--${iconFor(a.name)}`}>
          <span class="act__icon" aria-hidden="true"><Icon name={iconFor(a.name)} /></span>
          <span class="act__time">{formatTimeIST(a.ts)}</span>
          <span class="act__label">{eventLabel(a.name)}</span>
          {a.detail && <span class="act__detail">{a.detail}</span>}
        </li>
      ))}
    </ol>
  );
}

type IconName = "search" | "point" | "hand" | "check" | "send" | "count" | "confirm" | "phone" | "dot";

function iconFor(name: string): IconName {
  if (/found/.test(name)) return "check";
  if (/guid|search|lost/.test(name)) return "search";
  if (/point/.test(name)) return "point";
  if (/hand/.test(name)) return "hand";
  if (/order|sent|notify/.test(name)) return "send";
  if (/count/.test(name)) return "count";
  if (/confirm/.test(name)) return "confirm";
  if (/session/.test(name)) return "phone";
  return "dot";
}

const PATHS: Record<IconName, string> = {
  search: "M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm0-2a8 8 0 1 0 4.9 14.3l4.4 4.4 1.4-1.4-4.4-4.4A8 8 0 0 0 10 2z",
  point: "M11 2l1.5 6.5L19 10l-6.5 1.5L11 18l-1.5-6.5L3 10l6.5-1.5z",
  hand: "M8 3a1.5 1.5 0 0 1 3 0v6h1V5a1.5 1.5 0 0 1 3 0v5h1V7a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1a6 6 0 0 1-5.1-2.8L3.6 13a1.5 1.5 0 0 1 2.5-1.6L8 13.5z",
  check: "M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.4-1.4z",
  send: "M3 12l18-9-6 18-3-7z",
  count: "M4 5h16v2H4zm0 6h16v2H4zm0 6h10v2H4z",
  confirm: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1 14l-4-4 1.4-1.4L11 13.2l5.6-5.6L18 9z",
  phone: "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm0 2v14h10V4zm4 15h2v1h-2z",
  dot: "M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z",
};

function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" focusable="false">
      <path d={PATHS[name]} fill="currentColor" />
    </svg>
  );
}
