// The Live page: mission control for one AIRA session, built for a projector. The camera is the point of the
// page; the stepper in the header says what job AIRA is on; four cards on the right say what it is looking
// for, how hard it is working, what was said and what it did. Record mode strips everything but the camera
// and the captions into a clean 16:9 for screen recording.
import { useEffect, useRef, useState } from "preact/hooks";
import { formatTimeIST } from "../lib/format";
import { doneIndex, stepDurations, type Task } from "../lib/liveTask";
import {
  eventLabel, frameAge, overlayShapes, sessionClock, targetInfo, type ActivityItem, type ActivityTag, type Connection,
  type Frame, type LiveState, type MonitorRole, type MonitorState, type Shape, type TranscriptLine,
} from "../lib/monitor";

export interface LivePageProps {
  state: LiveState;
  conn: Connection;
  /** True when no monitor key is known yet: the page asks for one instead of connecting. */
  keyNeeded: boolean;
  onKey: (key: string) => void;
  /** Picks another of the shop's live sessions (`state.sessions`) to follow. */
  onFollow?: (sessionId: string) => void;
  /** Record mode: a clean 16:9 view without the dashboard chrome. */
  record?: boolean;
  onRecord?: (on: boolean) => void;
  now?: () => number;
}

const SPEAKER: Record<MonitorRole, string> = { owner: "Sam", customer: "Customer", aira: "AIRA" };
const TAG: Record<ActivityTag, string> = { sam: "Sam", aira: "AIRA", customer: "Customer", tool: "Tool", vision: "Vision", order: "Order" };

const STATUS_TEXT: Record<MonitorState, string> = {
  listening: "Listening", thinking: "Thinking", guiding: "Guiding", found: "Found", idle: "Idle",
};

const CONN_TEXT: Record<Connection, string> = {
  waiting: "Not connected", connecting: "Connecting…", live: "Connected", offline: "Reconnecting…", rejected: "Key refused",
};

const LANG_NAME: Record<string, string> = { ta: "Tamil", hi: "Hindi", en: "English" };

/** A clock that ticks four times a second while the page is open, for the T+ timer and the frame age. */
function useNow(now: () => number): number {
  const [t, setT] = useState(now);
  useEffect(() => {
    const h = setInterval(() => setT(now()), 250);
    return () => clearInterval(h);
  }, []);
  return t;
}

