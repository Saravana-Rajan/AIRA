// The Live page: what AIRA sees, hears and does for one shop, on a projector or a laptop. AIRA's own look:
// light, Inter, the blue -> purple brand gradient for AIRA itself, soft 20 px cards. Sam's camera is the
// point of the page; the conversation runs beside it as chat bubbles; under the camera the task stepper and
// a slim activity timeline say what AIRA is doing. Record mode strips everything but the cameras and the
// captions into a clean 16:9 for screen recording.
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { formatTimeIST } from "../lib/format";
import { browserDeps, createFrameDrawer, type DrawDeps } from "../lib/frameDraw";
import { doneIndex, stepDurations, type Task } from "../lib/liveTask";
import {
  eventLabel, frameAge, overlayShapes, type ActivityItem, type ActivityTag, type Connection,
  type Frame, type LiveState, type MonitorRole, type MonitorState, type Shape, type TranscriptLine,
} from "../lib/monitor";

export interface LivePageProps {
  state: LiveState;
  conn: Connection;
  /** True when no monitor key is known yet: the page asks for one instead of connecting. */
  keyNeeded: boolean;
  onKey: (key: string) => void;
  /** False when the dashboard has no AIRA Live address (VITE_AIRA_LIVE_WS): nothing to connect to. */
  configured?: boolean;
  /** Picks another of the shop's live sessions (`state.sessions`) to follow. */
  onFollow?: (sessionId: string) => void;
  /** Record mode: a clean 16:9 view without the dashboard chrome. */
  record?: boolean;
  onRecord?: (on: boolean) => void;
  shopName?: string;
  now?: () => number;
  /** The camera drawer's DOM hooks (tests inject fakes). */
  drawDeps?: DrawDeps;
}

const SPEAKER: Record<MonitorRole, string> = { owner: "Sam", customer: "Customer", aira: "AIRA" };

const STATUS_TEXT: Record<MonitorState, string> = {
  listening: "Listening", thinking: "Thinking", guiding: "Guiding", found: "Found", idle: "Idle",
};

const CONN_TEXT: Record<Connection, string> = {
  waiting: "Not connected", connecting: "Connecting…", live: "Connected", offline: "Reconnecting…", rejected: "Key refused",
};

const LANG_NAME: Record<string, string> = { ta: "Tamil", hi: "Hindi", en: "English" };

/** A clock that ticks four times a second while the page is open, for the step timer and the frame age. */
function useNow(now: () => number): number {
  const [t, setT] = useState(now);
  useEffect(() => {
    const h = setInterval(() => setT(now()), 250);
    return () => clearInterval(h);
  }, []);
  return t;
}