export function LivePage({ state, conn, keyNeeded, onKey, onFollow, record = false, onRecord, now = Date.now }: LivePageProps) {
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
          {room && <RoomCamera name={room[1].name} frame={room[1].frame} state={room[1]} now={t} />}
          <div class="record__sam">
            {frame ? <Camera state={{ ...state, frame }} now={t} /> : <Waiting conn={conn} />}
          </div>
        </div>
        <Captions lines={state.transcript} />
        <button class="record__exit" type="button" onClick={() => onRecord?.(false)}>Exit record mode</button>
      </div>
    );
  }

  return (
    <div class="livepage">
      <header class="mc">
        <div class="mc__id">
          <h2 class="mc__title">AIRA Live</h2>
          <p class="mc__sub">Live agent for a blind shopkeeper</p>
        </div>
        <Stepper task={state.task} now={t} />
        <div class="mc__phone">
          <span class={`mc__dot mc__dot--${state.session ? "on" : conn}`} aria-hidden="true" />
          <span class="mc__phone-name">Sam's phone</span>
          <span class="mc__conn" role="status">{state.session ? "Session open" : CONN_TEXT[conn]}</span>
          <SessionPicker sessions={state.sessions} current={state.session} onFollow={onFollow} />
        </div>
        <span class="mc__demo">Demo</span>
      </header>

      <section class="stage" aria-label="Sam's camera">
        <div class="stage__view">
          {keyNeeded ? <KeyPrompt onKey={onKey} /> : frame ? <Camera state={{ ...state, frame }} now={t} /> : <Waiting conn={conn} />}
          <Hud state={state} now={t} paused={paused} />
        </div>
        <Legend />
      </section>

      <aside class="cards">
        <TargetCard state={state} />
        <StatsCard state={state} now={t} />
        <section class="card card--talk" aria-labelledby="talk-title">
          <h3 id="talk-title" class="card__title">Conversation</h3>
          <Transcript lines={state.transcript} />
        </section>
        <section class="card card--log" aria-labelledby="activity-title">
          <h3 id="activity-title" class="card__title">Activity log</h3>
          <Activity items={state.activity} />
        </section>
      </aside>

      {room && (
        <section class="room" aria-label="Room camera">
          <RoomCamera name={room[1].name} frame={room[1].frame} state={room[1]} now={t} />
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

      <footer class="bar">
        <button class="bar__btn" type="button" onClick={() => onRecord?.(true)}>Record mode</button>
        <button class="bar__btn" type="button" aria-pressed={paused} onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume camera" : "Pause camera"}
        </button>
        <span class="bar__hint">Record mode shows the cameras and captions only, in 16:9.</span>
      </footer>
    </div>
  );
}

// ---------- header ----------
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
function Waiting({ conn }: { conn: Connection }) {
  return (
    <div class="stage__empty" role="status">
      <span class="stage__pulse" aria-hidden="true" />
      <p class="stage__empty-title">Waiting for Sam's phone…</p>
      <p class="stage__empty-sub">
        {conn === "rejected"
          ? "The monitor key was refused. Open this page with the right #key= link."
          : conn === "live"
            ? "Connected. The picture appears when Sam starts AIRA."
            : conn === "waiting"
              ? "AIRA Live is not configured for this dashboard."
              : "Connecting to AIRA Live."}
      </p>
    </div>
  );
}

/** Shown only when the shop has more than one live phone session: the page follows one at a time. */
function SessionPicker({ sessions, current, onFollow }: { sessions: LiveState["sessions"]; current: string | null; onFollow?: (id: string) => void }) {
  const ids = Object.keys(sessions);
  if (ids.length < 2) return null;
  return (
    <select class="mc__session" aria-label="Session to follow" value={current ?? ""} onChange={(e) => onFollow?.((e.target as HTMLSelectElement).value)}>
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

function Hud({ state, now, paused }: { state: LiveState; now: number; paused: boolean }) {
  return (
    <div class="hud" aria-label="Session">
      <span class={`hud__item hud__mode hud__mode--${state.status}`} role="status" aria-label={`AIRA is ${STATUS_TEXT[state.status].toLowerCase()}`}>
        <span class="hud__dot" aria-hidden="true" />{STATUS_TEXT[state.status]}
      </span>
      <span class="hud__item">{state.lang ? LANG_NAME[state.lang] ?? state.lang : "Language —"}</span>
      <span class="hud__item hud__clock">{sessionClock(state.sessionStartedAt, now)}</span>
      {paused && <span class="hud__item hud__paused">Paused</span>}
    </div>
  );
}

const TONE_CLASS = { object: "ov--object", fast: "ov--fast", danger: "ov--danger", target: "ov--target", found: "ov--found", hand: "ov--hand" } as const;

/** The latest frame with the vision overlay drawn in the frame's own pixel space, so it scales with the picture. */
function Camera({ state, now }: { state: LiveState; now: number }) {
  const { frame } = state;
  if (!frame) return null;
  const shapes = overlayShapes(state, frame.w, frame.h, now);
  const labelSize = Math.max(14, Math.round(frame.w / 30));
  const danger = shapes.some((s) => s.kind === "box" && s.tone === "danger");
  return (
    <div class={`camera${danger ? " camera--danger" : ""}`} style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
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
      <span class="camera__age">{frameAge(frame.ts, now)}</span>
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

function RoomCamera({ name, frame, state, now }: { name: string; frame: Frame | null; state: { mediapipe: LiveState["mediapipe"] }; now: number }) {
  const shapes = frame ? overlayShapes({ er2: { objects: [], ts: 0 }, mediapipe: state.mediapipe, target: null, found: false, hand: null, status: "idle" }, frame.w, frame.h, now) : [];
  return (
    <div class="roomcam">
      <h3 class="roomcam__title">Room camera: {name}</h3>
      {frame ? (
        <div class="camera camera--room" style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
          <img class="camera__img" src={frame.src} width={frame.w} height={frame.h} alt="" />
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

// ---------- cards ----------
function TargetCard({ state }: { state: LiveState }) {
  const info = targetInfo(state);
  return (
    <section class="card card--target" aria-labelledby="target-title">
      <div class="card__head">
        <h3 id="target-title" class="card__title">Target</h3>
        <span class="engine">Gemini Robotics-ER 2</span>
      </div>
      {!info ? (
        <p class="card__empty">Nothing being looked for. The target appears when Sam asks for an item.</p>
      ) : (
        <dl class="target">
          <div class="target__focus">
            <dt>Focus</dt>
            <dd class="target__label">
              <span class={`target__seen target__seen--${info.found ? "found" : info.visible ? "on" : "off"}`} aria-hidden="true" />
              {info.label}{" "}
              <span class="target__vis">{info.found ? "in hand" : info.visible ? "in view" : "not in view"}</span>
            </dd>
          </div>
          <div>
            <dt>Confidence</dt>
            <dd>
              <span class="meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={info.confidence === null ? undefined : Math.round(info.confidence * 100)} aria-label="Confidence">
                <span class="meter__fill" style={{ width: `${info.confidence === null ? 0 : Math.round(info.confidence * 100)}%` }} />
              </span>
              <span class="target__num">{info.confidence === null ? "—" : `${Math.round(info.confidence * 100)}%`}</span>
            </dd>
          </div>
          <div>
            <dt>Distance</dt>
            <dd>{info.distance === null ? "—" : info.distance === "arm" ? "Arm's length" : info.distance === "near" ? "Within reach" : "A step away"}</dd>
          </div>
          <div>
            <dt>Direction</dt>
            <dd>{info.side === null ? "—" : `${cap(info.side)}, ${info.shelf} shelf`}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function StatsCard({ state, now }: { state: LiveState; now: number }) {
  const st = state.stats;
  const busy = state.status === "thinking" || state.status === "guiding";
  return (
    <section class="card card--stats" aria-labelledby="stats-title">
      <div class="card__head">
        <h3 id="stats-title" class="card__title">Session stats</h3>
        <span class={`pill pill--${busy ? "busy" : "idle"}`} role="status">{busy ? "Executing" : "Idle"}</span>
      </div>
      <dl class="stats">
        <div><dt>Session time</dt><dd>{sessionClock(state.sessionStartedAt, now).slice(2)}</dd></div>
        <div><dt>Live API turns</dt><dd>{st ? st.turns : "—"}</dd></div>
        <div><dt>ER 2 calls</dt><dd>{st ? st.er2Calls : "—"}</dd></div>
        <div><dt>Flash calls</dt><dd>{st ? st.flashCalls : "—"}</dd></div>
        <div><dt>Vision latency</dt><dd>{st && st.visionMs !== null ? `${(st.visionMs / 1000).toFixed(1)}s` : "—"}</dd></div>
        <div><dt>Frames/s</dt><dd>{st ? st.fps.toFixed(1) : "—"}</dd></div>
      </dl>
    </section>
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
    return <p class="card__empty talk__empty">Nothing said yet. Sam's words, the customer's words and AIRA's replies appear here.</p>;
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

function Activity({ items }: { items: ActivityItem[] }) {
  const ref = useRef<HTMLOListElement>(null);
  const last = items.at(-1);
  useEffect(() => {
    try {
      const el = ref.current;
      if (el) el.scrollTop = el.scrollHeight;
    } catch {
      /* jsdom */
    }
  }, [last?.id]);
  if (items.length === 0) return <p class="card__empty activity__empty">What AIRA does shows up here: searching, pointing, counting, confirming.</p>;
  return (
    <ol class="activity__list" ref={ref}>
      {items.slice(-40).map((a) => (
        <li key={a.id} class={`act act--${a.tag}`}>
          <span class="act__time">{formatTimeIST(a.ts)}</span>
          <span class="act__tag">{TAG[a.tag]}</span>
          <span class="act__label">{eventLabel(a.name)}</span>
          {a.detail && <span class="act__detail">{a.detail}</span>}
        </li>
      ))}
    </ol>
  );
}