export function LivePage({
  state, conn, keyNeeded, onKey, configured = true, onFollow, record = false, onRecord, shopName = "Sunrise Dairy", now = Date.now, drawDeps = browserDeps,
}: LivePageProps) {
  const t = useNow(now);
  const [paused, setPaused] = useState(false);
  const frozen = useRef<Frame | null>(null);
  if (!paused) frozen.current = state.frame;
  const frame = paused ? frozen.current : state.frame;
  const rooms = Object.entries(state.rooms);
  const [roomId, setRoomId] = useState<string | null>(null);
  const room = rooms.find(([sid]) => sid === roomId) ?? rooms[0] ?? null;

  if (record) {
    return (
      <div class="livepage livepage--record" data-testid="record">
        <div class="record__cams">
          {room && <RoomCamera name={room[1].name} frame={room[1].frame} state={room[1]} now={t} drawDeps={drawDeps} />}
          <div class="record__sam">
            {frame ? <Camera state={{ ...state, frame }} now={t} drawDeps={drawDeps} showAge /> : <Waiting conn={conn} configured={configured} />}
          </div>
        </div>
        <Captions lines={state.transcript} />
        <button class="record__exit" type="button" onClick={() => onRecord?.(false)}>Exit record mode</button>
      </div>
    );
  }

  return (
    <div class="livepage">
      <header class="lv-head">
        <img class="lv-logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="" width={40} height={40} />
        <h2 class="lv-title">{shopName} - Live</h2>
        <span class={`lv-pill lv-pill--${state.status}`} role="status" aria-label={`AIRA is ${STATUS_TEXT[state.status].toLowerCase()}`}>
          <span class="lv-pill__dot" aria-hidden="true" />{STATUS_TEXT[state.status]}
        </span>
        <div class="lv-conn">
          <span class={`lv-conn__dot lv-conn__dot--${state.session ? "on" : conn}`} aria-hidden="true" />
          <span class="lv-conn__text" role="status">{state.session ? "Session open" : CONN_TEXT[conn]}</span>
          {state.lang && <span class="lv-conn__lang">{LANG_NAME[state.lang] ?? state.lang}</span>}
          <SessionPicker sessions={state.sessions} current={state.session} onFollow={onFollow} />
        </div>
      </header>

      <section class="cam" aria-labelledby="cam-title">
        <div class="cam__head">
          <h3 id="cam-title" class="cam__title">Sam's view</h3>
          {paused ? (
            <span class="cam__paused" role="status">Paused</span>
          ) : (
            <span class={`cam__live${frame ? " cam__live--on" : ""}`}>
              <span class="cam__live-dot" aria-hidden="true" />live
              <span class="cam__age">{frameAge(frame?.ts ?? null, t)}</span>
            </span>
          )}
        </div>
        <div class="cam__view">
          {keyNeeded ? <KeyPrompt onKey={onKey} /> : frame ? <Camera state={{ ...state, frame }} now={t} drawDeps={drawDeps} /> : <Waiting conn={conn} configured={configured} />}
        </div>
        <Legend />
      </section>

      <aside class="chat" aria-labelledby="chat-title">
        <h3 id="chat-title" class="chat__title">Conversation</h3>
        <Chat lines={state.transcript} />
      </aside>

      {room && (
        <section class="room" aria-label="Room camera">
          <RoomCamera name={room[1].name} frame={room[1].frame} state={room[1]} now={t} drawDeps={drawDeps} />
          {rooms.length > 1 && (
            <div class="room__pick" role="group" aria-label="Choose a room camera">
              {rooms.map(([sid, r]) => (
                <button key={sid} type="button" class={`room__btn${room[0] === sid ? " room__btn--on" : ""}`} onClick={() => setRoomId(sid)} aria-pressed={room[0] === sid}>
                  {r.name}
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      <Stepper task={state.task} now={t} />
      <Timeline items={state.activity} />

      <footer class="bar">
        <button class="bar__btn" type="button" onClick={() => onRecord?.(true)}>Record mode</button>
        <button class="bar__btn" type="button" aria-pressed={paused} onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume camera" : "Pause camera"}
        </button>
      </footer>
    </div>
  );
}

// ---------- the task stepper: a soft bar of chips ----------
function Stepper({ task, now }: { task: Task | null; now: number }) {
  if (!task) {
    return (
      <ol class="steps steps--idle" aria-label="Current task">
        <li class="step"><span class="step__label">Waiting for a task</span></li>
      </ol>
    );
  }
  const di = doneIndex(task);
  const durations = stepDurations(task);
  return (
    <ol class="steps" aria-label={task.title}>
      {task.steps.map((s, i) => {
        const cls = s.doneAt !== null ? "step--done" : i === di + 1 ? "step--active" : "";
        return (
          <li key={s.label} class={`step ${cls}`} aria-current={i === di + 1 ? "step" : undefined}>
            <span class="step__mark" aria-hidden="true">{s.doneAt !== null ? "✓" : i + 1}</span>
            <span class="step__label">{s.label}</span>
            {durations[i] && <span class="step__time">{durations[i]}</span>}
            {i === di + 1 && <span class="step__time step__time--live">{elapsed(task, now)}</span>}
          </li>
        );
      })}
    </ol>
  );
}

function elapsed(task: Task, now: number): string {
  const last = task.steps.reduce((acc, s) => (s.doneAt !== null ? Math.max(acc, s.doneAt) : acc), task.startedAt);
  const s = Math.max(0, (now - last) / 1000);
  return s < 10 ? `${s.toFixed(1)}s` : `${Math.round(s)}s`;
}

// ---------- the camera ----------
function waitingText(conn: Connection, configured: boolean): string {
  if (conn === "rejected") return "The monitor key was refused. Open this page with the right #key= link.";
  if (!configured) return "AIRA Live is not configured for this dashboard.";
  if (conn === "live") return "Connected. Waiting for the phone to start a session.";
  if (conn === "offline") return "Lost the connection to AIRA Live. Reconnecting…";
  return "Connecting to AIRA Live…";
}

function Waiting({ conn, configured }: { conn: Connection; configured: boolean }) {
  return (
    <div class="cam__empty" role="status">
      <span class="cam__pulse" aria-hidden="true" />
      <p class="cam__empty-title">Waiting for Sam's phone…</p>
      <p class="cam__empty-sub">
        {waitingText(conn, configured)}
      </p>
    </div>
  );
}

/** Shown only when the shop has more than one live phone session: the page follows one at a time. */
function SessionPicker({ sessions, current, onFollow }: { sessions: LiveState["sessions"]; current: string | null; onFollow?: (id: string) => void }) {
  const ids = Object.keys(sessions);
  if (ids.length < 2) return null;
  return (
    <select class="lv-session" aria-label="Session to follow" value={current ?? ""} onChange={(e) => onFollow?.((e.target as HTMLSelectElement).value)}>
      {ids.map((id) => (
        <option key={id} value={id}>{sessions[id].uid ? `${sessions[id].uid} · ${id.slice(0, 8)}` : id.slice(0, 8)}</option>
      ))}
    </select>
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
      <p class="keyprompt__help">Paste the key from the AIRA Live settings, or open this page with #key= at the end of the address.</p>
      <div class="keyprompt__row">
        <input id="monitor-key" class="keyprompt__input" type="password" autocomplete="off" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
        <button class="keyprompt__btn" type="submit">Connect</button>
      </div>
    </form>
  );
}

const TONE_CLASS = { object: "ov--object", fast: "ov--fast", danger: "ov--danger", target: "ov--target", found: "ov--found", hand: "ov--hand" } as const;

/** A canvas that always shows the newest frame it was given (decoded off the main thread when the browser can). */
function FrameCanvas({ frame, deps, class: cls }: { frame: Frame; deps: DrawDeps; class: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawer = useMemo(() => createFrameDrawer(() => ref.current, deps), [deps]);
  useEffect(() => () => drawer.stop(), [drawer]);
  useEffect(() => {
    drawer.push(frame);
  }, [drawer, frame]);
  return <canvas ref={ref} class={cls} width={frame.w} height={frame.h} data-frame-ts={frame.ts} aria-hidden="true" />;
}

/** The latest frame with the vision overlay drawn in the frame's own pixel space, so it scales with the picture. */
function Camera({ state, now, drawDeps, showAge = false }: { state: LiveState; now: number; drawDeps: DrawDeps; showAge?: boolean }) {
  const { frame } = state;
  if (!frame) return null;
  const shapes = overlayShapes(state, frame.w, frame.h, now);
  const labelSize = Math.max(14, Math.round(frame.w / 30));
  const danger = shapes.some((s) => s.kind === "box" && s.tone === "danger");
  return (
    <div class={`camera${danger ? " camera--danger" : ""}`} style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
      <FrameCanvas frame={frame} deps={drawDeps} class="camera__canvas" />
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
      {showAge && <span class="camera__age">{frameAge(frame.ts, now)}</span>}
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
  const tagH = labelSize * 1.5;
  const tagY = rect.y - tagH >= 0 ? rect.y - tagH : rect.y;
  const label = `${shape.tone === "found" ? "✓ " : ""}${shape.label}${shape.score !== null ? ` ${Math.round(shape.score * 100)}%` : ""}`;
  const tagW = Math.min(Math.max(rect.w, 40), label.length * labelSize * 0.62 + labelSize);
  return (
    <g class={`ov ${TONE_CLASS[shape.tone]}`} data-tone={shape.tone} data-label={shape.label}>
      <rect x={rect.x} y={rect.y} width={rect.w} height={rect.h} rx={shape.tone === "fast" ? 2 : 6} />
      {shape.tone !== "fast" && <rect class="ov__tag" x={rect.x} y={tagY} width={tagW} height={tagH} rx={4} />}
      <text x={rect.x + labelSize * 0.4} y={tagY + tagH * 0.72} font-size={shape.tone === "fast" ? labelSize * 0.8 : labelSize}>{label}</text>
    </g>
  );
}

function Legend() {
  return (
    <ul class="legend" aria-label="Box colours">
      <li class="legend__item legend__item--fast">MediaPipe (on-device)</li>
      <li class="legend__item legend__item--object">Gemini Robotics-ER 2</li>
      <li class="legend__item legend__item--danger">Obstacle</li>
      <li class="legend__item legend__item--found">Found</li>
      <li class="legend__item legend__item--hand">Fingertip</li>
    </ul>
  );
}

function RoomCamera({ name, frame, state, now, drawDeps }: { name: string; frame: Frame | null; state: { mediapipe: LiveState["mediapipe"] }; now: number; drawDeps: DrawDeps }) {
  const shapes = frame ? overlayShapes({ er2: { objects: [], ts: 0 }, mediapipe: state.mediapipe, target: null, found: false, hand: null, status: "idle" }, frame.w, frame.h, now) : [];
  return (
    <div class="roomcam">
      <h3 class="roomcam__title">Room camera: {name}</h3>
      {frame ? (
        <div class="camera camera--room" style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
          <FrameCanvas frame={frame} deps={drawDeps} class="camera__canvas" />
          <svg class="camera__overlay" viewBox={`0 0 ${frame.w} ${frame.h}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
            {shapes.map((s, i) => <OverlayShape key={i} shape={s} labelSize={Math.max(12, Math.round(frame.w / 36))} />)}
          </svg>
          <span class="camera__age">{frameAge(frame.ts, now)}</span>
        </div>
      ) : (
        <p class="roomcam__empty" role="status">Connected, no picture yet.</p>
      )}
    </div>
  );
}

// ---------- the conversation: chat bubbles ----------
function Chat({ lines }: { lines: TranscriptLine[] }) {
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
    return <p class="chat__empty">Nothing said yet. Sam's words, the customer's words and AIRA's replies appear here.</p>;
  }
  return (
    <ol class="chat__list" ref={ref} aria-live="polite" aria-relevant="additions text">
      {lines.map((l) => (
        <li key={l.id} class={`msg msg--${l.role}${l.final ? "" : " msg--partial"}`}>
          {l.role === "aira" && <span class="msg__avatar" aria-hidden="true">A</span>}
          <div class="msg__bubble">
            <span class="msg__meta">
              <span class="msg__who">{SPEAKER[l.role]}</span>
              {l.lang && <span class="msg__lang">{l.lang}</span>}
            </span>
            <p class="msg__text">{l.text}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function Captions({ lines }: { lines: TranscriptLine[] }) {
  const shown = lines.slice(-3);
  if (shown.length === 0) return <div class="captions" aria-live="polite" />;
  return (
    <ol class="captions" aria-live="polite">
      {shown.map((l) => (
        <li key={l.id} class={`caption caption--${l.role}${l.final ? "" : " caption--partial"}`}>
          <span class="caption__who">{SPEAKER[l.role]}</span>
          <span class="caption__text">{l.text}</span>
        </li>
      ))}
    </ol>
  );
}

// ---------- the activity timeline: a slim strip of icon chips ----------
const ICON: Record<ActivityTag, string> = {
  // 16x16 stroke icons: what each kind of activity is, not who is talking.
  sam: "M8 1.5a2.5 2.5 0 0 0-2.5 2.5v4a2.5 2.5 0 0 0 5 0V4A2.5 2.5 0 0 0 8 1.5zM3.5 7.5A4.5 4.5 0 0 0 8 12a4.5 4.5 0 0 0 4.5-4.5M8 12v2.5M5.5 14.5h5",
  aira: "M8 1.5l1.6 4.4L14 7.5l-4.4 1.6L8 13.5 6.4 9.1 2 7.5l4.4-1.6zM13 1.5v2M14 2.5h-2",
  customer: "M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 14.5a5.5 5.5 0 0 1 11 0",
  tool: "M10.5 2.5a3 3 0 0 0-3.9 3.6L2 10.7V14h3.3l4.6-4.6a3 3 0 0 0 3.6-3.9l-2 2-1.8-.4-.4-1.8z",
  vision: "M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  order: "M2 3.5h2l1.6 7h6.9l1.5-5H5M6.5 14a1 1 0 1 0 0-2 1 1 0 0 0 0 2zM12 14a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
};
const TAG_NAME: Record<ActivityTag, string> = { sam: "Sam", aira: "AIRA", customer: "Customer", tool: "Tool", vision: "Vision", order: "Order" };

function Timeline({ items }: { items: ActivityItem[] }) {
  const ref = useRef<HTMLOListElement>(null);
  const last = items.at(-1);
  useEffect(() => {
    try {
      const el = ref.current;
      if (el) el.scrollLeft = el.scrollWidth;
    } catch {
      /* jsdom */
    }
  }, [last?.id]);
  return (
    <section class="tl" aria-label="Activity">
      {items.length === 0 ? (
        <p class="tl__empty">What AIRA does shows up here: searching, pointing, counting, confirming.</p>
      ) : (
        <ol class="tl__list" ref={ref}>
          {items.slice(-40).map((a) => (
            <li key={a.id} class={`tl__item tl__item--${a.tag}`} title={a.detail || undefined}>
              <svg class="tl__icon" viewBox="0 0 16 16" aria-hidden="true"><path d={ICON[a.tag]} /></svg>
              <span class="tl__body">
                <span class="tl__label">{eventLabel(a.name)}</span>
                {a.detail && <span class="tl__detail">{a.detail}</span>}
              </span>
              <span class="tl__time"><span class="sr-only">{TAG_NAME[a.tag]}, </span>{formatTimeIST(a.ts)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
